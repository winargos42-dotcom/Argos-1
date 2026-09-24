"""Vision: камера, локальный анализ, Ollama vision, маршрутизация фраз — без железа."""
import io
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

np = pytest.importorskip("numpy")

from src.vision import argos_vision as V


def _frame(value=128, h=480, w=640):
    return np.full((h, w, 3), value, dtype=np.uint8)


class _FakeCap:
    def __init__(self, frames, opened=True):
        self.frames = list(frames)
        self.opened = opened
        self.released = False

    def isOpened(self):
        return self.opened

    def read(self):
        if self.frames:
            return True, self.frames.pop(0)
        return False, None

    def release(self):
        self.released = True


@pytest.fixture
def vis(monkeypatch):
    monkeypatch.setenv("ARGOS_DISABLE_GEMINI", "true")
    v = V.ArgosVision()
    assert v._client is None  # Gemini отключён — без ключа и без сети
    return v


# ── фразы ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "text,expected",
    [
        ("аргос посмотри в камеру", "describe"),
        ("что ты видишь", "describe"),
        ("аргос, что видишь?", "describe"),
        ("что видит камера", "describe"),
        ("включи камеру", "describe"),
        ("камера статус", "local"),
        ("есть кто перед камерой", "local"),
        ("что ты видишь в этом длинном куске кода на питоне", None),
        ("что ты видишь на экране", None),
        ("какая погода", None),
    ],
)
def test_camera_intent(text, expected):
    assert V.camera_intent(text) == expected


def test_camera_question_strips_triggers():
    assert V.camera_question("Аргос, посмотри в камеру") == ""
    assert V.camera_question("посмотри в камеру какого цвета стена") == "какого цвета стена"


# ── локальный анализ ─────────────────────────────────────────────────────────
@pytest.mark.skipif(not V.CV2_OK, reason="OpenCV не установлен")
def test_brightness_and_motion():
    dark, bright = _frame(10), _frame(220)
    assert V.frame_brightness(dark) < 20 and V.frame_brightness(bright) > 200
    assert V.motion_score(dark, dark) == 0.0
    moved = dark.copy()
    moved[100:300, 100:300] = 250
    assert V.motion_score(dark, moved) > 0.05


@pytest.mark.skipif(not V.CV2_OK, reason="OpenCV не установлен")
def test_grab_frames_warmup_and_release(vis, monkeypatch):
    monkeypatch.setenv("ARGOS_CAMERA_WARMUP", "3")
    cap = _FakeCap([_frame(i) for i in range(10)])
    monkeypatch.setattr(vis, "_open_camera", lambda: cap)
    frames = vis.grab_frames(count=2)
    assert [int(f[0, 0, 0]) for f in frames] == [3, 4]  # 3 кадра прогрева пропущены
    assert cap.released is True


def test_grab_frames_camera_unavailable(vis, monkeypatch):
    if not V.CV2_OK:
        pytest.skip("OpenCV не установлен")
    cap = _FakeCap([], opened=False)
    monkeypatch.setattr(vis, "_open_camera", lambda: cap)
    with pytest.raises(RuntimeError, match="Камера недоступна"):
        vis.grab_frames()
    assert vis.camera_report().startswith("❌ Камера")


@pytest.mark.skipif(not V.CV2_OK, reason="OpenCV не установлен")
def test_camera_local_analysis_with_faces(vis):
    vis._face_detector = SimpleNamespace(method="yunet", detect=lambda f: [(10, 20, 100, 120, 0.93)])
    a, b = _frame(100), _frame(100)
    b[0:240, :] = 255  # половина кадра изменилась
    info = vis.camera_local_analysis([a, b])
    assert (info["width"], info["height"]) == (640, 480)
    assert info["faces"] == 1 and info["face_method"] == "yunet"
    assert info["motion_detected"] is True
    text = V.ArgosVision.format_local_analysis(info)
    assert "640×480" in text and "лиц: 1" in text and "движение: да" in text


def test_face_detector_none_without_models(monkeypatch, tmp_path):
    monkeypatch.setenv("ARGOS_YUNET_MODEL", str(tmp_path / "missing.onnx"))
    det = V.FaceDetector()
    if det.method == "none":
        assert det.detect(_frame()) == []
    else:  # в системе нашёлся Haar — тоже допустимо
        assert det.method == "haar"


# ── Ollama vision ───────────────────────────────────────────────────────────
class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_describe_with_ollama_sends_image_locally(vis, monkeypatch, tmp_path):
    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8fakejpeg")
    monkeypatch.setattr(vis, "_encode_image_b64", lambda p: "QUJD")
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    monkeypatch.setenv("ARGOS_VISION_TIMEOUT", "321")
    seen = {}

    def _urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        seen["body"] = json.loads(req.data.decode())
        return _Resp(json.dumps({"message": {"content": "<think>x</think>Человек за столом."}}).encode())

    monkeypatch.setattr(V.urllib.request, "urlopen", _urlopen)
    out = vis.describe_with_ollama(str(img), "Что ты видишь?")
    assert out == "Человек за столом."
    assert seen["url"] == "http://127.0.0.1:11434/api/chat"
    assert seen["timeout"] == 321.0
    body = seen["body"]
    assert body["model"] == "qwen3.5:latest" and body["stream"] is False and body["think"] is False
    assert body["messages"][0]["images"] == ["QUJD"]
    assert "ollama_s" in vis.last_timings


