import asyncio
import json
import threading

import httpx
import pytest

from src import task_control as tasks


class Stream(httpx.AsyncByteStream):
    def __init__(self, blocks, cancel=None, fail=False):
        self.blocks = blocks
        self.cancel = cancel
        self.fail = fail
        self.closed = False
        self.interrupted = False

    async def __aiter__(self):
        for block in self.blocks:
            yield block
        if self.cancel is not None:
            self.cancel.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                self.interrupted = True
                raise
        if self.fail:
            raise httpx.ReadError("PRIVATE BODY")

    async def aclose(self):
        self.closed = True


def install(monkeypatch, stream, status=200):
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(status, stream=stream)
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(tasks, "_client_factory", lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs))
    return captured


def test_scope_restores_parent_and_cancellation_is_not_exception():
    first = tasks.ExecutionControl(threading.Event(), lambda text: None)
    second = tasks.ExecutionControl(threading.Event(), lambda text: None)
    assert tasks.current_control() is None
    with tasks.request_scope(first):
        with tasks.request_scope(second):
            assert tasks.current_control() is second
        assert tasks.current_control() is first
        first.cancel_event.set()
        with pytest.raises(tasks.TaskCancelled):
            tasks.checkpoint()
    assert tasks.current_control() is None
    assert not issubclass(tasks.TaskCancelled, Exception)


def test_emit_checks_cancellation_before_callback():
    chunks = []
    control = tasks.ExecutionControl(threading.Event(), chunks.append)
    with tasks.request_scope(control):
        tasks.emit_chunk("one")
        control.cancel_event.set()
        with pytest.raises(tasks.TaskCancelled):
            tasks.emit_chunk("two")
    assert chunks == ["one"]


def test_success_streams_unicode_and_preserves_input(monkeypatch):
    body = (json.dumps({"response": "Привет", "done": False}, ensure_ascii=False) + "\n" +
            json.dumps({"response": "!", "done": True}) + "\n").encode()
    stream = Stream([body[:17], body[17:]])
    captured = install(monkeypatch, stream)
    payload = {"model": "local", "prompt": "full prompt", "stream": False, "truncate": True}
    chunks = []
    with tasks.request_scope(tasks.ExecutionControl(threading.Event(), chunks.append)):
        assert tasks.stream_generate("http://test/api/generate", payload, 5) == "Привет!"
    assert chunks == ["Привет", "!"]
    assert payload["stream"] is False and payload["truncate"] is True
    assert captured[0] == {**payload, "stream": True, "truncate": False}
    assert stream.closed


def test_cancel_interrupts_and_closes_blocked_producer(monkeypatch):
    cancel = threading.Event()
    stream = Stream([b'{"response":"partial","done":false}\n'], cancel=cancel)
    install(monkeypatch, stream)
    chunks = []
    with tasks.request_scope(tasks.ExecutionControl(cancel, chunks.append)):
        with pytest.raises(tasks.TaskCancelled):
            tasks.stream_generate("http://test/api/generate", {}, 5)
    assert chunks == ["partial"]
    assert stream.interrupted
    assert stream.closed


@pytest.mark.parametrize("body", [
    b'{"response":"partial","done":false}\n',
    b'invalid PRIVATE JSON\n',
    b'{"error":"PRIVATE failure"}\n',
    b'[]\n',
    b'{"response":42,"done":true}\n',
    b'x' * 65537,
])
def test_failures_never_report_partial_success_or_private_errors(monkeypatch, body):
    stream = Stream([body])
    install(monkeypatch, stream)
    answer = tasks.stream_generate("http://test/api/generate", {}, 5)
    assert answer.startswith("Ошибка:")
    assert "PRIVATE" not in answer
    assert answer != "partial"
    assert stream.closed


def test_http_context_overflow_uses_safe_message(monkeypatch):
    stream = Stream([b'{"error":"input length exceeds context length PRIVATE"}'])
    install(monkeypatch, stream, status=400)
    answer = tasks.stream_generate("http://test/api/generate", {}, 5)
    assert "не обработан" in answer
    assert "PRIVATE" not in answer
    assert stream.closed


def test_transport_failure_is_not_success(monkeypatch):
    stream = Stream([b'{"response":"partial","done":false}\n'], fail=True)
    install(monkeypatch, stream)
    answer = tasks.stream_generate("http://test/api/generate", {}, 5)
    assert answer.startswith("Ошибка:") and "PRIVATE" not in answer
    assert stream.closed


def test_aggregate_output_limit(monkeypatch):
    stream = Stream([
        (json.dumps({"response": "x" * 40000, "done": False}) + "\n").encode(),
        (json.dumps({"response": "y" * 40000, "done": True}) + "\n").encode(),
    ])
    install(monkeypatch, stream)
    chunks = []
    with tasks.request_scope(tasks.ExecutionControl(threading.Event(), chunks.append)):
        answer = tasks.stream_generate("http://test/api/generate", {}, 5)
    assert answer.startswith("Ошибка:")
    assert sum(len(chunk) for chunk in chunks) <= 65536
    assert stream.closed


def test_pre_cancelled_request_never_calls_client(monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("Cancelled request must not start HTTP work")
    monkeypatch.setattr(tasks, "_client_factory", forbidden)
    cancel = threading.Event()
    cancel.set()
    with tasks.request_scope(tasks.ExecutionControl(cancel, lambda text: None)):
        with pytest.raises(tasks.TaskCancelled):
            tasks.stream_generate("http://test/api/generate", {}, 5)


def test_utf8_json_split_inside_codepoint(monkeypatch):
    body = (json.dumps({"response": "Я", "done": True}, ensure_ascii=False) + "\n").encode()
    offset = body.index("Я".encode()) + 1
    stream = Stream([body[:offset], body[offset:]])
    install(monkeypatch, stream)
    assert tasks.stream_generate("http://test/api/generate", {}, 5) == "Я"
