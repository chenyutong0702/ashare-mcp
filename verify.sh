#!/bin/bash
# Self-contained verification for ashare-mcp. Run on a machine with real network
# (the user's Mac Mini) to validate end-to-end:
#   bash verify.sh
#
# Checks: (1) import + 30 tools, (2) ruff, (3) pytest smoke (live data),
#         (4) HTTP transport health/auth/initialize, (5) `uv run` entrypoint banner.
# PYTHONUTF8=1 + PYTHONPATH=src make imports robust under the non-ASCII project path.
set +e
PROJ="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJ" || exit 9
export PYTHONUTF8=1
export PYTHONPATH="$PROJ/src"
PY=.venv/bin/python
RUFF=.venv/bin/ruff

echo "==== ashare-mcp verification $(date '+%F %T') ===="

echo; echo "---- 1. import + tool registration ----"
"$PY" - <<'PYEOF'
import asyncio, inspect, sys
import ashare_mcp.server          # registers all tools
from ashare_mcp.app import mcp
r = mcp.list_tools()
if inspect.iscoroutine(r): r = asyncio.run(r)
names = sorted(t.name for t in r)
print(f"IMPORT_OK  tools={len(names)}")
expected = {
 "get_realtime_quote","get_daily_kline","get_minute_kline","get_stock_info",
 "get_individual_fund_flow","get_market_fund_flow","get_sector_fund_flow_rank",
 "get_main_fund_flow_rank","get_lhb_daily","get_lhb_stock_detail",
 "get_lhb_institution_daily","get_lhb_active_branches","get_margin_summary",
 "get_margin_stock_detail","get_southbound_flow","get_northbound_top10_today",
 "get_northbound_holdings","get_northbound_realtime","get_northbound_daily_net_flow",
 "get_chip_distribution","get_financial_report","get_earnings_forecast",
 "get_earnings_express","get_announcements","get_research_reports",
 "get_zt_pool","get_stock_comment","get_restricted_release","search","fetch"}
print("MISSING", sorted(expected-set(names)) or "none")
print("EXTRA", sorted(set(names)-expected) or "none")
PYEOF

echo; echo "---- 2. ruff ----"
"$RUFF" check src tests && echo "RUFF_CLEAN" || echo "RUFF_ISSUES"

echo; echo "---- 3. pytest smoke (live data) ----"
"$PY" -m pytest -p no:cacheprovider -q -rN 2>&1 | tail -4

echo; echo "---- 4. HTTP transport (health / auth / initialize) ----"
"$PY" - <<'PYEOF'
import json,os,subprocess,sys,time,urllib.request,urllib.error
PORT=9899; TOKEN="verifytoken"
INIT={"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}
def req(m,p,tok=None,body=None):
    u=f"http://127.0.0.1:{PORT}{p}"; d=json.dumps(body).encode() if body is not None else None
    r=urllib.request.Request(u,data=d,method=m); r.add_header("Content-Type","application/json")
    r.add_header("Accept","application/json, text/event-stream")
    if tok: r.add_header("Authorization",f"Bearer {tok}")
    try:
        with urllib.request.urlopen(r,timeout=8) as x: return x.status,x.read(1500).decode("utf-8","replace")
    except urllib.error.HTTPError as e: return e.code,e.read(1500).decode("utf-8","replace")
    except Exception as e: return -1,type(e).__name__
env=dict(os.environ,MCP_AUTH_TOKEN=TOKEN,LOG_LEVEL="ERROR",PYTHONUTF8="1")
p=subprocess.Popen([sys.executable,"-m","ashare_mcp.server","--transport","http","--host","127.0.0.1","--port",str(PORT)],
                   env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
ready=False
for _ in range(40):
    time.sleep(0.5)
    if req("GET","/health")[0]==200: ready=True; break
    if p.poll() is not None: break
try:
    if not ready: print(f"HTTP_FAIL (exit={p.poll()})")
    else:
        c1,_=req("GET","/health"); c2,_=req("POST","/mcp",None,INIT); c3,b3=req("POST","/mcp",TOKEN,INIT)
        ok=(c1==200 and c2==401 and c3==200 and "ashare-mcp" in b3)
        print(f"health={c1}(200) noauth={c2}(401) auth={c3}(200) init_ok={'ashare-mcp' in b3} -> "+("HTTP_PASS" if ok else "HTTP_FAIL"))
finally:
    p.terminate()
    try: p.wait(timeout=5)
    except Exception: p.kill()
PYEOF

echo; echo "---- 5. uv run entrypoint (real launch path) ----"
# Mirrors exactly how Claude Desktop / Code / launchd start it: `uv run`.
# macOS has no `timeout`, so background + poll /health + kill.
PORT5=9912
LOG_LEVEL=ERROR uv run --directory "$PROJ" ashare-mcp --transport http --port "$PORT5" \
  >/tmp/uv_entry.log 2>&1 &
UVPID=$!
UV_OK=fail
for _ in $(seq 1 60); do
  if [ "$(curl -s -m2 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT5/health" 2>/dev/null)" = "200" ]; then
    UV_OK=ok; break
  fi
  kill -0 "$UVPID" 2>/dev/null || break
  sleep 0.5
done
kill -9 "$UVPID" 2>/dev/null; pkill -9 -f "port $PORT5" 2>/dev/null
[ "$UV_OK" = ok ] && echo "UV_ENTRYPOINT_OK" || { echo "UV_ENTRYPOINT_FAIL"; tail -3 /tmp/uv_entry.log; }

echo; echo "==== done ===="
