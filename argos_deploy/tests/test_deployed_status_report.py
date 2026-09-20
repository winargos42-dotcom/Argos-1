import json
import subprocess
from types import SimpleNamespace

import pytest

from src import status_report as report


@pytest.mark.parametrize('flag', ['--json', '--md', None])
def test_critical_report_formats_and_output(flag, tmp_path, monkeypatch, capsys):
    section = report.Section('Synthetic health')
    for status in ('ok', 'warn', 'error', 'skip'):
        section.add(status, status, 'line one\nline two')
    monkeypatch.setattr(report, 'collect_report', lambda: [section])
    destination = tmp_path / 'report'
    monkeypatch.setattr(report.sys, 'argv', ['status', *([flag] if flag else []), '--out', str(destination)])
    assert report.main() == 1
    text = destination.read_text()
    assert 'Synthetic health' in text
    if flag == '--json':
        data = json.loads(text)
        assert data['summary'] == dict(ok=1, warn=1, error=1, skip=1, overall='error')
        assert data['sections'][0]['worst_status'] == 'error'
    elif flag == '--md':
        assert 'line one<br>line two' in text
    assert text in capsys.readouterr().out


def test_status_priority():
    section = report.Section('empty')
    assert section.worst == 'skip'
    for status in ('skip', 'ok', 'warn', 'error'):
        section.add(status, status)
        assert section.worst == status
    assert json.loads(report.format_json([], 'now'))['summary']['overall'] == 'ok'


@pytest.mark.parametrize('outcome,expected', [(FileNotFoundError(),127), (subprocess.TimeoutExpired('synthetic',1),-1), (None,7)])
def test_command_failures(monkeypatch, outcome, expected):
    def execute(*args, **kwargs):
        if outcome is not None:
            raise outcome
        return SimpleNamespace(returncode=7, stdout='out', stderr='err')
    monkeypatch.setattr(report.subprocess, 'run', execute)
    code, detail = report._run(['synthetic'])
    assert code == expected
    assert detail


def test_environment_never_discloses_credentials(monkeypatch):
    monkeypatch.setattr(report.shutil, 'disk_usage', lambda path: SimpleNamespace(free=499*1024*1024))
    assert report.check_environment().worst == 'warn'
    for name in ('TELEGRAM_TOKEN','OPENAI_API_KEY','GIT_TOKEN','GIT_USER','GIT_EMAIL'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('TELEGRAM_TOKEN', 'synthetic-secret-value')
    checks = report.check_env_vars().checks
    assert checks[0].status == 'ok'
    assert checks[1].status == 'warn'
    assert 'synthetic-secret-value' not in report.format_json([report.check_env_vars()], 'now')


def test_dependency_and_tool_states(monkeypatch):
    monkeypatch.setattr(report, '_pkg_version', lambda name: '1.2' if name == 'openai' else None)
    monkeypatch.setattr(report, '_has_module', lambda name: name == 'dotenv')
    checks = {c.name:c for c in report.check_python_dependencies().checks}
    assert [checks[n].status for n in ('openai','python-dotenv','aiogram','kivy')] == ['ok','warn','warn','skip']
    monkeypatch.setattr(report, '_run', lambda cmd: {'git':(127,''),'pip':(3,'broken'),'java':(0,'java synthetic')}.get(cmd[0],(127,'')))
    checks = {c.name:c for c in report.check_system_tools().checks}
    assert [checks[n].status for n in ('git','pip','java','adb')] == ['error','warn','ok','skip']


def test_fixture_files_and_yaml(monkeypatch, tmp_path):
    monkeypatch.setattr(report, 'REPO_ROOT', tmp_path)
    assert report.check_github_actions().worst == 'error'
    (tmp_path/'main.py').write_text('value = 1\n')
    checks = {c.name:c for c in report.check_core_files().checks}
    assert checks['main.py'].status == 'ok'
    assert checks['requirements.txt'].status == 'error'
    directory = tmp_path/'.github'/'workflows'
    directory.mkdir(parents=True)
    (directory/'build_apk.yml').write_text('name: synthetic\n')
    (directory/'invalid.yml').write_text('name: [\n')
    checks = {c.name:c for c in report.check_github_actions().checks}
    assert checks['build_apk.yml YAML'].status == 'ok'
    assert checks['invalid.yml YAML'].status == 'error'
    assert checks['auto_push.yml'].status == 'warn'


@pytest.mark.parametrize('source', [None, 'def broken(:\n'])
def test_missing_invalid_runtime_source(monkeypatch, tmp_path, source):
    monkeypatch.setattr(report, 'REPO_ROOT', tmp_path)
    if source is not None:
        (tmp_path/'main.py').write_text(source)
    assert report.check_argos_runtime().worst == 'error'


def test_git_missing_and_clean_repository(monkeypatch):
    monkeypatch.setattr(report, '_run', lambda command:(127,''))
    assert report.check_git().worst == 'error'
    outputs = iter([(0,'git synthetic'),(0,'main'),(0,'abc synthetic'),(0,''),(0,'fixture-origin')])
    monkeypatch.setattr(report, '_run', lambda command:next(outputs))
    section = report.check_git()
    assert section.worst == 'ok'
    assert section.checks[-1].detail == 'fixture-origin'
