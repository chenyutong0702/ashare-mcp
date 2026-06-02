"""Stock code normalization and market detection.

Accepts ``600519`` / ``sh600519`` / ``SH600519`` / ``600519.SH`` / ``600519.sh`` and
produces canonical forms used by the various data sources.
"""

from __future__ import annotations

import re

__all__ = [
    "normalize",
    "market_of",
    "with_prefix",
    "to_dot",
    "to_baostock",
    "to_em",
    "to_tushare",
    "is_valid",
    "InvalidSymbolError",
]


class InvalidSymbolError(ValueError):
    pass


_DIGITS_RE = re.compile(r"(\d{6})")


def normalize(symbol: str) -> str:
    """Return the canonical 6-digit code, e.g. ``"600519"``.

    Raises :class:`InvalidSymbolError` if no 6-digit code can be extracted.
    """
    if symbol is None:
        raise InvalidSymbolError("symbol is None")
    s = str(symbol).strip().upper()
    # strip common prefixes / suffixes
    s = s.replace("SH", "").replace("SZ", "").replace("BJ", "")
    s = s.replace(".", "").replace(" ", "")
    m = _DIGITS_RE.search(s)
    if not m:
        raise InvalidSymbolError(f"cannot parse a 6-digit A-share code from {symbol!r}")
    return m.group(1)


def market_of(symbol: str) -> str:
    """Return ``"sh"`` / ``"sz"`` / ``"bj"`` for a code (canonicalized first)."""
    code = normalize(symbol)
    head = code[0]
    head3 = code[:3]
    if head == "6" or head3 in {"900", "688", "689"}:
        return "sh"
    if head in {"0", "3", "2", "1"} or head3 in {"000", "001", "002", "003", "300", "301"}:
        return "sz"
    if head in {"4", "8"} or head3 in {"920", "430", "830", "870", "871", "872", "873"}:
        return "bj"
    # default by first digit
    return "sh" if head == "6" else "sz"


def is_valid(symbol: str) -> bool:
    try:
        normalize(symbol)
        return True
    except InvalidSymbolError:
        return False


def with_prefix(symbol: str) -> str:
    """``sh600519`` style (lowercase market prefix)."""
    return market_of(symbol) + normalize(symbol)


def to_dot(symbol: str) -> str:
    """``600519.SH`` style (uppercase suffix)."""
    return f"{normalize(symbol)}.{market_of(symbol).upper()}"


def to_tushare(symbol: str) -> str:
    """tushare ts_code, same as :func:`to_dot`."""
    return to_dot(symbol)


def to_baostock(symbol: str) -> str:
    """``sh.600519`` style used by baostock."""
    return f"{market_of(symbol)}.{normalize(symbol)}"


def to_em(symbol: str) -> str:
    """``SH600519`` style used by some eastmoney ``*_by_report_em`` endpoints."""
    return f"{market_of(symbol).upper()}{normalize(symbol)}"
