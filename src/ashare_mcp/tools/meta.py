"""H. 元数据与情绪 tools: 涨停池、千股千评、限售解禁。"""

from __future__ import annotations

from ..app import mcp
from ..cache import TTL_FINANCIAL, cached, ttl_intraday_official
from ..data_sources import akshare_src as ak
from ..utils import codes, dates
from ._helpers import DISCLAIMER_NOT_ADVICE, clamp_limit, guard, ok


@mcp.tool
@guard
@cached(ttl=ttl_intraday_official)
def get_zt_pool(date: str = "", limit: int = 100) -> dict:
    """获取某交易日涨停股池(含连板、封单、炸板等)。

    参数: date "YYYY-MM-DD",留空取最近交易日; limit 默认 100。
    返回 data[]: code, name, price, pct_change, seal_amount(封板/封单资金),
      first_seal_time, last_seal_time, break_count(炸板次数),
      consecutive_boards(连板数), days_boards(几天几板), turnover_rate, float_market_cap 等。
    """
    n = clamp_limit(limit, default=100)
    d = date or dates.latest_trade_date().strftime("%Y-%m-%d")
    data = ak.zt_pool(d, limit=n)
    return ok(data, date=dates.to_dashed(d), count=len(data),
              source="akshare / 东方财富", note=DISCLAIMER_NOT_ADVICE)


@mcp.tool
@guard
@cached(ttl=ttl_intraday_official)
def get_stock_comment(symbol: str) -> dict:
    """获取个股千股千评(综合得分、机构参与度、关注指数等)。

    ⚠️说明: "千股千评" 为东方财富综合评分类软指标(含主观加权),仅供参考,不代表确定性结论。

    参数: symbol 股票代码。
    返回 data: composite_score(综合得分), institution_participation(机构参与度),
      current_rank(目前排名), attention_index(关注指数), main_cost(主力成本) 等。
    """
    sym = codes.normalize(symbol)
    data = ak.stock_comment_one(sym)
    return ok(data, symbol=sym, source="akshare / 东方财富",
              disclaimer="千股千评为东财综合评分类软指标,仅供参考。", note=DISCLAIMER_NOT_ADVICE)


@mcp.tool
@guard
@cached(ttl=TTL_FINANCIAL)
def get_restricted_release(start_date: str, end_date: str, limit: int = 50) -> dict:
    """获取限售解禁日程(区间内)。

    参数: start_date / end_date "YYYY-MM-DD"; limit 默认 50。
    返回 data[]: code, name, release_date(解禁时间), release_shares(解禁数量),
      release_market_cap(解禁市值), release_ratio(占解禁前流通市值比例), restricted_type 等。
    """
    n = clamp_limit(limit)
    data = ak.restricted_release(start_date, end_date, limit=n)
    return ok(data, start_date=dates.to_dashed(start_date), end_date=dates.to_dashed(end_date),
              count=len(data), source="akshare / 东方财富", note=DISCLAIMER_NOT_ADVICE)
