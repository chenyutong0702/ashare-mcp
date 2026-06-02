"""F. 筹码分布 tool. 东财算法,概率模型,精度有限。"""

from __future__ import annotations

from typing import Literal

from ..app import mcp
from ..cache import cached, ttl_intraday_official
from ..data_sources import akshare_src as ak
from ..utils import codes
from ._helpers import DISCLAIMER_CHIP, DISCLAIMER_NOT_ADVICE, clamp_limit, guard, ok


@mcp.tool
@guard
@cached(ttl=ttl_intraday_official)
def get_chip_distribution(
    symbol: str,
    adjust: Literal["qfq", "hfq", ""] = "",
    limit: int = 60,
) -> dict:
    """获取个股筹码分布(主力成本、获利盘比例、集中度、平均成本)。

    ⚠️口径警告: 筹码分布是概率模型估算,各家算法与口径不一,精度有限,仅供参考。

    参数: symbol 股票代码; adjust 复权方式(默认不复权); limit 返回最近多少个交易日(默认 60)。
    返回 data[]: date, profit_ratio(获利比例), avg_cost(平均成本),
      cost_90_low/cost_90_high/concentration_90(90% 成本区间与集中度),
      cost_70_low/cost_70_high/concentration_70(70% 成本区间与集中度)。
    """
    sym = codes.normalize(symbol)
    n = clamp_limit(limit, default=60)
    rows = ak.chip_distribution(sym, adjust=adjust)
    data = rows[-n:] if rows else rows
    return ok(data, symbol=sym, count=len(data), source="akshare / 东方财富",
              disclaimer=DISCLAIMER_CHIP, note=DISCLAIMER_NOT_ADVICE)
