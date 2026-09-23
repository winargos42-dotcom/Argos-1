"""
vision.py — Глаза Аргоса (Computer Vision)
  Анализирует скриншоты, изображения, фото с камеры.
  Описание: Gemini Vision (если есть ключ и не ARGOS_DISABLE_GEMINI) →
            локальная Ollama vision-модель (qwen3.5, офлайн) →
            базовое описание через PIL/OpenCV.
  Локальный анализ камеры (без сети): яркость, движение между кадрами,
  детекция лиц (OpenCV YuNet, fallback Haar).

  Кадры камеры по умолчанию не сохраняются: временный файл удаляется
  сразу после анализа (ARGOS_VISION_KEEP=1 — оставить в logs/camera.jpg).

Переменные окружения:
  ARGOS_CAMERA_DEVICE     — индекс или путь (/dev/video0), по умолчанию 0
  ARGOS_CAMERA_WARMUP     — сколько кадров пропустить на автоэкспозицию (8)
  ARGOS_VISION_MODEL      — vision-модель Ollama (qwen3.5:latest)
  ARGOS_VISION_OLLAMA     — on/off (on)
  ARGOS_VISION_TIMEOUT    — таймаут описания, с (600: CPU медленный)
  ARGOS_VISION_MAX_SIDE   — уменьшать кадр до N px по длинной стороне (640)
  ARGOS_YUNET_MODEL       — путь к face_detection_yunet_2023mar.onnx
  ARGOS_DISABLE_GEMINI    — true: не использовать Gemini
  ARGOS_VISION_CLOUD_ENABLED — true: явно разрешить облачный Vision (по умолчанию off)
"""

import os
import base64
import json
import platform
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from src.argos_logger import get_logger

log = get_logger("argos.vision")


class _VisionGeminiLimiter:
    def __init__(self, max_calls: int = 15, window_seconds: int = 60):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._hits = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = time.time()
        with self._lock:
            while self._hits and (now - self._hits[0]) >= self.window_seconds:
                self._hits.popleft()
            if len(self._hits) >= self.max_calls:
                return False
            self._hits.append(now)
            return True


# Лимит = кол-во ключей × 5 RPM (загружается динамически при первом запросе)
_GEMINI_VISION_LIMITER = _VisionGeminiLimiter(max_calls=25, window_seconds=60)

try:
    from google import genai as genai_sdk
    from google.genai import types as genai_types

    GEMINI_OK = True
except ImportError:
    genai_sdk = None
    genai_types = None
    GEMINI_OK = False

try:
    from PIL import Image

    PIL_OK = True
except ImportError:
    Image = None
    PIL_OK = False

try:
    import cv2

    CV2_OK = True
except ImportError:
    cv2 = None
    CV2_OK = False


_ON = {"1", "true", "on", "yes", "да", "вкл"}

# Фразы, по которым core/VisionModule отправляют запрос в камеру.
CAMERA_DESCRIBE_PHRASES = (
    "посмотри в камеру",
    "что видит камера",
    "включи камеру",
    "что ты видишь",
    "что видишь",
    "опиши что видишь",
)
CAMERA_LOCAL_PHRASES = (
    "камера статус",
    "статус камеры",
    "проверь камеру",
    "анализ камеры",
    "есть кто перед камерой",
    "кто перед камерой",
    "движение в камере",
)


_SHORT_ONLY = ("что ты видишь", "что видишь", "опиши что видишь")


def camera_intent(lowered: str):
    """'local' | 'describe' | None. «что (ты) видишь» — только в коротких фразах."""
    t = (lowered or "").lower().replace("ё", "е")
    if any(k in t for k in CAMERA_LOCAL_PHRASES):
        return "local"
    for k in CAMERA_DESCRIBE_PHRASES:
        if k in t:
            if k in _SHORT_ONLY and (len(t.split()) > 6 or "экран" in t):
                continue
            return "describe"
    return None


def camera_question(text: str) -> str:
    """Убирает из запроса имя и триггер-фразы; остаток — уточняющий вопрос."""
    q = (text or "").lower()
    for k in ("аргос",) + CAMERA_DESCRIBE_PHRASES:
        q = q.replace(k, " ")
    q = " ".join(q.replace(",", " ").split()).strip(" ?.!")
    return q


