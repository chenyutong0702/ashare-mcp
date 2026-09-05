from __future__ import annotations

from ashare_mcp.data_sources import realtime_src as rt
from ashare_mcp.data_sources._base import DataUnavailableError


def test_parse_tencent_quote():
    raw = (
        'v_sh600000="1~浦发银行~600000~9.59~9.37~9.37~1099035~723977~375059~'
        '9.58~504~9.57~1290~9.56~7319~9.55~4634~9.54~1336~9.59~21263~9.60~39283~'
        '9.61~6148~9.62~11163~9.63~5550~~20260610161426~0.22~2.35~9.59~9.34~'
        '9.59/1099035/1046292201~1099035~104629~0.33~6.35~~9.59~9.34~2.67~3194.03~'
        '3194.03~0.42~10.31~8.43~1.56~-68324~9.52~4.47~6.39~~~0.23~104629.2201~";'
    )
    row = rt._parse_tencent(raw)["600000"]
    assert row["name"] == "浦发银行"
    assert row["price"] == 9.59
    assert row["prev_close"] == 9.37
    assert row["pct_change"] == 2.35
    assert row["quote_time"] == "2026-06-10 16:14:26"
    assert row["amount"] == 1046292201.0
    assert row["volume"] == 109903500.0
    assert row["turnover_rate"] == 0.33
    assert row["pe_ttm"] == 6.35
    assert row["amplitude"] == 2.67
    assert row["float_market_cap"] == 3194.03 * 100_000_000
    assert row["total_market_cap"] == 3194.03 * 100_000_000
    assert row["pb"] == 0.42
    assert row["limit_up"] == 10.31
    assert row["limit_down"] == 8.43
    assert row["volume_ratio"] == 1.56
    assert row["bid_ask"]["bid_1"] == 9.58
    assert row["bid_ask"]["bid_1_vol"] == 50400.0
    assert row["bid_ask"]["ask_1"] == 9.59
    assert row["source"] == "Tencent Finance"


def test_parse_sina_quote():
    raw = (
        'var hq_str_sh600036="招商银行,38.100,38.100,37.860,38.500,37.800,37.850,37.860,'
        '35953534,1370335108.000,26700,37.850,8900,37.840,4300,37.830,7500,37.820,'
        '15900,37.810,862,37.860,32600,37.870,39400,37.880,145600,37.890,298101,37.900,'
        '2019-12-27,15:00:00,00,";'
    )
    row = rt._parse_sina(raw)["600036"]
    assert row["name"] == "招商银行"
    assert row["price"] == 37.86
    assert row["prev_close"] == 38.10
    assert row["open"] == 38.10
    assert row["high"] == 38.50
    assert row["low"] == 37.80
    assert row["volume"] == 35953534.0
    assert row["amount"] == 1370335108.0
    assert row["quote_time"] == "2019-12-27 15:00:00"
    assert row["bid_ask"]["bid_1"] == 37.85
    assert row["bid_ask"]["ask_1"] == 37.86
    assert row["source"] == "Sina Finance"


def test_realtime_falls_back_to_sina(monkeypatch):
    def fail_tencent(symbols, *, timeout=rt.DEFAULT_TIMEOUT_SECONDS):
        raise DataUnavailableError("tencent down")

    def fake_sina(symbols, *, timeout=rt.DEFAULT_TIMEOUT_SECONDS):
        return {
            "600519": {
                "code": "600519",
                "name": "贵州茅台",
                "price": 1500.0,
                "source": "Sina Finance",
                "is_realtime": True,
            }
        }

    monkeypatch.setattr(rt, "tencent_quotes", fail_tencent)
    monkeypatch.setattr(rt, "sina_quotes", fake_sina)

    rows, errors = rt.realtime_quotes(["600519"])
    assert rows["600519"]["source"] == "Sina Finance"
    assert errors and errors[0].startswith("Tencent:")


def test_realtime_only_falls_back_for_missing_symbols(monkeypatch):
    monkeypatch.setattr(
        rt,
        "tencent_quotes",
        lambda symbols, timeout=rt.DEFAULT_TIMEOUT_SECONDS: {
            "600519": {"code": "600519", "source": "Tencent Finance", "price": 1500.0}
        },
    )

    seen = []

    def fake_sina(symbols, *, timeout=rt.DEFAULT_TIMEOUT_SECONDS):
        seen.extend(symbols)
        return {"000001": {"code": "000001", "source": "Sina Finance", "price": 12.0}}

    monkeypatch.setattr(rt, "sina_quotes", fake_sina)
    rows, errors = rt.realtime_quotes(["600519", "000001"])
    assert errors == []
    assert seen == ["000001"]
    assert set(rows) == {"600519", "000001"}
