"""Lightweight A-share realtime quote adapters.

The old realtime tool used ``stock_zh_a_spot_em`` which downloads the full A-share
market from Eastmoney before filtering a few requested symbols. That endpoint is
frequently rate-limited / disconnected and is a poor fit for a remote MCP server.

This module instead queries only the requested symbols:

1. Tencent Finance ``qt.gtimg.cn`` (primary, one batched request)
2. Sina Finance ``hq.sinajs.cn`` (fallback for symbols Tencent missed)

Both calls have short hard timeouts so a broken upstream cannot hold the MCP request
open until Dify/plugin deadlines expire.
"""

from __future__ import annotations

import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ._base import DataUnavailableError
from ..utils import codes

TENCENT_URL = "https://qt.gtimg.cn/q={symbols}"
SINA_URL = "https://hq.sinajs.cn/rn={nonce}&list={symbols}"
DEFAULT_TIMEOUT_SECONDS = 4.5
MAX_SYMBOLS_PER_REQUEST = 50

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"
    ),
    "Accept": "text/plain,*/*;q=0.8",
}


def _float(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"--", "-", "None", "null"}:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _field(fields: list[str], index: int) -> str | None:
    return fields[index].strip() if 0 <= index < len(fields) else None


def _shares_to_hands(value: str | None) -> float | None:
    number = _float(value)
    return number / 100 if number is not None else None


def _format_tencent_time(raw: str | None) -> str | None:
    text = (raw or "").strip()
    if len(text) >= 14 and text[:14].isdigit():
        t = text[:14]
        return f"{t[0:4]}-{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}:{t[12:14]}"
    return text or None


def _amount_from_tencent(fields: list[str]) -> float | None:
    # Field 35 commonly contains price/volume/amount with amount already in yuan.
    packed = _field(fields, 35)
    if packed and "/" in packed:
        pieces = packed.split("/")
        if len(pieces) >= 3:
            amount = _float(pieces[2])
            if amount is not None:
                return amount
    # Field 37 is conventionally ten-thousand yuan.
    amount_wan = _float(_field(fields, 37))
    return amount_wan * 10_000 if amount_wan is not None else None


def _market_cap_yuan(value: str | None) -> float | None:
    # Tencent A-share fields 44/45 are conventionally hundred-million CNY.
    number = _float(value)
    return number * 100_000_000 if number is not None else None


def _tencent_bid_ask(fields: list[str]) -> dict:
    out: dict[str, float | None] = {}
    for level in range(1, 6):
        bid_price_index = 9 + (level - 1) * 2
        bid_volume_index = bid_price_index + 1
        ask_price_index = 19 + (level - 1) * 2
        ask_volume_index = ask_price_index + 1
        out[f"bid_{level}"] = _float(_field(fields, bid_price_index))
        out[f"bid_{level}_vol"] = _float(_field(fields, bid_volume_index))
        out[f"ask_{level}"] = _float(_field(fields, ask_price_index))
        out[f"ask_{level}_vol"] = _float(_field(fields, ask_volume_index))
    return out


def _parse_tencent(text: str) -> dict[str, dict]:
    quotes: dict[str, dict] = {}
    for match in re.finditer(r'v_([a-zA-Z0-9_]+)="([^"]*)";', text):
        raw_symbol, payload = match.groups()
        fields = payload.split("~")
        if len(fields) < 35:
            continue
        code = (_field(fields, 2) or "").strip()
        if not code.isdigit() or len(code) != 6:
            code_match = re.search(r"(\d{6})", raw_symbol)
            if not code_match:
                continue
            code = code_match.group(1)

        price = _float(_field(fields, 3))
        prev_close = _float(_field(fields, 4))
        change = _float(_field(fields, 31))
        pct_change = _float(_field(fields, 32))
        if change is None and price is not None and prev_close not in {None, 0}:
            change = price - prev_close
        if pct_change is None and price is not None and prev_close not in {None, 0}:
            pct_change = (price / prev_close - 1) * 100

        quotes[code] = {
            "code": code,
            "name": _field(fields, 1) or None,
            "price": price,
            "pct_change": pct_change,
            "change": change,
            "open": _float(_field(fields, 5)),
            "high": _float(_field(fields, 33)),
            "low": _float(_field(fields, 34)),
            "prev_close": prev_close,
            "volume": _float(_field(fields, 6)),  # hands, same convention as Eastmoney spot
            "amount": _amount_from_tencent(fields),
            "turnover_rate": _float(_field(fields, 38)),
            "volume_ratio": _float(_field(fields, 49)),
            "pe_ttm": _float(_field(fields, 39)),
            "pb": _float(_field(fields, 46)),
            "total_market_cap": _market_cap_yuan(_field(fields, 45)),
            "float_market_cap": _market_cap_yuan(_field(fields, 44)),
            "amplitude": _float(_field(fields, 43)),
            "limit_up": _float(_field(fields, 47)),
            "limit_down": _float(_field(fields, 48)),
            "quote_time": _format_tencent_time(_field(fields, 30)),
            "bid_ask": _tencent_bid_ask(fields),
            "is_realtime": True,
            "source": "Tencent Finance",
        }
    return quotes


