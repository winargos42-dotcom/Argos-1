import pytest

from src.skills import ha_camera_vision as cv

CAMS = [
    {"entity_id": "camera.dvor", "name": "Двор", "state": "streaming"},
    {"entity_id": "camera.prihozhaya", "name": "Прихожая", "state": "idle"},
]


def test_ignores_non_camera_text():
    assert cv.handle("что видит корал") is None
    assert cv.handle("статус дома") is None


def test_list_empty(monkeypatch):
    monkeypatch.setattr(cv, "list_cameras", lambda: [])
    out = cv.handle("камеры дома")
    assert "нет камер" in out.lower()


def test_list_cameras(monkeypatch):
    monkeypatch.setattr(cv, "list_cameras", lambda: CAMS)
    out = cv.handle("какие камеры дома")
    assert "Двор" in out and "Прихожая" in out and "(2)" in out


def test_pick_camera_by_name():
    assert cv.pick_camera(CAMS, "что на камере двор")["entity_id"] == "camera.dvor"
    assert cv.pick_camera(CAMS, "лица на камере прихожая")["entity_id"] == "camera.prihozhaya"
    assert cv.pick_camera(CAMS, "что на камере") is None  # неоднозначно при нескольких
    assert cv.pick_camera([CAMS[0]], "что на камере")["entity_id"] == "camera.dvor"  # одна — без имени


def test_look_detects_objects(monkeypatch):
    monkeypatch.setattr(cv, "list_cameras", lambda: [CAMS[0]])
    monkeypatch.setattr(cv, "snapshot", lambda e: b"jpegbytes")

    class FakeClient:
        def detect(self, img, model="objects", threshold=0.4, top_k=20):
            assert img == b"jpegbytes"
            return {"tpu": True, "inference_ms": 8.0,
                    "objects": [{"label": "person", "score": 0.8}]}

    monkeypatch.setattr(cv, "_coral", lambda: FakeClient())
    out = cv.handle("что на камере двор")
    assert out.startswith("Двор —") and "человек" in out


def test_faces_and_classify(monkeypatch):
    monkeypatch.setattr(cv, "list_cameras", lambda: CAMS)
    monkeypatch.setattr(cv, "snapshot", lambda e: b"x")

    class FakeClient:
        def detect(self, img, model="objects", threshold=0.4, top_k=20):
            return {"tpu": True, "inference_ms": 5.0, "objects": [{"label": "face", "score": 0.9}]}

        def classify(self, img, model="classify", threshold=0.1, top_k=5):
            return {"tpu": True, "inference_ms": 3.0, "labels": [{"label": "macaw", "score": 0.99}]}

    monkeypatch.setattr(cv, "_coral", lambda: FakeClient())
    assert "лиц в кадре — 1" in cv.handle("лица на камере прихожая")
    assert "попугай" in cv.handle("что за предмет на камере двор")


def test_ambiguous_camera_asks(monkeypatch):
    monkeypatch.setattr(cv, "list_cameras", lambda: CAMS)
    out = cv.handle("что на камере")
    assert "Какая камера" in out and "Двор" in out


def test_no_cameras_on_look(monkeypatch):
    monkeypatch.setattr(cv, "list_cameras", lambda: [])
    assert "нет камер" in cv.handle("что на камере двор").lower()


def test_snapshot_error_is_reported(monkeypatch):
    monkeypatch.setattr(cv, "list_cameras", lambda: [CAMS[0]])

    def boom(e):
        raise RuntimeError("камера не дала снимок (HTTP 500)")

    monkeypatch.setattr(cv, "snapshot", boom)
    out = cv.handle("что на камере двор")
    assert out.startswith("❌ Камера HA") and "500" in out
