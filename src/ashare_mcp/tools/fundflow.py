"""B. 资金流向 tools.

每个工具均带【口径警告】: 主力/大单拆分是东方财富按单笔成交金额机械分桶估算,
各软件口径不一,仅供参考,不代表真实机构意图。
"""

from __future__ import annotations

from typing import Literal

from ..app import mcp
from ..cache import cached, ttl_intraday_official
from ..data_sources import akshare_src as ak
from ._helpers import DISCLAIMER_FUNDFLOW, DISCLAIMER_NOT_ADVICE, clamp_limit, guard, ok


@mcp.tool
@guard
@cached(ttl=ttl_intraday_official)
def get_individual_fund_flow(symbol: str) -> dict:
    """获取个股近 100 日资金流向(主力/超大单/大单/中单/小单净流入)。

    ⚠️口径警告: 主力/超大/大/中/小单是东方财富按单笔成交金额机械分桶估算,
    不同行情软件口径不一致,仅供参考,不代表真实机构意图。

    参数: symbol 股票代码。
    返回 data[]: date, close, pct_change, main_net_inflow(主力净额),
      main_net_inflow_pct(主力净占比%), xl_*(超大单), lg_*(大单), md_*(中单),
      sm_*(小单) 净额与净占比。
    """
    data = ak.individual_fund_flow(symbol)
    return ok(data, symbol=symbol, count=len(data), source="akshare / 东方财富",
              disclaimer=DISCLAIMER_FUNDFLOW, note=DISCLAIMER_NOT_ADVICE)


@mcp.tool
@guard
@cached(ttl=ttl_intraday_official)
def get_market_fund_flow() -> dict:
    """获取大盘资金流向历史(沪深整体主力净流入)。

    ⚠️口径警告: 主力/大单拆分为东方财富机械分桶估算,各软件口径不一,仅供参考。

    返回 data[]: date, sh_close, sh_pct_change, sz_close, sz_pct_change,
      main_net_inflow, main_net_inflow_pct, xl_*/lg_*/md_*/sm_* 等。
    """
    data = ak.market_fund_flow()
    return ok(data, count=len(data), source="akshare / 东方财富",
              disclaimer=DISCLAIMER_FUNDFLOW, note=DISCLAIMER_NOT_ADVICE)


@mcp.tool
@guard
@cached(ttl=ttl_intraday_official)
def get_sector_fund_flow_rank(
    period: Literal["今日", "5日", "10日"] = "今日",
    sector_type: Literal["行业资金流", "概念资金流"] = "行业资金流",
    limit: int = 50,
) -> dict:
    """获取行业/概念板块资金流向排名。

    ⚠️口径警告: 主力/大单拆分为东方财富机械分桶估算,各软件口径不一,仅供参考。

    参数: period "今日"/"5日"/"10日"; sector_type "行业资金流"/"概念资金流"; limit 默认 50。
    返回 data[]: name(板块名), pct_change, main_net_inflow, main_net_inflow_pct,
      top_main_inflow_stock(主力净流入最大股) 等。
    """
    n = clamp_limit(limit)
    data = ak.sector_fund_flow_rank(period, sector_type, limit=n)
    return ok(data, period=period, sector_type=sector_type, count=len(data),
              source="akshare / 东方财富", disclaimer=DISCLAIMER_FUNDFLOW,
              note=DISCLAIMER_NOT_ADVICE)


@mcp.tool
@guard
@cached(ttl=ttl_intraday_official)
def get_main_fund_flow_rank(scope: str = "全部股票", limit: int = 50) -> dict:
    """获取个股主力净流入排名。

    ⚠️口径警告: 主力资金为东方财富按单笔金额机械分桶估算,各软件口径不一,仅供参考,
    不代表真实机构意图。

    参数: scope 取数范围(默认 "全部股票",亦可传板块名等 akshare 支持的取值); limit 默认 50。
    返回 data[]: code, name, price, pct_change, main_net_inflow, main_net_inflow_pct 等。
    """
    n = clamp_limit(limit)
    data = ak.main_fund_flow_rank(scope, limit=n)
    return ok(data, scope=scope, count=len(data), source="akshare / 东方财富",
              disclaimer=DISCLAIMER_FUNDFLOW, note=DISCLAIMER_NOT_ADVICE)
