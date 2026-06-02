"""Date helpers + a lazily-loaded A-share trading calendar.

The calendar is fetched once from akshare (``tool_trade_date_hist_sina``) and cached
in-process. If akshare is unavailable we fall back to "weekday" approximation so the
server keeps working (clearly a degraded heuristic, not holiday-aware).
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta

from loguru import logger

__all__ = [
    "today_str",
    "now_str",
    "to_compact",
    "to_dashed",
    "parse_date",
    "trade_dates",
    "latest_trade_date",
    "recent_trade_dates",
    "is_trade_day",
]

_RELATIVE_TODAY = {"today", "今天", "今日", "now", "最新", "", None}

_calendar_lock = threading.Lock()
_calendar: list[date] | None = None


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def to_compact(s: str) -> str:
    """``YYYY-MM-DD`` (or already compact) -> ``YYYYMMDD``."""
    if s is None:
        return ""
    return str(s).strip().replace("-", "").replace("/", "")[:8]


def to_dashed(s: str) -> str:
    """``YYYYMMDD`` -> ``YYYY-MM-DD`` (idempotent for already-dashed input)."""
    if s is None:
        return ""
    c = to_compact(s)
    if len(c) == 8:
        return f"{c[0:4]}-{c[4:6]}-{c[6:8]}"
    return str(s)


def parse_date(s: str | None, default_today: bool = True) -> date:
    """Parse a flexible date string into a :class:`datetime.date`.

    Accepts ``YYYY-MM-DD``, ``YYYYMMDD``, ``YYYY/MM/DD`` and relative words
    ("today"/"今天"/"最新"/empty -> today).
    """
    if s in _RELATIVE_TODAY:
        return datetime.now().date() if default_today else None  # type: ignore[return-value]
    txt = str(s).strip()
    if txt in _RELATIVE_TODAY:
        return datetime.now().date()
    c = to_compact(txt)
    if len(c) == 8 and c.isdigit():
        try:
            return datetime.strptime(c, "%Y%m%d").date()
        except ValueError:
            pass
    raise ValueError(f"unrecognized date: {s!r} (use YYYY-MM-DD or YYYYMMDD)")


def _load_calendar() -> list[date]:
    global _calendar
    if _calendar is not None:
        return _calendar
    with _calendar_lock:
        if _calendar is not None:
            return _calendar
        try:
            import akshare as ak

            df = ak.tool_trade_date_hist_sina()
            col = "trade_date" if "trade_date" in df.columns else df.columns[0]
            days: list[date] = []
            for v in df[col].tolist():
                if isinstance(v, (datetime, date)):
                    days.append(v if isinstance(v, date) and not isinstance(v, datetime) else v.date())
                else:
                    days.append(datetime.strptime(str(v)[:10].replace("/", "-"), "%Y-%m-%d").date())
            _calendar = sorted(days)
            logger.debug(f"trading calendar loaded: {len(_calendar)} days "
                         f"({_calendar[0]} .. {_calendar[-1]})")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"failed to load trading calendar from akshare ({e}); "
                           f"using weekday approximation")
            _calendar = []
    return _calendar


def trade_dates() -> list[date]:
    return _load_calendar()


def is_trade_day(d: date | None = None) -> bool:
    d = d or datetime.now().date()
    cal = _load_calendar()
    if cal:
        return d in set(cal)
    return d.weekday() < 5  # degraded heuristic


def latest_trade_date(ref: date | None = None) -> date:
    """Most recent trading day on/before ``ref`` (default: today)."""
    ref = ref or datetime.now().date()
    cal = _load_calendar()
    if cal:
        prior = [d for d in cal if d <= ref]
        if prior:
            return prior[-1]
        return cal[0]
    d = ref
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def recent_trade_dates(n: int, ref: date | None = None) -> list[date]:
    """The ``n`` most recent trading days on/before ``ref`` (ascending)."""
    ref = ref or datetime.now().date()
    cal = _load_calendar()
    if cal:
        prior = [d for d in cal if d <= ref]
        return prior[-n:] if prior else []
    out: list[date] = []
    d = ref
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))
