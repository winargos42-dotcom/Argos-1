import pytest

from src.ollama_input_policy import context_limit_message, generate_payload


class Response:
    def __init__(self, status, body):
        self.status_code = status
        self.body = body

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def test_payload_preserves_full_unicode_prompt_and_model():
    prompt = "Аргос 🧠 日本語\n" * 10000
    assert generate_payload("argos-local", prompt) == {
        "model": "argos-local", "prompt": prompt, "stream": False, "truncate": False,
    }


def test_options_are_copied_without_mutating_caller():
    options = {"num_ctx": 4096, "stop": ["STOP"]}
    payload = generate_payload("model", "prompt", options)
    payload["options"]["num_ctx"] = 16
    payload["options"]["stop"].append("END")
    assert options == {"num_ctx": 4096, "stop": ["STOP"]}
    assert generate_payload("model", "prompt", {})["options"] == {}


@pytest.mark.parametrize("error", [
    "request (5000 tokens) exceeds the available context size (4096 tokens), try increasing it",
    "the input length exceeds the context length",
    "the prompt is longer than the context length currently available to the model; shorten the prompt",
    {"message": "prompt exceeds the context window"},
    {"message": "context window exceeded"},
])
def test_context_overflow_returns_constant_private_safe_message(error):
    first = context_limit_message(Response(400, {"error": error}))
    assert first is not None
    assert "не обработан" in first
    assert "истори" in first
    assert "запрос" in first.lower()
    assert first == context_limit_message(Response(400, {
        "error": "PRIVATE SECRET: input length exceeds context length PRIVATE PROMPT"
    }))
    assert "PRIVATE" not in first


@pytest.mark.parametrize("status,body", [
    (401, {"error": "context window exceeded"}),
    (500, {"error": "input length exceeds context length"}),
    (200, {"error": "context window exceeded"}),
    (400, {"error": "invalid context parameter"}),
    (400, {"error": "context window must be positive"}),
    (400, {"error": "context deadline exceeded"}),
    (400, {"error": "input length exceeds maximum batch size"}),
    (400, {"error": {"message": "invalid API key"}}),
    (400, {"message": "context window exceeded"}),
    (400, {"error": None}),
    (400, {"error": ["context window exceeded"]}),
    (400, ["context window exceeded"]),
    (400, ValueError("PRIVATE malformed response")),
])
def test_unrelated_or_malformed_errors_are_not_overflow(status, body):
    assert context_limit_message(Response(status, body)) is None
