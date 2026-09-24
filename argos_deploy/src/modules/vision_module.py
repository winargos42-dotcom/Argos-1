from src.modules.base import BaseModule
from src.vision import camera_intent, camera_question


class VisionModule(BaseModule):
    module_id = "vision"
    title = "Vision"

    def can_handle(self, text: str, lowered: str) -> bool:
        keys = [
            "посмотри на экран",
            "что на экране",
            "скриншот",
            "посмотри в камеру",
            "что видит камера",
            "включи камеру",
            "проанализируй изображение",
            "анализ фото",
        ]
        return any(k in lowered for k in keys) or camera_intent(lowered) is not None

    def handle(self, text: str, lowered: str, admin=None, flasher=None) -> str | None:
        if not self.core or not self.core.vision:
            return None

        if any(k in lowered for k in ["посмотри на экран", "что на экране", "скриншот"]):
            question = (
                text.replace("аргос", "")
                .replace("посмотри на экран", "")
                .replace("что на экране", "")
                .replace("скриншот", "")
                .strip()
            )
            return self.core.vision.look_at_screen(question or "Что происходит на экране?")

        cam = camera_intent(lowered)
        if cam == "local" and hasattr(self.core.vision, "camera_report"):
            return self.core.vision.camera_report()
        if cam:
            question = camera_question(text)
            return self.core.vision.look_through_camera(question or "Что ты видишь?")

        if "проанализируй изображение" in lowered or "анализ фото" in lowered:
            path = text.split()[-1]
            return self.core.vision.analyze_file(path)

        return None
