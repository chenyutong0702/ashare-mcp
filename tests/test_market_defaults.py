from __future__ import annotations

from datetime import date

import pytest

from ashare_mcp.tools import market


def test_resolve_daily_range_defaults_to_recent_trade_days(monkeypatch):
    monkeypatch.setattr(market.dates, "latest_trade_date", lambda: date(2026, 9, 4))
    monkeypatch.setattr(
        market.dates,
        "recent_trade_dates",
        lambda n, ref=None: [date(2026, 3, 16), date(2026, 9, 4)],
    )

    start, end = market._resolve_daily_range("", "")

    assert start == "2026-03-16"
    assert end == "2026-09-04"


def test_resolve_daily_range_preserves_explicit_dates():
    start, end = market._resolve_daily_range("2025-05-06", "2025-07-10")

    assert start == "2025-05-06"
    assert end == "2025-07-10"


def test_resolve_daily_range_rejects_reverse_range():
    with pytest.raises(ValueError):
        market._resolve_daily_range("2026-09-04", "2026-08-01")
