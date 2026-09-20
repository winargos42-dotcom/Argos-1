import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture(params=['src.telegram_bot', 'src.connectivity.telegram_bot'])
def bot(request, monkeypatch):
    for key in ('TELEGRAM_BOT_TOKEN', 'ADMIN_IDS', 'USER_IDS', 'BOT_IDS', 'USER_ID', 'TG_VOICE_REPLY'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('ADMIN_IDS', '1, 4')
    monkeypatch.setenv('USER_IDS', '2')
    monkeypatch.setenv('BOT_IDS', '3')
    core = NS(process_logic_async=AsyncMock(return_value={'answer': 'Проверенный ответ', 'state': 'ok'}),
              sensors=NS(get_full_report=Mock(return_value='CPU 12%')),
              quantum=NS(generate_state=Mock(return_value={'name': 'stable'})),
              ai_mode_label=Mock(return_value='offline'), p2p=None, db=None,
              skill_loader=NS(list_skills=Mock(return_value='skill-a')),
              memory=None, alerts=None, smart_sys=None, iot_bridge=None, vision=None,
              transcribe_audio_path=Mock(return_value='Привет'),
              replicator=NS(create_replica=Mock(return_value='replica-result')))
    async def inline_thread(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', inline_thread)
    module = importlib.import_module(request.param)
    return module.ArgosTelegram(core, NS(), NS())


def update(uid=1):
    return NS(effective_user=NS(id=uid, is_bot=uid == 3),
              message=NS(reply_text=AsyncMock(), reply_voice=AsyncMock(), reply_document=AsyncMock(),
                         text='Привет', voice=None, audio=None, photo=[], caption=''))


def run(awaitable):
    return asyncio.run(awaitable)


def replies(u):
    return '\n'.join(c.args[0] for c in u.message.reply_text.call_args_list)


@pytest.mark.parametrize('uid,role', [(1, 'admin'), (2, 'user'), (3, 'bot'), (99, None)])
def test_explicit_roles(bot, uid, role):
    u = update(uid)
    assert bot._get_role(u) == role
    assert bot._auth(u) == (role is not None)
    assert bot._is_admin(u) == (role == 'admin')
    u.effective_user = None
    assert bot._get_role(u) is None


@pytest.mark.parametrize('command', ['cmd_start', 'cmd_help', 'cmd_status', 'cmd_voice_on', 'cmd_voice_off',
    'cmd_voice_reply_toggle', 'cmd_roles', 'cmd_providers', 'cmd_skills', 'cmd_network', 'cmd_sync',
    'cmd_crypto', 'cmd_history', 'cmd_geo', 'cmd_memory', 'cmd_alerts', 'cmd_replicate', 'cmd_smart', 'cmd_iot', 'cmd_apk',
    'handle_voice', 'handle_photo', 'handle_audio'])
def test_unknown_sender_cannot_execute_commands(bot, command):
    u = update(99)
    run(getattr(bot, command)(u, NS()))
    assert '⛔' in replies(u)
    bot.core.process_logic_async.assert_not_called()
    bot.core.replicator.create_replica.assert_not_called()


@pytest.mark.parametrize('command', ['cmd_roles', 'cmd_replicate', 'cmd_apk'])
def test_user_cannot_administer(bot, command):
    u = update(2)
    run(getattr(bot, command)(u, NS()))
    assert 'администратор' in replies(u)
    bot.core.replicator.create_replica.assert_not_called()


@pytest.mark.parametrize('uid', [1, 2, 3])
def test_start_help_and_keyboards(bot, uid):
    u = update(uid)
    run(bot.cmd_start(u, NS()))
    markup = u.message.reply_text.call_args.kwargs['reply_markup']
    buttons = [b.text for row in markup.keyboard for b in row]
    assert '/status' in buttons
    assert ('/providers' in buttons) == (uid == 1)
    run(bot.cmd_help(u, NS()))
    assert '/help' in replies(u)


def test_status_truncates_and_survives_p2p_failure(bot):
    u = update()
    bot.core.p2p = NS(network_status=Mock(return_value='network-ready'))
    run(bot.cmd_status(u, NS()))
    assert all(x in replies(u) for x in ('CPU 12%', 'stable', 'offline', 'network-ready'))
    bot.core.p2p.network_status.side_effect = RuntimeError('offline')
    bot.core.sensors.get_full_report.return_value = 'x' * 5000
    run(bot.cmd_status(u, NS()))
    assert len(u.message.reply_text.call_args.args[0]) <= 4000


def test_voice_controls_change_state(bot):
    u = update()
    run(bot.cmd_voice_on(u, NS()))
    assert bot.core.voice_on and bot.voice_reply
    run(bot.cmd_voice_off(u, NS()))
    assert not bot.core.voice_on and not bot.voice_reply
    run(bot.cmd_voice_reply_toggle(u, NS()))
    assert bot.voice_reply and not bot.core.voice_on
    run(bot.cmd_roles(u, NS()))
    assert '1, 4' in replies(u)


@pytest.mark.parametrize('command,attribute,method,missing', [
    ('cmd_network', 'p2p', 'network_status', 'не запущен'),
    ('cmd_history', 'db', 'format_history', 'не подключена'),
    ('cmd_alerts', 'alerts', 'status', 'не активирована'),
    ('cmd_smart', 'smart_sys', 'full_status', 'не подключены'),
    ('cmd_iot', 'iot_bridge', 'status', 'не подключен')])
def test_reports_use_live_dependency_and_explain_absence(bot, command, attribute, method, missing):
    u = update()
    run(getattr(bot, command)(u, NS()))
    assert missing in replies(u)
    service = NS(**{method: Mock(return_value='synthetic-service-report')})
    setattr(bot.core, attribute, service)
    run(getattr(bot, command)(u, NS()))
    assert u.message.reply_text.call_args.args[0] == 'synthetic-service-report'
    getattr(service, method).assert_called_once()


def test_skills_sync_and_replication(bot):
    u = update()
    run(bot.cmd_skills(u, NS()))
    assert replies(u) == 'skill-a'
    run(bot.cmd_sync(u, NS()))
    assert 'не запущен' in replies(u)
    bot.core.p2p = NS(sync_skills_from_network=Mock(return_value='synced-one'))
    run(bot.cmd_sync(u, NS()))
    assert u.message.reply_text.call_args.args[0] == 'synced-one'
    run(bot.cmd_replicate(u, NS()))
    assert u.message.reply_text.call_args.args[0] == 'replica-result'
    bot.core.replicator.create_replica.side_effect = RuntimeError('replica-failed')
    run(bot.cmd_replicate(u, NS()))
    assert 'replica-failed' in u.message.reply_text.call_args.args[0]


def test_markdown_rejection_retries_plain_text(bot):
    u = update()
    u.message.reply_text.side_effect = [ValueError('entities'), None]
    run(bot._safe_reply_text(u.message, 'ю' * 5000))
    assert u.message.reply_text.await_count == 2
    assert u.message.reply_text.call_args.args[0] == 'ю' * 4000
    assert u.message.reply_text.call_args.kwargs['parse_mode'] is None


@pytest.mark.parametrize('kind', ['voice', 'audio', 'photo'])
@pytest.mark.parametrize('outcome', ['success', 'empty', 'failure'])
def test_media_pipeline_removes_temporary_input(bot, kind, outcome, monkeypatch):
    u = update()
    media = NS(file_id='synthetic-file', file_name='record.mp3')
    setattr(u.message, kind, [media] if kind == 'photo' else media)
    downloaded = []
    async def download(custom_path):
        downloaded.append(Path(custom_path))
        Path(custom_path).write_bytes(b'synthetic-media')
    ctx = NS(bot=NS(get_file=AsyncMock(return_value=NS(download_to_drive=download))))
    if kind == 'photo':
        bot.core.vision = NS(analyze_image=Mock(return_value='image-result'))
        service = bot.core.vision.analyze_image
    else:
        service = bot.core.transcribe_audio_path
    if outcome == 'empty':
        service.return_value = ''
    if outcome == 'failure':
        service.side_effect = RuntimeError('synthetic-failure')
    run(getattr(bot, 'handle_' + kind)(u, ctx))
    assert len(downloaded) == 1 and not downloaded[0].exists()
    service.assert_called_once()
    if outcome == 'success':
        assert ('image-result' if kind == 'photo' else 'Проверенный ответ') in replies(u)
    elif outcome == 'failure':
        assert 'synthetic-failure' in replies(u)
    else:
        bot.core.process_logic_async.assert_not_called()


@pytest.mark.parametrize('kind', ['voice', 'audio', 'photo'])
def test_missing_media_is_explicit(bot, kind):
    u = update()
    run(getattr(bot, 'handle_' + kind)(u, NS()))
    assert 'не обнаружен' in replies(u)


def test_audio_user_cannot_execute_transcribed_admin_command(bot):
    u = update(2)
    u.message.audio = NS(file_id='fake', file_name='fake.mp3')
    bot.core.transcribe_audio_path.return_value = 'консоль whoami'
    ctx = NS(bot=NS(get_file=AsyncMock(return_value=NS(download_to_drive=AsyncMock()))))
    run(bot.handle_audio(u, ctx))
    assert 'администратор' in replies(u)
    bot.core.process_logic_async.assert_not_called()


def test_apk_success_and_build_failure(bot, tmp_path, monkeypatch):
    u = update()
    apk = tmp_path / 'synthetic.apk'
    apk.write_bytes(b'apk')
    monkeypatch.setattr(bot, '_build_apk_sync', lambda: (True, str(apk)))
    run(bot.cmd_apk(u, NS()))
    assert u.message.reply_document.call_args.kwargs['filename'] == 'synthetic.apk'
    monkeypatch.setattr(bot, '_build_apk_sync', lambda: (False, 'build-failed'))
    run(bot.cmd_apk(u, NS()))
    assert 'build-failed' in replies(u)


@pytest.mark.parametrize('text,method,args,kwargs', [
    ('создай файл note.txt привет', 'create_file', ('note.txt', 'привет'), {}),
    ('напиши файл note.txt', 'create_file', ('note.txt', ''), {}),
    ('прочитай файл note.txt', 'read_file', ('note.txt',), {}),
    ('открой файл note.txt', 'read_file', ('note.txt',), {}),
    ('покажи файлы folder', 'list_dir', ('folder',), {}),
    ('список файлов', 'list_dir', ('.',), {}),
    ('удали файл note.txt', 'delete_item', ('note.txt',), {}),
    ('удали папку folder', 'delete_item', ('folder',), {}),
    ('добавь в файл note.txt новая строка', 'append_file', ('note.txt', 'новая строка'), {}),
    ('скопируй файл a.txt b.txt', 'copy_file', ('a.txt', 'b.txt'), {}),
    ('переименуй файл a.txt b.txt', 'rename_file', ('a.txt', 'b.txt'), {}),
    ('консоль echo synthetic', 'run_cmd', ('echo synthetic',), {'user': 'telegram'}),
    ('терминал echo synthetic', 'run_cmd', ('echo synthetic',), {'user': 'telegram'}),
    ('список процессов', 'list_processes', (), {}),
    ('статус системы', 'get_stats', (), {}),
    ('убей процесс synthetic', 'kill_process', ('synthetic',), {}),
])
def test_direct_command_dispatches_exact_arguments(monkeypatch, text, method, args, kwargs):
    module = importlib.import_module('src.connectivity.telegram_bot')
    adm = NS(**{method: Mock(return_value='verified-operation-result')})
    instance = module.ArgosTelegram(NS(), adm, None)
    assert instance._try_direct_execute(text) == 'verified-operation-result'
    getattr(adm, method).assert_called_once_with(*args, **kwargs)


@pytest.mark.parametrize('text', ['добавь в файл a.txt', 'скопируй файл a.txt', 'переименуй файл a.txt'])
def test_incomplete_file_command_reports_required_arguments(text):
    module = importlib.import_module('src.connectivity.telegram_bot')
    instance = module.ArgosTelegram(NS(), NS(), None)
    assert instance._try_direct_execute(text).startswith('Формат:')


def test_direct_unknown_does_not_invent_execution():
    module = importlib.import_module('src.connectivity.telegram_bot')
    instance = module.ArgosTelegram(NS(), NS(), None)
    assert instance._try_direct_execute('Расскажи о погоде') is None
    instance.admin = NS(read_file=Mock(side_effect=OSError('denied')))
    assert 'denied' in instance._try_direct_execute('прочитай файл note.txt')


@pytest.mark.parametrize('outcome', ['text', 'timeout', 'exception', 'blank', 'blocked'])
def test_legacy_text_conversation_outcomes(monkeypatch, outcome):
    module = importlib.import_module('src.telegram_bot')
    instance = module.ArgosTelegram(NS(process_logic_async=AsyncMock(return_value={'answer': 'ANSWER', 'state': 'verified'})), None, None)
    instance.admin_ids = {'1'}
    instance.user_ids = {'2'}
    instance.bot_ids = set()
    instance.voice_reply = False
    u = update(2 if outcome == 'blocked' else 1)
    if outcome == 'timeout':
        instance.core.process_logic_async.side_effect = asyncio.TimeoutError()
    elif outcome == 'exception':
        instance.core.process_logic_async.side_effect = RuntimeError('model-failed')
    elif outcome == 'blank':
        u.message.text = '  '
    elif outcome == 'blocked':
        u.message.text = 'консоль whoami'
    run(instance.handle_message(u, NS()))
    if outcome == 'text':
        instance.core.process_logic_async.assert_awaited_once_with('Привет', None, None)
        assert 'ANSWER' in replies(u) and 'verified' in replies(u)
    elif outcome == 'timeout':
        assert 'время ожидания' in replies(u)
    elif outcome == 'exception':
        assert 'model-failed' in replies(u)
    else:
        instance.core.process_logic_async.assert_not_called()
        assert ('администратор' in replies(u)) if outcome == 'blocked' else not replies(u)


@pytest.mark.parametrize('failure', [False, True])
def test_polling_conflict_stops_existing_application_once(bot, failure):
    app = NS(updater=NS(stop=AsyncMock()), stop=AsyncMock())
    if failure:
        app.updater.stop.side_effect = RuntimeError('already stopped')
    ctx = NS(error=RuntimeError('Conflict: terminated by other getUpdates request'), application=app)
    run(bot._handle_telegram_error(None, ctx))
    run(bot._handle_telegram_error(None, ctx))
    app.updater.stop.assert_awaited_once()
    app.stop.assert_awaited_once()


@pytest.mark.parametrize('result', ['available', 'exception'])
def test_provider_report(bot, monkeypatch, result):
    import sys
    provider = Mock(return_value='synthetic-provider-report')
    if result == 'exception':
        provider.side_effect = RuntimeError('provider-unavailable')
    monkeypatch.setitem(sys.modules, 'src.ai_providers', NS(providers_status=provider))
    u = update()
    run(bot.cmd_providers(u, NS()))
    assert ('provider-unavailable' if result == 'exception' else 'synthetic-provider-report') in replies(u)


def test_memory_combines_status_and_legacy_without_querying_real_data(bot, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'src.mempalace_bridge', NS(status=lambda: 'synthetic-palace'))
    bot.core.memory = NS(format_memory=Mock(return_value='synthetic-fact'))
    u = update()
    if type(bot).__module__ == 'src.connectivity.telegram_bot':
        run(bot.cmd_memory(u, NS(args=[])))
        assert replies(u) == 'synthetic-fact'
        return
    run(bot.cmd_memory(u, NS()))
    assert 'synthetic-palace' in replies(u) and 'synthetic-fact' in replies(u)
    bot.core.memory.format_memory.side_effect = RuntimeError('unavailable')
    run(bot.cmd_memory(u, NS()))
    assert u.message.reply_text.call_args.args[0] == 'synthetic-palace'
