"""Exercise copied production modules; no live core, CLI, HTTP or event bus."""
import importlib.util
import logging
from pathlib import Path
import socket
import subprocess
import sys
import types

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def modules(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Template inspection must not invoke any process or network transport')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
    logger = types.ModuleType('src.argos_logger')
    logger.get_logger = logging.getLogger
    events = types.ModuleType('src.event_bus')
    events.get_bus = lambda: None
    events.Events = types.SimpleNamespace()
    package = types.ModuleType('src')
    package.__path__ = [str(ROOT / 'src')]
    monkeypatch.setitem(sys.modules, 'src', package)
    monkeypatch.setitem(sys.modules, 'src.argos_logger', logger)
    monkeypatch.setitem(sys.modules, 'src.event_bus', events)

    def load(name):
        full = 'src.' + name
        spec = importlib.util.spec_from_file_location(full, ROOT / 'src' / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, full, module)
        spec.loader.exec_module(module)
        return module
    integrator = load('claude_templates_integrator')
    api = load('argos_claude_api')
    return integrator, api


def sample_component(module, kind='command'):
    return module.ClaudeComponent(name='generate-tests', component_type=kind,
                                 category='testing', description='Generate useful tests',
                                 content='Template instructions', tools=['Read'])


def test_known_command_is_not_executed(modules):
    integrator, api_module = modules
    api = api_module.ArgosClaudeAPI(auto_init=False)
    component = sample_component(integrator)
    api._command_cache[component.name] = {
        'component': component, 'metadata': {'description': component.description}}
    result = api.execute_command(component.name, 'private-query-marker')
    assert result.success is False
    assert result.error == 'execution_not_configured'
    assert result.command == component.name
    assert 'private-query-marker' not in result.output
    assert api.get_command_info(component.name)['description'] == component.description


def test_unknown_command_error_preserved(modules):
    _, api_module = modules
    result = api_module.ArgosClaudeAPI(auto_init=False).execute_command('absent')
    assert result.success is False
    assert result.command == 'absent' and result.output == ''
    assert result.error == "Command 'absent' not found"


def test_agent_stub_explicitly_reports_no_invocation(modules):
    integrator, _ = modules
    component = sample_component(integrator, 'agent')
    obj = integrator.ClaudeTemplatesIntegrator()
    result = obj._invoke_claude_agent(component, 'private-query-marker')
    assert 'execution_not_configured' in result
    assert 'вызов не выполнен' in result.lower()
    assert component.name in result and component.description in result
    assert 'private-query-marker' not in result


def test_template_listing_preserves_metadata(modules):
    integrator, api_module = modules
    component = sample_component(integrator, 'agent')
    obj = integrator.ClaudeTemplatesIntegrator()
    obj.loader._components[component.id] = component
    obj.loader._categories['agents'] = [component.id]
    api = api_module.ArgosClaudeAPI(auto_init=False)
    api._integrator = obj
    rows = api.list_agents(category='testing')
    assert rows == [{'name': component.name, 'category': 'testing',
                     'description': component.description, 'tools': ['Read']}]
