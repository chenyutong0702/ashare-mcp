"""E. 沪深港通 tools.

北向: 2024-08-19 起官方取消盘中/日频买卖明细披露 —— 实时/日频净流入工具直接返回
"已停止披露" 错误;仅保留 T+1 总额、十大活跃股(无买卖拆分)、季度持仓。
南向: 完整可用,未受影响。
"""

from __future__ import annotations

from ..app import mcp
from ..cache import cached, ttl_intraday_official
from ..data_sources import akshare_src as ak
from ..utils import codes, dates
from ._helpers import (
    DISCLAIMER_NORTH,
    DISCLAIMER_NOT_ADVICE,
    DISCLAIMER_SOUTH,
    clamp_limit,
    err,
    guard,
    ok,
)


@mcp.tool
@guard
@cached(ttl=ttl_intraday_official)
def get_southbound_flow(limit: int = 60) -> dict:
    """获取南向资金(港股通)日频净买入历史 —— 完整可用,未受 2024 调整影响。

    参数: limit 返回最近多少条(默认 60)。
    返回 data[]: date, net_buy / net_inflow(当日净买入), balance 等(以 akshare 字段为准)。
    """
    n = clamp_limit(limit, default=60)
    rows = ak.southbound_hist()
    data = rows[-n:] if rows else rows
    return ok(data, count=len(data), direction="southbound", source="akshare / 东方财富",
              note=f"{DISCLAIMER_SOUTH} {DISCLAIMER_NOT_ADVICE}")


@mcp.tool
@guard
@cached(ttl=ttl_intraday_official)
def get_northbound_top10_today(date: str = "") -> dict:
    """获取北向资金当日十大成交活跃股(只有成交总额,无买卖拆分)。

    ⚠️北向口径: 2024-08-19 起官方取消盘中/日频买卖明细,本接口仅提供 T+1 成交总额口径,
    无买入/卖出拆分。

    参数: date "YYYY-MM-DD",留空取最近交易日。
    返回 data[]: 以 akshare 当前字段为准(code, name, 成交/持股相关字段)。
    """
    d = date or dates.latest_trade_date().strftime("%Y-%m-%d")
    data = ak.northbound_top10(d)
    return ok(data, date=dates.to_dashed(d), count=len(data), direction="northbound",
              source="akshare / 东方财富", disclaimer=DISCLAIMER_NORTH, note=DISCLAIMER_NOT_ADVICE)


@mcp.tool
@guard
@cached(ttl=ttl_intraday_official)
def get_northbound_holdings(symbol: str, limit: int = 60) -> dict:
    """获取某只个股的北向持仓变化。

    ⚠️北向口径: 持仓为季度披露口径,延迟约 3 个月;2024-08-19 后无盘中/日频明细。

    参数: symbol 股票代码; limit 返回最近多少条(默认 60)。
    返回 data[]: date, holding_shares(持股数量), holding_market_cap(持股市值),
      holding_pct(占A股百分比) 等(以 akshare 字段为准)。
    """
    sym = codes.normalize(symbol)
    n = clamp_limit(limit, default=60)
    rows = ak.northbound_holdings(sym)
    data = rows[-n:] if rows else rows
    return ok(data, symbol=sym, count=len(data), direction="northbound",
              source="akshare / 东方财富", disclaimer=DISCLAIMER_NORTH, note=DISCLAIMER_NOT_ADVICE)


@mcp.tool
@guard
def get_northbound_realtime() -> dict:
    """[已停用] 北向资金实时/盘中净流入。

    自 2024-08-19 起官方(沪深交易所/港交所)已取消北向资金盘中实时与日频买卖明细披露。
    本工具不再提供该数据,返回明确错误,请改用 get_northbound_top10_today(T+1 总额)
    或 get_northbound_holdings(季度持仓)。
    """
    return err(
        "discontinued",
        "北向资金盘中实时/日频净流入数据自 2024-08-19 起官方已停止披露。",
        "改用 get_northbound_top10_today(当日十大活跃股,仅成交总额) 或 "
        "get_northbound_holdings(季度持仓);南向资金请用 get_southbound_flow。",
    )


@mcp.tool
@guard
def get_northbound_daily_net_flow() -> dict:
    """[已停用] 北向资金日频净流入。

    同上:2024-08-19 起官方已停止披露北向日频净买卖明细。返回明确错误。
    """
    return err(
        "discontinued",
        "北向资金日频净流入数据自 2024-08-19 起官方已停止披露。",
        "改用 get_northbound_top10_today 或 get_northbound_holdings;南向用 get_southbound_flow。",
    )
