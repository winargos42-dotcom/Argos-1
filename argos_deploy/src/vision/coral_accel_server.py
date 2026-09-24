"""
coral_accel_server.py — сервис argos-vision-accel на узле argos-coral
═══════════════════════════════════════════════════════
Детекция объектов и лиц на Coral Edge TPU (PCIe, /dev/apex_0) по кадрам от ARGOS.

  GET  /v1/health                       — {"ok": true} без подписи (для мониторинга)
  GET  /v1/status                       — модели, устройство, задержки (с подписью)
  POST /v1/detect?model=objects&threshold=0.4&top_k=20  тело: JPEG/PNG (с подписью)

Подпись и защита от повтора — coral_protocol.py. Кадры обрабатываются в памяти
и не сохраняются. Принимаются клиенты только из частных сетей (или ARGOS_CORAL_ALLOW).

Окружение:
  ARGOS_CORAL_BIND=192.168.1.93:8770   (по умолчанию 127.0.0.1:8770)
  ARGOS_CORAL_SECRET_FILE=/etc/argos/coral.key
  ARGOS_CORAL_MODELS_DIR=/var/lib/argos-vision/models
  ARGOS_CORAL_ALLOW=192.168.1.240,127.0.0.1   (необязательно: точный список клиентов)
  ARGOS_CORAL_CPU=1                     (без TPU — только для проверки на CPU, медленно)

Запуск: python3 coral_accel_server.py [--benchmark N] [--model objects]
═══════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import io
import ipaddress
import json
import logging
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

try:
    from src.vision import coral_protocol as proto
except ImportError:  # на узле файлы лежат рядом, без пакета src
    import coral_protocol as proto

log = logging.getLogger("argos.coral")

MAX_BODY = 4 * 1024 * 1024
MAX_PIXELS = 4096 * 4096
DEFAULT_MODELS_DIR = "/var/lib/argos-vision/models"
MODELS = {
    # имя: (модель Edge TPU, модель CPU, файл меток или None)
    "objects": ("ssd_mobilenet_v2_coco_quant_postprocess_edgetpu.tflite",
                "ssd_mobilenet_v2_coco_quant_postprocess.tflite", "coco_labels.txt"),
    "faces": ("ssd_mobilenet_v2_face_quant_postprocess_edgetpu.tflite",
              "ssd_mobilenet_v2_face_quant_postprocess.tflite", None),
}
# Классификаторы (один выходной тензор вероятностей): MobileNet v2 ImageNet (1001 класс)
CLASSIFY_MODELS = {
    "classify": ("mobilenet_v2_1.0_224_quant_edgetpu.tflite",
                 "mobilenet_v2_1.0_224_quant.tflite", "imagenet_labels.txt"),
}


def read_labels(path: str | None) -> dict[int, str]:
    """Формат google-coral/test_data: «0  person» по строке; строки без номера нумеруются по порядку."""
    if not path or not os.path.isfile(path):
        return {}
    labels = {}
    with open(path, encoding="utf-8") as fh:
        for index, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            match = re.match(r"^(\d+)\s+(.+)$", line)
            labels[int(match.group(1)) if match else index] = (match.group(2) if match else line).strip()
    return labels


def _interpreter(model_path: str, edgetpu: bool):
    """tflite_runtime (или ai_edge_litert / tensorflow.lite) с делегатом Edge TPU."""
    try:
        from tflite_runtime.interpreter import Interpreter, load_delegate
    except ImportError:
        try:
            from ai_edge_litert.interpreter import Interpreter, load_delegate
        except ImportError:
            from tensorflow.lite import Interpreter  # type: ignore
            from tensorflow.lite.experimental import load_delegate  # type: ignore
    delegates = [load_delegate("libedgetpu.so.1")] if edgetpu else []
    interp = Interpreter(model_path=model_path, experimental_delegates=delegates)
    interp.allocate_tensors()
    return interp


def split_ssd_outputs(outputs: list):
    """Выходы SSD postprocess: boxes [1,N,4], classes [1,N], scores [1,N], count [1].

    Порядок выходов отличается между версиями TFLite, поэтому различаем по форме:
    boxes — последняя размерность 4, count — один элемент, а из двух [1,N]
    classes — целые числа, scores — дробные в [0,1] и не возрастают (после NMS).
    """
    import numpy as np

    arrays = [np.asarray(o) for o in outputs]
    boxes = next((a for a in arrays if a.ndim == 3 and a.shape[-1] == 4), None)
    count = next((a for a in arrays if a.size == 1), None)
    pair = [a for a in arrays if a is not boxes and a is not count]
    if boxes is None or count is None or len(pair) != 2:
        raise ValueError("Модель не похожа на SSD postprocess (ожидалось 4 выхода)")
    n = max(0, min(int(np.asarray(count).reshape(-1)[0]), pair[0].reshape(-1).size))
    a, b = (p.reshape(-1)[:n] for p in pair)

    def looks_like_scores(v):
        return v.size == 0 or (np.all((v >= 0) & (v <= 1)) and np.all(np.diff(v) <= 1e-6)
                               and not np.all(np.mod(v, 1) == 0))

    if looks_like_scores(b) and not looks_like_scores(a):
        classes, scores = a, b
    elif looks_like_scores(a) and not looks_like_scores(b):
        classes, scores = b, a
    else:  # неоднозначно (например, пустой результат) — классический порядок
        classes, scores = a, b
    return boxes.reshape(-1, 4)[:n], classes, scores


class Detector:
    """Одна модель SSD. Вызовы сериализуются: у TPU одна очередь."""

    def __init__(self, name: str, model_path: str, labels: dict[int, str], edgetpu: bool = True,
                 interpreter_factory=_interpreter):
        self.name, self.model_path, self.labels, self.edgetpu = name, model_path, labels, edgetpu
        self.interp = interpreter_factory(model_path, edgetpu)
        detail = self.interp.get_input_details()[0]
        self.input_index = detail["index"]
        _, self.height, self.width, _ = detail["shape"]
        self.input_dtype = detail["dtype"]
        self.lock = threading.Lock()
        self.calls, self.total_ms = 0, 0.0

    def detect(self, image, threshold: float = 0.4, top_k: int = 20) -> dict:
        import numpy as np

        image = image.convert("RGB")
        width, height = image.size
        resized = image.resize((int(self.width), int(self.height)))
        tensor = np.expand_dims(np.asarray(resized, dtype=self.input_dtype), 0)
        with self.lock:
            started = time.perf_counter()
            self.interp.set_tensor(self.input_index, tensor)
            self.interp.invoke()
            outputs = [self.interp.get_tensor(d["index"]) for d in self.interp.get_output_details()]
            elapsed = (time.perf_counter() - started) * 1000
            self.calls += 1
            self.total_ms += elapsed
        boxes, classes, scores = split_ssd_outputs(outputs)
        found = []
        for box, cls, score in zip(boxes, classes, scores):
            if float(score) < threshold:
                continue
            ymin, xmin, ymax, xmax = (float(min(max(v, 0.0), 1.0)) for v in box)
            cid = int(cls)
            found.append({
                "label": self.labels.get(cid, "face" if self.name == "faces" else str(cid)),
                "class_id": cid, "score": round(float(score), 3),
                "box": [round(xmin * width), round(ymin * height), round(xmax * width), round(ymax * height)],
            })
            if len(found) >= top_k:
                break
        return {"model": self.name, "tpu": self.edgetpu, "inference_ms": round(elapsed, 2),
                "image": [width, height], "objects": found}

    def stats(self) -> dict:
        return {"calls": self.calls, "avg_ms": round(self.total_ms / self.calls, 2) if self.calls else None,
                "input": [int(self.width), int(self.height)], "tpu": self.edgetpu}


class Classifier:
    """Классификатор с одним выходным тензором вероятностей (MobileNet ImageNet)."""

    def __init__(self, name: str, model_path: str, labels: dict[int, str], edgetpu: bool = True,
                 interpreter_factory=_interpreter):
        self.name, self.model_path, self.labels, self.edgetpu = name, model_path, labels, edgetpu
        self.interp = interpreter_factory(model_path, edgetpu)
        detail = self.interp.get_input_details()[0]
        self.input_index = detail["index"]
        _, self.height, self.width, _ = detail["shape"]
        self.input_dtype = detail["dtype"]
        out = self.interp.get_output_details()[0]
        self.output_index = out["index"]
        self.out_scale, self.out_zero = out.get("quantization", (0.0, 0))[:2]
        self.lock = threading.Lock()
        self.calls, self.total_ms = 0, 0.0

    def classify(self, image, top_k: int = 5, threshold: float = 0.1) -> dict:
        import numpy as np

        resized = image.convert("RGB").resize((int(self.width), int(self.height)))
        tensor = np.expand_dims(np.asarray(resized, dtype=self.input_dtype), 0)
        with self.lock:
            started = time.perf_counter()
            self.interp.set_tensor(self.input_index, tensor)
            self.interp.invoke()
            scores = np.asarray(self.interp.get_tensor(self.output_index)).reshape(-1).astype(np.float32)
            elapsed = (time.perf_counter() - started) * 1000
            self.calls += 1
            self.total_ms += elapsed
        if self.out_scale:  # деквантизация uint8 → вероятность
            scores = (scores - self.out_zero) * self.out_scale
        order = scores.argsort()[::-1][:max(1, top_k)]
        labels = [{"label": self.labels.get(int(i), str(int(i))), "score": round(float(scores[i]), 3)}
                  for i in order if float(scores[i]) >= threshold]
        return {"model": self.name, "tpu": self.edgetpu, "inference_ms": round(elapsed, 2), "labels": labels}

    def stats(self) -> dict:
        return {"calls": self.calls, "avg_ms": round(self.total_ms / self.calls, 2) if self.calls else None,
                "input": [int(self.width), int(self.height)], "tpu": self.edgetpu, "kind": "classify"}


def load_detectors(models_dir: str, edgetpu: bool, factory=_interpreter) -> dict:
    detectors = {}
    for name, (tpu_file, cpu_file, labels_file) in MODELS.items():
        path = os.path.join(models_dir, tpu_file if edgetpu else cpu_file)
        if not os.path.isfile(path):
            log.warning("Модель %s не найдена: %s", name, path)
            continue
        labels = read_labels(os.path.join(models_dir, labels_file) if labels_file else None)
        detectors[name] = Detector(name, path, labels, edgetpu, factory)
        log.info("Модель %s загружена (%s)", name, "Edge TPU" if edgetpu else "CPU")
    for name, (tpu_file, cpu_file, labels_file) in CLASSIFY_MODELS.items():
        path = os.path.join(models_dir, tpu_file if edgetpu else cpu_file)
        if not os.path.isfile(path):
            log.warning("Модель %s не найдена: %s", name, path)
            continue
        labels = read_labels(os.path.join(models_dir, labels_file) if labels_file else None)
        detectors[name] = Classifier(name, path, labels, edgetpu, factory)
        log.info("Классификатор %s загружен (%s)", name, "Edge TPU" if edgetpu else "CPU")
    return detectors


def client_allowed(host: str, allow: set[str]) -> bool:
    if allow:
        return host in allow
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback


def decode_image(body: bytes):
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    image = Image.open(io.BytesIO(body))
    if image.format not in ("JPEG", "PNG"):
        raise ValueError("Нужен JPEG или PNG")
    image.load()
    return image


class AccelService:
    """Логика запросов отдельно от HTTP — чтобы проверять её в тестах."""

    def __init__(self, key: bytes, detectors: dict[str, Detector], allow: set[str] | None = None,
                 clock=time.time):
        self.key, self.detectors, self.allow = key, detectors, allow or set()
        self.verifier = proto.Verifier(key, clock=clock)
        self.started = clock()
        self.clock = clock

    def handle(self, client: str, method: str, target: str, headers, body: bytes):
        """Возвращает (status, dict, nonce|None)."""
        if not client_allowed(client, self.allow):
            return 403, {"error": "forbidden"}, None
        path = urlsplit(target).path
        if method == "GET" and path == "/v1/health":
            return 200, {"ok": True, "service": "argos-vision-accel"}, None
        try:
            nonce = self.verifier.verify(method, target, headers, body)
        except ValueError as e:
            return 401, {"error": str(e)}, None
        if method == "GET" and path == "/v1/status":
            return 200, {"ok": True, "uptime_s": round(self.clock() - self.started),
                         "models": {n: d.stats() for n, d in self.detectors.items()}}, nonce
        if method == "POST" and path == "/v1/detect":
            query = parse_qs(urlsplit(target).query)
            name = (query.get("model") or ["objects"])[0]
            detector = self.detectors.get(name)
            if detector is None:
                return 404, {"error": f"модель {name} не загружена"}, nonce
            try:
                threshold = min(max(float((query.get("threshold") or ["0.4"])[0]), 0.05), 0.99)
                top_k = min(max(int((query.get("top_k") or ["20"])[0]), 1), 100)
                image = decode_image(body)
            except Exception as e:
                return 400, {"error": f"плохой кадр или параметры: {e}"}, nonce
            if not isinstance(detector, Detector):
                return 404, {"error": f"{name} — не детектор (используй /v1/classify)"}, nonce
            try:
                return 200, detector.detect(image, threshold, top_k), nonce
            except Exception as e:
                log.exception("Ошибка детекции")
                return 500, {"error": f"ошибка детекции: {e}"}, nonce
        if method == "POST" and path == "/v1/classify":
            query = parse_qs(urlsplit(target).query)
            name = (query.get("model") or ["classify"])[0]
            classifier = self.detectors.get(name)
            if not isinstance(classifier, Classifier):
                return 404, {"error": f"классификатор {name} не загружен"}, nonce
            try:
                threshold = min(max(float((query.get("threshold") or ["0.1"])[0]), 0.0), 0.99)
                top_k = min(max(int((query.get("top_k") or ["5"])[0]), 1), 20)
                image = decode_image(body)
            except Exception as e:
                return 400, {"error": f"плохой кадр или параметры: {e}"}, nonce
            try:
                return 200, classifier.classify(image, top_k, threshold), nonce
            except Exception as e:
                log.exception("Ошибка классификации")
                return 500, {"error": f"ошибка классификации: {e}"}, nonce
        return 404, {"error": "not found"}, nonce


def make_handler(service: AccelService):
    class Handler(BaseHTTPRequestHandler):
        server_version = "argos-vision-accel/1"
        sys_version = ""

        def _serve(self, method: str):
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0 or length > MAX_BODY:
                return self._reply(413, {"error": "кадр слишком большой"}, None)
            body = self.rfile.read(length) if length else b""
            status, payload, nonce = service.handle(self.client_address[0], method, self.path, self.headers, body)
            self._reply(status, payload, nonce)

        def _reply(self, status, payload, nonce):
            data = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            if nonce:
                self.send_header(proto.H_MAC, proto.sign_response(service.key, nonce, status, data))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._serve("GET")

        def do_POST(self):
            self._serve("POST")

        def log_message(self, fmt, *args):
            log.info("%s %s", self.client_address[0], fmt % args)

    return Handler


def parse_bind(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    return host or "127.0.0.1", int(port)


def benchmark(detectors: dict, name: str, runs: int) -> None:
    from PIL import Image

    detector = detectors[name]
    run = detector.classify if isinstance(detector, Classifier) else detector.detect
    image = Image.new("RGB", (640, 480), (90, 120, 150))
    run(image)  # первый вызов загружает модель в TPU
    times = []
    for _ in range(runs):
        times.append(run(image)["inference_ms"])
    times.sort()
    print(f"{name}: {'Edge TPU' if detector.edgetpu else 'CPU'}, {runs} прогонов, "
          f"медиана {times[len(times) // 2]:.2f} мс, мин {times[0]:.2f} мс, макс {times[-1]:.2f} мс")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ARGOS: детекция на Coral Edge TPU")
    parser.add_argument("--benchmark", type=int, default=0, help="прогнать N раз на пустом кадре и выйти")
    parser.add_argument("--model", default="objects")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    edgetpu = os.getenv("ARGOS_CORAL_CPU", "").strip().lower() not in ("1", "true", "on", "yes")
    detectors = load_detectors(os.getenv("ARGOS_CORAL_MODELS_DIR", DEFAULT_MODELS_DIR), edgetpu)
    if not detectors:
        log.error("Нет ни одной модели — запусти fetch-models.sh")
        return 2
    if args.benchmark:
        benchmark(detectors, args.model, args.benchmark)
        return 0

    key = proto.load_key()
    allow = {h.strip() for h in os.getenv("ARGOS_CORAL_ALLOW", "").split(",") if h.strip()}
    host, port = parse_bind(os.getenv("ARGOS_CORAL_BIND", "127.0.0.1:8770"))
    if host in ("0.0.0.0", "::", ""):
        log.error("Привяжи сервис к конкретному LAN-адресу, а не ко всем интерфейсам")
        return 2
    server = ThreadingHTTPServer((host, port), make_handler(AccelService(key, detectors, allow)))
    log.info("argos-vision-accel слушает %s:%d, модели: %s", host, port, ", ".join(detectors))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
