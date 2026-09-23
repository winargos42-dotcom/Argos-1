"""ARGOS Vision modules.

ArgosVision (argos_vision.py) — экран/камера/файлы: локальный анализ OpenCV +
описание через Gemini или локальную Ollama vision-модель.
ShadowVision (shadow_vision.py) — фоновое наблюдение за рабочим столом (AWA).

Раньше здесь экспортировался ShadowVision под именем ArgosVision, из-за чего
пакет src/vision/ затенял src/vision.py и core.vision не имел методов
look_through_camera/analyze_image — камерные команды падали.
"""
try:
    from .argos_vision import (  # noqa: F401
        CAMERA_DESCRIBE_PHRASES,
        CAMERA_LOCAL_PHRASES,
        ArgosVision,
        FaceDetector,
        camera_intent,
        camera_question,
        frame_brightness,
        motion_score,
    )
except ImportError:  # pragma: no cover - broken optional deps
    ArgosVision = None

try:
    from .shadow_vision import ShadowVision  # noqa: F401
except ImportError:  # pragma: no cover
    ShadowVision = None

__all__ = [
    "ArgosVision",
    "ShadowVision",
    "FaceDetector",
    "camera_intent",
    "camera_question",
    "frame_brightness",
    "motion_score",
    "CAMERA_DESCRIBE_PHRASES",
    "CAMERA_LOCAL_PHRASES",
]
