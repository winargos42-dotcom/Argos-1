from __future__ import annotations

import os
import threading
import time
from typing import Any

import aiohttp
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware


class ArgosMCPServer:
    """Минимальный HTTP MCP endpoint для локальной интеграции."""

    def __init__(self, core=None, admin=None):
        self.core = core
        self.admin = admin
        self.started_at = time.time()
        self.app = self._create_app()

    def _providers(self) -> str:
        try:
            from src.ai_providers import providers_status

            return providers_status()
        except Exception as exc:
            return f"providers error: {exc}"

    def _skills(self) -> str:
        if self.core and getattr(self.core, "skill_loader", None):
            try:
                return self.core.skill_loader.list_skills()
            except Exception as exc:
                return f"skills error: {exc}"
        return "skill_loader not initialized"

    def _limits(self) -> str:
        try:
            from src.connectivity.telegram_bot import ArgosTelegram

            bot = ArgosTelegram(self.core, self.admin, None)
            return bot._build_limits_report()
        except Exception as exc:
            return f"limits error: {exc}"

    def _status(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": True,
            "uptime_seconds": int(time.time() - self.started_at),
            "ai_mode": self.core.ai_mode_label() if self.core and hasattr(self.core, "ai_mode_label") else "unknown",
        }
        try:
            import psutil

            out["cpu_pct"] = psutil.cpu_percent(interval=0.1)
            out["ram_pct"] = psutil.virtual_memory().percent
        except Exception:
            pass
        return out

    def _image_generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        steps: int = 20,
        width: int = 1024,
        height: int = 1024,
        model_name: str | None = None,
    ) -> str:
        from src.tools.image_generator import ArgosImageGenerator

        gen = ArgosImageGenerator(model_name=model_name)
        return gen.generate(
            prompt=prompt,
            negative_prompt=negative_prompt,
            steps=steps,
            width=width,
            height=height,
        )

    async def _run_command(self, text: str) -> str:
        """Async version: runs command without creating new event loop."""
        if not text.strip():
            return "empty command"
        runner = getattr(self, "task_runner", None)
        if runner is not None:
            import asyncio
            from src.task_runtime import TERMINAL
            try:
                task = runner.submit(text)
            except (ValueError, RuntimeError) as exc:
                return f"Ошибка: {exc}"
            while task["status"] not in TERMINAL:
                await asyncio.sleep(0.1)
                task = runner.get(task["id"])
                if task is None:
                    return "Ошибка: история задачи недоступна"
            return task["answer"]
        if self.core and hasattr(self.core, "process_logic_async"):
            try:
                # Use await directly since we're already in async context
                result = await self.core.process_logic_async(text, self.admin, None)
                if isinstance(result, dict):
                    return str(result.get("answer", result))
                return str(result)
            except Exception as exc:
                return f"command error: {exc}"
        return "core not initialized"

    def _cloudflare_models(self) -> str:
        models = [
            "@cf/moonshotai/kimi-k2.5",
            "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
            "@cf/meta/llama-3.1-8b-instruct",
            "@cf/meta/llama-3.1-70b-instruct",
            "@cf/meta/llama-2-7b-chat-int8",
            "@cf/mistral/mistral-7b-instruct-v0.2",
            "@cf/mistral/mistral-7b-instruct-v0.1",
            "@cf/google/gemma-2b-it",
            "@cf/google/gemma-7b-it",
            "@cf/qwen/qwen1.5-14b-chat-awq",
            "@cf/qwen/qwen1.5-7b-chat-awq",
            "@cf/qwen/qwen1.5-1.8b-chat",
            "@cf/deepseek-ai/deepseek-math-7b-instruct",
            "@cf/openchat/openchat-3.5-0106",
            "@cf/thebloke/discolm-german-7b-v1-awq",
            "@cf/tiiuae/falcon-7b-instruct",
            "@cf/microsoft/phi-2",
            "@cf/defog/sqlcoder-7b-2",
            "@cf/lynn/soupprompts-7b",
            "@cf/meta/llama-3-8b-instruct",
            "@cf/nousresearch/hermes-2-pro-mistral-7b",
            "@cf/neuralmagic/mistral-7b-instruct-v0.3-awq",
            "@cf/huggingfacehq/zephyr-7b-beta-awq",
            "@cf/unga/tinyllama-1.1b-chat-v1.0",
            "@cf/eleutherai/pythia-2.8b",
        ]
        return "\n".join(models)

    async def _cloudflare_chat(self, prompt: str, model: str | None = None, system: str | None = None, temperature: float = 0.4, max_tokens: int = 1200) -> str:
        import aiohttp
        api_token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
        account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
        if not api_token or not account_id:
            return "Missing CLOUDFLARE_API_TOKEN or CLOUDFLARE_ACCOUNT_ID"
        model_id = model or os.getenv("CLOUDFLARE_MODEL", "@cf/moonshotai/kimi-k2.5")
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model_id}"
        payload = {
            "messages": [
                {"role": "system", "content": system or "You are a helpful assistant."},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            timeout = aiohttp.ClientTimeout(total=120)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers={"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}, json=payload) as resp:
                    data = await resp.json()
                    if data.get("success"):
                        choices = data.get("result", {}).get("choices", [])
                        if choices:
                            return choices[0].get("message", {}).get("content", "")
                        return "Empty response from Cloudflare AI"
                    err = data.get("errors", [{}])[0]
                    return f"Cloudflare AI error: {err.get('message', data)}"
        except Exception as exc:
            return f"Cloudflare request error: {exc}"

    def _create_app(self) -> FastAPI:
        app = FastAPI(title="Argos MCP", version="1.0")
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @app.get("/health")
        def health():
            return self._status()

        @app.get("/mcp")
        def mcp_ping():
            return {
                "name": "argos",
                "ok": True,
                "transport": "http",
                "hint": "POST JSON-RPC to /mcp",
            }

        @app.post("/mcp")
        async def mcp_rpc(request: Request):
            payload = await request.json()
            if not isinstance(payload, dict):
                raise HTTPException(status_code=400, detail="JSON object expected")

            method = payload.get("method", "")
            req_id = payload.get("id")          # None для notifications
            is_notification = req_id is None    # MCP notifications не имеют id

            def _ok(result: Any):
                return {"jsonrpc": "2.0", "id": req_id, "result": result}

            def _err(code: int, message: str):
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

            # ── Notifications (нет id) → пустой ответ 200 ────────────────────
            if is_notification:
                # notifications/initialized, notifications/cancelled, etc.
                return {}

            # ── ping ─────────────────────────────────────────────────────────
            if method == "ping":
                return _ok({})

            # ── initialize ───────────────────────────────────────────────────
            if method == "initialize":
                return _ok(
                    {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "argos", "version": "2.1.3"},
                        "capabilities": {
                            "tools": {"listChanged": False},
                        },
                        "instructions": (
                            "ARGOS Universal OS — AI-экосистема. "
                            "Используй инструмент 'command' для выполнения любых команд ARGOS. "
                            "Инструменты: providers, skills, limits, status, command, image_generate, cloudflare_models, cloudflare_chat."
                        ),
                    }
                )

            # ── tools/list ───────────────────────────────────────────────────
            if method == "tools/list":
                tools = [
                    {
                        "name": "providers",
                        "description": "Показывает статус всех AI-провайдеров ARGOS (Gemini, GigaChat, Grok, OpenAI, Groq, DeepSeek, Kimi, Ollama и др.) с лимитами и квотами.",
                        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                    },
                    {
                        "name": "skills",
                        "description": "Список загруженных скилов (навыков) ARGOS — внешние интеграции и инструменты.",
                        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                    },
                    {
                        "name": "limits",
                        "description": "Отчёт о текущих лимитах и квотах провайдеров.",
                        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                    },
                    {
                        "name": "status",
                        "description": "Текущий статус ARGOS: uptime, CPU, RAM, режим ИИ.",
                        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                    },
                    {
                        "name": "image_generate",
                        "description": "Generate image from prompt and return absolute file path.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "prompt": {"type": "string"},
                                "negative_prompt": {"type": "string"},
                                "steps": {"type": "integer", "minimum": 1, "maximum": 80},
                                "width": {"type": "integer", "minimum": 256, "maximum": 1536},
                                "height": {"type": "integer", "minimum": 256, "maximum": 1536},
                                "model_name": {"type": "string"},
                            },
                            "required": ["prompt"],
                            "additionalProperties": False,
                        },
                    },
                    {
                        "name": "cloudflare_models",
                        "description": "Список доступных моделей Cloudflare Workers AI (текстовые LLM).",
                        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                    },
                    {
                        "name": "cloudflare_chat",
                        "description": "Отправить запрос к любой модели Cloudflare Workers AI. По умолчанию используется @cf/moonshotai/kimi-k2.5.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "prompt": {"type": "string", "description": "Текст запроса пользователя"},
                                "model": {"type": "string", "description": "ID модели Cloudflare, например @cf/moonshotai/kimi-k2.5"},
                                "system": {"type": "string", "description": "Системный промпт (опционально)"},
                                "temperature": {"type": "number", "minimum": 0, "maximum": 2, "default": 0.4},
                                "max_tokens": {"type": "integer", "minimum": 1, "maximum": 4096, "default": 1200},
                            },
                            "required": ["prompt"],
                            "additionalProperties": False,
                        },
                    },
                    {
                        "name": "command",
                        "description": (
                            "Выполнить команду через ядро ARGOS. "
                            "Примеры: 'статус', 'hf status', 'провайдеры', 'память', 'мысли', 'эволюция', 'режим ии grok'. "
                            "Поддерживаются все команды ARGOS."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "text": {
                                    "type": "string",
                                    "description": "Команда для ARGOS на русском или английском языке",
                                }
                            },
                            "required": ["text"],
                            "additionalProperties": False,
                        },
                    },
                ]
                return _ok({"tools": tools})

            # ── tools/call ───────────────────────────────────────────────────
            if method == "tools/call":
                params = payload.get("params") or {}
                name = params.get("name")
                args = params.get("arguments") or {}
                try:
                    if name == "providers":
                        text = self._providers()
                    elif name == "skills":
                        text = self._skills()
                    elif name == "limits":
                        text = self._limits()
                    elif name == "status":
                        text = str(self._status())
                    elif name == "image_generate":
                        text = self._image_generate(
                            prompt=str(args.get("prompt", "")),
                            negative_prompt=str(args.get("negative_prompt", "")),
                            steps=int(args.get("steps", 20) or 20),
                            width=int(args.get("width", 1024) or 1024),
                            height=int(args.get("height", 1024) or 1024),
                            model_name=(str(args.get("model_name")) if args.get("model_name") else None),
                        )
                    elif name == "cloudflare_models":
                        text = self._cloudflare_models()
                    elif name == "cloudflare_chat":
                        text = await self._cloudflare_chat(
                            prompt=str(args.get("prompt", "")),
                            model=str(args.get("model")) if args.get("model") else None,
                            system=str(args.get("system")) if args.get("system") else None,
                            temperature=float(args.get("temperature", 0.4) or 0.4),
                            max_tokens=int(args.get("max_tokens", 1200) or 1200),
                        )
                    elif name == "command":
                        text = await self._run_command(str(args.get("text", "")))
                    else:
                        return _err(-32601, f"Unknown tool: {name}")
                except Exception as exc:
                    text = f"tool error: {exc}"
                return _ok({"content": [{"type": "text", "text": text}]})

            return _err(-32601, f"Method not found: {method}")

        return app


def start_mcp_api(core=None, admin=None, host: str = "127.0.0.1", port: int = 8000):
    server = ArgosMCPServer(core=core, admin=admin)
    config = uvicorn.Config(server.app, host=host, port=port, log_level="warning")
    uv_server = uvicorn.Server(config)
    thread = threading.Thread(target=uv_server.run, daemon=True, name="ArgosMCP")
    thread.start()
    return thread


app = ArgosMCPServer(core=None, admin=None).app
