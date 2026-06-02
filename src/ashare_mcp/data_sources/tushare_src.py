"""Optional tushare fallback for financial statements. Active only when TUSHARE_TOKEN
is set AND the tushare package is installed (``uv sync --extra tushare``)."""

from __future__ import annotations

from loguru import logger

from ._base import DataUnavailableError, EmptyDataError, df_to_records
from ..config import settings
from ..utils import codes

_pro = None

_API = {"income": "income", "balance": "balancesheet", "cashflow": "cashflow"}


def is_available() -> bool:
    if not settings.tushare_token:
        return False
    try:
        import tushare  # noqa: F401

        return True
    except ImportError:
        return False


def _get_pro():
    global _pro
    if _pro is not None:
        return _pro
    if not settings.tushare_token:
        raise DataUnavailableError("TUSHARE_TOKEN not set")
    try:
        import tushare as ts
    except ImportError as e:
        raise DataUnavailableError(
            "tushare not installed; run: uv sync --extra tushare"
        ) from e
    ts.set_token(settings.tushare_token)
    _pro = ts.pro_api()
    logger.debug("tushare pro_api initialized")
    return _pro


def financial(symbol: str, report_type: str, period: str | None = None) -> list[dict]:
    api = _API.get(report_type)
    if not api:
        raise DataUnavailableError(f"tushare has no fallback for report_type {report_type!r}")
    pro = _get_pro()
    fn = getattr(pro, api)
    kwargs = {"ts_code": codes.to_tushare(symbol)}
    if period:
        kwargs["period"] = period.replace("-", "")
    df = fn(**kwargs)
    if df is None or df.empty:
        raise EmptyDataError("tushare returned empty")
    return df_to_records(df)
