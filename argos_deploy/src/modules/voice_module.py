from src.modules.base import BaseModule


class VoiceModule(BaseModule):
    module_id = "voice"
    title = "Voice"

    def can_handle(self, text: str, lowered: str) -> bool:
        return any(
            k in lowered
            for k in [
                "голос вкл",
                "включи голос",
                "голос выкл",
                "выключи голос",
                "включи wake word",
                "wake word вкл",
                "выключи wake word",
                "wake word выкл",
                "перестань слушать",
            ]
        )

    def handle(self, text: str, lowered: str, admin=None, flasher=None) -> str | None:
        if not self.core:
            return None

        if any(k in lowered for k in ["голос вкл", "включи голос"]):
            self.core.voice_on = True
            return "🔊 Голосовой модуль активирован."

        if any(k in lowered for k in ["голос выкл", "выключи голос"]):
            self.core.voice_on = False
            return "🔇 Голосовой модуль отключён."

        if any(k in lowered for k in ["выключи wake word", "wake word выкл", "перестань слушать"]):
            stop = getattr(self.core, "stop_wake_word", None)
            return stop() if stop else "Wake Word не запущен."

        if any(k in lowered for k in ["включи wake word", "wake word вкл"]):
            return self.core.start_wake_word(admin, flasher)

        return None
