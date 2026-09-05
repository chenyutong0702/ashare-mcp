"""Unified daily technical and observed market sentiment, without new providers."""
from __future__ import annotations

import math
from typing import Literal

from ..app import mcp
from ..utils import codes
from . import technical
from .fundflow import get_individual_fund_flow
from ._helpers import DISCLAIMER_FUNDFLOW, err, guard, ok


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _sentiment(data, flow, as_of):
    volume = data.get("volume_price", {})
    activity = _number(volume.get("volume_ratio_vs_prior_20d"))
    change = _number(volume.get("latest_pct_change"))
    flags = []
    rows = flow.get("data", []) if flow.get("ok") else []
    dated = {str(r.get("date"))[:10]: r for r in rows if r.get("date")}
    aligned = dated.get(as_of)
    net = _number(aligned.get("main_net_inflow")) if aligned else None
    components = {}
    if change is not None:
        components["price_strength"] = max(0, min(100, 50 + change * 5))
    if net is not None:
        components["fund_flow"] = 75 if net > 0 else 25 if net < 0 else 50
    else:
        flags.append("fund_flow_unavailable_or_date_mismatch")
    if activity is None:
        flags.append("volume_activity_unavailable")
    score = round(sum(components.values()) / len(components), 2) if components else None
    label = "unavailable" if score is None else "偏积极" if score >= 60 else "偏消极" if score <= 40 else "中性"
    return {
        "as_of": as_of,
        "activity_ratio_vs_prior_20d": activity,
        "activity_label": "unavailable" if activity is None else "活跃" if activity >= 1.3 else "低迷" if activity <= 0.7 else "正常",
        "volume_ratio": None,
        "turnover_rate": None,
        "quote_status": "unavailable: daily-only analysis; no synchronized quote",
        "fund_flow_net": net,
        "fund_flow_tendency": "unavailable" if net is None else "inflow" if net > 0 else "outflow" if net < 0 else "neutral",
        "pct_change": change,
        "short_term_label": label,
        "score_components": components,
        "score": score,
        "scope": "Individual-stock price/volume and reported fund flow proxy; not news sentiment or market breadth",
    }, flags


@mcp.tool
@guard
def technical_sentiment_analysis(
    symbol: str, period: Literal["daily"] = "daily", lookback: int = 120,
) -> dict:
    """统一技术分析+市场情绪。复用现有技术与资金流工具，原工具保持兼容。

    symbol: 600519 / sh600519 / 600519.SH。period: 当前仅支持daily。
    lookback: 80-250个交易日，默认120。返回data.technical(均线、MACD、RSI、
    KDJ、BOLL、ATR、量价、支撑压力、交叉和超买超卖)、sentiment、risk_flags、
    technical_score、sentiment_score、overall_signal、summary。缺失项null/unavailable。
    评分0-100为启发式强弱分，不是上涨概率。资金流只采用与K线末日一致的数据。
    """
    if period != "daily" or isinstance(lookback, bool) or not isinstance(lookback, int) or not 80 <= lookback <= 250:
        return err("bad_request", "period must be daily; lookback must be an integer in 80..250")
    sym = codes.normalize(symbol)
    result = technical.get_technical_analysis(sym, lookback, include_realtime=False)
    if not result.get("ok"):
        return result
    data = result["data"]
    as_of = data["summary"]["latest_completed_bar"]
    flow = get_individual_fund_flow(sym)
    sentiment, flags = _sentiment(data, flow, as_of)
    quote, quote_errors = technical._realtime_overlay(sym)
    if quote:
        sentiment["volume_ratio"] = _number(quote.get("volume_ratio"))
        sentiment["turnover_rate"] = _number(quote.get("turnover_rate"))
        sentiment["quote_time"] = quote.get("quote_time")
        sentiment["quote_status"] = "separate snapshot; excluded from daily score"
    if quote_errors:
        flags.append("quote_provider_degraded")
    flags += data.get("risk_signals", [])
    if result.get("bars", 0) < lookback:
        flags.append("insufficient_requested_history")
    technical_score = _number(data["summary"].get("technical_score"))
    if data.get("volume_price", {}).get("latest_volume") is None:
        flags.append("technical_score_unavailable_missing_volume")
        technical_score = None
        data["summary"]["technical_score"] = None
        data["volume_price"] = {"classification": "unavailable", "latest_volume": None}
        data["emotion_proxy"] = None
    sentiment_score = sentiment["score"]
    combined = (technical_score + sentiment_score) / 2 if technical_score is not None and sentiment_score is not None else None
    signal = "unavailable" if combined is None else "偏强" if combined >= 60 else "偏弱" if combined <= 40 else "中性"
    return ok({
        "technical": data, "sentiment": sentiment,
        "technical_score": technical_score, "sentiment_score": sentiment_score,
        "overall_signal": signal, "risk_flags": list(dict.fromkeys(flags)),
        "summary": f"日线技术与情绪综合{signal}；情绪为行情与资金流代理。",
        "scoring": "sentiment=mean(clip(50+5*pct_change,0,100), flow sign 75/50/25), available components only; overall=equal-weight scores, thresholds 40/60",
    }, symbol=sym, period=period, lookback=lookback, as_of=as_of,
        source={"technical": result.get("source"), "fund_flow": flow.get("source")},
        disclaimer=DISCLAIMER_FUNDFLOW)


