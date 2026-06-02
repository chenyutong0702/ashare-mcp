"""Shared data-source primitives: exceptions, DataFrame->records conversion, column map.

Per PRD: never expose akshare's raw Chinese column names. We rename to English
snake_case keys (values stay as-is, often Chinese text). ``COLUMN_MAP`` covers the
columns the ~25 tools actually return; unmapped columns fall back to a slug (and, for
unmapped Chinese, keep the original as a last resort + a debug log so we can extend).
"""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class DataSourceError(Exception):
    """Base class for all data-source failures."""


class EmptyDataError(DataSourceError):
    """A source responded but returned no rows (retryable / 'no_data')."""


class DataUnavailableError(DataSourceError):
    """A source is unavailable after retries + fallbacks."""


# --------------------------------------------------------------------------- #
# Column map (Chinese -> english snake_case)
# --------------------------------------------------------------------------- #
COLUMN_MAP: dict[str, str] = {
    # generic / time / identity
    "日期": "date", "时间": "time", "交易日期": "date", "上榜日": "list_date",
    "上榜日期": "list_date", "公告日期": "announce_date", "报告期": "report_period",
    "报告日": "report_date", "序号": "rank", "代码": "code", "股票代码": "code",
    "证券代码": "code", "标的证券代码": "code", "名称": "name", "股票简称": "name",
    "证券简称": "name", "标的证券简称": "name", "数量": "quantity", "类型": "type",
    # price / OHLC / quote
    "开盘": "open", "今开": "open", "收盘": "close", "最高": "high", "最低": "low",
    "昨收": "prev_close", "最新价": "price", "最新": "price", "收盘价": "close",
    "成交量": "volume", "成交额": "amount", "成交金额": "turnover", "振幅": "amplitude",
    "涨跌幅": "pct_change", "涨跌额": "change", "涨跌": "change", "换手率": "turnover_rate",
    "换手": "turnover_rate", "量比": "volume_ratio", "涨速": "speed",
    "5分钟涨跌": "change_5min", "60日涨跌幅": "pct_change_60d",
    "年初至今涨跌幅": "pct_change_ytd", "今日涨跌幅": "pct_change", "均价": "avg_price",
    "市盈率-动态": "pe_ttm", "市盈率": "pe", "市净率": "pb",
    "总市值": "total_market_cap", "流通市值": "float_market_cap",
    # five-level bid/ask (stock_bid_ask_em items)
    "卖一": "ask_1", "卖二": "ask_2", "卖三": "ask_3", "卖四": "ask_4", "卖五": "ask_5",
    "买一": "bid_1", "买二": "bid_2", "买三": "bid_3", "买四": "bid_4", "买五": "bid_5",
    "卖一量": "ask_1_vol", "卖二量": "ask_2_vol", "卖三量": "ask_3_vol",
    "卖四量": "ask_4_vol", "卖五量": "ask_5_vol", "买一量": "bid_1_vol",
    "买二量": "bid_2_vol", "买三量": "bid_3_vol", "买四量": "bid_4_vol",
    "买五量": "bid_5_vol", "总手": "volume", "外盘": "outer_volume",
    "内盘": "inner_volume", "涨停": "limit_up", "跌停": "limit_down", "涨幅": "pct_change",
    # stock info (stock_individual_info_em)
    "总股本": "total_shares", "流通股": "float_shares", "行业": "industry",
    "上市时间": "list_date", "上市日期": "list_date",
    # fund flow
    "主力净流入-净额": "main_net_inflow", "主力净流入-净占比": "main_net_inflow_pct",
    "超大单净流入-净额": "xl_net_inflow", "超大单净流入-净占比": "xl_net_inflow_pct",
    "大单净流入-净额": "lg_net_inflow", "大单净流入-净占比": "lg_net_inflow_pct",
    "中单净流入-净额": "md_net_inflow", "中单净流入-净占比": "md_net_inflow_pct",
    "小单净流入-净额": "sm_net_inflow", "小单净流入-净占比": "sm_net_inflow_pct",
    "今日主力净流入-净额": "main_net_inflow", "今日主力净流入-净占比": "main_net_inflow_pct",
    "今日超大单净流入-净额": "xl_net_inflow", "今日超大单净流入-净占比": "xl_net_inflow_pct",
    "今日大单净流入-净额": "lg_net_inflow", "今日大单净流入-净占比": "lg_net_inflow_pct",
    "今日中单净流入-净额": "md_net_inflow", "今日中单净流入-净占比": "md_net_inflow_pct",
    "今日小单净流入-净额": "sm_net_inflow", "今日小单净流入-净占比": "sm_net_inflow_pct",
    "今日主力净流入最大股": "top_main_inflow_stock", "主力净流入最大股": "top_main_inflow_stock",
    "上证-收盘价": "sh_close", "上证-涨跌幅": "sh_pct_change",
    "深证-收盘价": "sz_close", "深证-涨跌幅": "sz_pct_change",
    # LHB
    "解读": "interpretation", "龙虎榜净买额": "lhb_net_buy", "龙虎榜买入额": "lhb_buy",
    "龙虎榜卖出额": "lhb_sell", "龙虎榜成交额": "lhb_turnover",
    "市场总成交额": "market_turnover", "净买额占总成交比": "net_buy_ratio",
    "成交额占总成交比": "turnover_ratio", "上榜原因": "reason",
    "上榜后1日": "after_1d", "上榜后2日": "after_2d", "上榜后5日": "after_5d",
    "上榜后10日": "after_10d", "买方机构数": "buy_inst_count",
    "卖方机构数": "sell_inst_count", "机构买入总额": "inst_buy_total",
    "机构卖出总额": "inst_sell_total", "机构买入净额": "inst_net_buy",
    "营业部名称": "branch_name", "交易营业部名称": "branch_name",
    "买入金额": "buy_amount", "卖出金额": "sell_amount", "净额": "net_amount",
    "买入次数": "buy_count", "卖出次数": "sell_count", "买入股票": "buy_stocks",
    "上榜后1天": "after_1d", "上榜后2天": "after_2d", "上榜后5天": "after_5d",
    "上榜后10天": "after_10d",
    # margin
    "信用交易日期": "date", "融资余额": "margin_balance", "融资买入额": "margin_buy",
    "融资偿还额": "margin_repay", "融券余量": "short_volume",
    "融券余额": "short_balance", "融券卖出量": "short_sell_volume",
    "融券偿还量": "short_repay_volume", "融券余量金额": "short_balance_amount",
    "融资融券余额": "margin_short_balance",
    # HSGT
    "当日资金流入": "net_inflow", "当日余额": "balance", "当日成交净买额": "net_buy",
    "持股市值": "holding_market_cap", "持股数量": "holding_shares",
    "持股市值变化-1日": "holding_change_1d", "持股市值变化-5日": "holding_change_5d",
    "持股市值变化-10日": "holding_change_10d", "持股数量占A股百分比": "holding_pct",
    "持股占流通股比": "holding_float_pct", "机构名称": "institution", "净买额": "net_buy",
    "买入额": "buy_amount", "卖出额": "sell_amount",
    # chip distribution (stock_cyq_em)
    "获利比例": "profit_ratio", "平均成本": "avg_cost", "90成本-低": "cost_90_low",
    "90成本-高": "cost_90_high", "90集中度": "concentration_90",
    "70成本-低": "cost_70_low", "70成本-高": "cost_70_high",
    "70集中度": "concentration_70",
    # financial / forecast / announcements / research
    "净利润": "net_profit", "营业收入": "revenue", "营业总收入": "total_revenue",
    "净利润同比": "net_profit_yoy", "营业收入同比": "revenue_yoy",
    "预测指标": "forecast_indicator", "业绩变动": "performance_change",
    "预测数值": "forecast_value", "预告数值": "forecast_value", "预告类型": "forecast_type",
    "业绩变动原因": "change_reason", "预测内容": "forecast_content",
    "业绩变动幅度": "change_range", "摘要": "summary", "标题": "title",
    "公告标题": "title", "公告类型": "notice_type", "网址": "url", "研报标题": "title",
    "东财评级": "rating", "最新评级": "rating", "机构": "institution",
    "分析师": "analyst", "报告名称": "title", "评级机构": "institution",
    "选项": "item",
    # zt pool / meta
    "连板数": "consecutive_boards", "首次封板时间": "first_seal_time",
    "最后封板时间": "last_seal_time", "封板资金": "seal_amount",
    "炸板次数": "break_count", "涨停原因类别": "reason_category",
    "几天几板": "days_boards", "封单资金": "seal_amount", "涨停统计": "limit_up_stat",
    # stock comment
    "机构参与度": "institution_participation", "综合得分": "composite_score",
    "上升": "rising", "目前排名": "current_rank", "关注指数": "attention_index",
    "主力成本": "main_cost", "市场热度": "market_heat",
    # restricted release
    "解禁时间": "release_date", "解禁数量": "release_shares",
    "实际解禁数量": "actual_release_shares", "解禁股流通市值": "release_market_cap",
    "占解禁前流通市值比例": "release_ratio", "限售股类型": "restricted_type",
    "解禁前一交易日收盘价": "prev_close", "股东户数": "shareholder_count",
}

