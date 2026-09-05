import asyncio
import json
from datetime import date, timedelta

import pytest

from ashare_mcp.tools import _valuation as v


def test_disclosure_and_future_revision():
    facts = [
        {"period": "2023-12-31", "available_date": "2024-03-01", "income_ytd": 100, "equity_parent": 500},
        {"period": "2024-03-31", "available_date": "2024-04-20", "income_ytd": 40},
        {"period": "2023-03-31", "available_date": "2023-04-20", "income_ytd": 20},
        {"period": "2023-12-31", "available_date": "2024-05-01", "income_ytd": 200},
    ]
    prices = [{"date": d, "total_market_cap": 1200} for d in ("2024-04-20", "2024-04-21", "2024-05-02")]
    r = v.reconstruct(prices, facts)
    assert [x["pe_ttm"] for x in r] == [12, 10, 1200 / 220]
    assert r[0]["pb"] == 2.4
    assert r[0]["ps_ttm"] is None


def test_incomplete_ttm_and_cap_not_guessed():
    facts = [{"period": "2024-03-31", "available_date": "2024-04-20", "income_ytd": 40, "equity_parent": -1}]
    r = v.reconstruct([{"date": "2024-04-21", "total_market_cap": 1000}], facts)[0]
    assert r["pe_ttm"] is None and r["pb"] is None
    r = v.reconstruct([{"date": "2024-04-21", "close": 10, "total_shares": 100}], facts)[0]
    assert all(r[k] is None for k in v.METRICS)


def test_financial_snapshot_revision_date():
    raw = {"REPORT_DATE": "2023-12-31", "NOTICE_DATE": "2024-03-01", "UPDATE_DATE": "2024-05-01", "PARENT_NETPROFIT": 100}
    facts = v.normalize_facts([raw], "income")
    assert facts[0]["available_date"] == "2024-05-01"
    assert v.reconstruct([{"date": "2024-04-01", "total_market_cap": 1000}], facts)[0]["pe_ttm"] is None
    del raw["UPDATE_DATE"]
    assert v.normalize_facts([raw], "income") == []


def fixture_rows():
    start, end = date(2021, 9, 3), date(2026, 9, 4)
    return [{"date": (start + timedelta(days=i)).isoformat(), "pe_ttm": 10, "pb": 2, "ps_ttm": 3}
            for i in range((end-start).days+1) if (start+timedelta(days=i)).weekday() < 5]


def test_quantiles_ties_windows_and_signal():
    rows = fixture_rows()
    r = v.summarize(rows, "2026-09-04", "fixture", "provider_daily")
    assert r["history"]["pe_5y_percentile"] == 50
    assert r["history"]["pe_median"] == 10
    assert r["valuation_signal"] == "neutral"
    rows[-1]["pe_ttm"], rows[-1]["pb"] = 1, .1
    r = v.summarize(rows, "2026-09-04")
    assert r["valuation_signal"] == "historically_low"
    assert r["signal_window_years"] == 5
    rows[-1]["pe_ttm"], rows[-1]["pb"] = 100, 100
    assert v.summarize(rows, "2026-09-04")["valuation_signal"] == "historically_high"


def test_short_history_stale_nonfinite_and_future():
    rows = fixture_rows()[-100:]
    rows.append({"date": "2030-01-01", "pe_ttm": 999})
    r = v.summarize(rows, "2026-09-04")
    assert r["current"]["pe_ttm"] == 10
    assert r["history"]["pe_1y_percentile"] is None
    assert r["history"]["pe_max"] == 10
    rows[-2]["pe_ttm"] = float("inf")
    assert v.summarize(rows, "2026-09-04")["current"]["pe_ttm"] is None
    r = v.summarize(rows[:-2], "2026-09-04")
    assert r["current"]["pb"] is None
    json.dumps(r, allow_nan=False)


def test_duplicate_conflict_excluded():
    r = v.summarize([{"date": "2026-09-04", "pb": 2}, {"date": "2026-09-04", "pb": 3}], "2026-09-04")
    assert r["current"]["pb"] is None
    assert "conflicting_duplicate_dates_excluded" in r["reasons"]


def test_fallback_invokes_existing_data(monkeypatch):
    def fail(*a):
        raise TimeoutError()
    monkeypatch.setattr(v.bs, "valuation_history", fail)
    monkeypatch.setattr(v, "get_daily_kline", lambda *a, **kw: {"ok": True, "data": [{"date": "2026-09-04", "total_market_cap": 1000}]})
    def report(symbol, kind, **kw):
        return {"ok": True, "data": [{"report_date": "2025-12-31", "notice_date": "2026-03-01", "update_date": "2026-03-01", "parent_netprofit": 100, "total_parent_equity": 500}]}
    monkeypatch.setattr(v, "get_financial_report", report)
    rows, _, method, reasons = v._load("600519", "2026-09-04")
    assert rows[0]["pe_ttm"] == 10 and rows[0]["pb"] == 2
    assert method == "reconstructed" and reasons


def test_bounded_failure(monkeypatch):
    monkeypatch.setattr(v, "_load", lambda *a: (_ for _ in ()).throw(RuntimeError("failure")))
    rows, _, method, reasons = v._bounded_load.__wrapped__("600519", "2026-09-04")
    assert rows == [] and method == "unavailable" and reasons
    assert not v._worker.locked()


def test_mcp_call_contract(monkeypatch):
    from ashare_mcp.tools import technical_sentiment as mod
    from ashare_mcp.server import mcp
    from test_technical_analysis import _sample_rows
    rows = _sample_rows()
    monkeypatch.setattr(mod.technical, "_load_daily", lambda *a: (rows, "fixture", rows[0]["date"], rows[-1]["date"]))
    monkeypatch.setattr(mod.technical, "_realtime_overlay", lambda *a: (None, []))
    monkeypatch.setattr(mod, "get_individual_fund_flow", lambda *a: {"ok": False})
    monkeypatch.setattr(v, "_bounded_load", lambda *a: ([], None, "unavailable", ["fixture"]))
    from fastmcp import Client
    async def run():
        async with Client(mcp) as client:
            listing = await client.list_tools()
            tool = next(t for t in listing if t.name == "technical_sentiment_analysis")
            assert set(tool.inputSchema["properties"]) == {"symbol", "period", "lookback"}
            assert "valuation" in tool.description
            result = await client.call_tool(tool.name, {"symbol": "600519"})
            payload = json.loads(result.content[0].text)
            assert payload["ok"]
            assert "valuation" in payload["data"]
            assert all(k in payload["data"] for k in ("technical_score", "sentiment_score", "overall_signal"))
    asyncio.run(run())
