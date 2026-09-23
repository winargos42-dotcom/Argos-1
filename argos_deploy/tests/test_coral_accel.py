import io
import threading
from http.server import ThreadingHTTPServer

import numpy as np
import pytest
from PIL import Image

from src.skills import coral as coral_skill
from src.vision import coral_accel_server as srv
from src.vision import coral_protocol as proto
from src.vision.coral_client import CoralClient, CoralUnavailable

KEY = b"k" * 40


def jpeg(size=(64, 48)):
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buf, "JPEG")
    return buf.getvalue()


# ── протокол ─────────────────────────────────────────────
def test_signed_request_verifies_once():
    headers = proto.sign_request(KEY, "POST", "/v1/detect?model=objects", b"abc")
    verifier = proto.Verifier(KEY)
    assert verifier.verify("POST", "/v1/detect?model=objects", headers, b"abc") == headers[proto.H_NONCE]
    with pytest.raises(ValueError, match="Повтор"):
        verifier.verify("POST", "/v1/detect?model=objects", headers, b"abc")


@pytest.mark.parametrize("method,target,body,key", [
    ("POST", "/v1/detect?model=faces", b"abc", KEY),   # подменили query
    ("POST", "/v1/detect?model=objects", b"abd", KEY),  # подменили тело
    ("POST", "/v1/detect?model=objects", b"abc", b"x" * 40),  # чужой ключ
])
def test_tampered_request_rejected(method, target, body, key):
    headers = proto.sign_request(KEY, "POST", "/v1/detect?model=objects", b"abc")
    with pytest.raises(ValueError, match="подпись"):
        proto.Verifier(key).verify(method, target, headers, body)


def test_stale_request_rejected():
    headers = proto.sign_request(KEY, "GET", "/v1/status", clock=lambda: 1000.0)
    with pytest.raises(ValueError, match="устарел"):
        proto.Verifier(KEY, clock=lambda: 1100.0).verify("GET", "/v1/status", headers, b"")


def test_weak_keys_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("ARGOS_CORAL_SECRET", "change_me")
    monkeypatch.delenv("ARGOS_CORAL_SECRET_FILE", raising=False)
    with pytest.raises(ValueError):
        proto.load_key()
    path = tmp_path / "coral.key"
    path.write_bytes(b"z" * 48)
    path.chmod(0o644)
    with pytest.raises(ValueError, match="закрытым"):
        proto.load_key(str(path))
    path.chmod(0o600)
    assert proto.load_key(str(path)) == b"z" * 48


def test_response_signature():
    mac = proto.sign_response(KEY, "a" * 32, 200, b"{}")
    assert proto.verify_response(KEY, "a" * 32, 200, b"{}", mac)
    assert not proto.verify_response(KEY, "a" * 32, 500, b"{}", mac)
    assert not proto.verify_response(KEY, "a" * 32, 200, b"{}", None)


# ── разбор выходов SSD ───────────────────────────────────
BOXES = np.array([[[0.1, 0.2, 0.5, 0.6], [0.0, 0.0, 1.0, 1.0], [0, 0, 0, 0]]], dtype=np.float32)
CLASSES = np.array([[0.0, 16.0, 0.0]], dtype=np.float32)
SCORES = np.array([[0.91, 0.55, 0.0]], dtype=np.float32)
COUNT = np.array([2.0], dtype=np.float32)


@pytest.mark.parametrize("order", [
    (BOXES, CLASSES, SCORES, COUNT),   # TF1-порядок
    (SCORES, BOXES, COUNT, CLASSES),   # TF2-порядок (StatefulPartitionedCall)
])
def test_split_ssd_outputs_any_order(order):
    boxes, classes, scores = srv.split_ssd_outputs(list(order))
    assert boxes.shape == (2, 4)
    assert list(classes) == [0.0, 16.0]
    assert list(np.round(scores, 2)) == [0.91, 0.55]


def test_read_labels(tmp_path):
    path = tmp_path / "labels.txt"
    path.write_text("0  person\n16  dog\n\n", encoding="utf-8")
    assert srv.read_labels(str(path)) == {0: "person", 16: "dog"}
    assert srv.read_labels(None) == {}


# ── детектор с поддельным интерпретатором ────────────────
class FakeInterp:
    def __init__(self, *_):
        self.tensor = None

    def get_input_details(self):
        return [{"index": 0, "shape": np.array([1, 300, 300, 3]), "dtype": np.uint8}]

    def get_output_details(self):
        return [{"index": i} for i in range(1, 5)]

    def set_tensor(self, index, tensor):
        assert tensor.shape == (1, 300, 300, 3) and tensor.dtype == np.uint8
        self.tensor = tensor

    def invoke(self):
        pass

    def get_tensor(self, index):
        return {1: BOXES, 2: CLASSES, 3: SCORES, 4: COUNT}[index]


def make_detector(name="objects"):
    return srv.Detector(name, "fake.tflite", {0: "person", 16: "dog"}, True, lambda *_: FakeInterp())


def test_detector_maps_boxes_to_pixels():
    result = make_detector().detect(Image.new("RGB", (640, 480)), threshold=0.6)
    assert result["objects"] == [{"label": "person", "class_id": 0, "score": 0.91, "box": [128, 48, 384, 240]}]
    assert result["tpu"] is True and result["image"] == [640, 480]
    assert make_detector().stats()["calls"] == 0


# ── сервис: доступ, подпись, ошибки ──────────────────────
@pytest.fixture
def service():
    return srv.AccelService(KEY, {"objects": make_detector()})


