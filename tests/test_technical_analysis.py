from __future__ import annotations

import math
from datetime import date, timedelta

from ashare_mcp.tools.technical import (
    _add_indicators,
    _prepare_frame,
    _rsi_divergence,
    _score_snapshot,
    _support_resistance,
    _trend_label,
    _volume_price_label,
)


def _sample_rows(n: int = 120) -> list[dict]:
    rows: list[dict] = []
    start = date(2026, 1, 1)
    previous_close = None
    for i in range(n):
        trend = 10.0 + i * 0.035
        wave = math.sin(i / 5.0) * 0.35
        close = trend + wave
        open_price = close - math.sin(i / 3.0) * 0.08
        high = max(open_price, close) + 0.15
        low = min(open_price, close) - 0.15
        volume = 1_000_000 + (i % 12) * 45_000
        pct = None
        if previous_close is not None:
            pct = (close / previous_close - 1.0) * 100.0
        rows.append(
            {
                "date": (start + timedelta(days=i)).isoformat(),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "amount": volume * close,
                "pct_change": pct,
            }
        )
        previous_close = close
    return rows


def test_indicators_are_populated_and_bounded():
    df = _add_indicators(_prepare_frame(_sample_rows()))
    latest = df.iloc[-1]

    assert latest["ma5"] > 0
    assert latest["ma20"] > 0
    assert latest["ma60"] > 0
    assert 0 <= latest["rsi14"] <= 100
    assert latest["atr14"] > 0
    assert latest["boll_upper"] > latest["boll_mid"] > latest["boll_lower"]
    assert 0 <= latest["position_60d"] <= 1


def test_support_resistance_and_score_are_structured():
    df = _add_indicators(_prepare_frame(_sample_rows()))
    latest = df.iloc[-1]
    previous = df.iloc[-2]
    price = float(latest["close"])
    atr = float(latest["atr14"])

    support, resistance = _support_resistance(df, price, atr)
    assert support
    assert all("zone" in item and "basis" in item for item in support)
    assert all(item["level"] < price for item in support)
    assert all(item["level"] > price for item in resistance)

    divergence = _rsi_divergence(df)
    score, label, components = _score_snapshot(
        latest,
        previous,
        price,
        breakout=False,
        failed_breakout=False,
        divergence=divergence,
    )
    assert 0 <= score <= 100
    assert label in {"强", "偏强", "震荡", "偏弱", "弱"}
    assert set(components) == {
        "trend",
        "momentum",
        "volume_price",
        "structure",
        "risk_quality",
    }
    assert _trend_label(latest, price) in {"强", "偏强", "震荡", "偏弱", "弱", "数据不足"}


def test_volume_price_classification():
    assert _volume_price_label(2.0, 1.6) == "放量上涨"
    assert _volume_price_label(1.0, 0.7) == "缩量上涨"
    assert _volume_price_label(-2.0, 1.5) == "放量下跌"
    assert _volume_price_label(-1.0, 0.7) == "缩量下跌"
    assert _volume_price_label(0.1, 1.6) == "放量震荡"
