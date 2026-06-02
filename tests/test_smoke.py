"""Minimal smoke tests — one per MCP tool — hitting real data sources.

Tolerant by design: a tool "passes" if it returns a dict that is either ``ok`` or a
*graceful* error (``data_source_unavailable`` / ``no_data``). Code bugs surface as
exceptions, ``internal_error`` or ``bad_request`` and will fail the test. The two
discontinued northbound tools must return ``discontinued``.

These tests require network access to akshare endpoints. Run:  uv run pytest
"""

from __future__ import annotations

import pytest

from ashare_mcp.utils import dates

TOLERATED = {"data_source_unavailable", "no_data"}


def _assert_ok_or_tolerated(r):
    assert isinstance(r, dict), f"expected dict, got {type(r)}"
    if r.get("error"):
        assert r["error"] in TOLERATED, f"unexpected error: {r}"
    else:
        assert r.get("ok") is True, f"missing ok flag: {r}"


@pytest.fixture(scope="session")
def d():
    rec = dates.recent_trade_dates(8) or []
    latest = dates.latest_trade_date().strftime("%Y-%m-%d")
    return {
        "latest": latest,
        "mid": rec[-4].strftime("%Y-%m-%d") if len(rec) >= 4 else latest,
        "start": rec[0].strftime("%Y-%m-%d") if rec else latest,
    }


# --- A. market ---
def test_get_stock_info():
    from ashare_mcp.tools.market import get_stock_info
    _assert_ok_or_tolerated(get_stock_info("600519"))


def test_get_realtime_quote():
    from ashare_mcp.tools.market import get_realtime_quote
    _assert_ok_or_tolerated(get_realtime_quote(["600519", "000001"]))


def test_get_daily_kline(d):
    from ashare_mcp.tools.market import get_daily_kline
    _assert_ok_or_tolerated(get_daily_kline("600519", d["start"], d["latest"]))


def test_get_minute_kline():
    from ashare_mcp.tools.market import get_minute_kline
    _assert_ok_or_tolerated(get_minute_kline("600519", "5"))


# --- B. fund flow ---
def test_get_individual_fund_flow():
    from ashare_mcp.tools.fundflow import get_individual_fund_flow
    _assert_ok_or_tolerated(get_individual_fund_flow("600519"))


def test_get_market_fund_flow():
    from ashare_mcp.tools.fundflow import get_market_fund_flow
    _assert_ok_or_tolerated(get_market_fund_flow())


def test_get_sector_fund_flow_rank():
    from ashare_mcp.tools.fundflow import get_sector_fund_flow_rank
    _assert_ok_or_tolerated(get_sector_fund_flow_rank("今日", "行业资金流", 5))


def test_get_main_fund_flow_rank():
    from ashare_mcp.tools.fundflow import get_main_fund_flow_rank
    _assert_ok_or_tolerated(get_main_fund_flow_rank("全部股票", 5))


# --- C. LHB ---
def test_get_lhb_daily(d):
    from ashare_mcp.tools.lhb import get_lhb_daily
    _assert_ok_or_tolerated(get_lhb_daily(d["start"], d["latest"], 5))


def test_get_lhb_stock_detail(d):
    from ashare_mcp.tools.lhb import get_lhb_stock_detail
    _assert_ok_or_tolerated(get_lhb_stock_detail("600519", d["mid"]))


def test_get_lhb_institution_daily(d):
    from ashare_mcp.tools.lhb import get_lhb_institution_daily
    _assert_ok_or_tolerated(get_lhb_institution_daily(d["mid"], 5))


def test_get_lhb_active_branches():
    from ashare_mcp.tools.lhb import get_lhb_active_branches
    _assert_ok_or_tolerated(get_lhb_active_branches("", "", 5))


# --- D. margin ---
def test_get_margin_summary(d):
    from ashare_mcp.tools.margin import get_margin_summary
    _assert_ok_or_tolerated(get_margin_summary("sh", d["mid"]))


def test_get_margin_stock_detail(d):
    from ashare_mcp.tools.margin import get_margin_stock_detail
    _assert_ok_or_tolerated(get_margin_stock_detail("sz", d["mid"], 5))


# --- E. HSGT ---
def test_get_southbound_flow():
    from ashare_mcp.tools.hsgt import get_southbound_flow
    _assert_ok_or_tolerated(get_southbound_flow(5))


def test_get_northbound_top10_today():
    from ashare_mcp.tools.hsgt import get_northbound_top10_today
    _assert_ok_or_tolerated(get_northbound_top10_today(""))


def test_get_northbound_holdings():
    from ashare_mcp.tools.hsgt import get_northbound_holdings
    _assert_ok_or_tolerated(get_northbound_holdings("600519"))


def test_northbound_discontinued():
    from ashare_mcp.tools.hsgt import get_northbound_daily_net_flow, get_northbound_realtime
    assert get_northbound_realtime().get("error") == "discontinued"
    assert get_northbound_daily_net_flow().get("error") == "discontinued"


# --- F. chip ---
def test_get_chip_distribution():
    from ashare_mcp.tools.chip import get_chip_distribution
    _assert_ok_or_tolerated(get_chip_distribution("600519"))


# --- G. financial ---
def test_get_financial_report():
    from ashare_mcp.tools.financial import get_financial_report
    _assert_ok_or_tolerated(get_financial_report("600519", "income"))


def test_get_earnings_forecast():
    from ashare_mcp.tools.financial import get_earnings_forecast
    _assert_ok_or_tolerated(get_earnings_forecast("2025-03-31", 5))


def test_get_earnings_express():
    from ashare_mcp.tools.financial import get_earnings_express
    _assert_ok_or_tolerated(get_earnings_express("2025-03-31", 5))


def test_get_announcements():
    from ashare_mcp.tools.financial import get_announcements
    _assert_ok_or_tolerated(get_announcements("600519", 5))


def test_get_research_reports():
    from ashare_mcp.tools.financial import get_research_reports
    _assert_ok_or_tolerated(get_research_reports("600519", 5))


# --- H. meta ---
def test_get_zt_pool(d):
    from ashare_mcp.tools.meta import get_zt_pool
    _assert_ok_or_tolerated(get_zt_pool(d["mid"], 5))


def test_get_stock_comment():
    from ashare_mcp.tools.meta import get_stock_comment
    _assert_ok_or_tolerated(get_stock_comment("600519"))


def test_get_restricted_release(d):
    from ashare_mcp.tools.meta import get_restricted_release
    _assert_ok_or_tolerated(get_restricted_release(d["start"], d["latest"], 5))


# --- I. ChatGPT compat ---
def test_search():
    from ashare_mcp.tools.search_fetch import search
    r = search("茅台")
    assert isinstance(r, dict) and isinstance(r.get("results"), list)


def test_fetch():
    from ashare_mcp.tools.search_fetch import fetch
    r = fetch("stock:600519")
    assert isinstance(r, dict)
    for k in ("id", "title", "text", "url", "metadata"):
        assert k in r, f"fetch result missing {k}"
