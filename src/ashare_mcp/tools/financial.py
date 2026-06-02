"""G. 财报与公告 tools. akshare 主源,失败降级 baostock / tushare(若配置)。"""

from __future__ import annotations

from typing import Literal

from ..app import mcp
from ..cache import TTL_FINANCIAL, cached
from ..data_sources import akshare_src as ak
from ..data_sources import baostock_src as bs
from ..data_sources import tushare_src as ts
from ..data_sources._base import DataSourceError, DataUnavailableError
from ..utils import codes, dates
from ._helpers import DISCLAIMER_NOT_ADVICE, clamp_limit, guard, ok


def _period_to_yq(period: str) -> tuple[int | None, int | None]:
    p = dates.to_compact(period)
    if len(p) >= 6 and p[:4].isdigit():
        q = {"03": 1, "06": 2, "09": 3, "12": 4}.get(p[4:6])
        if q:
            return int(p[:4]), q
    return None, None


@mcp.tool
@guard
@cached(ttl=TTL_FINANCIAL)
def get_financial_report(
    symbol: str,
    report_type: Literal["balance", "income", "cashflow"],
    period: str = "",
    limit: int = 8,
) -> dict:
    """获取财务报表(资产负债表/利润表/现金流量表),按报告期。

    用途: 基本面分析。主源 akshare(东财按报告期),失败降级 baostock,再降级 tushare(若配置 token)。
    参数:
      symbol: 股票代码。
      report_type: "balance"资产负债表 / "income"利润表 / "cashflow"现金流量表。
      period: 报告期 "YYYY-MM-DD"(如 2024-03-31);留空则返回最近 limit 期。
      limit: 返回最近多少个报告期(默认 8)。字段名为东财原始英文字段(已转小写 snake_case)。
    返回 data[]: 各报表科目(字段随数据源而定,英文 key,值保留原值)。source 标注实际来源。
    """
    sym = codes.normalize(symbol)
    n = clamp_limit(limit, default=8, maximum=100)
    errors: list[str] = []

    try:
        data = ak.financial_report(sym, report_type, period or None, limit=n)
        return ok(data, symbol=sym, report_type=report_type, period=period,
                  count=len(data), source="akshare / 东方财富", note=DISCLAIMER_NOT_ADVICE)
    except DataSourceError as e:
        errors.append(f"akshare: {e}")

    y, q = _period_to_yq(period)
    if y and q:
        try:
            data = bs.financial(sym, report_type, y, q)
            return ok(data, symbol=sym, report_type=report_type, period=period,
                      count=len(data), source="baostock (fallback)", note=DISCLAIMER_NOT_ADVICE)
        except DataSourceError as e:
            errors.append(f"baostock: {e}")

    if ts.is_available():
        try:
            data = ts.financial(sym, report_type, period or None)
            return ok(data, symbol=sym, report_type=report_type, period=period,
                      count=len(data), source="tushare (fallback)", note=DISCLAIMER_NOT_ADVICE)
        except DataSourceError as e:
            errors.append(f"tushare: {e}")

    raise DataUnavailableError("; ".join(errors) or "all financial sources failed")


@mcp.tool
@guard
@cached(ttl=TTL_FINANCIAL)
def get_earnings_forecast(date: str, limit: int = 100) -> dict:
    """获取业绩预告(某报告期)。

    参数: date 报告期 "YYYY-MM-DD"(如 2024-03-31,通常用季末日); limit 默认 100。
    返回 data[]: code, name, forecast_indicator(预测指标), performance_change(业绩变动),
      forecast_value(预测数值), forecast_type(预告类型), change_reason 等。
    """
    n = clamp_limit(limit, default=100)
    data = ak.earnings_forecast(date, limit=n)
    return ok(data, date=dates.to_dashed(date), count=len(data),
              source="akshare / 东方财富", note=DISCLAIMER_NOT_ADVICE)


@mcp.tool
@guard
@cached(ttl=TTL_FINANCIAL)
def get_earnings_express(date: str, limit: int = 100) -> dict:
    """获取业绩快报(某报告期)。

    参数: date 报告期 "YYYY-MM-DD"; limit 默认 100。
    返回 data[]: code, name, revenue, net_profit, revenue_yoy, net_profit_yoy 等。
    """
    n = clamp_limit(limit, default=100)
    data = ak.earnings_express(date, limit=n)
    return ok(data, date=dates.to_dashed(date), count=len(data),
              source="akshare / 东方财富", note=DISCLAIMER_NOT_ADVICE)


@mcp.tool
@guard
@cached(ttl=TTL_FINANCIAL)
def get_announcements(symbol: str, limit: int = 20) -> dict:
    """获取个股近期公告(标题/类型/日期/链接)。

    参数: symbol 股票代码; limit 默认 20。
    返回 data[]: title(公告标题), notice_type/type, announce_date/date, url 等。
    数据源优先巨潮(cninfo)个股披露,失败降级东财当日公告并按代码过滤。
    """
    n = clamp_limit(limit, default=20)
    data = ak.announcements(symbol=symbol, limit=n)
    return ok(data, symbol=codes.normalize(symbol), count=len(data),
              source="akshare / 巨潮·东方财富", note=DISCLAIMER_NOT_ADVICE)


@mcp.tool
@guard
@cached(ttl=TTL_FINANCIAL)
def get_research_reports(symbol: str, limit: int = 10) -> dict:
    """获取个股卖方研报列表。

    参数: symbol 股票代码; limit 默认 10。
    返回 data[]: title(研报标题), institution(机构), analyst(分析师), rating(评级),
      date 等。
    """
    n = clamp_limit(limit, default=10)
    data = ak.research_reports(symbol, limit=n)
    return ok(data, symbol=codes.normalize(symbol), count=len(data),
              source="akshare / 东方财富", note=DISCLAIMER_NOT_ADVICE)
