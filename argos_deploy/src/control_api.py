from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from src import mempalace_bridge
from src.task_runtime import TERMINAL


def panel_response():
    page = Path(__file__).with_name("interface") / "control_panel.html"
    return HTMLResponse(page.read_text(encoding="utf-8"), headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
        "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'",
    })


def create_control_router(runner, status_provider):
    router = APIRouter(prefix="/api")

    def require_task(task_id):
        task = runner.get(task_id)
        if task is None:
            raise HTTPException(404, "Задача не найдена")
        return task

    async def text_body(request, limit):
        try:
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > limit * 6 + 100:
                    raise ValueError
                raw.extend(chunk)
            data = json.loads(raw)
            text = data.get("text") if isinstance(data, dict) else None
            if not isinstance(text, str) or not text.strip() or len(text) > limit:
                raise ValueError
            return text
        except (ValueError, TypeError):
            raise HTTPException(422, f"Введите текст длиной от 1 до {limit} символов")

    @router.get("/status")
    def status():
        return {**status_provider(), "memory": mempalace_bridge.memory_status()}

    @router.get("/tasks")
    def tasks(limit: int = 50):
        return runner.list(limit)

    @router.post("/tasks", status_code=202)
    async def submit(request: Request):
        text = await text_body(request, 24000)
        try:
            return runner.submit(text)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        except RuntimeError as exc:
            raise HTTPException(429, str(exc))

    @router.get("/tasks/{task_id}")
    def task(task_id: str):
        return require_task(task_id)

    @router.post("/tasks/{task_id}/cancel")
    def cancel(task_id: str):
        require_task(task_id)
        return runner.cancel(task_id)

    @router.get("/tasks/{task_id}/events")
    async def events(task_id: str, request: Request):
        require_task(task_id)

        async def generate():
            version = None
            heartbeat = 0
            while not await request.is_disconnected():
                snapshot = runner.get(task_id)
                if snapshot is None:
                    return
                if snapshot["version"] != version:
                    version = snapshot["version"]
                    yield "data: " + json.dumps(snapshot, ensure_ascii=False) + "\n\n"
                if snapshot["status"] in TERMINAL:
                    return
                heartbeat += 1
                if heartbeat % 40 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(generate(), media_type="text/event-stream", headers={
            "Cache-Control": "no-store", "X-Accel-Buffering": "no",
        })

    @router.get("/memory/search")
    def memory_search(q: str = "", limit: int = 5):
        if not q.strip() or len(q) > 512 or not 1 <= limit <= 10:
            raise HTTPException(422, "Укажите запрос до 512 символов и лимит от 1 до 10")
        return mempalace_bridge.search_memory(q, top_k=limit)

    @router.post("/memory/facts", status_code=201)
    async def memory_save(request: Request):
        text = await text_body(request, 10000)
        try:
            return await asyncio.to_thread(mempalace_bridge.save_fact, text)
        except Exception:
            raise HTTPException(503, "Хранилище новых фактов недоступно. Проверьте его настройку.")

    return router
