import asyncio
import json
from ashare_mcp.tools import technical_sentiment as mod
from test_technical_analysis import _sample_rows


def test_missing_and_stale_flow():
    s, flags = mod._sentiment({}, {"ok": True, "data": [{"date": "2020-01-01", "main_net_inflow": 9}]}, "2026-01-01")
    assert s["fund_flow_net"] is None and s["score"] is None
    assert flags


def test_zero_is_not_missing():
    s, _ = mod._sentiment({"volume_price": {"latest_pct_change": 0, "volume_ratio_vs_prior_20d": 0}}, {"ok": True, "data": [{"date": "2026-01-01", "main_net_inflow": 0}]}, "2026-01-01")
    assert s["score"] == 50 and s["fund_flow_tendency"] == "neutral"
    assert s["activity_ratio_vs_prior_20d"] == 0


def test_full_tool(monkeypatch):
    rows = _sample_rows()
    monkeypatch.setattr(mod.technical, "_load_daily", lambda *a: (rows, "fixture", rows[0]["date"], rows[-1]["date"]))
    monkeypatch.setattr(mod.technical, "_realtime_overlay", lambda *a: ({"volume_ratio": 1.4, "turnover_rate": 3}, []))
    monkeypatch.setattr(mod, "get_individual_fund_flow", lambda *a: {"ok": True, "data": [{"date": rows[-1]["date"], "main_net_inflow": 100}]})
    r = mod.technical_sentiment_analysis("600519")
    assert r["ok"], r
    assert r["data"]["sentiment"]["turnover_rate"] == 3
    assert 0 <= r["data"]["technical_score"] <= 100
    json.dumps(r, allow_nan=False)


def test_validation():
    assert mod.technical_sentiment_analysis("600519", "5")["error"] == "bad_request"
    assert mod.technical_sentiment_analysis("600519", lookback=2)["error"] == "bad_request"


def test_registration():
    from ashare_mcp.server import mcp
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert {"technical_sentiment_analysis", "get_technical_analysis", "get_daily_kline"} <= names