def test_health_is_public_but_lan_only(service):
    assert service.handle("192.168.1.240", "GET", "/v1/health", {}, b"")[0] == 200
    assert service.handle("8.8.8.8", "GET", "/v1/health", {}, b"")[0] == 403


def test_allowlist(service):
    service.allow = {"192.168.1.240"}
    assert service.handle("192.168.1.50", "GET", "/v1/health", {}, b"")[0] == 403


def test_unsigned_detect_rejected(service):
    assert service.handle("127.0.0.1", "POST", "/v1/detect", {}, jpeg())[0] == 401


def test_signed_detect_and_errors(service):
    def call(target, body):
        return service.handle("127.0.0.1", "POST", target, proto.sign_request(KEY, "POST", target, body), body)

    status, payload, nonce = call("/v1/detect?model=objects&threshold=0.5", jpeg())
    assert status == 200 and nonce and payload["objects"][0]["label"] == "person"
    assert call("/v1/detect?model=faces", jpeg())[0] == 404
    assert call("/v1/detect", b"not an image")[0] == 400


def test_bind_parsing():
    assert srv.parse_bind("192.168.1.93:8770") == ("192.168.1.93", 8770)
    assert srv.parse_bind("8770") == ("127.0.0.1", 8770)


# ── клиент ↔ сервер по настоящему HTTP ───────────────────
@pytest.fixture
def live_server(service):
    server = ThreadingHTTPServer(("127.0.0.1", 0), srv.make_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_client_roundtrip(live_server):
    client = CoralClient(live_server, KEY)
    assert client.health()
    result = client.detect(jpeg(), threshold=0.5)
    assert [o["label"] for o in result["objects"]] == ["person", "dog"]
    assert client.status()["models"]["objects"]["calls"] == 1


def test_client_rejects_wrong_key(live_server):
    with pytest.raises(CoralUnavailable, match="подпис"):
        CoralClient(live_server, b"w" * 40).status()


def test_client_unreachable():
    client = CoralClient("http://127.0.0.1:9", KEY, timeout=0.5)
    assert client.health() is False
    with pytest.raises(CoralUnavailable, match="недоступен"):
        client.status()


# ── навык ────────────────────────────────────────────────
def test_skill_ignores_other_text():
    assert coral_skill.handle("status") is None
    assert coral_skill.handle("что ты видишь") is None


def test_skill_not_configured(monkeypatch):
    monkeypatch.delenv("ARGOS_CORAL_URL", raising=False)
    assert "не настроен" in coral_skill.handle("корал статус")


def test_skill_look(monkeypatch, live_server):
    monkeypatch.setenv("ARGOS_CORAL_URL", live_server)
    monkeypatch.setenv("ARGOS_CORAL_SECRET", KEY.decode())
    monkeypatch.delenv("ARGOS_CORAL_SECRET_FILE", raising=False)
    monkeypatch.setattr(coral_skill, "_frame_jpeg", lambda core=None: jpeg())
    answer = coral_skill.handle("Аргос, что видит корал?")
    assert answer.startswith("👁 Coral видит: человек") and "TPU" in answer
    assert "Узел Coral работает" in coral_skill.handle("статус корала")


def test_summarize_counts():
    result = {"tpu": True, "inference_ms": 7.1, "objects": [
        {"label": "person", "score": 0.9}, {"label": "person", "score": 0.7}, {"label": "cat", "score": 0.6}]}
    assert coral_skill.summarize(result) == "👁 Coral видит: человек ×2, кошка (уверенность до 90%, 7.1 мс на TPU)."


# ── отчёт камеры ArgosVision с Coral ─────────────────────
def test_camera_analysis_uses_coral(monkeypatch, live_server):
    from src.vision import argos_vision as V

    if not V.CV2_OK:
        pytest.skip("нет OpenCV")
    monkeypatch.setenv("ARGOS_DISABLE_GEMINI", "true")
    monkeypatch.setenv("ARGOS_CORAL_URL", live_server)
    monkeypatch.setenv("ARGOS_CORAL_SECRET", KEY.decode())
    monkeypatch.delenv("ARGOS_CORAL_SECRET_FILE", raising=False)
    frame = np.full((480, 640, 3), 120, dtype=np.uint8)
    info = V.ArgosVision().camera_local_analysis([frame, frame])
    assert [o["label"] for o in info["coral"]["objects"]] == ["person", "dog"]
    assert "\n👁 Coral видит: человек, собака" in V.ArgosVision.format_local_analysis(info)


def test_camera_analysis_survives_coral_outage(monkeypatch):
    from src.vision import argos_vision as V

    if not V.CV2_OK:
        pytest.skip("нет OpenCV")
    monkeypatch.setenv("ARGOS_DISABLE_GEMINI", "true")
    monkeypatch.setenv("ARGOS_CORAL_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ARGOS_CORAL_SECRET", KEY.decode())
    monkeypatch.delenv("ARGOS_CORAL_SECRET_FILE", raising=False)
    frame = np.full((480, 640, 3), 120, dtype=np.uint8)
    info = V.ArgosVision().camera_local_analysis([frame])
    assert "недоступен" in info["coral"]["error"]
    report = V.ArgosVision.format_local_analysis(info)
    assert report.startswith("📷 Камера: 640×480") and "Coral недоступен" in report


def test_camera_analysis_without_coral(monkeypatch):
    from src.vision import argos_vision as V

    monkeypatch.delenv("ARGOS_CORAL_URL", raising=False)
    assert V.ArgosVision.coral_detect(np.zeros((4, 4, 3), dtype=np.uint8)) is None
