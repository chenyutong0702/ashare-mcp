# ashare-mcp

生产级**中国 A 股数据 MCP server**,基于 [fastmcp](https://gofastmcp.com) 3.x +
[akshare](https://akshare.akfamily.xyz)。一份代码同时支持 **stdio** 与 **Streamable HTTP**
两种传输,可作为常驻服务接入 Claude Desktop / Claude Code / Claude.ai / ChatGPT。

覆盖:实时行情、日/分钟 K 线、个股资料、资金流向、龙虎榜、融资融券、沪深港通、
筹码分布、财报/业绩预告快报/公告/研报、涨停池、千股千评、限售解禁,以及 ChatGPT 兼容的
`search` / `fetch`。共 **30 个 tool**。

- 主数据源 **akshare**(东方财富等);K 线/财报失败自动降级 **baostock**,可选 **tushare**。
- 失败重试(指数退避 1s/2s)→ 降级 → 结构化错误,**异常绝不冒泡**(不会拖垮客户端连接)。
- SQLite 缓存(分级 TTL)、loguru 日志(轮转)、股票代码与日期统一规范化。
- DataFrame 一律转 `list[dict]`,中文列名映射为英文 `snake_case`(值保留原文)。

---

## ⚠️ 数据可靠性边界(必读)

不同数据"硬度"差别很大,用前务必理解:

| 数据 | 可靠性 | 说明 |
|---|---|---|
| 龙虎榜 | ✅ 可靠 | 交易所官方 T+1 原始数据 |
| 融资融券 | ✅ 可靠 | 交易所官方 T+1 |
| 财务报表 / 业绩预告快报 | ✅ 可靠 | 上市公司披露原始数据 |
| 行情 / K 线 | ✅ 可靠 | 交易所行情,实时快照有数十秒级延迟 |
| 南向资金(港股通) | ✅ 可用 | 完整,未受 2024 调整影响 |
| **主力/大单资金流向** | ⚠️ 软指标 | 东方财富按**单笔成交金额机械分桶**估算,各软件口径不一,**不代表真实机构意图** |
| **筹码分布 / 获利盘** | ⚠️ 软指标 | **概率模型**估算,各家算法不同,精度有限 |
| **千股千评** | ⚠️ 软指标 | 东财综合评分(含主观加权),仅供参考 |
| **北向资金** | 🚫 受限 | **2024-08-19 起官方取消盘中/日频买卖明细披露**。仅余:T+1 成交总额、十大活跃股(无买卖拆分)、季度持仓(延迟约 3 个月)。实时/日频净流入工具会直接返回"已停用"错误 |

> 任何数据与分析均**不构成投资建议**。

---

## 1. 快速开始(Mac Mini M4 / Apple Silicon, macOS 14+)

```bash
# 安装 uv(如未安装)
brew install uv

cd ashare-mcp
uv sync                     # 安装依赖(Python 3.11 由 uv 自动管理)
cp .env.example .env        # 按需填写(全部可留空)

# 本地 stdio 运行(给 Claude Desktop / Claude Code)
uv run ashare-mcp

# 远程 HTTP 运行(给 Claude.ai / ChatGPT,经 cloudflared 暴露)
uv run ashare-mcp --transport http --port 9876
# 健康检查: curl http://127.0.0.1:9876/health
```

可选安装:

```bash
uv sync --extra tushare     # 启用 tushare 降级(还需在 .env 配 TUSHARE_TOKEN)
uv sync --extra dev         # 安装 pytest / ruff(开发用)
```

> 实测环境:fastmcp 3.3.1、akshare 1.18.64、baostock 0.9.10、pandas 3.0.3、Python 3.11.15。

---

## 2. 配置(.env)

| 变量 | 默认 | 说明 |
|---|---|---|
| `TUSHARE_TOKEN` | 空 | 留空禁用 tushare 降级。免费申请: https://tushare.pro/user/token |
| `CACHE_DIR` | `~/.ashare-mcp` | SQLite 缓存与日志目录 |
| `LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`(DEBUG 记录每次外部 API 调用 URL/耗时/缓存命中) |
| `MCP_AUTH_TOKEN` | 空 | HTTP 模式 Bearer 鉴权令牌。**对公网暴露前务必设置** |
| `DEFAULT_LIMIT` | `50` | 排名/全市场类工具默认返回条数 |

---

## 3. 工具清单(30)

**A. 行情** `get_realtime_quote` · `get_daily_kline` · `get_minute_kline` · `get_stock_info`
**B. 资金流向**(均带口径警告) `get_individual_fund_flow` · `get_market_fund_flow` · `get_sector_fund_flow_rank` · `get_main_fund_flow_rank`
**C. 龙虎榜**(官方 T+1) `get_lhb_daily` · `get_lhb_stock_detail` · `get_lhb_institution_daily` · `get_lhb_active_branches`
**D. 融资融券**(官方 T+1) `get_margin_summary` · `get_margin_stock_detail`
**E. 沪深港通** `get_southbound_flow`(可用) · `get_northbound_top10_today`(降级) · `get_northbound_holdings`(季度) · `get_northbound_realtime`/`get_northbound_daily_net_flow`(已停用,返回明确错误)
**F. 筹码分布**(概率模型) `get_chip_distribution`
**G. 财报与公告** `get_financial_report` · `get_earnings_forecast` · `get_earnings_express` · `get_announcements` · `get_research_reports`
**H. 元数据与情绪** `get_zt_pool` · `get_stock_comment` · `get_restricted_release`
**I. ChatGPT 兼容** `search` · `fetch`

股票代码接受 `600519` / `sh600519` / `600519.SH`;日期统一 `YYYY-MM-DD`(也接受 `YYYYMMDD`)。

---

## 4. 接入 Claude Desktop(本地 stdio)

编辑 `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ashare": {
      "command": "uv",
      "args": ["--directory", "/Users/<你>/.../ashare-mcp", "run", "ashare-mcp"],
      "env": {}
    }
  }
}
```

> `command` 建议写 uv 绝对路径(`which uv`,Apple Silicon 通常 `/Users/<你>/.local/bin/uv`),
> 因为 Claude Desktop 的 PATH 可能不含它。重启 Claude Desktop,在对话里 `/` 菜单确认 `ashare` 已连接。

---

## 5. 接入 Claude Code(本地 stdio)

```bash
claude mcp add --scope user --transport stdio ashare -- \
  uv --directory /Users/<你>/.../ashare-mcp run ashare-mcp
claude mcp list          # 验证
# 会话内输入 /mcp 查看已连接的 server 与工具
```

---

## 6. 接入 Claude.ai 网页 / Cowork(远程 HTTP)

### 6.1 用 cloudflared 暴露到公网(免费,推荐)

```bash
brew install cloudflared
cloudflared tunnel login
cloudflared tunnel create ashare-mcp

# 编辑 ~/.cloudflared/config.yml:
# tunnel: <tunnel-id>
# credentials-file: /Users/<你>/.cloudflared/<tunnel-id>.json
# ingress:
#   - hostname: ashare-mcp.<你的域>.com
#     service: http://127.0.0.1:9876
#     originRequest:
#       httpHostHeader: 127.0.0.1:9876     # 防 421 Misdirected Request
#   - service: http_status:404

cloudflared tunnel route dns ashare-mcp ashare-mcp.<你的域>.com
sudo cloudflared service install          # 用 launchd 常驻 tunnel
```

先本地起服务:`uv run ashare-mcp --transport http --port 9876`(或用第 9 节 launchd 常驻)。

### 6.2 OAuth(Claude.ai 强制 OAuth 2.1 + DCR + PKCE)

本 server 自带的 `MCP_AUTH_TOKEN` 是**静态 Bearer**,适合 ChatGPT / 自用脚本;
Claude.ai 需要完整 OAuth,**推荐把 MCP 套在 Cloudflare Access 之后**:

1. Cloudflare Zero Trust → Access → Applications → **Add a SaaS application** → 选 **OIDC**。
2. 回调地址(Redirect URL)填 Claude.ai 添加 connector 时给出的 URL。
3. 用 Access Policy 限制可登录的邮箱/身份。
4. 把上面的 `ingress` 主机名纳入该 Access 应用保护。

这样 Claude.ai 走 Cloudflare Access 的 OAuth,MCP server 本身可不开 `MCP_AUTH_TOKEN`
(或同时开 Bearer 作为二次防线)。

### 6.3 在 Claude.ai 添加

Settings → Connectors → **Add custom connector** → URL 填
`https://ashare-mcp.<你的域>.com/mcp` → 按 Cloudflare Access 提示完成 OAuth 登录。

---

## 7. 接入 ChatGPT(Plus / Pro / Business+)

Settings → **Apps & Connectors** → Advanced → **Developer Mode** → 添加自定义 MCP →
URL 填 `https://ashare-mcp.<你的域>.com/mcp`,鉴权用 Bearer(即 `MCP_AUTH_TOKEN`)。

> **注意**:Plus/Pro 仅能使用 `search` / `fetch` 两个工具(deep research / company knowledge);
> 完整 30 个工具需 Business / Enterprise / Edu。本 server 已实现兼容 OpenAI Apps SDK 的
> `search`/`fetch` schema,deep research 可直接用本服务作数据连接器。

---

## 8. 常驻运行(launchd,开机自启 HTTP 模式)

模板见 `deploy/com.ashare-mcp.http.plist`:

```bash
# 1) 编辑 plist 内的 uv 路径 / 项目路径 / 端口
# 2) 把 MCP_AUTH_TOKEN 改成一长串随机串(对公网暴露必做)
cp deploy/com.ashare-mcp.http.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.ashare-mcp.http.plist
launchctl list | grep ashare-mcp                 # 验证
tail -f ~/.ashare-mcp/logs/launchd.err.log       # 看启动 banner / 日志
# 停止: launchctl unload -w ~/Library/LaunchAgents/com.ashare-mcp.http.plist
```

---

## 9. 缓存与日志

- **缓存**:`~/.ashare-mcp/cache.db`(SQLite + WAL)。TTL 分级:实时报价 10s、分钟 K 5min、
  历史日 K 永久、当日日 K/龙虎榜/两融/资金流/筹码 当日盘后到次日 9:00(盘中 30min)、
  财报/公告/研报 6h。key = `sha256(tool_name + json(args))`。
- **日志**:`~/.ashare-mcp/logs/server.log`,10MB 轮转、保留 7 天;ERROR 同时上 stderr
  (stdio 模式下也能被客户端看到)。`LOG_LEVEL=DEBUG` 记录每次外部 API 的 URL/耗时/缓存命中。

启动时打印 banner:版本号、akshare 版本、缓存路径、传输模式、HTTP 鉴权状态、工具数。

---

## 10. 资源占用(参考)

- 空载:50–100 MB 内存,< 1% CPU。
- 满载(约 10 并发查询):峰值 ~300 MB,< 10% CPU。
- SQLite 缓存运行约 1 个月后约 200–500 MB。

---

## 11. 故障排查

- **客户端连不上(stdio)**:`command` 用 uv 绝对路径;确认 `uv --directory <项目> run ashare-mcp`
  能在终端正常启动。
- **某工具返回 `data_source_unavailable`**:akshare 接口偶发抖动或被限频,稍后重试;或 `uv sync`
  升级 akshare。
- **北向相关报 `discontinued`**:这是预期行为(见数据可靠性边界)。
- **HTTP 421 / 域名问题**:cloudflared `httpHostHeader: 127.0.0.1:9876`。
- **HTTP 401**:缺少或错误的 `Authorization: Bearer <MCP_AUTH_TOKEN>`。
- 测试:`uv run --extra dev pytest`(每个工具一个冒烟测试,联网拉真实数据;无网/被限流时工具返回
  `data_source_unavailable` 也算通过——属预期降级)。一键自检:`bash verify.sh`。

---

## 12. 免责声明

本项目仅用于技术研究与个人数据获取,数据版权归原始来源(交易所、东方财富、巨潮、各券商等)。
所有数据与衍生分析**不构成任何投资建议**,据此操作风险自负。

---

## 13. 许可证

本项目以 [MIT License](LICENSE) 开源。© 2026 CharmYue

## Unified technical and sentiment tool

`technical_sentiment_analysis(symbol, period="daily", lookback=120)` adds one tool to
this server. Existing tools retain their names and signatures. `period` currently
accepts only `daily`; `lookback` is an integer from 80 to 250 trading days.

Example Dify arguments: `{"symbol":"600460.SH","period":"daily","lookback":120}`.
After deploying this revision, refresh the existing MCP provider in Dify; no second
service or new URL is needed.

The normal `ok/data` response contains:
- `technical`: existing MA/EMA, MACD, RSI, KDJ, BOLL, ATR, ADX, OBV, volume-price,
  support/resistance, crossovers, overbought/oversold, trend and risk details.
- `sentiment`: activity versus prior 20-day volume (not intraday volume ratio),
  separately timestamped quote volume ratio/turnover, date-aligned main fund flow,
  daily percentage change, short-term label and explicit score components.
- `technical_score`, `sentiment_score`, `overall_signal`, `summary`, `risk_flags`.

Scores are descriptive heuristics, not probabilities. Sentiment averages available
price-strength and fund-flow-sign components; unavailable inputs are excluded, and
all-missing scores are null. Fund flow on a different date is unavailable, never zero.
Real-time quote values are shown separately and do not affect daily scoring.
The sentiment scope is the individual stock, not news sentiment or market-wide breadth.
