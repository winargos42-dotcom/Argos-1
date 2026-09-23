from types import SimpleNamespace

from src.core import ArgosCore


class FakeConsciousness:
    def __init__(self):
        self.calls = []

    def on_interaction(self, user_input, response):
        self.calls.append(("interaction", user_input, response))

    def handle_command(self, cmd):
        self.calls.append(("command", cmd))
        return f"handled:{cmd}"

    def full_status(self):
        return "full-status"


def test_dialog_turn_feeds_consciousness_without_memory():
    consciousness = FakeConsciousness()
    core = SimpleNamespace(consciousness=consciousness, memory=None)
    ArgosCore._remember_dialog_turn(core, "вопрос", "ответ", "Direct")
    assert consciousness.calls == [("interaction", "вопрос", "ответ")]


def test_dialog_turn_survives_consciousness_failure():
    class Broken:
        def on_interaction(self, *_):
            raise RuntimeError("boom")
    core = SimpleNamespace(consciousness=Broken(), memory=None)
    ArgosCore._remember_dialog_turn(core, "вопрос", "ответ", "Direct")


def test_consciousness_commands_are_routed():
    consciousness = FakeConsciousness()
    core = SimpleNamespace(consciousness=consciousness)
    for text, expected in (("Поток сознания", "handled:поток сознания"),
                           ("цели?", "handled:цели"),
                           ("добавь цель выучить KNX", "handled:добавь цель выучить knx"),
                           ("разум статус", "full-status")):
        assert ArgosCore.execute_intent(core, text, admin=object(), flasher=None) == expected
