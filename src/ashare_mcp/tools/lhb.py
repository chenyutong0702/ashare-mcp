"""C. 龙虎榜 tools. 官方 T+1 数据,可靠。"""

from __future__ import annotations

from ..app import mcp
from ..cache import cached, ttl_intraday_official
from ..data_sources import akshare_src as ak
from ..data_sources._base import DataSourceError
from ..utils import codes, dates
from ._helpers import DISCLAIMER_OFFICIAL, clamp_limit, guard, ok


@mcp.tool
@guard
@cached(ttl=ttl_intraday_official)
def get_lhb_daily(start_date: str, end_date: str, limit: int = 50) -> dict:
    """获取区间内所有龙虎榜上榜个股明细(官方 T+1,可靠)。

    用途: 找出某段时间的游资/机构博弈标的。
    参数: start_date / end_date "YYYY-MM-DD"; limit 返回条数上限(默认 50)。
    返回 data[]: code, name, list_date(上榜日), reason(上榜原因), close,
      pct_change, lhb_net_buy(龙虎榜净买额), lhb_buy, lhb_sell, lhb_turnover,
      net_buy_ratio(净买额占总成交比), turnover_rate, float_market_cap 等。
    """
    n = clamp_limit(limit)
    data = ak.lhb_detail(start_date, end_date, limit=n)
    return ok(data, count=len(data), start_date=dates.to_dashed(start_date),
              end_date=dates.to_dashed(end_date), source="akshare / 东方财富(交易所T+1)",
              note=DISCLAIMER_OFFICIAL)


@mcp.tool
@guard
@cached(ttl=ttl_intraday_official)
def get_lhb_stock_detail(symbol: str, date: str) -> dict:
    """获取单只个股某日龙虎榜的买卖席位明细(5 买 + 5 卖)。

    用途: 看具体哪些营业部/机构专用席位在买卖(含机构专用、知名游资营业部)。
    参数: symbol 股票代码; date 上榜日 "YYYY-MM-DD"。
    返回 data: { buy_seats:[...], sell_seats:[...] },每个席位含 branch_name(营业部名称),
      buy_amount, sell_amount, net_amount 等。
    """
    sym = codes.normalize(symbol)

    def _safe(flag: str) -> list[dict]:
        try:
            return ak.lhb_stock_detail(sym, date, flag)
        except DataSourceError:
            return []

    buy = _safe("买入")
    sell = _safe("卖出")
    return ok({"buy_seats": buy, "sell_seats": sell}, symbol=sym, date=dates.to_dashed(date),
              source="akshare / 东方财富(交易所T+1)", note=DISCLAIMER_OFFICIAL)


@mcp.tool
@guard
@cached(ttl=ttl_intraday_official)
def get_lhb_institution_daily(date: str, limit: int = 50) -> dict:
    """获取某日机构买卖每日统计(机构专用席位当日净买卖)。

    用途: 观察机构资金当日整体动向。
    参数: date "YYYY-MM-DD"; limit 默认 50。
    返回 data[]: code, name, close, pct_change, buy_inst_count(买方机构数),
      sell_inst_count(卖方机构数), inst_buy_total, inst_sell_total, inst_net_buy 等。
    """
    n = clamp_limit(limit)
    d = dates.to_dashed(date)
    data = ak.lhb_institution_daily(d, d, limit=n)
    return ok(data, count=len(data), date=d, source="akshare / 东方财富(交易所T+1)",
              note=DISCLAIMER_OFFICIAL)


@mcp.tool
@guard
@cached(ttl=ttl_intraday_official)
def get_lhb_active_branches(start_date: str = "", end_date: str = "", limit: int = 50) -> dict:
    """获取近期活跃营业部(席位)排行。

    用途: 跟踪知名游资营业部动向。
    参数: start_date / end_date 留空则默认最近约 30 个自然日; limit 默认 50。
    返回 data[]: branch_name(营业部名称), buy_amount, sell_amount, 上榜/买卖个股数等。
    """
    n = clamp_limit(limit)
    if not end_date:
        end_date = dates.today_str()
    if not start_date:
        sd = dates.recent_trade_dates(20)
        start_date = sd[0].strftime("%Y-%m-%d") if sd else end_date
    data = ak.lhb_active_branches(start_date, end_date, limit=n)
    return ok(data, count=len(data), start_date=dates.to_dashed(start_date),
              end_date=dates.to_dashed(end_date), source="akshare / 东方财富",
              note=DISCLAIMER_OFFICIAL)
