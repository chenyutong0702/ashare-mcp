"""baostock fallback for K-line and financial reports (used only when akshare fails).

baostock is stateful (login/logout) and not thread-safe, so every call goes through a
process-wide lock and a single lazy login that is kept open for the process lifetime.
"""

from __future__ import annotations

import threading

from loguru import logger

from ._base import DataUnavailableError, EmptyDataError, df_to_records
from ..utils import codes, dates

_lock = threading.Lock()
_logged_in = False

# akshare adjust -> baostock adjustflag (1:后复权 2:前复权 3:不复权)
_ADJ = {"qfq": "2", "hfq": "1", "": "3"}

_FIN_FN = {
    "income": "query_profit_data",
    "balance": "query_balance_data",
    "cashflow": "query_cash_flow_data",
}


def _login():
    global _logged_in
    import baostock as bs

    if not _logged_in:
        r = bs.login()
        if getattr(r, "error_code", "1") != "0":
            raise DataUnavailableError(f"baostock login failed: {getattr(r, 'error_msg', '?')}")
        _logged_in = True
        logger.debug("baostock logged in")
    return bs


def _rs_to_df(rs):
    import pandas as pd

    if getattr(rs, "error_code", "1") != "0":
        raise DataUnavailableError(f"baostock query failed: {getattr(rs, 'error_msg', '?')}")
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        raise EmptyDataError("baostock returned empty")
    return pd.DataFrame(rows, columns=rs.fields)


def daily_kline(symbol: str, start_date: str, end_date: str, adjust: str = "qfq") -> list[dict]:
    import pandas as pd

    fields = "date,open,high,low,close,preclose,volume,amount,turn,pctChg"
    with _lock:
        bs = _login()
        rs = bs.query_history_k_data_plus(
            codes.to_baostock(symbol),
            fields,
            start_date=dates.to_dashed(start_date),
            end_date=dates.to_dashed(end_date),
            frequency="d",
            adjustflag=_ADJ.get(adjust or "", "3"),
        )
        df = _rs_to_df(rs)
    for c in ["open", "high", "low", "close", "preclose", "volume", "amount", "turn", "pctChg"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df_to_records(
        df,
        rename={"preclose": "prev_close", "turn": "turnover_rate", "pctChg": "pct_change"},
    )


def financial(symbol: str, report_type: str, year: int, quarter: int) -> list[dict]:
    bsname = _FIN_FN.get(report_type)
    if not bsname:
        raise DataUnavailableError(f"baostock has no fallback for report_type {report_type!r}")
    with _lock:
        bs = _login()
        fn = getattr(bs, bsname, None)
        if fn is None:
            raise DataUnavailableError(f"baostock missing {bsname}")
        rs = fn(code=codes.to_baostock(symbol), year=year, quarter=quarter)
        df = _rs_to_df(rs)
    return df_to_records(df)


def valuation_history(symbol: str, start_date: str, end_date: str) -> list[dict]:
    """Provider daily ratios; no reconstruction using today's financial statements."""
    import pandas as pd

    with _lock:
        bs = _login()
        rs = bs.query_history_k_data_plus(
            codes.to_baostock(symbol), "date,close,peTTM,pbMRQ,psTTM,tradestatus",
            start_date=dates.to_dashed(start_date), end_date=dates.to_dashed(end_date),
            frequency="d", adjustflag="3",
        )
        df = _rs_to_df(rs)
    df = df[df["tradestatus"].astype(str) == "1"].copy()
    for col in ("close", "peTTM", "pbMRQ", "psTTM"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df_to_records(df, rename={"peTTM": "pe_ttm", "pbMRQ": "pb", "psTTM": "ps_ttm"})