def _gemini_disabled() -> bool:
    return os.getenv("ARGOS_DISABLE_GEMINI", "").strip().lower() in _ON


def _models_dir() -> str:
    return os.getenv("ARGOS_MODELS_DIR", "/home/.argos-storage/models").strip() or "/home/.argos-storage/models"


def _camera_device():
    raw = os.getenv("ARGOS_CAMERA_DEVICE", "0").strip() or "0"
    return int(raw) if raw.isdigit() else raw


def frame_brightness(frame) -> float:
    """Средняя яркость кадра 0..255 (по серому каналу)."""
    if frame is None:
        return 0.0
    if getattr(frame, "ndim", 2) == 3:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if CV2_OK else frame.mean(axis=2)
    else:
        gray = frame
    return float(gray.mean())


def motion_score(frame_a, frame_b, pixel_threshold: int = 25) -> float:
    """Доля (0..1) пикселей, заметно изменившихся между двумя кадрами."""
    if frame_a is None or frame_b is None or not CV2_OK:
        return 0.0
    ga = cv2.GaussianBlur(cv2.cvtColor(frame_a, cv2.COLOR_BGR2GRAY), (21, 21), 0)
    gb = cv2.GaussianBlur(cv2.cvtColor(frame_b, cv2.COLOR_BGR2GRAY), (21, 21), 0)
    diff = cv2.absdiff(ga, gb)
    _, mask = cv2.threshold(diff, pixel_threshold, 255, cv2.THRESH_BINARY)
    return float((mask > 0).mean())


def _brightness_label(value: float) -> str:
    if value < 40:
        return "темно"
    if value < 90:
        return "полумрак"
    if value < 190:
        return "нормальное освещение"
    return "очень светло"


class FaceDetector:
    """YuNet (OpenCV DNN) → Haar cascade → none."""

    def __init__(self, yunet_path: str = None):
        self.method = "none"
        self._yunet = None
        self._haar = None
        path = yunet_path or os.getenv("ARGOS_YUNET_MODEL", "").strip() or os.path.join(
            _models_dir(), "opencv", "face_detection_yunet_2023mar.onnx"
        )
        if not CV2_OK:
            return
        if os.path.isfile(path) and hasattr(cv2, "FaceDetectorYN"):
            try:
                self._yunet = cv2.FaceDetectorYN.create(path, "", (320, 320), 0.8, 0.3, 50)
                self.method = "yunet"
                return
            except Exception as e:
                log.warning("YuNet: %s", e)
        if hasattr(cv2, "CascadeClassifier"):
            candidates = []
            data = getattr(cv2, "data", None)
            if data is not None and getattr(data, "haarcascades", ""):
                candidates.append(os.path.join(data.haarcascades, "haarcascade_frontalface_default.xml"))
            candidates += [
                "/usr/share/opencv5/haarcascades/haarcascade_frontalface_default.xml",
                "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml",
            ]
            for c in candidates:
                if os.path.isfile(c):
                    clf = cv2.CascadeClassifier(c)
                    if not clf.empty():
                        self._haar = clf
                        self.method = "haar"
                        return

    def detect(self, frame) -> list:
        """Возвращает список (x, y, w, h, score)."""
        if frame is None or self.method == "none":
            return []
        if self.method == "yunet":
            h, w = frame.shape[:2]
            self._yunet.setInputSize((w, h))
            _, faces = self._yunet.detect(frame)
            if faces is None:
                return []
            return [(int(f[0]), int(f[1]), int(f[2]), int(f[3]), round(float(f[-1]), 2)) for f in faces]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return [(int(x), int(y), int(fw), int(fh), 1.0) for (x, y, fw, fh) in self._haar.detectMultiScale(gray, 1.1, 5)]


