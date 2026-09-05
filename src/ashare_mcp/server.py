"""Entry point: argparse switches stdio / Streamable HTTP.

stdio  -> ``mcp.run(transport="stdio")`` (for Claude Desktop / Claude Code).
http   -> Streamable HTTP at /mcp via uvicorn, with optional Bearer auth middleware
          (for Claude.ai custom connector / ChatGPT, typically behind cloudflared).
"""

from __future__ import annotations

import argparse
import hmac
import re
import sys

from loguru import logger

from . import __version__
from .app import mcp
from .config import settings, setup_logging

# Import all tool modules so their @mcp.tool decorators register on import.
from .tools import (  # noqa: F401  (imported for side effects)
    chip,
    financial,
    fundflow,
    hsgt,
    lhb,
    margin,
    market,
    meta,
    search_fetch,
    technical_sentiment,
    technical,
)

MCP_PATH = "/mcp"


def _ver_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def _tool_count() -> int | str:
    try:
        import asyncio
        import inspect

        r = mcp.list_tools()
        if inspect.iscoroutine(r):
            r = asyncio.run(r)
        return len(r)
    except Exception:  # noqa: BLE001
        tm = getattr(mcp, "_tool_manager", None)
        d = getattr(tm, "_tools", None) if tm is not None else None
        return len(d) if isinstance(d, dict) else "?"


def _print_banner(transport: str, host: str | None = None, port: int | None = None) -> None:
    try:
        import akshare

        ak_ver = akshare.__version__
    except Exception:  # noqa: BLE001
        ak_ver = "?"

    endpoint = f"http://{host}:{port}{MCP_PATH}" if transport == "http" else "(stdio)"
    auth = "ON" if (transport == "http" and settings.mcp_auth_token) else "OFF"
    banner = f"""
╭───────────────────────────────────────────────────────────╮
│  ashare-mcp  v{__version__}   (China A-share data MCP server)
│  transport : {transport:<10s}  endpoint: {endpoint}
│  http auth : {auth:<10s}  tools   : {_tool_count()}
│  akshare   : {ak_ver}
│  cache db  : {settings.db_path}
│  log dir   : {settings.log_dir}  (level={settings.log_level})
│  tushare   : {"enabled" if settings.tushare_token else "disabled"}
╰───────────────────────────────────────────────────────────╯
"""
    print(banner, file=sys.stderr, flush=True)

    if _ver_tuple(ak_ver) < (1, 18, 0):
        logger.warning(f"akshare {ak_ver} < 1.18.0 — 建议升级: uv add 'akshare>=1.18.0'")


class BearerAuthMiddleware:
    """Minimal ASGI middleware enforcing ``Authorization: Bearer <token>`` on the HTTP
    transport when ``MCP_AUTH_TOKEN`` is set. ``/health`` is always open."""

    def __init__(self, app, token: str = "") -> None:
        self.app = app
        self.token = token or ""

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from starlette.requests import Request
        from starlette.responses import JSONResponse

        request = Request(scope, receive=receive)
        path = request.url.path.rstrip("/")

        if path.endswith("/health"):
            await JSONResponse({"status": "ok", "service": "ashare-mcp",
                                "version": __version__})(scope, receive, send)
            return

        if self.token:
            header = request.headers.get("authorization", "")
            presented = header[7:].strip() if header[:7].lower() == "bearer " else ""
            if not (presented and hmac.compare_digest(presented, self.token)):
                await JSONResponse(
                    {"error": "unauthorized", "detail": "missing or invalid bearer token"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )(scope, receive, send)
                return

        await self.app(scope, receive, send)


def _run_http(host: str, port: int) -> None:
    import uvicorn
    from starlette.middleware import Middleware

    token = settings.mcp_auth_token
    if token:
        logger.info("HTTP transport: Bearer auth ENABLED (MCP_AUTH_TOKEN set)")
    else:
        logger.warning(
            "HTTP transport: Bearer auth DISABLED — set MCP_AUTH_TOKEN before exposing publicly!"
        )

    app = mcp.http_app(path=MCP_PATH, middleware=[Middleware(BearerAuthMiddleware, token=token)])
    uvicorn.run(app, host=host, port=port, log_level=settings.log_level.lower())


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(
        prog="ashare-mcp",
        description="China A-share data MCP server (akshare/baostock; stdio & Streamable HTTP).",
    )
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio",
                        help="stdio (local clients) or http (remote via cloudflared). Default: stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9876, help="HTTP bind port (default 9876)")
    args = parser.parse_args()

    _print_banner(args.transport, args.host, args.port)

    if args.transport == "http":
        _run_http(args.host, args.port)
    else:
        mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
