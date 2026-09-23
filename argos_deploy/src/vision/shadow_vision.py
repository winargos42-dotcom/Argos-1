"""Opt-in background screen context via verified local Ollama only."""

from __future__ import annotations

import base64
import os
import threading
import time

from src.argos_logger import get_logger

log = get_logger("argos.shadow_vision")

_VISION_MODEL = os.getenv("ARGOS_VISION_MODEL", "qwen3.5:latest")
_CAPTURE_INTERVAL = int(os.getenv("SHADOW_VISION_INTERVAL", "30"))


class ShadowVision:
    """
    Фоновое зрение Аргоса.
    Сохраняет AWA API; запуск разрешается явной настройкой.
    """

    def __init__(self, core=None):
        self.core = core
        self.active = False
        self._thread: threading.Thread | None = None
        self._last_analysis: str = ""

    # ── ЗАПУСК / ОСТАНОВКА ───────────────────────────────────────────────────

    def start_vision_loop(self) -> None:
        """Запуск цикла наблюдения в фоновом потоке."""
        if self._thread and self._thread.is_alive():
            log.debug("[ShadowVision] Уже запущен.")
            return
        self.active = True
        self._thread = threading.Thread(target=self._watch, daemon=True, name="shadow-vision")
        self._thread.start()
        log.info(
            "[ShadowVision] Запущен (интервал %ds, модель %s)", _CAPTURE_INTERVAL, _VISION_MODEL
        )

    def stop(self) -> None:
        """Остановка цикла."""
        self.active = False
        log.info("[ShadowVision] Остановлен.")

    # ── ОСНОВНОЙ ЦИКЛ ────────────────────────────────────────────────────────

    def _watch(self) -> None:
        while self.active:
            try:
                from .interactive import LocalAnalyzer
                LocalAnalyzer().ensure_available()
                img_b64 = self._capture_screen_b64()
                if img_b64:
                    analysis = self._analyse(img_b64)
                    if analysis:
                        self._last_analysis = analysis
                        self._handle_analysis(analysis)
            except Exception as e:
                log.debug("[ShadowVision] _watch ошибка: %s", type(e).__name__)

            time.sleep(_CAPTURE_INTERVAL)

    def _capture_screen_b64(self) -> str | None:
        """
        Делает скриншот основного монитора через mss,
        сжимает до 1024×1024 и возвращает base64-строку для Ollama.
        """
        try:
            from .interactive import ArgosVision
            return base64.b64encode(ArgosVision._capture_worker("screen")).decode("ascii")
        except Exception as error:
            log.debug("[ShadowVision] Capture unavailable: %s", type(error).__name__)
            return None

    def _analyse(self, img_b64: str) -> str | None:
        """
        Отправляет изображение только в проверенный локальный Ollama.
        Возвращает текстовое описание происходящего на экране.
        """
        try:
            from .interactive import LocalAnalyzer
            analyzer = LocalAnalyzer()
            analyzer.ensure_available()
            return analyzer.analyze(base64.b64decode(img_b64, validate=True),
                                    "Что происходит на экране? Опиши кратко суть работы.")
        except Exception as error:
            log.debug("[ShadowVision] Local analysis unavailable: %s", type(error).__name__)
            return None

    def _handle_analysis(self, analysis: str) -> None:
        """
        Обрабатывает результат анализа экрана.
        Если замечена работа с кодом или ошибки — уведомляет Аргос.
        """
        lower = analysis.lower()
        if any(kw in lower for kw in ("код", "code", "ошибка", "error", "exception", "traceback")):
            log.info("[ShadowVision] Обновлён контекст активности")
            try:
                if self.core and hasattr(self.core, "awa") and self.core.awa:
                    if hasattr(self.core.awa, "trigger_event"):
                        self.core.awa.trigger_event("context_update", analysis)
            except Exception as e:
                log.debug("[ShadowVision] trigger_event ошибка: %s", type(e).__name__)

    # ── ВСПОМОГАТЕЛЬНОЕ ──────────────────────────────────────────────────────

    @property
    def last_analysis(self) -> str:
        """Возвращает последний результат анализа экрана."""
        return self._last_analysis