def test_describe_with_ollama_error_returns_empty(vis, monkeypatch, tmp_path):
    img = tmp_path / "x.jpg"
    img.write_bytes(b"x")
    monkeypatch.setattr(vis, "_encode_image_b64", lambda p: "QQ==")

    def _boom(*a, **k):
        raise V.urllib.error.URLError("connection refused")

    monkeypatch.setattr(V.urllib.request, "urlopen", _boom)
    assert vis.describe_with_ollama(str(img)) == ""


def test_analyze_image_uses_ollama_when_gemini_disabled(vis, monkeypatch, tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"x")
    monkeypatch.setattr(vis, "describe_with_ollama", lambda p, q: "Комната.")
    out = vis.analyze_image(str(img), "Что там?")
    assert "Комната." in out and "qwen3.5" in out


def test_analyze_image_ollama_off_falls_back(vis, monkeypatch, tmp_path):
    monkeypatch.setenv("ARGOS_VISION_OLLAMA", "off")
    called = MagicMock()
    monkeypatch.setattr(vis, "describe_with_ollama", called)
    img = tmp_path / "x.png"
    img.write_bytes(b"not an image")
    out = vis.analyze_image(str(img))
    called.assert_not_called()
    assert out  # PIL-fallback или подсказка по установке


# ── look_through_camera: кадр не сохраняется ────────────────────────────────
@pytest.mark.skipif(not V.CV2_OK, reason="OpenCV не установлен")
def test_look_through_camera_deletes_frame(vis, monkeypatch):
    monkeypatch.delenv("ARGOS_VISION_KEEP", raising=False)
    monkeypatch.setattr(vis, "grab_frames", lambda count=1, interval=0.0, warmup=None: [_frame(90), _frame(90)])
    vis._face_detector = SimpleNamespace(method="none", detect=lambda f: [])
    paths = []

    def _analyze(path, question):
        paths.append(path)
        assert os.path.exists(path)
        return "👁️ описание"

    monkeypatch.setattr(vis, "analyze_image", _analyze)
    out = vis.look_through_camera("что ты видишь")
    assert "📷 Камера: 640×480" in out and "описание" in out
    assert paths and not os.path.exists(paths[0])


# ── маршрутизация ───────────────────────────────────────────────────────────
def test_vision_module_routes_camera_phrases():
    from src.modules.vision_module import VisionModule

    mod = VisionModule()
    vision = SimpleNamespace(
        look_through_camera=MagicMock(return_value="desc"),
        camera_report=MagicMock(return_value="local"),
        look_at_screen=MagicMock(return_value="screen"),
    )
    mod.setup(SimpleNamespace(vision=vision))
    assert mod.can_handle("что ты видишь", "что ты видишь")
    assert mod.handle("Что ты видишь", "что ты видишь") == "desc"
    vision.look_through_camera.assert_called_with("Что ты видишь?")
    assert mod.handle("камера статус", "камера статус") == "local"
    assert mod.handle("посмотри на экран", "посмотри на экран") == "screen"
    assert not mod.can_handle("какая погода", "какая погода")


# ── защита от OOM ───────────────────────────────────────────────────────────
def test_memory_guard_skips_when_ram_is_low(vis, monkeypatch, tmp_path):
    monkeypatch.delenv("ARGOS_VISION_MEM_GUARD", raising=False)
    monkeypatch.setattr(vis, "_ollama_loaded", lambda: False)
    monkeypatch.setattr(vis, "_ollama_model_size_mb", lambda: 6600.0)
    monkeypatch.setattr(V.ArgosVision, "_mem_available_mb", staticmethod(lambda: 5800.0))
    chat = MagicMock()
    monkeypatch.setattr(V.urllib.request, "urlopen", chat)
    img = tmp_path / "x.jpg"
    img.write_bytes(b"x")
    assert vis.describe_with_ollama(str(img)) == ""
    chat.assert_not_called()
    assert "мало RAM" in vis.last_skip_reason


def test_memory_guard_allows_when_model_already_loaded(vis, monkeypatch):
    monkeypatch.setattr(vis, "_ollama_loaded", lambda: True)
    monkeypatch.setattr(V.ArgosVision, "_mem_available_mb", staticmethod(lambda: 100.0))
    assert vis.vision_memory_check() == (True, "")


def test_memory_guard_can_be_disabled(vis, monkeypatch):
    monkeypatch.setenv("ARGOS_VISION_MEM_GUARD", "off")
    monkeypatch.setattr(V.ArgosVision, "_mem_available_mb", staticmethod(lambda: 1.0))
    assert vis.vision_memory_check()[0] is True
