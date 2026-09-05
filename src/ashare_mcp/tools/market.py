"""A. 行情 / K线 / 实时报价 tools."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from ..app import mcp
from ..cache import TTL_INFO, TTL_MINUTE, TTL_REALTIME, cached, ttl_daily_kline
from ..data_sources import akshare_src as ak
from ..data_sources import baostock_src as bs
from ..data_sources import realtime_src as rt
from ..data_sources._base import DataSourceError, DataUnavailableError
from ..utils import codes, dates
from ._helpers import DISCLAIMER_NOT_ADVICE, err, guard, ok


def _fmt_min_dt(s: str, days_ago: int, end: bool) -> str:
    tail = " 15:00:00" if end else " 09:30:00"
    if not s:
        base = datetime.now() - timedelta(days=days_ago)
        return base.strftime("%Y-%m-%d") + tail
    s = str(s).strip()
    if len(s) == 10:
        return s + tail
    if len(s) == 8 and s.isdigit():
        return dates.to_dashed(s) + tail
    return s


def _resolve_daily_range(start_date: str, end_date: str, lookback_trade_days: int = 120) -> tuple[str, str]:
    """Resolve omitted daily-K dates to a recent, current-market window.

    - end_date omitted: latest A-share trading day on/before today.
    - start_date omitted: roughly the previous ``lookback_trade_days`` trading days.
    - explicit dates are preserved (apart from normalizing to YYYY-MM-DD).
    """
    raw_start = str(start_date or "").strip()
    raw_end = str(end_date or "").strip()

    if raw_end:
        end_ref = dates.parse_date(raw_end)
        resolved_end = end_ref.strftime("%Y-%m-%d")
    else:
        end_ref = dates.latest_trade_date()
        resolved_end = end_ref.strftime("%Y-%m-%d")

    if raw_start:
        start_ref = dates.parse_date(raw_start)
        resolved_start = start_ref.strftime("%Y-%m-%d")
    else:
        trade_days = dates.recent_trade_dates(lookback_trade_days, ref=end_ref)
        if trade_days:
            start_ref = trade_days[0]
        else:
            # Defensive fallback if the exchange calendar cannot be loaded.
            start_ref = end_ref - timedelta(days=180)
        resolved_start = start_ref.strftime("%Y-%m-%d")

    if start_ref > end_ref:
        raise ValueError(
            f"start_date {resolved_start} cannot be later than end_date {resolved_end}"
        )

    return resolved_start, resolved_end


@mcp.tool
@guard
@cached(ttl=TTL_REALTIME)
def get_realtime_quote(symbols: list[str]) -> dict:
    """获取一只或多只 A 股的轻量实时行情快照(含五档盘口)。

    用途: 盯盘 / 报价查询。主源为腾讯财经单股/批量轻量接口，腾讯失败或漏股时
    自动回退新浪财经；不再为了查询少量股票下载东方财富全市场 5000+ 行数据。
    每个上游请求设置约 4.5 秒硬超时，避免数据源卡死拖到 Dify/MCP deadline。

    参数:
      symbols: 股票代码列表,接受 "600519" / "sh600519" / "600519.SH" 任意写法。

    返回 data[]: 每只股票一条,字段含 code, name, price(最新价), pct_change(涨跌幅%),
      change(涨跌额), open, high, low, prev_close, volume(股), amount(元),
      turnover_rate(换手率%), volume_ratio(量比), pe_ttm, pb, total_market_cap,
      float_market_cap, quote_time, is_realtime, source, 以及 bid_ask 五档盘口。

    若腾讯不可用会自动尝试新浪；若两者都不可用则快速返回 data_source_unavailable，
    调用方应改用 get_daily_kline 的最新交易日收盘数据，而不是长时间等待。
    """
    if not symbols:
        return err("bad_request", "symbols 不能为空", "示例: ['600519','000001']")

    quotes, provider_errors = rt.realtime_quotes(symbols)
    if not quotes:
        detail = "; ".join(provider_errors) or "Tencent/Sina returned no quote rows"
        raise DataUnavailableError(detail)

    out: list[dict] = []
    missing_symbols: list[str] = []
    for symbol in symbols:
        code = codes.normalize(symbol)
        row = quotes.get(code)
        if row is None:
            missing_symbols.append(code)
            row = {
                "code": code,
                "name": None,
                "price": None,
                "quote_time": None,
                "is_realtime": False,
                "source": None,
                "error": "quote_unavailable",
            }
        out.append(dict(row))

    providers_used = sorted(
        {str(row.get("source")) for row in out if row.get("source")}
    )
    return ok(
        out,
        count=len(out),
        source=" + ".join(providers_used) or "Tencent/Sina realtime",
        provider_errors=provider_errors,
        missing_symbols=missing_symbols,
        units={"volume": "share", "amount": "CNY", "market_cap": "CNY"},
        note=DISCLAIMER_NOT_ADVICE,
    )


@mcp.tool
@guard
@cached(ttl=ttl_daily_kline)
def get_daily_kline(
    symbol: str,
    start_date: str = "",
    end_date: str = "",
    adjust: Literal["qfq", "hfq", ""] = "qfq",
) -> dict:
    """获取个股日线 K 线(前复权/后复权/不复权)。

    用途: 技术分析、回测取数。主源 akshare(东财),失败自动降级 baostock。
    参数:
      symbol: 股票代码(多种写法均可)。
      start_date / end_date: "YYYY-MM-DD" 或 "YYYYMMDD"。
        两者都可留空；默认自动取截至最新交易日的最近约 120 个交易日。
        仅留空 start_date 时，会从 end_date 向前取约 120 个交易日；
        仅留空 end_date 时，会自动使用最新交易日。
      adjust: "qfq"前复权(默认) / "hfq"后复权 / ""不复权。
    返回 data[]: date, open, high, low, close, volume, amount, amplitude(振幅%),
      pct_change(涨跌幅%), change(涨跌额), turnover_rate(换手率%)。source 标注实际取数来源。
    """
    sym = codes.normalize(symbol)
    sd, ed = _resolve_daily_range(start_date, end_date)
    source = "akshare"
    try:
        data = ak.daily_hist(sym, sd, ed, adjust)
    except DataSourceError:
        source = "baostock (fallback)"
        data = bs.daily_kline(sym, sd, ed, adjust)
    return ok(
        data,
        symbol=sym,
        count=len(data),
        adjust=adjust,
        start=sd,
        end=ed,
        source=source,
        note=DISCLAIMER_NOT_ADVICE,
    )


@mcp.tool
@guard
@cached(ttl=TTL_MINUTE)
def get_minute_kline(
    symbol: str,
    period: Literal["1", "5", "15", "30", "60"] = "5",
    start_date: str = "",
    end_date: str = "",
    adjust: Literal["qfq", "hfq", ""] = "qfq",
) -> dict:
    """获取个股分钟级 K 线(1/5/15/30/60 分钟)。

    用途: 日内结构分析。数据源东方财富,缓存 5 分钟。
    参数:
      symbol: 股票代码。
      period: 分钟周期 "1"/"5"/"15"/"30"/"60"(默认 "5")。
      start_date / end_date: "YYYY-MM-DD [HH:MM:SS]"。留空则默认取最近约 5 个自然日至今。
        注意: 1 分钟数据东财仅保留最近一段时间。
      adjust: 复权方式,默认前复权。
    返回 data[]: time(时间), open, high, low, close, volume, amount 等。
    """
    sym = codes.normalize(symbol)
    sd = _fmt_min_dt(start_date, days_ago=5, end=False)
    ed = _fmt_min_dt(end_date, days_ago=0, end=True)
    data = ak.minute_hist(sym, period, sd, ed, adjust)
    return ok(data, symbol=sym, period=period, count=len(data),
              start=sd, end=ed, source="akshare / 东方财富", note=DISCLAIMER_NOT_ADVICE)


@mcp.tool
@guard
@cached(ttl=TTL_INFO)
def get_stock_info(symbol: str) -> dict:
    """获取个股基础信息(名称、行业、上市日期、总/流通股本、市值)。

    用途: 公司基本面速览。数据源东方财富,缓存 24 小时。
    参数: symbol 股票代码(多种写法均可)。
    返回 data: name(简称), code, industry(行业), list_date(上市时间),
      total_shares(总股本), float_shares(流通股), total_market_cap(总市值),
      float_market_cap(流通市值)。
    """
    sym = codes.normalize(symbol)
    info = ak.individual_info(sym)
    return ok(info, symbol=sym, source="akshare / 东方财富", note=DISCLAIMER_NOT_ADVICE)
