from types import SimpleNamespace

from src.memory import ArgosMemory


def facts_context(rows):
    return ArgosMemory.get_context(SimpleNamespace(get_all_facts=lambda: rows))


def test_dreamer_insights_are_deduplicated_and_capped():
    rows = [("user", "name", "Всеволод", "")]
    rows += [("dreamer", f"insight_{ts}_0", "1. Пользователь ценит быстрый и точный ответ.", "")
             for ts in range(1000, 1010)]
    rows += [("dreamer", f"insight_{ts}_1", f"2. Уникальный вывод {ts}", "") for ts in range(2000, 2005)]
    text = facts_context(rows)
    assert "[user] name: Всеволод" in text
    assert text.count("[dreamer]") == 3
    assert "Уникальный вывод 2004" in text


def test_last_dialogue_echo_is_skipped():
    rows = [("dialogue", "last_user_query", "Q", ""), ("dialogue", "last_argos_response", "A", "")]
    assert facts_context(rows) == ""


def test_low_score_rag_hits_are_dropped(monkeypatch):
    monkeypatch.delenv("ARGOS_RAG_MIN_SCORE", raising=False)
    hits = [{"text": "relevant", "score": 0.46}, {"text": "noise", "score": 0.14}]
    mem = SimpleNamespace(search_semantic=lambda q, top_k: hits)
    text = ArgosMemory.get_rag_context(mem, "q")
    assert "relevant" in text and "noise" not in text
    mem = SimpleNamespace(search_semantic=lambda q, top_k: hits[1:])
    assert ArgosMemory.get_rag_context(mem, "q") == ""


def test_consciousness_lessons_are_capped_like_dreamer():
    rows = [("learning", f"lesson_{ts}", f"урок {ts}", "") for ts in range(100, 110)]
    text = facts_context(rows)
    assert text.count("[learning]") == 3
    assert "урок 109" in text and "урок 100" not in text
