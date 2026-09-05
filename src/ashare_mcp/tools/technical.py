"""Deterministic A-share technical-analysis tool.

The tool converts recent daily OHLCV plus an optional lightweight realtime quote into
a structured technical snapshot. Observed indicators and heuristic interpretation are
kept separate: scores, regime labels, and emotion proxies are descriptive aids rather
than forecasts or trading signals.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pandas as pd

from ..app import mcp
from ..cache import TTL_MINUTE, cached
from ..data_sources import baostock_src as bs
from ..data_sources import realtime_src as rt
from ..data_sources._base import DataSourceError, df_to_records
from ..data_sources._retry import call_ak
from ..utils import codes, dates
from ._helpers import DISCLAIMER_NOT_ADVICE, err, guard, ok

_MIN_LOOKBACK = 80
_MAX_LOOKBACK = 250
_DAILY_TIMEOUT_SECONDS = 8.0


def _num(value: Any, digits: int = 4) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _prepare_frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        raise ValueError("daily kline is empty")
    df = pd.DataFrame(rows).copy()
    required = {"date", "open", "high", "low", "close"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"daily kline missing columns: {sorted(missing)}")

    for col in ("open", "high", "low", "close", "volume", "amount", "pct_change"):
        if col not in df.columns:
            df[col] = None
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "open", "high", "low", "close"])
    df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    df = df.reset_index(drop=True)
    if len(df) < 35:
        raise ValueError(f"insufficient daily bars: {len(df)} (need >=35)")
    return df


def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = out["close"]
    high = out["high"]
    low = out["low"]
    volume = out["volume"].fillna(0.0)

    for window in (5, 10, 20, 60):
        out[f"ma{window}"] = close.rolling(window).mean()
    for span in (5, 12, 20, 26, 60):
        out[f"ema{span}"] = close.ewm(span=span, adjust=False).mean()

    out["ma20_slope_5d_pct"] = (out["ma20"] / out["ma20"].shift(5) - 1.0) * 100.0
    out["ma60_slope_5d_pct"] = (out["ma60"] / out["ma60"].shift(5) - 1.0) * 100.0
    out["ema20_slope_5d_pct"] = (out["ema20"] / out["ema20"].shift(5) - 1.0) * 100.0

    out["macd_dif"] = out["ema12"] - out["ema26"]
    out["macd_dea"] = out["macd_dif"].ewm(span=9, adjust=False).mean()
    out["macd_hist"] = (out["macd_dif"] - out["macd_dea"]) * 2.0

    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    out["rsi14"] = 100.0 - (100.0 / (1.0 + rs))
    out.loc[(avg_loss == 0) & (avg_gain > 0), "rsi14"] = 100.0

    low9 = low.rolling(9).min()
    high9 = high.rolling(9).max()
    rsv = (close - low9) / (high9 - low9).replace(0, pd.NA) * 100.0
    out["kdj_k"] = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    out["kdj_d"] = out["kdj_k"].ewm(alpha=1 / 3, adjust=False).mean()
    out["kdj_j"] = 3.0 * out["kdj_k"] - 2.0 * out["kdj_d"]

    out["boll_mid"] = close.rolling(20).mean()
    boll_std = close.rolling(20).std(ddof=0)
    out["boll_upper"] = out["boll_mid"] + 2.0 * boll_std
    out["boll_lower"] = out["boll_mid"] - 2.0 * boll_std
    out["boll_width_pct"] = (
        (out["boll_upper"] - out["boll_lower"]) / out["boll_mid"] * 100.0
    )
    out["boll_z"] = (close - out["boll_mid"]) / boll_std.replace(0, pd.NA)

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr14"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    out["atr14_pct"] = out["atr14"] / close * 100.0
    out["atr14_pct_ma20"] = out["atr14_pct"].rolling(20).mean()

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        ((up_move > down_move) & (up_move > 0)).astype(float).values * up_move.fillna(0.0).values,
        index=out.index,
    )
    minus_dm = pd.Series(
        ((down_move > up_move) & (down_move > 0)).astype(float).values * down_move.fillna(0.0).values,
        index=out.index,
    )
    atr_wilder = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    plus_smoothed = plus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    minus_smoothed = minus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    out["plus_di14"] = 100.0 * plus_smoothed / atr_wilder.replace(0, pd.NA)
    out["minus_di14"] = 100.0 * minus_smoothed / atr_wilder.replace(0, pd.NA)
    dx = (
        (out["plus_di14"] - out["minus_di14"]).abs()
        / (out["plus_di14"] + out["minus_di14"]).replace(0, pd.NA)
        * 100.0
    )
    out["adx14"] = dx.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    out["volume_ma5"] = volume.rolling(5).mean()
    out["volume_ma20"] = volume.rolling(20).mean()
    out["volume_ma20_prior"] = volume.shift(1).rolling(20).mean()
    out["volume_ratio_20"] = volume / out["volume_ma20_prior"].replace(0, pd.NA)

    direction = close.diff().fillna(0.0)
    signed_volume = pd.Series(0.0, index=out.index)
    signed_volume.loc[direction > 0] = volume.loc[direction > 0]
    signed_volume.loc[direction < 0] = -volume.loc[direction < 0]
    out["obv"] = signed_volume.cumsum()
    out["obv_ma20"] = out["obv"].rolling(20).mean()
    out["obv_change_5d_pct"] = (
        (out["obv"] - out["obv"].shift(5))
        / out["obv"].shift(5).abs().replace(0, pd.NA)
        * 100.0
    )

    for window in (5, 20, 60):
        out[f"return_{window}d_pct"] = (close / close.shift(window) - 1.0) * 100.0
        rolling_high = high.rolling(window).max()
        rolling_low = low.rolling(window).min()
        out[f"position_{window}d"] = (
            (close - rolling_low) / (rolling_high - rolling_low).replace(0, pd.NA)
        )

    out["prev_20d_high"] = high.shift(1).rolling(20).max()
    out["prev_20d_low"] = low.shift(1).rolling(20).min()
    out["prev_60d_high"] = high.shift(1).rolling(60).max()
    out["prev_60d_low"] = low.shift(1).rolling(60).min()

    computed_pct = close.pct_change() * 100.0
    out["pct_change"] = out["pct_change"].fillna(computed_pct)
    return out


def _cross_state(
    previous_fast: float | None,
    previous_slow: float | None,
    fast: float | None,
    slow: float | None,
) -> str:
    if None in (previous_fast, previous_slow, fast, slow):
        return "unavailable"
    assert previous_fast is not None and previous_slow is not None
    assert fast is not None and slow is not None
    if previous_fast <= previous_slow and fast > slow:
        return "golden_cross"
    if previous_fast >= previous_slow and fast < slow:
        return "death_cross"
    return "above" if fast > slow else "below" if fast < slow else "equal"


def _recent_pivots(df: pd.DataFrame, window: int = 60) -> tuple[list[float], list[float]]:
    recent = df.tail(window).reset_index(drop=True)
    lows: list[float] = []
    highs: list[float] = []
    if len(recent) < 5:
        return lows, highs
    for idx in range(2, len(recent) - 2):
        low_slice = recent.loc[idx - 2 : idx + 2, "low"]
        high_slice = recent.loc[idx - 2 : idx + 2, "high"]
        low_value = float(recent.loc[idx, "low"])
        high_value = float(recent.loc[idx, "high"])
        if low_value <= float(low_slice.min()):
            lows.append(low_value)
        if high_value >= float(high_slice.max()):
            highs.append(high_value)
    return lows[-4:], highs[-4:]


def _cluster_levels(candidates: list[tuple[float | None, str]]) -> list[dict]:
    levels: list[dict] = []
    for raw_value, basis in candidates:
        value = _num(raw_value, 4)
        if value is None or value <= 0:
            continue
        merged = False
        for item in levels:
            if abs(value - item["level"]) / item["level"] <= 0.012:
                old_count = len(item["basis"])
                item["level"] = round(
                    (item["level"] * old_count + value) / (old_count + 1), 4
                )
                item["basis"].append(basis)
                merged = True
                break
        if not merged:
            levels.append({"level": value, "basis": [basis]})
    return levels


def _support_resistance(
    df: pd.DataFrame,
    current_price: float,
    atr: float | None,
) -> tuple[list[dict], list[dict]]:
    latest = df.iloc[-1]
    pivot_lows, pivot_highs = _recent_pivots(df)

    base_candidates: list[tuple[float | None, str]] = [
        (_num(latest.get("ma20")), "MA20"),
        (_num(latest.get("ema20")), "EMA20"),
        (_num(latest.get("ma60")), "MA60"),
        (_num(latest.get("boll_mid")), "BOLL中轨"),
        (_num(latest.get("prev_20d_low")), "20日区间低点"),
        (_num(latest.get("prev_20d_high")), "20日区间高点"),
        (_num(latest.get("prev_60d_low")), "60日区间低点"),
        (_num(latest.get("prev_60d_high")), "60日区间高点"),
    ]
    base_candidates.extend((value, "近期摆动低点") for value in pivot_lows)
    base_candidates.extend((value, "近期摆动高点") for value in pivot_highs)

    clustered = _cluster_levels(base_candidates)
    support = [item for item in clustered if item["level"] < current_price]
    resistance = [item for item in clustered if item["level"] > current_price]
    support.sort(key=lambda item: item["level"], reverse=True)
    resistance.sort(key=lambda item: item["level"])

    def with_zone(item: dict) -> dict:
        level = float(item["level"])
        half_width = max(level * 0.004, (atr or 0.0) * 0.25)
        return {
            "level": round(level, 4),
            "zone": [round(level - half_width, 4), round(level + half_width, 4)],
            "basis": item["basis"],
            "distance_pct": round((level / current_price - 1.0) * 100.0, 2),
        }

    return [with_zone(x) for x in support[:3]], [with_zone(x) for x in resistance[:3]]


def _rsi_divergence(df: pd.DataFrame) -> str | None:
    recent = df.tail(30).dropna(subset=["rsi14", "close"])
    if len(recent) < 20:
        return None
    midpoint = len(recent) // 2
    first = recent.iloc[:midpoint]
    second = recent.iloc[midpoint:]

    first_high_idx = first["close"].idxmax()
    second_high_idx = second["close"].idxmax()
    first_low_idx = first["close"].idxmin()
    second_low_idx = second["close"].idxmin()

    first_high = float(recent.loc[first_high_idx, "close"])
    second_high = float(recent.loc[second_high_idx, "close"])
    first_high_rsi = float(recent.loc[first_high_idx, "rsi14"])
    second_high_rsi = float(recent.loc[second_high_idx, "rsi14"])

    if second_high >= first_high * 1.01 and second_high_rsi <= first_high_rsi - 5.0:
        return "bearish"

    first_low = float(recent.loc[first_low_idx, "close"])
    second_low = float(recent.loc[second_low_idx, "close"])
    first_low_rsi = float(recent.loc[first_low_idx, "rsi14"])
    second_low_rsi = float(recent.loc[second_low_idx, "rsi14"])
    if second_low <= first_low * 0.99 and second_low_rsi >= first_low_rsi + 5.0:
        return "bullish"
    return None


def _volume_price_label(
    pct_change: float | None,
    volume_ratio: float | None,
    *,
    price: float | None = None,
    ma20: float | None = None,
    breakout: bool = False,
) -> str:
    pct = pct_change or 0.0
    vr = volume_ratio
    if vr is None:
        return "量能数据不足"
    if breakout and vr >= 1.3:
        return "放量突破"
    if pct >= 0.5 and vr >= 1.3:
        return "放量上涨"
    if pct >= 0.5 and vr <= 0.8:
        return "缩量上涨"
    if pct <= -0.5 and vr >= 1.3:
        return "放量下跌"
    if (
        pct < 0
        and vr <= 0.8
        and price is not None
        and ma20 is not None
        and price > ma20
    ):
        return "缩量回踩"
    if pct <= -0.5 and vr <= 0.8:
        return "缩量下跌"
    if abs(pct) < 0.5 and vr >= 1.3:
        return "放量震荡"
    return "量价中性"


def _regime_label(latest: pd.Series, price: float) -> dict:
    adx = _num(latest.get("adx14"))
    plus_di = _num(latest.get("plus_di14"))
    minus_di = _num(latest.get("minus_di14"))
    ma20 = _num(latest.get("ma20"))
    ma60 = _num(latest.get("ma60"))
    slope20 = _num(latest.get("ma20_slope_5d_pct")) or 0.0

    if adx is None:
        regime = "unknown"
        label = "数据不足"
    elif adx < 20:
        regime = "range"
        label = "震荡市"
    elif adx >= 25 and ma20 is not None and ma60 is not None:
        if price > ma20 > ma60 and slope20 > 0 and (plus_di or 0) >= (minus_di or 0):
            regime = "trending_up"
            label = "上升趋势"
        elif price < ma20 < ma60 and slope20 < 0 and (minus_di or 0) >= (plus_di or 0):
            regime = "trending_down"
            label = "下降趋势"
        else:
            regime = "trend_transition"
            label = "趋势过渡/分歧"
    else:
        regime = "transition"
        label = "震荡向趋势过渡"

    strength = "unknown"
    if adx is not None:
        if adx >= 40:
            strength = "very_strong"
        elif adx >= 25:
            strength = "strong"
        elif adx >= 20:
            strength = "building"
        else:
            strength = "weak"

    return {
        "regime": regime,
        "label": label,
        "adx14": adx,
        "trend_strength": strength,
        "plus_di14": plus_di,
        "minus_di14": minus_di,
    }


def _trend_label(latest: pd.Series, price: float) -> str:
    ma20 = _num(latest.get("ma20"))
    ma60 = _num(latest.get("ma60"))
    slope20 = _num(latest.get("ma20_slope_5d_pct"))
    adx = _num(latest.get("adx14"))
    if ma20 is None or ma60 is None:
        return "数据不足"
    if price > ma20 > ma60 and (slope20 or 0.0) > 0:
        return "强" if (adx or 0) >= 25 else "偏强"
    if price > ma20 and (slope20 or 0.0) >= 0:
        return "偏强"
    if price < ma20 < ma60 and (slope20 or 0.0) < 0:
        return "弱" if (adx or 0) >= 25 else "偏弱"
    if price < ma20 and (slope20 or 0.0) <= 0:
        return "偏弱"
    return "震荡"


def _overbought_oversold(latest: pd.Series, regime: dict, price: float) -> dict:
    rsi = _num(latest.get("rsi14"))
    k = _num(latest.get("kdj_k"))
    d = _num(latest.get("kdj_d"))
    j = _num(latest.get("kdj_j"))
    upper = _num(latest.get("boll_upper"))
    lower = _num(latest.get("boll_lower"))
    mid = _num(latest.get("boll_mid"))
    z = _num(latest.get("boll_z"))

    if rsi is None:
        rsi_state = "unavailable"
    elif rsi >= 80:
        rsi_state = "extreme_overbought"
    elif rsi >= 70:
        rsi_state = "overbought"
    elif rsi <= 20:
        rsi_state = "extreme_oversold"
    elif rsi <= 30:
        rsi_state = "oversold"
    else:
        rsi_state = "neutral"

    if k is None or d is None:
        kdj_state = "unavailable"
    elif k >= 80 and d >= 80:
        kdj_state = "overbought"
    elif k <= 20 and d <= 20:
        kdj_state = "oversold"
    else:
        kdj_state = "neutral"

    if upper is None or lower is None or mid is None:
        boll_state = "unavailable"
    elif price > upper:
        boll_state = "above_upper"
    elif price < lower:
        boll_state = "below_lower"
    elif z is not None and z >= 1.5:
        boll_state = "near_upper"
    elif z is not None and z <= -1.5:
        boll_state = "near_lower"
    elif price >= mid:
        boll_state = "upper_half"
    else:
        boll_state = "lower_half"

    interpretation: list[str] = []
    trend_regime = regime.get("regime") in {"trending_up", "trending_down"}
    if rsi_state in {"overbought", "extreme_overbought"}:
        interpretation.append(
            "RSI处于高位；趋势市中更可能代表动能强，不应单独视为卖出信号。"
            if trend_regime
            else "RSI处于高位且当前非明确趋势市，均值回归风险上升。"
        )
    elif rsi_state in {"oversold", "extreme_oversold"}:
        interpretation.append(
            "RSI处于低位；下降趋势中可能继续钝化，不应单独视为见底信号。"
            if trend_regime
            else "RSI处于低位且当前非明确趋势市，反弹概率条件改善但仍需价格确认。"
        )

    if j is not None and j >= 100:
        interpretation.append("KDJ-J>100，短线动能极热。")
    elif j is not None and j <= 0:
        interpretation.append("KDJ-J<0，短线动能极冷。")
    if boll_state == "above_upper":
        interpretation.append("价格位于布林上轨之外，属于强势延伸或过热，需要结合趋势与量能判断。")
    elif boll_state == "below_lower":
        interpretation.append("价格位于布林下轨之外，属于弱势延伸或超跌，需要等待止跌确认。")

    return {
        "rsi14_state": rsi_state,
        "kdj_state": kdj_state,
        "kdj_j_extreme": "high" if (j is not None and j >= 100) else "low" if (j is not None and j <= 0) else "normal",
        "bollinger_position": boll_state,
        "boll_z": z,
        "interpretation": interpretation,
    }


def _score_snapshot(
    latest: pd.Series,
    previous: pd.Series,
    price: float,
    breakout: bool,
    failed_breakout: bool,
    divergence: str | None,
) -> tuple[int, str, dict]:
    ma20 = _num(latest.get("ma20"))
    ma60 = _num(latest.get("ma60"))
    slope20 = _num(latest.get("ma20_slope_5d_pct"))
    hist = _num(latest.get("macd_hist"))
    prev_hist = _num(previous.get("macd_hist"))
    rsi = _num(latest.get("rsi14"))
    k = _num(latest.get("kdj_k"))
    d = _num(latest.get("kdj_d"))
    pct = _num(latest.get("pct_change"))
    volume_ratio = _num(latest.get("volume_ratio_20"))
    position60 = _num(latest.get("position_60d"))
    atr_pct = _num(latest.get("atr14_pct"))
    adx = _num(latest.get("adx14"))
    obv_change = _num(latest.get("obv_change_5d_pct"))

    trend = 15.0
    if ma20 is not None:
        trend += 5.0 if price > ma20 else -5.0
    if ma20 is not None and ma60 is not None:
        trend += 5.0 if ma20 > ma60 else -5.0
    if slope20 is not None:
        trend += 3.0 if slope20 > 0 else -3.0
    if adx is not None and adx >= 25:
        trend += 2.0
    trend = _clamp(trend, 0.0, 30.0)

    momentum = 12.0
    if hist is not None:
        momentum += 5.0 if hist > 0 else -5.0
    if hist is not None and prev_hist is not None:
        momentum += 3.0 if hist > prev_hist else -3.0
    if rsi is not None:
        if 50 <= rsi <= 70:
            momentum += 3.0
        elif rsi < 40:
            momentum -= 3.0
        elif rsi > 80:
            momentum -= 2.0
    if k is not None and d is not None:
        momentum += 2.0 if k > d else -2.0
    momentum = _clamp(momentum, 0.0, 25.0)

    volume_price = 10.0
    if pct is not None and volume_ratio is not None:
        if pct > 0 and volume_ratio >= 1.2:
            volume_price += 5.0
        elif pct < 0 and volume_ratio >= 1.2:
            volume_price -= 5.0
    if obv_change is not None:
        volume_price += 2.0 if obv_change > 0 else -2.0
    if breakout:
        volume_price += 3.0
    if failed_breakout:
        volume_price -= 5.0
    volume_price = _clamp(volume_price, 0.0, 20.0)

    structure = 7.0
    if position60 is not None:
        if 0.55 <= position60 <= 0.90:
            structure += 4.0
        elif position60 < 0.25:
            structure -= 3.0
    if ma60 is not None:
        structure += 2.0 if price > ma60 else -2.0
    if divergence == "bearish":
        structure -= 3.0
    elif divergence == "bullish":
        structure += 2.0
    structure = _clamp(structure, 0.0, 15.0)

    risk_quality = 5.0
    if rsi is not None and rsi > 80:
        risk_quality -= 2.0
    if atr_pct is not None and atr_pct > 5.0:
        risk_quality -= 2.0
    if failed_breakout:
        risk_quality -= 2.0
    if divergence == "bullish":
        risk_quality += 2.0
    risk_quality = _clamp(risk_quality, 0.0, 10.0)

    total = int(round(trend + momentum + volume_price + structure + risk_quality))
    total = int(_clamp(total, 0, 100))
    if total >= 75:
        label = "强"
    elif total >= 60:
        label = "偏强"
    elif total >= 40:
        label = "震荡"
    elif total >= 25:
        label = "偏弱"
    else:
        label = "弱"
    components = {
        "trend": round(trend, 1),
        "momentum": round(momentum, 1),
        "volume_price": round(volume_price, 1),
        "structure": round(structure, 1),
        "risk_quality": round(risk_quality, 1),
    }
    return total, label, components


def _emotion_proxy(
    latest: pd.Series,
    price: float,
    trend: str,
    breakout: bool,
    failed_breakout: bool,
    divergence: str | None,
) -> dict:
    ret20 = _num(latest.get("return_20d_pct")) or 0.0
    position60 = _num(latest.get("position_60d"))
    rsi = _num(latest.get("rsi14"))
    volume_ratio = _num(latest.get("volume_ratio_20"))
    pct = _num(latest.get("pct_change")) or 0.0
    ma20 = _num(latest.get("ma20"))

    evidence: list[str] = []
    if failed_breakout or (divergence == "bearish" and (position60 or 0.0) > 0.75):
        phase = "distribution_risk"
        label = "高位分歧/派发风险"
        evidence.append("冲高未站稳或出现价格-RSI顶背离")
    elif ret20 >= 15 and (position60 or 0.0) >= 0.85 and (
        (rsi or 0.0) >= 70 or (volume_ratio or 0.0) >= 1.5
    ):
        phase = "acceleration_crowding"
        label = "加速/拥挤"
        evidence.append("20日涨幅较高且价格靠近60日区间上沿")
    elif breakout:
        phase = "early_breakout"
        label = "突破确认候选"
        evidence.append("价格突破前20日高点并得到量能确认")
    elif trend in {"强", "偏强"} and pct < 0 and (volume_ratio or 1.0) < 0.9 and (
        ma20 is not None and price > ma20
    ):
        phase = "healthy_pullback"
        label = "趋势内缩量回踩"
        evidence.append("回落时量能收缩且价格仍在MA20上方")
    elif (position60 or 1.0) <= 0.20 and (rsi or 100.0) <= 35:
        phase = "panic_oversold"
        label = "恐慌/超卖代理"
        evidence.append("价格位于60日低位区域且RSI偏低")
    elif ma20 is not None and price < ma20 and pct < 0 and (volume_ratio or 0.0) >= 1.2:
        phase = "de_risking"
        label = "去风险"
        evidence.append("跌破MA20附近同时下跌放量")
    elif trend in {"强", "偏强"}:
        phase = "trend_acceptance"
        label = "趋势接受"
        evidence.append("价格与中期均线结构保持偏强")
    else:
        phase = "range_rotation"
        label = "震荡/轮动"
        evidence.append("趋势、动能与量价尚未形成单边共振")

    crowding = "低"
    if (rsi or 0.0) >= 75 or ret20 >= 20:
        crowding = "高"
    elif (rsi or 0.0) >= 65 or ret20 >= 10:
        crowding = "中"
    return {
        "phase": phase,
        "label": label,
        "crowding_proxy": crowding,
        "evidence": evidence,
        "scope_note": "仅为价格/成交量情绪代理，不包含新闻、论坛或真实持仓拥挤度。",
    }


def _load_daily(symbol: str, lookback: int) -> tuple[list[dict], str, str, str]:
    end_ref = dates.latest_trade_date()
    requested = max(_MIN_LOOKBACK, min(int(lookback), _MAX_LOOKBACK))
    trade_days = dates.recent_trade_dates(requested, ref=end_ref)
    if trade_days:
        start_ref = trade_days[0]
    else:
        start_ref = end_ref - timedelta(days=max(180, requested * 2))
    start = start_ref.strftime("%Y-%m-%d")
    end = end_ref.strftime("%Y-%m-%d")

    try:
        frame = call_ak(
            "stock_zh_a_hist",
            timeout_seconds=_DAILY_TIMEOUT_SECONDS,
            attempts=1,
            symbol=codes.normalize(symbol),
            period="daily",
            start_date=dates.to_compact(start),
            end_date=dates.to_compact(end),
            adjust="qfq",
        )
        rows = df_to_records(frame)
        source = "akshare / 东方财富"
    except DataSourceError:
        rows = bs.daily_kline(symbol, start, end, "qfq")
        source = "baostock (fallback)"
    return rows, source, start, end


def _realtime_overlay(symbol: str) -> tuple[dict | None, list[str]]:
    try:
        quotes, provider_errors = rt.realtime_quotes([symbol])
        return quotes.get(codes.normalize(symbol)), provider_errors
    except DataSourceError as exc:
        return None, [str(exc)]


@mcp.tool
@guard
@cached(ttl=TTL_MINUTE)
def get_technical_analysis(
    symbol: str,
    lookback: int = 120,
    include_realtime: bool = True,
) -> dict:
    """计算 A 股技术面快照，可叠加腾讯/新浪轻量实时行情。

    指标:
      MA5/10/20/60，EMA5/12/20/26/60，MACD(12,26,9)，RSI14，KDJ(9,3,3)，
      BOLL(20,2)，ATR14，ADX14/+DI/-DI，成交量MA5/20、20日量比、OBV。

    结构化判断:
      趋势/震荡 regime、支撑压力、20日突破/假突破、MA/EMA/MACD/KDJ 金叉死叉、
      RSI/KDJ/BOLL 超买超卖状态、量价关系、波动扩张/收缩、RSI背离、验证/失效条件。

    参数:
      symbol: A股代码，支持 600519 / sh600519 / 600519.SH。
      lookback: 日线回看交易日数量，默认120，自动限制80-250。
      include_realtime: 是否叠加腾讯财经实时行情；腾讯失败自动回退新浪。

    注意:
      technical_score 是启发式综合分，不是上涨概率或买卖评级。
      超买/超卖不等于立即反转；趋势市中 RSI 高位可能只是趋势强度。
    """
    if not symbol:
        return err("bad_request", "symbol 不能为空", "示例: 600519")

    sym = codes.normalize(symbol)
    requested = max(_MIN_LOOKBACK, min(int(lookback), _MAX_LOOKBACK))
    rows, daily_source, requested_start, requested_end = _load_daily(sym, requested)
    df = _add_indicators(_prepare_frame(rows))
    latest = df.iloc[-1]
    previous = df.iloc[-2]

    quote = None
    provider_errors: list[str] = []
    if include_realtime:
        quote, provider_errors = _realtime_overlay(sym)

    latest_close = float(latest["close"])
    realtime_price = _num(quote.get("price")) if quote else None
    current_price = realtime_price if realtime_price and realtime_price > 0 else latest_close
    price_basis = "realtime_quote" if realtime_price and realtime_price > 0 else "latest_daily_close"

    latest_pct = _num(latest.get("pct_change"))
    volume_ratio = _num(latest.get("volume_ratio_20"))
    prev20_high = _num(latest.get("prev_20d_high"))
    breakout = bool(
        prev20_high is not None
        and latest_close > prev20_high
        and volume_ratio is not None
        and volume_ratio >= 1.3
    )
    failed_breakout = bool(
        prev20_high is not None
        and float(latest["high"]) > prev20_high
        and latest_close < prev20_high
    )
    divergence = _rsi_divergence(df)
    regime = _regime_label(latest, current_price)
    trend = _trend_label(latest, current_price)
    score, score_label, score_components = _score_snapshot(
        latest,
        previous,
        current_price,
        breakout,
        failed_breakout,
        divergence,
    )

    atr = _num(latest.get("atr14"))
    support, resistance = _support_resistance(df, current_price, atr)

    ma5 = _num(latest.get("ma5"))
    ma10 = _num(latest.get("ma10"))
    ma20 = _num(latest.get("ma20"))
    ma60 = _num(latest.get("ma60"))
    ema5 = _num(latest.get("ema5"))
    ema12 = _num(latest.get("ema12"))
    ema20 = _num(latest.get("ema20"))
    ema26 = _num(latest.get("ema26"))
    ema60 = _num(latest.get("ema60"))
    dif = _num(latest.get("macd_dif"))
    dea = _num(latest.get("macd_dea"))
    hist = _num(latest.get("macd_hist"))
    rsi = _num(latest.get("rsi14"))
    k = _num(latest.get("kdj_k"))
    d = _num(latest.get("kdj_d"))
    j = _num(latest.get("kdj_j"))

    crossovers = {
        "ma5_ma10": _cross_state(
            _num(previous.get("ma5")), _num(previous.get("ma10")), ma5, ma10
        ),
        "ma10_ma20": _cross_state(
            _num(previous.get("ma10")), _num(previous.get("ma20")), ma10, ma20
        ),
        "ma20_ma60": _cross_state(
            _num(previous.get("ma20")), _num(previous.get("ma60")), ma20, ma60
        ),
        "ema12_ema26": _cross_state(
            _num(previous.get("ema12")), _num(previous.get("ema26")), ema12, ema26
        ),
        "macd_dif_dea": _cross_state(
            _num(previous.get("macd_dif")), _num(previous.get("macd_dea")), dif, dea
        ),
        "kdj_k_d": _cross_state(
            _num(previous.get("kdj_k")), _num(previous.get("kdj_d")), k, d
        ),
    }

    overbought_oversold = _overbought_oversold(latest, regime, current_price)
    volume_price = _volume_price_label(
        latest_pct,
        volume_ratio,
        price=current_price,
        ma20=ma20,
        breakout=breakout,
    )

    signals: list[str] = []
    risk_signals: list[str] = []

    if all(value is not None for value in (ma5, ma10, ma20, ma60)):
        if ma5 > ma10 > ma20 > ma60:
            signals.append("MA5>MA10>MA20>MA60，多头均线排列")
        elif ma5 < ma10 < ma20 < ma60:
            risk_signals.append("MA5<MA10<MA20<MA60，空头均线排列")

    crossover_labels = {
        "ma5_ma10": "MA5/MA10",
        "ma10_ma20": "MA10/MA20",
        "ma20_ma60": "MA20/MA60",
        "ema12_ema26": "EMA12/EMA26",
        "macd_dif_dea": "MACD DIF/DEA",
        "kdj_k_d": "KDJ K/D",
    }
    for key, state in crossovers.items():
        if state == "golden_cross":
            signals.append(f"{crossover_labels[key]} 金叉")
        elif state == "death_cross":
            risk_signals.append(f"{crossover_labels[key]} 死叉")

    if ma20 is not None:
        if current_price > ma20:
            signals.append("价格位于MA20上方")
        else:
            risk_signals.append("价格位于MA20下方")

    if hist is not None:
        if hist > 0:
            signals.append("MACD柱线为正")
        elif hist < 0:
            risk_signals.append("MACD柱线为负")
    prev_hist = _num(previous.get("macd_hist"))
    if hist is not None and prev_hist is not None:
        if hist > prev_hist:
            signals.append("MACD动能较前一交易日改善")
        elif hist < prev_hist:
            risk_signals.append("MACD动能较前一交易日走弱")

    rsi_state = overbought_oversold["rsi14_state"]
    if rsi_state in {"overbought", "extreme_overbought"}:
        risk_signals.append("RSI14进入超买区；趋势市中仅视为过热提醒，不单独判反转")
    elif rsi_state in {"oversold", "extreme_oversold"}:
        signals.append("RSI14进入超卖区；不等于见底，需等待价格确认")

    if overbought_oversold["kdj_state"] == "overbought":
        risk_signals.append("KDJ位于超买区")
    elif overbought_oversold["kdj_state"] == "oversold":
        signals.append("KDJ位于超卖区")

    if breakout:
        signals.append("收盘突破前20日高点且量能>=20日均量1.3倍")
    if failed_breakout:
        risk_signals.append("盘中突破前20日高点但收盘跌回，存在假突破/冲高回落")
    if divergence == "bearish":
        risk_signals.append("近30日检测到价格-RSI顶背离")
    elif divergence == "bullish":
        signals.append("近30日检测到价格-RSI底背离")

    if volume_price in {"放量上涨", "放量突破", "缩量回踩"}:
        signals.append(volume_price)
    elif volume_price in {"放量下跌", "放量震荡"}:
        risk_signals.append(volume_price)

    obv_change = _num(latest.get("obv_change_5d_pct"))
    if obv_change is not None:
        if obv_change > 2:
            signals.append("OBV近5日上行，量能方向对价格形成一定确认")
        elif obv_change < -2:
            risk_signals.append("OBV近5日下行，量能方向偏弱")

    atr_pct = _num(latest.get("atr14_pct"))
    atr_pct_ma20 = _num(latest.get("atr14_pct_ma20"))
    if atr_pct is None or atr_pct_ma20 is None:
        volatility_regime = "unavailable"
    elif atr_pct >= atr_pct_ma20 * 1.15:
        volatility_regime = "expanding"
    elif atr_pct <= atr_pct_ma20 * 0.85:
        volatility_regime = "contracting"
    else:
        volatility_regime = "normal"

    nearest_support = support[0] if support else None
    nearest_resistance = resistance[0] if resistance else None
    confirmation = {
        "strengthen": {
            "price_level": nearest_resistance["zone"][1] if nearest_resistance else None,
            "volume_ratio_min": 1.3,
            "condition": (
                "收盘有效站上最近压力区上沿，并配合至少约1.3倍20日均量"
                if nearest_resistance
                else "等待新高突破并观察量能是否同步放大"
            ),
        },
        "weaken": {
            "price_level": nearest_support["zone"][0] if nearest_support else None,
            "condition": (
                "收盘跌破最近支撑区下沿，若同时放量则弱化信号更强"
                if nearest_support
                else "跌破近期区间低点且放量时视为结构转弱"
            ),
        },
    }

    quote_payload = None
    if quote:
        quote_payload = {
            "price": _num(quote.get("price")),
            "pct_change": _num(quote.get("pct_change")),
            "open": _num(quote.get("open")),
            "high": _num(quote.get("high")),
            "low": _num(quote.get("low")),
            "volume_ratio": _num(quote.get("volume_ratio")),
            "turnover_rate": _num(quote.get("turnover_rate")),
            "quote_time": quote.get("quote_time"),
            "source": quote.get("source"),
            "provider_realtime_flag": bool(quote.get("is_realtime")),
        }
        if ma20:
            quote_payload["vs_ma20_pct"] = round((current_price / ma20 - 1.0) * 100.0, 2)

    latest_date = latest["date"].strftime("%Y-%m-%d")
    data = {
        "summary": {
            "technical_score": score,
            "score_label": score_label,
            "trend": trend,
            "regime": regime,
            "price": round(current_price, 4),
            "price_basis": price_basis,
            "latest_completed_bar": latest_date,
            "score_is_heuristic": True,
            "score_components": score_components,
        },
        "trend": {
            "ma5": ma5,
            "ma10": ma10,
            "ma20": ma20,
            "ma60": ma60,
            "ema5": ema5,
            "ema12": ema12,
            "ema20": ema20,
            "ema26": ema26,
            "ema60": ema60,
            "ma20_slope_5d_pct": _num(latest.get("ma20_slope_5d_pct")),
            "ma60_slope_5d_pct": _num(latest.get("ma60_slope_5d_pct")),
            "ema20_slope_5d_pct": _num(latest.get("ema20_slope_5d_pct")),
            "return_5d_pct": _num(latest.get("return_5d_pct")),
            "return_20d_pct": _num(latest.get("return_20d_pct")),
            "return_60d_pct": _num(latest.get("return_60d_pct")),
        },
        "crossovers": crossovers,
        "momentum": {
            "macd_dif": dif,
            "macd_dea": dea,
            "macd_hist": hist,
            "rsi14": rsi,
            "kdj_k": k,
            "kdj_d": d,
            "kdj_j": j,
            "rsi_divergence_30d": divergence,
            "adx14": _num(latest.get("adx14")),
            "plus_di14": _num(latest.get("plus_di14")),
            "minus_di14": _num(latest.get("minus_di14")),
        },
        "overbought_oversold": overbought_oversold,
        "volume_price": {
            "classification": volume_price,
            "latest_pct_change": latest_pct,
            "latest_volume": _num(latest.get("volume"), 2),
            "volume_ma5": _num(latest.get("volume_ma5"), 2),
            "volume_ma20": _num(latest.get("volume_ma20"), 2),
            "volume_ratio_vs_prior_20d": volume_ratio,
            "obv": _num(latest.get("obv"), 2),
            "obv_ma20": _num(latest.get("obv_ma20"), 2),
            "obv_change_5d_pct": obv_change,
            "breakout_20d_confirmed": breakout,
            "failed_breakout_20d": failed_breakout,
        },
        "volatility": {
            "atr14": atr,
            "atr14_pct": atr_pct,
            "atr14_pct_ma20": atr_pct_ma20,
            "volatility_regime": volatility_regime,
            "boll_mid": _num(latest.get("boll_mid")),
            "boll_upper": _num(latest.get("boll_upper")),
            "boll_lower": _num(latest.get("boll_lower")),
            "boll_width_pct": _num(latest.get("boll_width_pct")),
            "boll_z": _num(latest.get("boll_z")),
        },
        "structure": {
            "position_20d": _num(latest.get("position_20d")),
            "position_60d": _num(latest.get("position_60d")),
            "prev_20d_high": _num(latest.get("prev_20d_high")),
            "prev_20d_low": _num(latest.get("prev_20d_low")),
            "prev_60d_high": _num(latest.get("prev_60d_high")),
            "prev_60d_low": _num(latest.get("prev_60d_low")),
            "support": support,
            "resistance": resistance,
        },
        "emotion_proxy": _emotion_proxy(
            latest,
            current_price,
            trend,
            breakout,
            failed_breakout,
            divergence,
        ),
        "signals": list(dict.fromkeys(signals)),
        "risk_signals": list(dict.fromkeys(risk_signals)),
        "confirmation": confirmation,
        "realtime": quote_payload,
    }

    return ok(
        data,
        symbol=sym,
        lookback=requested,
        bars=len(df),
        requested_window={"start": requested_start, "end": requested_end},
        actual_window={
            "start": df.iloc[0]["date"].strftime("%Y-%m-%d"),
            "end": latest_date,
        },
        source={
            "daily": daily_source,
            "realtime": quote.get("source") if quote else None,
        },
        provider_errors=provider_errors,
        methodology=(
            "MA/EMA/MACD/RSI/KDJ/BOLL/ATR/ADX/OBV + 量价 + 20/60日结构 + 摆动高低点；"
            "先判趋势/震荡regime，再解释超买超卖与交叉信号。"
        ),
        note=DISCLAIMER_NOT_ADVICE,
    )