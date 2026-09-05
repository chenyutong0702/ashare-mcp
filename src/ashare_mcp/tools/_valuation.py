"""Internal valuation component. No additional MCP tool or scoring side effects.

Provider ratios are labelled provider-reported, never certified as vintage data.
Reconstruction requires dated market capitalisation and disclosure-versioned facts.
"""
from __future__ import annotations

import math
import threading
from datetime import date, timedelta

import pandas as pd

from ..cache import TTL_MINUTE, cached
from ..data_sources import baostock_src as bs
from ..data_sources._retry import _invoke_with_timeout
from .financial import get_financial_report
from .market import get_daily_kline

_worker = threading.Lock()
METRICS = ("pe_ttm", "pb", "ps_ttm")


def number(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def years_before(d, years):
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        return d.replace(year=d.year - years, day=28)


def ratio(cap, denominator):
    cap, denominator = number(cap), number(denominator)
    if cap is None or denominator is None or cap <= 0 or denominator <= 0:
        return None
    return number(cap / denominator)


def reconstruct(prices, facts):
    """PIT facts: period, available_date, income_ytd/revenue_ytd/equity_parent.

    All monetary amounts in CNY. Prices must contain actual date-specific total
    market cap, not today's shares multiplied by historical prices. Only facts
    disclosed strictly BEFORE a bar date are used (date-only release uncertainty).
    Each revision has its own available_date; future revisions cannot overwrite
    older vintages. Annual + current YTD - prior-year YTD constructs TTM.
    """
    valid = [f for f in facts if day(f.get("period")) and day(f.get("available_date"))
             and day(f["period"]) <= day(f["available_date"])]
    out = []
    for bar in prices:
        d = day(bar.get("date"))
        if d is None:
            continue
        known = sorted((f for f in valid if day(f["available_date"]) < d),
                       key=lambda f: day(f["available_date"]))
        # Merge distinct statement types, retaining nulls from explicit revisions.
        periods = {}
        for f in known:
            periods.setdefault(day(f["period"]), {}).update(f)
        values = {}
        for field in ("income_ytd", "revenue_ytd", "equity_parent"):
            eligible = [p for p, f in periods.items() if field in f]
            if not eligible:
                values[field] = None
                continue
            p = max(eligible)
            v = number(periods[p].get(field))
            if field != "equity_parent" and (p.month, p.day) != (12, 31):
                annual = number(periods.get(date(p.year - 1, 12, 31), {}).get(field))
                prior = number(periods.get(years_before(p, 1), {}).get(field))
                v = annual + v - prior if all(x is not None for x in (annual, v, prior)) else None
            values[field] = v
        cap = bar.get("total_market_cap")
        out.append({"date": d.isoformat(), "pe_ttm": ratio(cap, values.get("income_ytd")),
                    "pb": ratio(cap, values.get("equity_parent")),
                    "ps_ttm": ratio(cap, values.get("revenue_ytd"))})
    return out


def normalize_facts(rows, kind):
    """Conservative Eastmoney adapter. Use the latest notice/update timestamp.

    A revised snapshot can be used only after its update date, never assigned to
    the original disclosure. Missing version/disclosure metadata is rejected.
    """
    out = []
    mapping = {"income": {"parent_netprofit": "income_ytd", "total_operate_income": "revenue_ytd"},
               "balance": {"total_parent_equity": "equity_parent"}}[kind]
    for raw in rows:
        row = {str(k).lower(): v for k, v in raw.items()}
        period = day(row.get("report_date"))
        notice = day(row.get("notice_date"))
        updated = day(row.get("update_date"))
        # Without an update timestamp this endpoint's revision vintage is unknown.
        if period is None or notice is None or updated is None:
            continue
        fact = {"period": period.isoformat(), "available_date": max(notice, updated).isoformat()}
        fact.update({dest: number(row[src]) for src, dest in mapping.items() if src in row})
        out.append(fact)
    return out


def summarize(rows, as_of, source=None, method="unavailable", reasons=None):
    end = day(as_of)
    if end is None:
        raise ValueError("invalid valuation as_of")
    start = years_before(end, 5)
    # Conflicting duplicate dates are excluded, rather than silently choosing one.
    dated, conflicts = {}, set()
    for r in rows:
        d = day(r.get("date"))
        if d is None or not start <= d <= end:
            continue
        clean = {k: number(r.get(k)) for k in METRICS}
        if d in dated and dated[d] != clean:
            conflicts.add(d)
        dated[d] = clean
    for d in conflicts:
        dated.pop(d)
    current = {k: dated.get(end, {}).get(k) for k in METRICS}
    # Zero/negative ratios are not meaningful cheapness observations.
    current = {k: v if v is not None and v > 0 else None for k, v in current.items()}
    history = {"windows": {}}
    for y in (1, 3, 5):
        lower = years_before(end, y)
        window = {}
        for short, key in (("pe", "pe_ttm"), ("pb", "pb")):
            samples = [(d, r[key]) for d, r in sorted(dated.items())
                       if lower <= d <= end and r[key] is not None and r[key] > 0]
            vals = [v for _, v in samples]
            # Suppress named full-window percentiles for short IPO history/gaps.
            enough = (len(vals) >= 120 * y and samples[0][0] <= lower + timedelta(days=14)
                      and samples[-1][0] == end)
            quantiles = pd.Series(vals, dtype=float).quantile([0, .25, .5, .75, 1]).tolist() if vals else [None] * 5
            pct = (100 * (sum(v < current[key] for v in vals) + .5 * sum(v == current[key] for v in vals)) / len(vals)
                   if enough and current[key] is not None else None)
            stats = dict(zip(("min", "p25", "median", "p75", "max"), quantiles))
            stats.update({"percentile": round(pct, 4) if pct is not None else None,
                          "count": len(vals), "start": samples[0][0].isoformat() if vals else None,
                          "end": samples[-1][0].isoformat() if vals else None,
                          "status": "available" if enough else "insufficient_history"})
            window[short] = stats
            history[f"{short}_{y}y_percentile"] = stats["percentile"]
        history["windows"][f"{y}y"] = window
    for short in ("pe", "pb"):
        for stat in ("min", "median", "max", "p25", "p75"):
            history[f"{short}_{stat}"] = history["windows"]["5y"][short][stat]
    chosen = next((y for y in (5, 3, 1) if all(history[f"{m}_{y}y_percentile"] is not None for m in ("pe", "pb"))), None)
    signal = "unavailable"
    if chosen:
        pcts = [history[f"{m}_{chosen}y_percentile"] for m in ("pe", "pb")]
        signal = "historically_low" if max(pcts) <= 25 else "historically_high" if min(pcts) >= 75 else "neutral"
    issues = list(reasons or [])
    if conflicts:
        issues.append("conflicting_duplicate_dates_excluded")
    if not any(current.values()):
        issues.append("no_valid_ratios_on_analysis_date")
    return {"status": "available" if chosen else "partial" if any(current.values()) else "unavailable",
            "as_of": as_of, "current": current, "history": history,
            "valuation_signal": signal, "signal_window_years": chosen,
            "source": source, "method": method,
            "point_in_time": {"status": "disclosure_matched" if method == "reconstructed" else "provider_reported_not_independently_verified" if method == "provider_daily" else "unavailable"},
            "peers": {"status": "unavailable", "data": None, "reason": "No verified comparable peer valuation interface in existing adapters"},
            "reasons": issues,
            "rules": "Positive finite ratios only; calendar windows 1/3/5 years; min/median/max/p25/p75 describe available samples (may be partial); percentile=100*(less+0.5*equal)/N. Full-window percentile requires >=120*y observations, first within 14 days of start and last on as_of. Signal uses longest common PE/PB window: both <=25 low, both >=75 high, otherwise neutral. No valuation_score; existing scores unchanged."}


def _load(symbol, as_of):
    start = years_before(day(as_of), 5).isoformat()
    reasons = []
    try:
        rows = bs.valuation_history(symbol, start, as_of)
        if any(number(r.get(k)) is not None for r in rows for k in METRICS):
            return rows, "baostock", "provider_daily", reasons
        reasons.append("provider_daily_ratios_empty")
    except Exception as exc:
        reasons.append(f"provider_daily_failed:{type(exc).__name__}")
    prices = get_daily_kline(symbol, start, as_of, adjust="")
    facts = []
    for kind in ("income", "balance"):
        report = get_financial_report(symbol, kind, limit=32)
        facts += normalize_facts(report.get("data", []) if report.get("ok") else [], kind)
    bars = prices.get("data", []) if prices.get("ok") else []
    if not any(number(r.get("total_market_cap")) for r in bars):
        reasons.append("historical_market_cap_unavailable; current_shares_and_adjusted_prices_not_substituted")
    if not facts:
        reasons.append("disclosure_versioned_financial_facts_unavailable")
    return reconstruct(bars, facts), "existing_kline_and_financial_adapters", "reconstructed", reasons


@cached(ttl=TTL_MINUTE)
def _bounded_load(symbol, as_of):
    # Keep at most one abandoned worker across requests if an upstream hangs.
    if not _worker.acquire(blocking=False):
        return [], None, "unavailable", ["valuation_provider_busy"]

    def run():
        try:
            return _load(symbol, as_of)
        finally:
            _worker.release()

    try:
        return _invoke_with_timeout(run, {}, timeout_seconds=12, call_name="valuation")
    except Exception as exc:
        return [], None, "unavailable", [f"valuation_provider_failed:{type(exc).__name__}"]


def get_valuation(symbol, as_of):
    rows, source, method, reasons = _bounded_load(symbol, as_of)
    return summarize(rows, as_of, source, method, reasons)
