"""src/vision/interactive.py — локальное одноразовое зрение (развёрнуто Codex 2026-09-23).

Без камеры, экрана и Ollama: захват и анализатор подменяются.
"""
import io

import pytest

from src.vision import interactive
from src.vision.interactive import ArgosVision, LocalAnalyzer, MAX_IMAGE


class FakeAnalyzer:
    def __init__(self, available=True, answer="кот на столе"):
        self.available, self.answer, self.calls = available, answer, []

    def ensure_available(self):
        if not self.available:
            raise RuntimeError("vision_model_unavailable")

    def analyze(self, image, question):
        self.calls.append((image, question))
        return self.answer


@pytest.mark.parametrize("host", [
    "http://192.168.1.10:11434", "https://127.0.0.1:11434", "http://user:pw@127.0.0.1:11434",
    "http://127.0.0.1:11434/proxy", "http://example.com",
])
def test_analyzer_refuses_non_loopback_or_unusual_ollama(host):
    with pytest.raises(ValueError, match="vision_requires_loopback_ollama"):
        LocalAnalyzer(host=host)


def test_analyzer_accepts_localhost_and_normalises():
    analyzer = LocalAnalyzer(host="http://localhost:11500", model="m")
    assert (analyzer.host, analyzer.port, analyzer.model) == ("127.0.0.1", 11500, "m")


def test_analyzer_rejects_empty_or_oversized_image():
    analyzer = LocalAnalyzer(host="http://127.0.0.1:11434", model="m")
    with pytest.raises(ValueError):
        analyzer.analyze(b"", "q")
    with pytest.raises(ValueError):
        analyzer.analyze(b"x" * (MAX_IMAGE + 1), "q")


def test_camera_frame_goes_to_local_analyzer(monkeypatch):
    captured = []
    monkeypatch.setattr(ArgosVision, "_capture_worker",
                        staticmethod(lambda kind, idx=0: captured.append((kind, idx)) or b"jpeg"))
    analyzer = FakeAnalyzer()
    result = ArgosVision(analyzer=analyzer).look_through_camera("что видно?", camera_index=2)
    assert result.startswith("👁️ VISION АНАЛИЗ:") and "кот на столе" in result
    assert captured == [("camera", 2)]
    assert analyzer.calls == [(b"jpeg", "что видно?")]


@pytest.mark.parametrize("index", [-1, 64, "0", 1.0])
def test_invalid_camera_index_never_captures(monkeypatch, index):
    monkeypatch.setattr(ArgosVision, "_capture_worker",
                        staticmethod(lambda *a: pytest.fail("capture must not run")))
    result = ArgosVision(analyzer=FakeAnalyzer()).look_through_camera(camera_index=index)
    assert result == "❌ Локальное зрение недоступно: ValueError"


def test_missing_model_reported_without_capture(monkeypatch):
    monkeypatch.setattr(ArgosVision, "_capture_worker",
                        staticmethod(lambda *a: pytest.fail("capture must not run")))
    result = ArgosVision(analyzer=FakeAnalyzer(available=False)).look_at_screen()
    assert result == "❌ Локальное зрение недоступно: vision_model_unavailable"


def test_errors_do_not_leak_details(monkeypatch):
    def boom(*a):
        raise RuntimeError("/secret/path GEMINI_API_KEY=abc")
    monkeypatch.setattr(ArgosVision, "_capture_worker", staticmethod(boom))
    result = ArgosVision(analyzer=FakeAnalyzer()).look_at_screen()
    assert "secret" not in result and "GEMINI" not in result


def test_analyze_image_reads_bounded_file(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    path = tmp_path / "pic.png"
    Image.new("RGB", (2000, 50), "red").save(path)
    analyzer = FakeAnalyzer()
    assert "кот" in ArgosVision(analyzer=analyzer).analyze_file(str(path))
    jpeg = analyzer.calls[0][0]
    with Image.open(io.BytesIO(jpeg)) as img:
        assert img.format == "JPEG" and max(img.size) <= 1024


def test_analyze_image_missing_file(tmp_path):
    result = ArgosVision(analyzer=FakeAnalyzer()).analyze_image(str(tmp_path / "none.png"))
    assert result == "❌ Локальное зрение недоступно: ValueError"


def test_capture_worker_rejects_empty_output(monkeypatch):
    class Done:
        stdout = b""
    monkeypatch.setattr(interactive.subprocess, "run", lambda *a, **k: Done())
    with pytest.raises(ValueError, match="invalid_capture_size"):
        ArgosVision._capture_worker("screen")
