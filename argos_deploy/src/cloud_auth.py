"""Separate owner API and ARGOS network credentials; reject missing configuration."""
from __future__ import annotations

import hmac
import os
import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_BEARER = re.compile(rb"Bearer +([A-Za-z0-9._~+/-]+=*)", re.IGNORECASE)


class CloudBearerAuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if (scope["type"] == "http" and scope.get("method") in {"GET", "HEAD"}
                and path in {"/", "/health", "/ui"}):
            await self.app(scope, receive, send)
            return
        network = path == "/network" or path.startswith("/network/")
        key = os.environ.get("ARGOS_NETWORK_KEY" if network else "ARGOS_MCP_API_KEY", "")
        status, detail = 503, "Cloud API access is not configured"
        headers = {"Cache-Control": "no-store"}
        if key.strip():
            values = [value for name, value in scope.get("headers", []) if name.lower() == b"authorization"]
            match = _BEARER.fullmatch(values[0]) if len(values) == 1 else None
            if match and hmac.compare_digest(match.group(1), key.encode("utf-8")):
                await self.app(scope, receive, send)
                return
            status, detail = 401, "Bearer authentication required"
            headers["WWW-Authenticate"] = "Bearer"
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await JSONResponse({"detail": detail}, status_code=status, headers=headers)(scope, receive, send)
