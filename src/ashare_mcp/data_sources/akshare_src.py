"""Thin akshare wrappers. Each returns JSON-friendly dict / list[dict] (no DataFrames).

Function-name candidate lists make these resilient to akshare's interface renames.
Confirmed against akshare 1.18.64. Code/date normalization happens here so the tool
layer can pass user-friendly inputs.
"""

from __future__ import annotations

from ._base import DataUnavailableError, df_kv_to_dict, df_to_records
from ._retry import call_ak
from ..utils import codes, dates

# Fragile Eastmoney analytics endpoints should never be allowed to consume the whole
# Dify/MCP deadline. One short attempt is enough; the tool layer will return a clean
# data_source_unavailable result and the agent can continue with other evidence.
FAST_EM_TIMEOUT_SECONDS = 7.0
SLOW_EM_TIMEOUT_SECONDS = 15.0


def _call_em_fast(candidates: str | list[str], **kwargs):
    return call_ak(
        candidates,
        timeout_seconds=FAST_EM_TIMEOUT_SECONDS,
        attempts=1,
        **kwargs,
    )


def _call_em_slow(candidates: str | list[str], **kwargs):
    return call_ak(
        candidates,
        timeout_seconds=SLOW_EM_TIMEOUT_SECONDS,
        attempts=1,
        **kwargs,
    )


# ---- per-call rename overrides (where COLUMN_MAP needs help) ----
_BID_ASK_RENAME = {
    "买一": "bid_1", "买二": "bid_2", "买三": "bid_3", "买四": "bid_4", "买五": "bid_5",
    "卖一": "ask_1", "卖二": "ask_2", "卖三": "ask_3", "卖四": "ask_4", "卖五": "ask_5",
    "买1": "bid_1", "买2": "bid_2", "买3": "bid_3", "买4": "bid_4", "买5": "bid_5",
    "卖1": "ask_1", "卖2": "ask_2", "卖3": "ask_3", "卖4": "ask_4", "卖5": "ask_5",
    "buy_1": "bid_1", "buy_2": "bid_2", "buy_3": "bid_3", "buy_4": "bid_4", "buy_5": "bid_5",
    "sell_1": "ask_1", "sell_2": "ask_2", "sell_3": "ask_3", "sell_4": "ask_4", "sell_5": "ask_5",
}


# --------------------------------------------------------------------------- #
# A. Market
# --------------------------------------------------------------------------- #
def spot_lookup(symbols: list[str]) -> list[dict]:
    wanted = {codes.normalize(s) for s in symbols}
    df = call_ak("stock_zh_a_spot_em")
    code_col = "代码" if "代码" in df.columns else df.columns[1]
    sub = df[df[code_col].astype(str).isin(wanted)]
    return df_to_records(sub)


def spot_top(limit: int) -> list[dict]:
    df = call_ak("stock_zh_a_spot_em")
    return df_to_records(df, limit=limit)


def bid_ask(symbol: str) -> dict:
    df = call_ak("stock_bid_ask_em", symbol=codes.normalize(symbol))
    return df_kv_to_dict(df, rename=_BID_ASK_RENAME)


def individual_info(symbol: str) -> dict:
    df = call_ak("stock_individual_info_em", symbol=codes.normalize(symbol))
    return df_kv_to_dict(df)


def daily_hist(symbol: str, start_date: str, end_date: str, adjust: str = "qfq") -> list[dict]:
    df = call_ak(
        "stock_zh_a_hist",
        symbol=codes.normalize(symbol),
        period="daily",
        start_date=dates.to_compact(start_date),
        end_date=dates.to_compact(end_date),
        adjust=adjust or "",
    )
    return df_to_records(df)


def minute_hist(
    symbol: str, period: str, start_date: str, end_date: str, adjust: str = ""
) -> list[dict]:
    df = call_ak(
        "stock_zh_a_hist_min_em",
        symbol=codes.normalize(symbol),
        period=str(period),
        start_date=start_date,
        end_date=end_date,
        adjust=adjust or "",
    )
    return df_to_records(df)


# --------------------------------------------------------------------------- #
# B. Fund flow
# --------------------------------------------------------------------------- #
def individual_fund_flow(symbol: str) -> list[dict]:
    df = _call_em_fast(
        "stock_individual_fund_flow",
        stock=codes.normalize(symbol),
        market=codes.market_of(symbol),
    )
    return df_to_records(df)