class ArgosVision:
    def __init__(self, api_key: str = None):
        self._face_detector = None
        self.last_timings: dict = {}
        self.last_skip_reason = ""
        if _gemini_disabled() and not api_key:
            self._key = ""
        # Используем пул ключей из ai_router если явный ключ не передан
        elif api_key:
            self._key = api_key
        else:
            try:
                from src.ai_router import _GEMINI_POOL
                _GEMINI_POOL.reload()
                slot = _GEMINI_POOL.get_key() if _GEMINI_POOL.available() else None
                self._key = slot[1] if slot else os.getenv("GEMINI_API_KEY", "")
                # обновляем лимитер под реальное кол-во ключей
                n = len(_GEMINI_POOL._keys)
                if n > 0:
                    _GEMINI_VISION_LIMITER.max_calls = n * _GEMINI_POOL.MAX_RPM
            except Exception:
                self._key = os.getenv("GEMINI_API_KEY", "")
        self._client = None
        self._model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
        if (os.getenv("ARGOS_VISION_CLOUD_ENABLED", "false").strip().lower() in _ON
                and GEMINI_OK and self._key and self._key != "your_key_here"):
            self._client = genai_sdk.Client(api_key=self._key)
            log.info("Vision: Gemini Vision подключён.")
        else:
            log.info("Vision: Gemini не используется — описание через локальную Ollama (%s).", self.ollama_model)

    @property
    def ollama_model(self) -> str:
        return os.getenv("ARGOS_VISION_MODEL", "qwen3.5:latest").strip() or "qwen3.5:latest"

    @property
    def ollama_host(self) -> str:
        host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").strip() or "http://127.0.0.1:11434"
        if not host.startswith("http"):
            host = "http://" + host
        return host.rstrip("/")

    # ── REAL-TIME FEEDBACK (OpenCV) ───────────────────────
    def live_feed(self, timeout=10):
        """
        Запускает окно предпросмотра с детекцией лиц (Haar Cascades).
        Работает только в GUI-среде. В консоли выведет лог.
        """
        if not CV2_OK:
            return "❌ OpenCV не установлен (pip install opencv-python)."

        # Попытка открыть камеру
        cap = self._open_camera()
        if not cap.isOpened():
            return f"❌ Не удалось открыть камеру ({_camera_device()})."

        detector = self.face_detector()

        log.info("Запуск видеопотока Vision Feedback...")
        print("🎥 Открываю окно предпросмотра... Нажмите 'q' для выхода.")

        try:
            import time

            start_time = time.time()

            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                faces = detector.detect(frame)

                # Рисуем рамки
                for x, y, w, h, _score in faces:
                    cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                    cv2.putText(
                        frame, "HUMAN", (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2
                    )

                # Добавляем HUD Аргоса
                cv2.putText(
                    frame,
                    "ARGOS VISION SYSTEM v1.3",
                    (20, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 255),
                    2,
                )
                cv2.putText(
                    frame,
                    "Searching for targets...",
                    (20, 60),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 200, 200),
                    1,
                )

                cv2.imshow("Argos Vision Feedback", frame)

                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

                # Автовыход для демо-режима если нет окна (headless)
                # cv2.imshow может не создать окно в docker/ssh без X11
                # поэтому просто читаем кадры некоторое время
                if timeout and (time.time() - start_time > timeout):
                    # headless (без X11) окно не закрыть — выходим по таймеру, камера освобождается
                    break

        except Exception as e:
            return f"⚠️ Ошибка Vision Feedback: {e} (Возможно, нет GUI дисплея)"
        finally:
            cap.release()
            cv2.destroyAllWindows()
            # Для Linux/Mac иногда нужно пару раз вызвать waitKey
            cv2.waitKey(1)

        return "✅ Сессия Vision завершена."

    # ── СКРИНШОТ ──────────────────────────────────────────
    def screenshot(self, save_path: str = "logs/screenshot.png") -> str:
        """Делает скриншот экрана и возвращает путь к файлу."""
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        try:
            # pyautogui — кроссплатформенный
            import pyautogui

            img = pyautogui.screenshot()
            img.save(save_path)
            log.info("Скриншот: %s", save_path)
            return save_path
        except ImportError:
            pass

        # Fallback: PIL ImageGrab (Windows/macOS)
        try:
            from PIL import ImageGrab

            img = ImageGrab.grab()
            img.save(save_path)
            return save_path
        except Exception:
            pass

        # Linux: scrot
        if platform.system() == "Linux":
            import subprocess

            try:
                subprocess.run(["scrot", save_path], check=True)
                return save_path
            except Exception:
                pass

        return ""

    # ── АНАЛИЗ ИЗОБРАЖЕНИЯ ────────────────────────────────
    def analyze_image(
        self, image_path: str, question: str = "Опиши что на изображении подробно."
    ) -> str:
        """Анализирует изображение через Gemini Vision."""
        if not os.path.exists(image_path):
            return f"❌ Файл не найден: {image_path}"

        if self._client:
            try:
                if not _GEMINI_VISION_LIMITER.allow():
                    return "❌ Gemini Vision: превышен лимит 15 запросов в минуту. Повтори позже."

                ext = os.path.splitext(image_path)[1].lower()
                mime = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".webp": "image/webp",
                    ".gif": "image/gif",
                    ".bmp": "image/bmp",
                }.get(ext, "image/jpeg")

                with open(image_path, "rb") as f:
                    img_bytes = f.read()

                image_part = genai_types.Part.from_bytes(data=img_bytes, mime_type=mime)
                resp = self._client.models.generate_content(
                    model=self._model_name,
                    contents=[question, image_part],
                )
                log.info("Vision анализ: %s", image_path)
                return f"👁️ VISION АНАЛИЗ:\n{getattr(resp, 'text', '')}"
            except Exception as e:
                log.error("Gemini Vision ошибка: %s", e)

        # Локальная vision-модель Ollama (офлайн)
        if os.getenv("ARGOS_VISION_OLLAMA", "on").strip().lower() in _ON:
            text = self.describe_with_ollama(image_path, question)
            if text:
                return f"👁️ VISION АНАЛИЗ ({self.ollama_model}, {self.last_timings.get('ollama_s', 0):.0f} с):\n{text}"

        skip = getattr(self, "last_skip_reason", "")
        hint = f"\n  (Описание пропущено: {skip})" if skip else (
            f"\n  (Для описания нужна Ollama vision-модель {self.ollama_model} или Gemini API)"
        )
        # Fallback — базовая информация через OpenCV/PIL
        if CV2_OK:
            img = cv2.imread(image_path)
            if img is not None:
                h, w = img.shape[:2]
                return (
                    f"👁️ Изображение: {os.path.basename(image_path)}\n"
                    f"  Размер: {w}×{h} px, яркость {frame_brightness(img):.0f}/255{hint}"
                )
        if PIL_OK:
            try:
                img = Image.open(image_path)
                w, h = img.size
                mode = img.mode
                return (
                    f"👁️ Изображение: {os.path.basename(image_path)}\n"
                    f"  Размер: {w}×{h} px\n"
                    f"  Режим:  {mode}{hint}"
                )
            except Exception as e:
                return f"❌ PIL ошибка: {e}"

        return "❌ Для анализа изображений установи: pip install google-genai Pillow"

    # ── СКРИНШОТ + АНАЛИЗ ─────────────────────────────────
    def look_at_screen(self, question: str = "Что происходит на экране? Опиши кратко.") -> str:
        """Делает скриншот и сразу анализирует его."""
        path = self.screenshot()
        if not path:
            return "❌ Не удалось сделать скриншот. Установи: pip install pyautogui"
        return self.analyze_image(path, question)

    # ── ЛОКАЛЬНАЯ VISION-МОДЕЛЬ (Ollama) ──────────────────
    def _encode_image_b64(self, image_path: str) -> str:
        """JPEG base64, уменьшенный до ARGOS_VISION_MAX_SIDE (ускоряет CPU-инференс)."""
        max_side = int(os.getenv("ARGOS_VISION_MAX_SIDE", "640") or 640)
        if CV2_OK:
            img = cv2.imread(image_path)
            if img is not None:
                h, w = img.shape[:2]
                scale = min(1.0, float(max_side) / max(h, w))
                if scale < 1.0:
                    img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
                ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                if ok:
                    return base64.b64encode(buf.tobytes()).decode("ascii")
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")

    @staticmethod
    def _mem_available_mb() -> float:
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) / 1024.0
        except Exception:
            pass
        return -1.0

    def _ollama_model_size_mb(self) -> float:
        """Размер модели из /api/tags (МБ) или -1, если неизвестно."""
        try:
            with urllib.request.urlopen(self.ollama_host + "/api/tags", timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for m in data.get("models", []):
                if m.get("name") == self.ollama_model or m.get("model") == self.ollama_model:
                    return float(m.get("size", 0)) / (1024 * 1024)
        except Exception:
            pass
        return -1.0

    def _ollama_loaded(self) -> bool:
        try:
            with urllib.request.urlopen(self.ollama_host + "/api/ps", timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return any(m.get("name") == self.ollama_model or m.get("model") == self.ollama_model
                       for m in data.get("models", []))
        except Exception:
            return False

    def vision_memory_check(self) -> tuple:
        """
        (ok, причина). Защита от OOM: vision-модель грузится целиком в RAM
        (qwen3.5 9.7B ≈ 6.6 ГБ + KV/проектор). Требуем MemAvailable ≥ размер×1.2 + резерв (1 ГБ).
        ARGOS_VISION_MEM_GUARD=off — отключить проверку.
        """
        if os.getenv("ARGOS_VISION_MEM_GUARD", "on").strip().lower() not in _ON:
            return True, ""
        if self._ollama_loaded():
            return True, ""
        size = self._ollama_model_size_mb()
        avail = self._mem_available_mb()
        if size <= 0 or avail <= 0:
            return True, ""
        reserve = float(os.getenv("ARGOS_VISION_MEM_RESERVE_MB", "1024") or 1024)
        need = size * float(os.getenv("ARGOS_VISION_MEM_FACTOR", "1.2") or 1.2) + reserve
        if avail < need:
            return False, (
                f"мало RAM для {self.ollama_model}: свободно {avail / 1024:.1f} ГБ, "
                f"нужно ≈{need / 1024:.1f} ГБ (иначе OOM-kill Ollama)"
            )
        return True, ""

    def describe_with_ollama(self, image_path: str, question: str = "Что ты видишь?") -> str:
        """Описание изображения локальной vision-моделью Ollama. Ничего не уходит в сеть."""
        self.last_skip_reason = ""
        ok, reason = self.vision_memory_check()
        if not ok:
            self.last_skip_reason = reason
            log.warning("Ollama vision пропущен: %s", reason)
            return ""
        timeout = float(os.getenv("ARGOS_VISION_TIMEOUT", "600") or 600)
        prompt = (
            f"{question.strip() or 'Что на изображении?'}\n"
            "Ответь по-русски, кратко: 2-4 предложения, только то, что реально видно."
        )
        payload = {
            "model": self.ollama_model,
            "messages": [{"role": "user", "content": prompt, "images": [self._encode_image_b64(image_path)]}],
            "stream": False,
            "think": False,
            "keep_alive": os.getenv("ARGOS_VISION_KEEP_ALIVE", "2m"),
            "options": {"num_predict": int(os.getenv("ARGOS_VISION_NUM_PREDICT", "200") or 200)},
        }
        req = urllib.request.Request(
            self.ollama_host + "/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            self.last_timings["ollama_s"] = time.monotonic() - started
            log.warning("Ollama vision (%s): %s", self.ollama_model, e)
            return ""
        self.last_timings["ollama_s"] = time.monotonic() - started
        text = ((data.get("message") or {}).get("content") or data.get("response") or "").strip()
        if "</think>" in text:
            text = text.split("</think>", 1)[1].strip()
        log.info("Ollama vision: %.1f с", self.last_timings["ollama_s"])
        return text

    # ── КАМЕРА ────────────────────────────────────────────
    def _open_camera(self):
        device = _camera_device()
        backend = getattr(cv2, "CAP_V4L2", 0) if platform.system() == "Linux" else 0
        cap = cv2.VideoCapture(device, backend) if backend else cv2.VideoCapture(device)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(device)
        return cap

    def grab_frames(self, count: int = 1, interval: float = 0.0, warmup: int = None) -> list:
        """Снимает count кадров (после прогрева автоэкспозиции). Камера сразу освобождается."""
        if not CV2_OK:
            raise RuntimeError("OpenCV не установлен (pip install opencv-python-headless)")
        warmup = int(os.getenv("ARGOS_CAMERA_WARMUP", "8")) if warmup is None else warmup
        cap = self._open_camera()
        if not cap.isOpened():
            raise RuntimeError(f"Камера недоступна ({_camera_device()})")
        frames = []
        try:
            for _ in range(max(0, warmup)):
                cap.read()
            for i in range(count):
                ok, frame = cap.read()
                if ok and frame is not None:
                    frames.append(frame)
                if interval and i < count - 1:
                    time.sleep(interval)
        finally:
            cap.release()
        if not frames:
            raise RuntimeError("Кадр не получен")
        return frames

    def face_detector(self) -> FaceDetector:
        if self._face_detector is None:
            self._face_detector = FaceDetector()
        return self._face_detector

    def camera_local_analysis(self, frames: list = None) -> dict:
        """Офлайн-анализ: разрешение, яркость, движение, лица. Кадры не сохраняются."""
        if frames is None:
            frames = self.grab_frames(count=2, interval=0.5)
        frame = frames[-1]
        h, w = frame.shape[:2]
        det = self.face_detector()
        faces = det.detect(frame)
        motion = motion_score(frames[0], frames[-1]) if len(frames) > 1 else 0.0
        bright = frame_brightness(frame)
        return {
            "width": int(w),
            "height": int(h),
            "brightness": round(bright, 1),
            "brightness_label": _brightness_label(bright),
            "motion": round(motion, 4),
            "motion_detected": motion > float(os.getenv("ARGOS_MOTION_THRESHOLD", "0.02")),
            "faces": len(faces),
            "face_boxes": faces,
            "face_method": det.method,
        }

    @staticmethod
    def format_local_analysis(info: dict) -> str:
        faces = info.get("faces", 0)
        face_txt = (
            f"лиц: {faces}" if info.get("face_method") != "none" else "детектор лиц недоступен"
        )
        return (
            f"📷 Камера: {info['width']}×{info['height']}, яркость {info['brightness']:.0f}/255 "
            f"({info['brightness_label']}), движение: {'да' if info['motion_detected'] else 'нет'} "
            f"({info['motion'] * 100:.1f}%), {face_txt} [{info.get('face_method')}]"
        )

    def camera_report(self) -> str:
        """Быстрый локальный отчёт по камере (без LLM)."""
        try:
            return self.format_local_analysis(self.camera_local_analysis())
        except Exception as e:
            return f"❌ Камера: {e}"

    def capture_camera(self, save_path: str = "logs/camera.jpg") -> str:
        """Снимает кадр с веб-камеры и сохраняет в save_path. Возвращает путь или '❌ ...'."""
        if not CV2_OK:
            return "❌ Установи: pip install opencv-python-headless"
        try:
            frame = self.grab_frames(count=1)[0]
            directory = os.path.dirname(save_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            cv2.imwrite(save_path, frame)
            log.info("Камера: %s", save_path)
            return save_path
        except Exception as e:
            return f"❌ Камера: {e}"

    def look_through_camera(self, question: str = "Что ты видишь? Опиши подробно.") -> str:
        """Снимает с камеры: локальный анализ + описание (Gemini/Ollama). Кадр удаляется."""
        if not CV2_OK:
            return "❌ Установи: pip install opencv-python-headless"
        keep = os.getenv("ARGOS_VISION_KEEP", "").strip().lower() in _ON
        try:
            frames = self.grab_frames(count=2, interval=0.4)
        except Exception as e:
            return f"❌ Камера: {e}"
        local = ""
        try:
            local = self.format_local_analysis(self.camera_local_analysis(frames))
        except Exception as e:
            log.warning("Vision local analysis: %s", e)
        if keep:
            os.makedirs("logs", exist_ok=True)
            path = "logs/camera.jpg"
        else:
            fd, path = tempfile.mkstemp(prefix="argos-cam-", suffix=".jpg")
            os.close(fd)
        try:
            cv2.imwrite(path, frames[-1])
            described = self.analyze_image(path, question)
        finally:
            if not keep:
                try:
                    os.remove(path)
                except OSError:
                    pass
        return f"{local}\n{described}".strip()

    # ── АНАЛИЗ ФАЙЛА ──────────────────────────────────────
    def analyze_file(self, path: str) -> str:
        """Анализирует любой переданный файл-изображение."""
        supported = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
        if not any(path.lower().endswith(ext) for ext in supported):
            return f"❌ Неподдерживаемый формат. Поддерживаю: {', '.join(supported)}"
        return self.analyze_image(path)
