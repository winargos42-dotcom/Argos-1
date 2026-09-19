"""Bearer authentication for the publicly reachable cloud application."""
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
        if (
            scope["type"] == "http"
            and scope.get("method") in {"GET", "HEAD"}
            and scope.get("path") in {"/", "/health", "/ui"}
        ):
            await self.app(scope, receive, send)
            return

        api_key = os.environ.get("ARGOS_MCP_API_KEY", "")
        status = 503
        detail = "Cloud API access is not configured"
        headers = {"Cache-Control": "no-store"}
        if api_key.strip():
            authorization = [
                value for name, value in scope.get("headers", [])
                if name.lower() == b"authorization"
            ]
            match = _BEARER.fullmatch(authorization[0]) if len(authorization) == 1 else None
            if match and hmac.compare_digest(match.group(1), api_key.encode("utf-8")):
                await self.app(scope, receive, send)
                return
            status = 401
            detail = "Bearer authentication required"
            headers["WWW-Authenticate"] = "Bearer"

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response = JSONResponse({"detail": detail}, status_code=status, headers=headers)
        await response(scope, receive, send)