_NONWORD_RE = re.compile(r"[^0-9a-zA-Z]+")


def _fallback_key(col: str) -> str:
    s = str(col).strip()
    if s.isascii():
        slug = _NONWORD_RE.sub("_", s).strip("_").lower()
        return slug or "field"
    logger.debug(f"unmapped column kept as-is: {col!r}")
    return s


def _clean(v: Any) -> Any:
    """Convert numpy/pandas scalars + NaN/NaT to JSON-friendly native python."""
    if v is None:
        return None
    if isinstance(v, float):
        return None if math.isnan(v) else v
    try:
        if v is pd.NaT or (np.isscalar(v) and pd.isna(v)):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        f = float(v)
        return None if math.isnan(f) else f
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:  # noqa: BLE001
            return v
    return v


def _key_for(col: str, rename: dict[str, str] | None) -> str:
    if rename and col in rename:
        return rename[col]
    if col in COLUMN_MAP:
        return COLUMN_MAP[col]
    return _fallback_key(col)


def df_to_records(
    df: pd.DataFrame | None,
    rename: dict[str, str] | None = None,
    fields: list[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Convert a DataFrame to a list of dicts with mapped english keys + cleaned values."""
    if df is None or len(df) == 0:
        return []
    if fields:
        keep = [c for c in fields if c in df.columns]
        if keep:
            df = df[keep]
    if limit is not None:
        df = df.head(limit)
    mapping = {c: _key_for(c, rename) for c in df.columns}
    df2 = df.rename(columns=mapping)
    records = df2.to_dict(orient="records")
    return [{k: _clean(v) for k, v in rec.items()} for rec in records]


def df_kv_to_dict(
    df: pd.DataFrame | None,
    key_col: str = "item",
    val_col: str = "value",
    rename: dict[str, str] | None = None,
) -> dict:
    """Convert a 2-column (item/value) DataFrame into a flat dict with mapped keys."""
    if df is None or len(df) == 0:
        return {}
    cols = list(df.columns)
    kc = key_col if key_col in cols else cols[0]
    vc = val_col if val_col in cols else cols[-1]
    out: dict = {}
    for _, row in df.iterrows():
        raw = str(row[kc])
        out[_key_for(raw, rename)] = _clean(row[vc])
    return out