def market_fund_flow() -> list[dict]:
    df = _call_em_fast("stock_market_fund_flow")
    return df_to_records(df)


def sector_fund_flow_rank(indicator: str, sector_type: str, limit: int | None = None) -> list[dict]:
    df = _call_em_fast(
        "stock_sector_fund_flow_rank",
        indicator=indicator,
        sector_type=sector_type,
    )
    return df_to_records(df, limit=limit)


def main_fund_flow_rank(symbol: str = "全部股票", limit: int | None = None) -> list[dict]:
    df = _call_em_fast("stock_main_fund_flow", symbol=symbol)
    return df_to_records(df, limit=limit)


# --------------------------------------------------------------------------- #
# C. LHB (dragon-tiger list)
# --------------------------------------------------------------------------- #
def lhb_detail(start_date: str, end_date: str, limit: int | None = None) -> list[dict]:
    df = _call_em_fast(
        "stock_lhb_detail_em",
        start_date=dates.to_compact(start_date),
        end_date=dates.to_compact(end_date),
    )
    return df_to_records(df, limit=limit)


def lhb_stock_detail(symbol: str, date: str, flag: str) -> list[dict]:
    df = _call_em_fast(
        "stock_lhb_stock_detail_em",
        symbol=codes.normalize(symbol),
        date=dates.to_compact(date),
        flag=flag,
    )
    return df_to_records(df)


def lhb_institution_daily(start_date: str, end_date: str, limit: int | None = None) -> list[dict]:
    df = _call_em_fast(
        "stock_lhb_jgmmtj_em",
        start_date=dates.to_compact(start_date),
        end_date=dates.to_compact(end_date),
    )
    return df_to_records(df, limit=limit)


def lhb_active_branches(start_date: str, end_date: str, limit: int | None = None) -> list[dict]:
    df = _call_em_fast(
        "stock_lhb_hyyyb_em",
        start_date=dates.to_compact(start_date),
        end_date=dates.to_compact(end_date),
    )
    return df_to_records(df, limit=limit)


# --------------------------------------------------------------------------- #
# D. Margin trading
# --------------------------------------------------------------------------- #
def margin_sse_summary(start_date: str, end_date: str, limit: int | None = None) -> list[dict]:
    df = call_ak(
        "stock_margin_sse",
        start_date=dates.to_compact(start_date),
        end_date=dates.to_compact(end_date),
    )
    return df_to_records(df, limit=limit)


def margin_szse_summary(date: str) -> list[dict]:
    df = call_ak("stock_margin_szse", date=dates.to_compact(date))
    return df_to_records(df)


def margin_detail_sse(date: str, limit: int | None = None) -> list[dict]:
    df = call_ak("stock_margin_detail_sse", date=dates.to_compact(date))
    return df_to_records(df, limit=limit)


def margin_detail_szse(date: str, limit: int | None = None) -> list[dict]:
    df = call_ak("stock_margin_detail_szse", date=dates.to_compact(date))
    return df_to_records(df, limit=limit)


# --------------------------------------------------------------------------- #
# E. HSGT (stock connect)
# --------------------------------------------------------------------------- #
def southbound_hist(limit: int | None = None) -> list[dict]:
    df = call_ak(["stock_hsgt_hist_em"], symbol="南向资金")
    return df_to_records(df, limit=limit)


def hsgt_summary() -> list[dict]:
    df = call_ak("stock_hsgt_fund_flow_summary_em")
    return df_to_records(df)


def northbound_top10(date: str, limit: int | None = None) -> list[dict]:
    """Best-effort northbound activity. Degraded since 2024-08-19 (no buy/sell split)."""
    df = call_ak(
        "stock_hsgt_stock_statistics_em",
        symbol="北向持股",
        start_date=dates.to_compact(date),
        end_date=dates.to_compact(date),
    )
    return df_to_records(df, limit=limit)


def northbound_holdings(symbol: str, limit: int | None = None) -> list[dict]:
    df = call_ak("stock_hsgt_individual_em", symbol=codes.normalize(symbol))
    return df_to_records(df, limit=limit)


# --------------------------------------------------------------------------- #
# F. Chip distribution
# --------------------------------------------------------------------------- #
def chip_distribution(symbol: str, adjust: str = "", limit: int | None = None) -> list[dict]:
    df = _call_em_fast("stock_cyq_em", symbol=codes.normalize(symbol), adjust=adjust or "")
    return df_to_records(df, limit=limit)


