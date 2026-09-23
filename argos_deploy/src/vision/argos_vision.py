"""
vision.py — Глаза Аргоса (Computer Vision)
  Анализирует скриншоты, изображения, фото с камеры через Gemini Vision.
  Fallback: базовое описание через PIL.
"""

import os
import base64
import platform
import threading
import time
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


class ArgosVision:
    def __init__(self, api_key: str = None):
        # Используем пул ключей из ai_router если явный ключ не передан
        if api_key:
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
        if GEMINI_OK and self._key and self._key != "your_key_here":
            self._client = genai_sdk.Client(api_key=self._key)
            log.info("Vision: Gemini Vision подключён.")
        else:
            log.warning("Vision: Gemini недоступен — анализ изображений ограничен.")

    # ── REAL-TIME FEEDBACK (OpenCV) ───────────────────────
    def live_feed(self, timeout=10):
        """
        Запускает окно предпросмотра с детекцией лиц (Haar Cascades).
        Работает только в GUI-среде. В консоли выведет лог.
        """
        if not CV2_OK:
            return "❌ OpenCV не установлен (pip install opencv-python)."

        # Попытка открыть камеру
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            return "❌ Не удалось открыть камеру (индекс 0)."

        face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )

        log.info("Запуск видеопотока Vision Feedback...")
        print("🎥 Открываю окно предпросмотра... Нажмите 'q' для выхода.")

        try:
            import time

            start_time = time.time()

            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = face_cascade.detectMultiScale(gray, 1.1, 4)

                # Рисуем рамки
                for x, y, w, h in faces:
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
                    # Если мы в headless, окно не закроется "крестиком", нужен выход по таймеру
                    # Но если пользователь смотрит, ему может быть неприятно.
                    # Оставим ручной выход 'q' приоритетным, но добавим проверку headless
                    pass

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

        # Fallback — базовая информация через PIL
        if PIL_OK:
            try:
                img = Image.open(image_path)
                w, h = img.size
                mode = img.mode
                return (
                    f"👁️ Изображение: {os.path.basename(image_path)}\n"
                    f"  Размер: {w}×{h} px\n"
                    f"  Режим:  {mode}\n"
                    f"  (Для детального анализа нужен Gemini API)"
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

    # ── КАМЕРА ────────────────────────────────────────────
    def capture_camera(self, save_path: str = "logs/camera.jpg") -> str:
        """Снимает кадр с веб-камеры."""
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        try:
            import cv2

            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return "❌ Камера недоступна."
            ret, frame = cap.read()
            cap.release()
            if ret:
                cv2.imwrite(save_path, frame)
                log.info("Камера: %s", save_path)
                return save_path
            return "❌ Кадр не получен."
        except ImportError:
            return "❌ Установи: pip install opencv-python"
        except Exception as e:
            return f"❌ Камера: {e}"

    def look_through_camera(self, question: str = "Что ты видишь? Опиши подробно.") -> str:
        """Снимает с камеры и анализирует."""
        path = self.capture_camera()
        if path.startswith("❌"):
            return path
        return self.analyze_image(path, question)

    # ── АНАЛИЗ ФАЙЛА ──────────────────────────────────────
    def analyze_file(self, path: str) -> str:
        """Анализирует любой переданный файл-изображение."""
        supported = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
        if not any(path.lower().endswith(ext) for ext in supported):
            return f"❌ Неподдерживаемый формат. Поддерживаю: {', '.join(supported)}"
        return self.analyze_image(path)
