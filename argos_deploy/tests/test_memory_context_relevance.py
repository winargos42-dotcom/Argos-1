import hashlib

import pytest

from src import mempalace_bridge as bridge
from src.memory_index import save_fact


@pytest.fixture
def facts(tmp_path, monkeypatch):
    path = tmp_path / 'facts.sqlite3'
    save_fact(path, 'Ответь одним коротким предложением на русском языке. FORMAT_ONLY')
    monkeypatch.setenv('ARGOS_MEMPALACE_FACTS_PATH', str(path))
    monkeypatch.delenv('ARGOS_MEMPALACE_SQLITE_PATH', raising=False)
    monkeypatch.delenv('ARGOS_MEMPALACE_INDEX_PATH', raising=False)
    monkeypatch.setattr(bridge, '_MEMPALACE_ENABLED', True)
    return path


def test_formatting_matches_do_not_enter_automatic_context(facts):
    assert bridge.get_memory_context('Почему лёд плавает на воде? Ответь одним коротким предложением.') == ''


def test_topical_fact_remains_and_explicit_search_is_unchanged(facts):
    save_fact(facts, 'Лёд плавает на воде из-за меньшей плотности. TOPICAL_FACT')
    before = hashlib.sha256(facts.read_bytes()).hexdigest()
    context = bridge.get_memory_context('Почему лёд плавает на воде? Ответь одним коротким предложением.')
    assert 'TOPICAL_FACT' in context
    assert 'FORMAT_ONLY' not in context
    assert 'fact:' in context
    assert any('FORMAT_ONLY' in hit['text'] for hit in bridge.search_memory('ответь коротким предложением'))
    assert hashlib.sha256(facts.read_bytes()).hexdigest() == before


@pytest.mark.parametrize('query', ['', '?!', 'Ответь одним коротким предложением на русском языке.', 'Почему и как?'])
def test_no_topic_does_not_search_or_inject(monkeypatch, facts, query):
    def forbidden(*args, **kwargs):
        pytest.fail('No-topic query must not request arbitrary memories')
    monkeypatch.setattr(bridge, 'search_memory', forbidden)
    assert bridge.get_memory_context(query) == ''
