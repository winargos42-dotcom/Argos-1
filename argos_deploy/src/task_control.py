import asyncio
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
import json
import threading
from typing import Callable

import httpx

from src.ollama_input_policy import context_limit_message


class TaskCancelled(BaseException):
    pass


@dataclass
class ExecutionControl:
    cancel_event: threading.Event
    on_chunk: Callable[[str], None]


_control: ContextVar[ExecutionControl | None] = ContextVar("argos_execution_control", default=None)
_client_factory = httpx.AsyncClient
_LIMIT = 65536


@contextmanager
def request_scope(control: ExecutionControl):
    token = _control.set(control)
    try:
        yield control
    finally:
        _control.reset(token)


def current_control() -> ExecutionControl | None:
    return _control.get()


def checkpoint():
    control = current_control()
    if control is not None and control.cancel_event.is_set():
        raise TaskCancelled()


def emit_chunk(text):
    checkpoint()
    control = current_control()
    if control is not None:
        control.on_chunk(text)


async def _read_error(response):
    body = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=4096):
        if len(body) + len(chunk) > _LIMIT:
            return None
        body.extend(chunk)
    return context_limit_message(httpx.Response(response.status_code, content=bytes(body)))


async def _generate(url, payload, timeout):
    parts = []
    output_bytes = 0
    pending = bytearray()

    def consume(line):
        nonlocal output_bytes
        if not line.strip():
            return False
        data = json.loads(line)
        if not isinstance(data, dict) or "error" in data:
            raise ValueError()
        text = data.get("response", "")
        if not isinstance(text, str):
            raise ValueError()
        output_bytes += len(text.encode("utf-8"))
        if output_bytes > _LIMIT:
            raise ValueError()
        if text:
            emit_chunk(text)
            parts.append(text)
        return data.get("done") is True

    async with _client_factory(timeout=timeout) as client:
        async with client.stream("POST", url, json={**payload, "stream": True, "truncate": False}) as response:
            if response.status_code != 200:
                overflow = await _read_error(response) if response.status_code == 400 else None
                return overflow or "Ошибка: модель отклонила запрос."
            async for block in response.aiter_bytes():
                checkpoint()
                pending.extend(block)
                while b"\n" in pending:
                    line, _, rest = pending.partition(b"\n")
                    pending = bytearray(rest)
                    if len(line) > _LIMIT:
                        raise ValueError()
                    if consume(line):
                        return "".join(parts) or "Ошибка: модель вернула пустой ответ."
                if len(pending) > _LIMIT:
                    raise ValueError()
            if pending and consume(pending):
                return "".join(parts) or "Ошибка: модель вернула пустой ответ."
            return "Ошибка: ответ модели оборвался до завершения."


async def _cancel_watch(control):
    while not control.cancel_event.is_set():
        await asyncio.sleep(0.1)


async def _run(url, payload, timeout):
    checkpoint()
    work = asyncio.create_task(_generate(url, payload, timeout))
    control = current_control()
    watcher = asyncio.create_task(_cancel_watch(control)) if control is not None else None
    try:
        if watcher is not None:
            await asyncio.wait((work, watcher), return_when=asyncio.FIRST_COMPLETED)
            checkpoint()
        return await work
    finally:
        for task in (work, watcher):
            if task is not None:
                if not task.done():
                    task.cancel()
                with suppress(asyncio.CancelledError, TaskCancelled, Exception):
                    await task


def stream_generate(url, payload, timeout):
    checkpoint()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        return "Ошибка: потоковый запрос нельзя выполнить в текущем режиме."
    try:
        return asyncio.run(_run(url, payload, timeout))
    except Exception:
        return "Ошибка: потоковый ответ модели не получен полностью."