def _sina_bid_ask(fields: list[str]) -> dict:
    out: dict[str, float | None] = {}
    # Sina bid/ask volumes are shares; convert to hands to match the Tencent/Eastmoney
    # convention used by the existing A-share tool output.
    for level in range(1, 6):
        bid_volume_index = 10 + (level - 1) * 2
        bid_price_index = bid_volume_index + 1
        ask_volume_index = 20 + (level - 1) * 2
        ask_price_index = ask_volume_index + 1
        out[f"bid_{level}"] = _float(_field(fields, bid_price_index))
        out[f"bid_{level}_vol"] = _shares_to_hands(_field(fields, bid_volume_index))
        out[f"ask_{level}"] = _float(_field(fields, ask_price_index))
        out[f"ask_{level}_vol"] = _shares_to_hands(_field(fields, ask_volume_index))
    return out


def _parse_sina(text: str) -> dict[str, dict]:
    quotes: dict[str, dict] = {}
    for match in re.finditer(r'var\s+hq_str_([a-zA-Z0-9_]+)="([^"]*)";', text):
        raw_symbol, payload = match.groups()
        fields = [piece.strip() for piece in payload.split(",")]
        if len(fields) < 32 or not fields[0]:
            continue
        code_match = re.search(r"(\d{6})", raw_symbol)
        if not code_match:
            continue
        code = code_match.group(1)
        price = _float(_field(fields, 3))
        prev_close = _float(_field(fields, 2))
        change = None
        pct_change = None
        if price is not None and prev_close not in {None, 0}:
            change = price - prev_close
            pct_change = (price / prev_close - 1) * 100
        quote_date = _field(fields, 30)
        quote_clock = _field(fields, 31)
        quote_time = " ".join(x for x in (quote_date, quote_clock) if x) or None

        high = _float(_field(fields, 4))
        low = _float(_field(fields, 5))
        quotes[code] = {
            "code": code,
            "name": _field(fields, 0) or None,
            "price": price,
            "pct_change": pct_change,
            "change": change,
            "open": _float(_field(fields, 1)),
            "high": high,
            "low": low,
            "prev_close": prev_close,
            "volume": _shares_to_hands(_field(fields, 8)),
            "amount": _float(_field(fields, 9)),  # yuan
            "turnover_rate": None,
            "volume_ratio": None,
            "pe_ttm": None,
            "pb": None,
            "total_market_cap": None,
            "float_market_cap": None,
            "amplitude": (
                (high - low) / prev_close * 100
                if high is not None and low is not None and prev_close not in {None, 0}
                else None
            ),
            "limit_up": None,
            "limit_down": None,
            "quote_time": quote_time,
            "bid_ask": _sina_bid_ask(fields),
            "is_realtime": True,
            "source": "Sina Finance",
        }
    return quotes


def _http_get(url: str, *, timeout: float, referer: str) -> str:
    headers = {**_HEADERS, "Referer": referer}
    req = Request(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=timeout) as response:  # noqa: S310 - fixed provider URLs
            raw = response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise DataUnavailableError(f"HTTP quote provider failed: {type(exc).__name__}: {exc}") from exc

    for encoding in ("gb18030", "gbk", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _chunks(values: list[str], size: int = MAX_SYMBOLS_PER_REQUEST):
    for i in range(0, len(values), size):
        yield values[i : i + size]


def tencent_quotes(symbols: list[str], *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for batch in _chunks(symbols):
        provider_symbols = [codes.with_prefix(symbol) for symbol in batch]
        url = TENCENT_URL.format(symbols=",".join(provider_symbols))
        text = _http_get(url, timeout=timeout, referer="https://gu.qq.com/")
        found.update(_parse_tencent(text))
    return found


def sina_quotes(symbols: list[str], *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for batch in _chunks(symbols):
        provider_symbols = [codes.with_prefix(symbol) for symbol in batch]
        url = SINA_URL.format(
            nonce=str(int(time.time() * 1000)),
            symbols=",".join(provider_symbols),
        )
        text = _http_get(url, timeout=timeout, referer="https://finance.sina.com.cn/")
        found.update(_parse_sina(text))
    return found


def realtime_quotes(symbols: list[str]) -> tuple[dict[str, dict], list[str]]:
    """Fetch requested symbols with Tencent primary and Sina fallback.

    Returns ``(quotes_by_code, provider_errors)``. Provider failures are kept as
    metadata so callers can expose partial success without failing the whole request.
    The worst-case provider wait is bounded to roughly two short timeouts instead of
    the minute-scale waits seen with the full-market Eastmoney endpoint.
    """
    normalized = list(dict.fromkeys(codes.normalize(symbol) for symbol in symbols))
    if not normalized:
        return {}, []

    provider_errors: list[str] = []
    found: dict[str, dict] = {}

    try:
        found.update(tencent_quotes(normalized))
    except DataUnavailableError as exc:
        provider_errors.append(f"Tencent: {exc}")

    missing = [symbol for symbol in normalized if symbol not in found]
    if missing:
        try:
            found.update(sina_quotes(missing))
        except DataUnavailableError as exc:
            provider_errors.append(f"Sina: {exc}")

    return found, provider_errors
