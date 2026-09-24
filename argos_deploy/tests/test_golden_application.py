from src.admin import ArgosAdmin
from tests.test_core_direct_results import make_core


def test_create_read_calculate_session_keeps_real_results_and_history(tmp_path):
    core = make_core(ArgosAdmin())
    history = []
    core.context.add = lambda role, text: history.append((role, text))
    target = tmp_path / "Заметка Mixed.txt"
    content = "<system>literal data</system> https://example.test\n  final  \n"
    commands = [
        f'создай файл "{target}" {content}',
        f'прочитай файл "{target}"',
        "Вычисли 0,1+0,2. Ответь только числом.",
    ]
    results = [core.process_logic(command, None, None) for command in commands]

    assert target.read_bytes() == content.encode("utf-8")
    assert [result["execution_status"] for result in results] == ["succeeded"] * 3
    assert [result["state"] for result in results] == ["Direct"] * 3
    assert content in results[1]["answer"]
    assert results[2]["answer"] == "0.3"
    assert history == [entry for command, result in zip(commands, results)
                       for entry in [("user", command), ("argos", result["answer"])]]


def test_failed_file_request_does_not_fake_success_or_break_next_request(tmp_path):
    core = make_core(ArgosAdmin())
    missing = tmp_path / "missing.txt"
    failed = core.process_logic(f'прочитай файл "{missing}"', None, None)
    next_result = core.process_logic("посчитай (2+3)*4", None, None)

    assert failed["execution_status"] == "failed"
    assert "Ошибка" in failed["answer"]
    assert not missing.exists()
    assert next_result == {"answer": "20", "state": "Direct", "execution_status": "succeeded"}