# --------------------------------------------------------------------------- #
# G. Financial / announcements / research
# --------------------------------------------------------------------------- #
_REPORT_FN = {
    "balance": "stock_balance_sheet_by_report_em",
    "income": "stock_profit_sheet_by_report_em",
    "cashflow": "stock_cash_flow_sheet_by_report_em",
}


def financial_report(symbol: str, report_type: str, period: str | None = None,
                     limit: int | None = None) -> list[dict]:
    fn = _REPORT_FN.get(report_type)
    if not fn:
        raise DataUnavailableError(f"unknown report_type {report_type!r}")
    df = call_ak(fn, symbol=codes.to_em(symbol))
    if period:
        p = dates.to_compact(period)
        for col in ("报告期", "REPORT_DATE", "报表日期"):
            if col in df.columns:
                df = df[df[col].astype(str).str.replace("-", "").str.startswith(p)]
                break
    return df_to_records(df, limit=limit)


def financial_abstract(symbol: str, limit: int | None = None) -> list[dict]:
    df = call_ak(["stock_financial_abstract"], symbol=codes.normalize(symbol))
    return df_to_records(df, limit=limit)


def earnings_forecast(date: str, limit: int | None = None) -> list[dict]:
    df = call_ak("stock_yjyg_em", date=dates.to_compact(date))
    return df_to_records(df, limit=limit)


def earnings_express(date: str, limit: int | None = None) -> list[dict]:
    df = call_ak("stock_yjkb_em", date=dates.to_compact(date))
    return df_to_records(df, limit=limit)


def announcements(symbol: str | None = None, limit: int = 20) -> list[dict]:
    """Recent announcements. Prefer per-stock disclosure (cninfo); fall back to the
    market-wide notice feed for the latest trading day filtered by code."""
    if symbol:
        code = codes.normalize(symbol)
        try:
            df = call_ak(
                ["stock_zh_a_disclosure_report_cninfo"],
                symbol=code,
                market="沪深京",
                start_date=dates.to_dashed(
                    (dates.recent_trade_dates(60) or [None])[0].strftime("%Y-%m-%d")
                    if dates.recent_trade_dates(60) else dates.today_str()
                ),
                end_date=dates.today_str(),
            )
            return df_to_records(df, limit=limit)
        except DataUnavailableError:
            pass
    # fallback: market-wide notices for latest trade day
    d = dates.to_compact(dates.latest_trade_date().strftime("%Y-%m-%d"))
    df = call_ak("stock_notice_report", symbol="全部", date=d)
    if symbol:
        code = codes.normalize(symbol)
        for col in ("代码", "股票代码"):
            if col in df.columns:
                df = df[df[col].astype(str) == code]
                break
    return df_to_records(df, limit=limit)


def research_reports(symbol: str, limit: int = 10) -> list[dict]:
    df = call_ak("stock_research_report_em", symbol=codes.normalize(symbol))
    return df_to_records(df, limit=limit)


# --------------------------------------------------------------------------- #
# H. Meta / sentiment
# --------------------------------------------------------------------------- #
def zt_pool(date: str, limit: int | None = None) -> list[dict]:
    df = _call_em_fast("stock_zt_pool_em", date=dates.to_compact(date))
    return df_to_records(df, limit=limit)


def stock_comment_all(limit: int | None = None) -> list[dict]:
    # stock_comment_em paginates across the whole market, so allow a little more time
    # than the other Eastmoney analytics calls, but never let it run unbounded.
    df = _call_em_slow("stock_comment_em")
    return df_to_records(df, limit=limit)


def stock_comment_one(symbol: str) -> dict:
    code = codes.normalize(symbol)
    # AkShare currently exposes 千股千评 as a market-wide paginated endpoint. Keep the
    # same semantics but cap the total wait so a slow Eastmoney response cannot take
    # down the Dify agent.
    df = _call_em_slow("stock_comment_em")
    code_col = "代码" if "代码" in df.columns else df.columns[1]
    sub = df[df[code_col].astype(str) == code]
    recs = df_to_records(sub)
    return recs[0] if recs else {}


def restricted_release(start_date: str, end_date: str, limit: int | None = None) -> list[dict]:
    df = call_ak(
        "stock_restricted_release_summary_em",
        start_date=dates.to_compact(start_date),
        end_date=dates.to_compact(end_date),
    )
    return df_to_records(df, limit=limit)
