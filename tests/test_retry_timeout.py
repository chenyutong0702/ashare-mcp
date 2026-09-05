from __future__ import annotations

import time

import pandas as pd
import pytest

from ashare_mcp.data_sources import _retry
from ashare_mcp.data_sources._base import DataUnavailableError


def test_call_ak_hard_timeout_returns_quickly(monkeypatch):
    def slow_source():
        time.sleep(0.25)
        return pd.DataFrame([{"value": 1}])

    monkeypatch.setattr(_retry, "_resolve", lambda names: ("slow_source", slow_source))

    started = time.monotonic()
    with pytest.raises(DataUnavailableError, match="hard timeout"):
        _retry.call_ak("slow_source", timeout_seconds=0.03, attempts=1)
    elapsed = time.monotonic() - started

    assert elapsed < 0.18


def test_call_ak_hard_timeout_allows_fast_success(monkeypatch):
    expected = pd.DataFrame([{"value": 1}])

    def fast_source():
        return expected

    monkeypatch.setattr(_retry, "_resolve", lambda names: ("fast_source", fast_source))

    result = _retry.call_ak("fast_source", timeout_seconds=0.2, attempts=1)
    assert result.equals(expected)
