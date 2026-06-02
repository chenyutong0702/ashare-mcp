"""D. 融资融券 tools. 官方 T+1,可靠。"""

from __future__ import annotations

from typing import Literal

from ..app import mcp
from ..cache import cached, ttl_intraday_official
from ..data_sources import akshare_src as ak
from ..utils import dates
from ._helpers import DISCLAIMER_OFFICIAL, clamp_limit, err, guard, ok


@mcp.tool
@guard
@cached(ttl=ttl_intraday_official)
def get_margin_summary(market: Literal["sh", "sz"], date: str) -> dict:
    """获取沪市或深市某交易日的两融汇总(官方 T+1,可靠)。

    用途: 观察市场杠杆资金总量与变化。
    参数: market "sh"(上交所)/"sz"(深交所); date "YYYY-MM-DD"。
    返回 data[]: date, margin_balance(融资余额), margin_buy(融资买入额),
      short_balance(融券余额), short_volume(融券余量), margin_short_balance(两融余额) 等。
    """
    d = dates.to_dashed(date)
    if market == "sh":
        data = ak.margin_sse_summary(d, d)
    elif market == "sz":
        data = ak.margin_szse_summary(d)
    else:
        return err("bad_request", f"market 必须是 'sh' 或 'sz',收到 {market!r}", "")
    return ok(data, market=market, date=d, count=len(data),
              source="akshare / 交易所(T+1)", note=DISCLAIMER_OFFICIAL)


@mcp.tool
@guard
@cached(ttl=ttl_intraday_official)
def get_margin_stock_detail(market: Literal["sh", "sz"], date: str, limit: int = 50) -> dict:
    """获取沪市或深市某交易日的个股两融明细。

    用途: 找出杠杆资金集中加减仓的个股。
    参数: market "sh"/"sz"; date "YYYY-MM-DD"; limit 默认 50(数据量大,建议分页/缩小)。
    返回 data[]: code, name, margin_balance, margin_buy, margin_repay,
      short_volume, short_balance 等。
    """
    n = clamp_limit(limit)
    d = dates.to_dashed(date)
    if market == "sh":
        data = ak.margin_detail_sse(d, limit=n)
    elif market == "sz":
        data = ak.margin_detail_szse(d, limit=n)
    else:
        return err("bad_request", f"market 必须是 'sh' 或 'sz',收到 {market!r}", "")
    return ok(data, market=market, date=d, count=len(data),
              source="akshare / 交易所(T+1)", note=DISCLAIMER_OFFICIAL)
