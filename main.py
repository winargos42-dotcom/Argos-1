"""
main.py — ArgosUniversal OS v2.1.3
Оркестратор: запускает все подсистемы в правильном порядке.
Режимы: desktop | mobile | server
Флаги: --no-gui | --mobile | --root | --dashboard | --wake | --openai-tools-demo

ПАТЧИ (исправленные баги):
  [FIX-1] RootManager импортируется в начале файла (был NameError при --root)
  [FIX-2] Каждый шаг __init__ изолирован в try/except (частичный сбой не роняет всё)
  [FIX-3] boot_server использует threading.Event + signal.SIGTERM (graceful shutdown)
  [FIX-4] _start_telegram сохраняет ссылку на поток, tg=None при сбое
  [FIX-5] Режимы запуска разбираются через if/elif (нет конфликта флагов)
  [FIX-6] ArgosOrchestrator() и boot_*() обёрнуты в try/except с понятными сообщениями
  [FIX-7] Исправлен импорт db_init → src.db_init (ModuleNotFoundError на Windows)
  [FIX-8] KIVY_NO_ARGS=1 — Kivy больше не перехватывает --dashboard, --no-gui и др.
"""

import os
import sys

# === Android/Kivy detection ===
# На Android — делегируем в main_kivy.py (Kivy UI entry point)
if 'ANDROID_ARGUMENT' in os.environ or 'ANDROID_PRIVATE' in os.environ:
    _ROOT = os.path.dirname(os.path.abspath(__file__))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from main_kivy import main as _kivy_main
    _kivy_main()
    sys.exit(0)

import signal
import threading
import datetime
import uuid
import socket
import time
import urllib.request
import urllib.error
import subprocess

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_CONTROL_STOP_FILE = os.path.join(_PROJECT_ROOT, "data", "runtime", "argos.stop")


def _early_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on", "да")


def _early_env(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value not in (None, ""):
        return value
    env_path = os.path.join(_PROJECT_ROOT, ".env")
    try:
        with open(env_path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                raw = line.strip()
                if not raw or raw.startswith("#") or "=" not in raw:
                    continue
                key, val = raw.split("=", 1)
                if key.strip() == name:
                    return val.strip().strip('"').strip("'") or default
    except OSError:
        pass
    return default


def _early_server_mode() -> bool:
    argv = sys.argv[1:]
    return "--mobile" not in argv and "--desktop" not in argv


def _early_mcp_alive(host: str, port: int, timeout: float = 1.5) -> bool:
    check_host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    url = f"http://{check_host}:{port}/mcp"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= int(resp.status) < 500
    except urllib.error.HTTPError as err:
        return 200 <= int(err.code) < 500
    except Exception:
        return False


def _early_listener_owner_pid(port: int) -> int | None:
    try:
        import psutil

        for conn in psutil.net_connections(kind="inet"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr and int(conn.laddr.port) == int(port):
                return conn.pid
    except Exception:
        pass
    if os.name != "nt":
        return None
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"],
            cwd=_PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=5,
        )
        needle = f":{int(port)}"
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1].endswith(needle) and parts[3].upper() == "LISTENING":
                return int(parts[-1])
    except Exception:
        return None
    return None


def _early_kill_process_tree(pid: int) -> tuple[bool, str]:
    if pid == os.getpid():
        return False, "refuse to stop current process"
    try:
        import psutil

        proc = psutil.Process(pid)
        for child in proc.children(recursive=True):
            try:
                child.kill()
            except Exception as exc:
                last_error = f"child {child.pid}: {exc}"
            else:
                last_error = ""
        try:
            _, alive_children = psutil.wait_procs(proc.children(recursive=True), timeout=3)
            if alive_children:
                last_error = f"children still alive: {', '.join(str(p.pid) for p in alive_children[:5])}"
        except Exception:
            pass
        try:
            proc.kill()
            proc.wait(timeout=5)
            return True, "psutil kill ok"
        except psutil.NoSuchProcess:
            return True, "process already gone"
        except Exception as exc:
            last_error = str(exc)
            if "AccessDenied" in exc.__class__.__name__:
                last_error = f"access denied: {exc}"
            if last_error:
                pass
    except Exception as exc:
        last_error = str(exc)
    else:
        last_error = ""
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                cwd=_PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=10,
            )
            if result.returncode == 0:
                return True, "taskkill ok"
            detail = (result.stderr or result.stdout or "").strip()
            return False, detail or f"taskkill exit code {result.returncode}"
        except Exception as exc:
            return False, str(exc)
    try:
        os.kill(pid, signal.SIGTERM)
        return True, "SIGTERM sent"
    except Exception as exc:
        return False, str(exc)


def _early_wait_mcp_down(host: str, port: int, timeout_seconds: float = 15.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not _early_mcp_alive(host, port, timeout=0.5):
            return True
        time.sleep(0.5)
    return not _early_mcp_alive(host, port, timeout=0.5)


def _early_write_stop_request(reason: str) -> tuple[bool, str]:
    try:
        os.makedirs(os.path.dirname(_CONTROL_STOP_FILE), exist_ok=True)
        token = f"{time.time():.3f} pid={os.getpid()} reason={reason or 'control'}\n"
        with open(_CONTROL_STOP_FILE, "w", encoding="utf-8") as fh:
            fh.write(token)
        return True, _CONTROL_STOP_FILE
    except Exception as exc:
        return False, str(exc)


def _early_request_graceful_stop(host: str, port: int, timeout_seconds: float, reason: str) -> tuple[bool, str]:
    ok, detail = _early_write_stop_request(reason)
    if not ok:
        return False, detail
    if _early_wait_mcp_down(host, port, timeout_seconds=timeout_seconds):
        return True, "cooperative stop ok"
    return False, f"cooperative stop timeout via {detail}"


def _start_control_stop_watcher(stop_event: threading.Event, logger) -> None:
    """Watch a local stop-request file so restart works without forced taskkill."""
    start_ts = time.time()
    try:
        if os.path.exists(_CONTROL_STOP_FILE):
            os.remove(_CONTROL_STOP_FILE)
    except Exception:
        pass

    def _loop() -> None:
        while not stop_event.is_set():
            try:
                if os.path.exists(_CONTROL_STOP_FILE):
                    mtime = os.path.getmtime(_CONTROL_STOP_FILE)
                    if mtime >= start_ts - 1:
                        try:
                            os.remove(_CONTROL_STOP_FILE)
                        except Exception:
                            pass
                        logger.warning("[CONTROL] cooperative stop request received")
                        stop_event.set()
                        return
            except Exception as exc:
                logger.debug("[CONTROL] stop watcher error: %s", exc)
            time.sleep(1.0)

    threading.Thread(target=_loop, daemon=True, name="ArgosControlStopWatcher").start()


def _early_secondary_guard() -> None:
    if not _early_server_mode():
        return
    restart_requested = "--restart" in sys.argv or "--force-restart" in sys.argv
    stop_requested = "--stop" in sys.argv
    status_requested = "--status" in sys.argv
    control_flags = ("--restart", "--force-restart", "--stop", "--status")
    sys.argv[:] = [arg for arg in sys.argv if arg not in control_flags]
    if _early_truthy(_early_env("ARGOS_ALLOW_SECONDARY", "0")):
        return
    try:
        port = int(_early_env("ARGOS_MCP_PORT", "8000") or "8000")
    except ValueError:
        port = 8000
    host = _early_env("ARGOS_MCP_HOST", "0.0.0.0")
    owner_pid = _early_listener_owner_pid(port)
    http_alive = _early_mcp_alive(host, port)
    alive = bool(owner_pid) or http_alive
    web_port = _early_env("ARGOS_WEB_PORT", "8080")
    dash_port = _early_env("ARGOS_DASHBOARD_PORT", "8081")

    if status_requested:
        if alive:
            health = "ok" if http_alive else "port-listening/http-slow"
            print(
                "\n".join(
                    [
                        f"ARGOS работает: PID {owner_pid or 'unknown'}, MCP http://127.0.0.1:{port}/mcp ({health})",
                        f"Dashboard: http://127.0.0.1:{dash_port}/",
                        f"Web UI: http://127.0.0.1:{web_port}/",
                        "Перезапуск без админа: python main.py --restart",
                        "Остановка без админа: python main.py --stop",
                    ]
                )
            )
        else:
            print("ARGOS сейчас не запущен. Запуск: python main.py")
        raise SystemExit(0)

    if stop_requested:
        if not alive:
            print("ARGOS уже остановлен.")
            raise SystemExit(0)
        print(f"Останавливаю ARGOS PID {owner_pid or 'unknown'}...")
        soft_ok, soft_reason = _early_request_graceful_stop(host, port, timeout_seconds=20, reason="stop")
        if soft_ok:
            print("ARGOS остановлен мягко.")
            raise SystemExit(0)
        ok, reason = _early_kill_process_tree(owner_pid) if owner_pid else (False, "owner pid unknown")
        if ok and _early_wait_mcp_down(host, port, timeout_seconds=15):
            print("ARGOS остановлен.")
            raise SystemExit(0)
        print(f"Не удалось остановить ARGOS автоматически: {reason}; soft={soft_reason}", file=sys.stderr)
        print(
            "Порт MCP всё ещё занят. Закрой старое окно ARGOS/PowerShell или запусти эту же команду из того же elevated-контекста.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if not alive:
        return

    if restart_requested:
        print(f"Перезапуск ARGOS: останавливаю PID {owner_pid or 'unknown'} на MCP-порту {port}...")
        kill_reason = "owner pid unknown"
        soft_ok, soft_reason = _early_request_graceful_stop(host, port, timeout_seconds=10, reason="restart")
        if soft_ok:
            return
        if owner_pid:
            _, kill_reason = _early_kill_process_tree(owner_pid)
        if _early_wait_mcp_down(host, port, timeout_seconds=15):
            return
        print(f"ARGOS не остановился за 15 секунд; запуск отменён. Причина: {kill_reason}; soft={soft_reason}", file=sys.stderr)
        print(
            "Закрой старое окно ARGOS/PowerShell или запусти restart из того же elevated-контекста, где был стартован PID.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    else:
        print(
            "\n".join(
                [
                    f"ARGOS уже работает: PID {owner_pid or 'unknown'}, MCP порт {port}.",
                    f"Dashboard: http://127.0.0.1:{dash_port}/",
                    f"Web UI: http://127.0.0.1:{web_port}/",
                    "Это не падение: второй экземпляр не запускается, чтобы не сломать Telegram.",
                    "Перезапуск без админа: python main.py --restart",
                    "Статус: python main.py --status",
                ]
            ),
            file=sys.stderr,
        )
        raise SystemExit(0)


_early_secondary_guard()

# Прогрев нативных библиотек в ГЛАВНОМ потоке до загрузки skills.
# Параллельный импорт numpy/onnx/torch/etc из потоков skill_loader не thread-safe
# → "circular import NDArray" / segmentation fault. Импортируем заранее (в sys.modules).
for _mod in ("numpy", "numpy.typing", "scipy", "torch",
             "ctranslate2", "cv2", "faster_whisper", "opuslib_next",
             "sentence_transformers", "chromadb"):
    try:
        __import__(_mod)
    except Exception as _e:
        print(f"[warmup] {_mod}: {str(_e)[:60]}")
try:
    from numpy.typing import NDArray as _NDArray  # noqa
    import numpy as _np
    _ = _np.zeros(1)
except Exception:
    pass

# [FIX-8] РћС‚РєР»СЋС‡Р°РµРј РїРµСЂРµС…РІР°С‚ Р°СЂРіСѓРјРµРЅС‚РѕРІ РєРѕРјР°РЅРґРЅРѕР№ СЃС‚СЂРѕРєРё Kivy.
# Р‘РµР· СЌС‚РѕРіРѕ Kivy Р»РѕРІРёС‚ --dashboard, --no-gui Рё С‚.Рґ. Рё РїР°РґР°РµС‚ СЃ РѕС€РёР±РєРѕР№
# "option --dashboard not recognized". Р”РѕР»Р¶РЅРѕ Р±С‹С‚СЊ Р”Рћ Р»СЋР±РѕРіРѕ РёРјРїРѕСЂС‚Р° Kivy.
os.environ.setdefault("KIVY_NO_ARGS", "1")

# [FIX-10] РџСЂРёРЅСѓРґРёС‚РµР»СЊРЅРѕ РїРµСЂРµС…РѕРґРёРј РІ РґРёСЂРµРєС‚РѕСЂРёСЋ РїСЂРѕРµРєС‚Р°.
# Р‘РµР· СЌС‚РѕРіРѕ os.getcwd() Рё find_dotenv(usecwd=True) РјРѕРіСѓС‚ РІРµСЂРЅСѓС‚СЊ
# C:\Users\...\AppData\Local\Temp РёР»Рё Р»СЋР±РѕР№ РґСЂСѓРіРѕР№ CWD Р·Р°РїСѓСЃС‚РёРІС€РµРіРѕ РїСЂРѕС†РµСЃСЃР°,
# С‡С‚Рѕ Р»РѕРјР°РµС‚ РїРѕРёСЃРє .env, data/, src/ Рё Р»СЋР±С‹С… РѕС‚РЅРѕСЃРёС‚РµР»СЊРЅС‹С… РїСѓС‚РµР№.
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(_PROJECT_ROOT)
# Р”РѕР±Р°РІР»СЏРµРј РєРѕСЂРµРЅСЊ РїСЂРѕРµРєС‚Р° РІ sys.path, РµСЃР»Рё РµРіРѕ С‚Р°Рј РЅРµС‚
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# [FIX-9] РџРѕРґР°РІР»СЏРµРј РѕРєРЅРѕ Kivy РїСЂРё РЅРµ-mobile Р·Р°РїСѓСЃРєРµ
if "--mobile" not in sys.argv:
    os.environ.setdefault("KIVY_NO_ENV_CONFIG", "1")
    os.environ.setdefault("KIVY_HEADLESS", "1")

from dotenv import load_dotenv

# Р’СЃРµРіРґР° РіСЂСѓР·РёРј .env РёР· РїР°РїРєРё РїСЂРѕРµРєС‚Р° вЂ” CWD СѓР¶Рµ РїСЂР°РІРёР»СЊРЅС‹Р№ Р±Р»Р°РіРѕРґР°СЂСЏ FIX-10
_env_path = os.path.join(_PROJECT_ROOT, ".env")
load_dotenv(_env_path, override=True)

from src.argos_logger import get_logger
from src.launch_config import normalize_launch_args

log = get_logger("argos.main")


def _ensure_venv_bootstrap() -> bool:
    """
    РђРІС‚РѕРїРµСЂРµС…РѕРґ РІ .venv РїСЂРё РѕР±С‹С‡РЅРѕРј Р·Р°РїСѓСЃРєРµ Argos.
    Р’РѕР·РІСЂР°С‰Р°РµС‚ True, РµСЃР»Рё РІС‹РїРѕР»РЅРµРЅ re-exec РІ venv (С‚РµРєСѓС‰РёР№ РїСЂРѕС†РµСЃСЃ РґРѕР»Р¶РµРЅ Р·Р°РІРµСЂС€РёС‚СЊСЃСЏ).
    """
    enabled = os.getenv("ARGOS_AUTO_VENV", "on").strip().lower() in ("1", "on", "true", "yes", "РґР°")
    if not enabled:
        return False

    # РЈР¶Рµ РІРЅСѓС‚СЂРё РІРёСЂС‚СѓР°Р»СЊРЅРѕРіРѕ РѕРєСЂСѓР¶РµРЅРёСЏ
    if (getattr(sys, "base_prefix", sys.prefix) != sys.prefix) or os.getenv("VIRTUAL_ENV"):
        return False

    project_root = os.path.dirname(__file__)
    venv_dir = os.path.join(project_root, ".venv")
    if os.name == "nt":
        venv_python = os.path.join(venv_dir, "Scripts", "python.exe")
    else:
        venv_python = os.path.join(venv_dir, "bin", "python")

    try:
        if not os.path.exists(venv_python):
            log.info("[VENV] РЎРѕР·РґР°СЋ .venv...")
            subprocess.check_call([sys.executable, "-m", "venv", venv_dir], cwd=project_root)

        log.info("[VENV] РћР±РЅРѕРІР»СЏСЋ pip Рё Р·Р°РІРёСЃРёРјРѕСЃС‚Рё...")
        subprocess.check_call([venv_python, "-m", "pip", "install", "--upgrade", "pip"], cwd=project_root)
        subprocess.check_call([venv_python, "-m", "pip", "install", "-r", "requirements.txt"], cwd=project_root)

        install_arc = os.getenv("ARGOS_AUTO_ARC", "on").strip().lower() in ("1", "on", "true", "yes", "РґР°")
        if install_arc:
            try:
                # arc-agi v0.0.7 вЂ” РґР°С‚Р°СЃРµС‚-РїР°РєРµС‚ (ARC1/ARC2), arcengine С‚СЂРµР±СѓРµС‚ Python>=3.12
                # РЈСЃС‚Р°РЅР°РІР»РёРІР°РµРј С‚РѕР»СЊРєРѕ arc-agi; arcengine вЂ” РґР»СЏ .venv_arc (ARC-AGI-3 РёРіСЂРѕРІРѕР№ РґРІРёР¶РѕРє)
                subprocess.check_call([venv_python, "-m", "pip", "install", "arc-agi"], cwd=project_root)
            except Exception as e:
                # РќРµ СЂРѕРЅСЏРµРј Р·Р°РїСѓСЃРє Argos РёР·-Р·Р° РѕРїС†РёРѕРЅР°Р»СЊРЅРѕРіРѕ РїР°РєРµС‚Р°
                log.warning("[VENV] РќРµ СѓРґР°Р»РѕСЃСЊ СѓСЃС‚Р°РЅРѕРІРёС‚СЊ arc-agi: %s", e)

        log.info("[VENV] РџРµСЂРµР·Р°РїСѓСЃРє Argos РёР· .venv...")
        os.execv(venv_python, [venv_python, __file__, *sys.argv[1:]])
    except Exception as e:
        log.warning("[VENV] РђРІС‚РѕРїРµСЂРµС…РѕРґ РІ .venv РЅРµ РІС‹РїРѕР»РЅРµРЅ: %s", e)
        return False

    return True


def _mcp_http_alive(host: str, port: int, timeout: float = 5.0) -> bool:
    # 0.0.0.0 вЂ” СЌС‚Рѕ bind-Р°РґСЂРµСЃ, РЅРµ destination; РґР»СЏ РїСЂРѕРІРµСЂРєРё РёСЃРїРѕР»СЊР·СѓРµРј 127.0.0.1
    check_host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    url = f"http://{check_host}:{port}/mcp"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return int(getattr(resp, "status", 0)) == 200
    except Exception:
        return False


def _listener_owner_pid(port: int) -> int | None:
    try:
        import psutil

        for conn in psutil.net_connections(kind="inet"):
            if not conn.laddr or conn.status != psutil.CONN_LISTEN:
                continue
            if int(conn.laddr.port) == int(port):
                return conn.pid
    except Exception:
        return None
    return None


def _primary_runtime_already_active(host: str, port: int) -> tuple[bool, int | None]:
    """Return True when another ARGOS MCP endpoint is already healthy."""
    owner_pid = _listener_owner_pid(port)
    if owner_pid and owner_pid != os.getpid() and _mcp_http_alive(host, port, timeout=2.0):
        return True, owner_pid
    return False, owner_pid


def _start_mcp_with_guard(core, admin, host: str, port: int) -> bool:
    try:
        # Р”Р»СЏ РїСЂРѕРІРµСЂРєРё Р·Р°РЅСЏС‚РѕСЃС‚Рё РїРѕСЂС‚Р° РёСЃРїРѕР»СЊР·СѓРµРј 127.0.0.1 (0.0.0.0 РЅРµ connectable)
        check_host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            busy = s.connect_ex((check_host, port)) == 0
        owner_pid = _listener_owner_pid(port) if busy else None
        if busy and owner_pid and owner_pid != os.getpid():
            log.warning("[MCP] Port %d is occupied by another PID %s", port, owner_pid)
            return False
        if busy and _mcp_http_alive(host, port):
            return True
        if busy and not _mcp_http_alive(host, port):
            return False
        from src.mcp_api import start_mcp_api

        start_mcp_api(core=core, admin=admin, host=host, port=port)
        return True
    except Exception:
        return False


def _start_mcp_watchdog(core, admin, host: str, port: int):
    enabled = os.getenv("ARGOS_MCP_WATCHDOG", "on").strip().lower() in ("1", "on", "true", "yes", "РґР°")
    if not enabled:
        return None
    try:
        interval = max(15, int(os.getenv("ARGOS_MCP_WATCHDOG_INTERVAL", "30")))
    except ValueError:
        interval = 30

    def _loop():
        log.info("[MCP] Watchdog Р°РєС‚РёРІРµРЅ: check РєР°Р¶РґС‹Рµ %ss", interval)
        while True:
            try:
                if not _mcp_http_alive(host, port, timeout=5.0):
                    # Double-check: if port is busy but MCP temporarily slow, don't restart
                    check_host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.settimeout(1.0)
                        busy = s.connect_ex((check_host, port)) == 0
                    if busy:
                        # Port is busy but health check timed out — wait, don't restart
                        log.warning("[MCP] Watchdog: port %d занят, но health check медленный — пропускаю", port)
                        time.sleep(interval)
                        continue
                    ok = _start_mcp_with_guard(core, admin, host, port)
                    if ok:
                        log.info("[MCP] Watchdog: endpoint восстановлен на http://%s:%d/mcp", host, port)
                    else:
                        log.warning("[MCP] Watchdog: не удалось восстановить endpoint на %s:%d", host, port)
            except Exception as e:
                log.warning("[MCP] Watchdog error: %s", e)
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="ArgosMCPWatchdog")
    t.start()
    return t


def _start_entity_bus_sidecar():
    enabled = os.getenv("ARGOS_ENTITY_BUS_AUTOSTART", "1").strip().lower() in ("1", "on", "true", "yes", "да")
    if not enabled:
        return None
    try:
        lock_port = int(os.getenv("ARGOS_ENTITY_BUS_LOCK_PORT", "47283"))
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as lock_check:
            lock_check.settimeout(0.5)
            if lock_check.connect_ex(("127.0.0.1", lock_port)) == 0:
                log.info("[ENTITY BUS] Уже запущен на lock-порту %d, дубль не стартую", lock_port)
                return None
        entity_script = os.path.join(_PROJECT_ROOT, "src", "run_entities.py")
        if not os.path.exists(entity_script):
            log.warning("[ENTITY BUS] Скрипт не найден: %s", entity_script)
            return None
        logs_dir = os.path.join(_PROJECT_ROOT, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        out_path = os.path.join(logs_dir, "entity_bus.out.log")
        err_path = os.path.join(logs_dir, "entity_bus.err.log")
        configured_python = os.getenv("ARGOS_ENTITY_PYTHON", "").strip()
        py = configured_python if configured_python and os.path.exists(configured_python) else sys.executable
        out_fh = open(out_path, "a", encoding="utf-8")
        err_fh = open(err_path, "a", encoding="utf-8")
        proc = subprocess.Popen(
            [py, "-u", entity_script],
            cwd=_PROJECT_ROOT,
            stdout=out_fh,
            stderr=err_fh,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        log.info("[ENTITY BUS] Sidecar запущен (PID %d)", proc.pid)
        return proc
    except Exception as e:
        log.warning("[ENTITY BUS] Не удалось запустить sidecar: %s", e)
        return None


class ArgosAbsolute:
    """Р›С‘РіРєРёР№ РїСѓР±Р»РёС‡РЅС‹Р№ С„Р°СЃР°Рґ ARGOS, РЅРµ С‚СЂРµР±СѓСЋС‰РёР№ С‚СЏР¶С‘Р»С‹С… Р·Р°РІРёСЃРёРјРѕСЃС‚РµР№.

    РСЃРїРѕР»СЊР·СѓРµС‚СЃСЏ РІ status_report.py Рё telegram_bot.py РґР»СЏ Р±С‹СЃС‚СЂРѕР№
    РїСЂРѕРІРµСЂРєРё СЂР°Р±РѕС‚РѕСЃРїРѕСЃРѕР±РЅРѕСЃС‚Рё СЏРґСЂР° Р±РµР· РїРѕРґРЅСЏС‚РёСЏ РїРѕР»РЅРѕРіРѕ РѕСЂРєРµСЃС‚СЂР°С‚РѕСЂР°.
    """

    def __init__(self):
        self.version = "2.1.3"
        self.node_id = str(
            uuid.uuid5(uuid.NAMESPACE_DNS, os.uname().nodename if hasattr(os, "uname") else "argos")
        )
        self.start_time = datetime.datetime.now()

    def execute(self, cmd: str) -> str:
        cmd = cmd.lower().strip()
        if cmd == "status":
            uptime = datetime.datetime.now() - self.start_time
            return (
                f"OS: Argos v{self.version} | Status: ACTIVE | "
                f"Uptime: {uptime} | Node: {self.node_id}"
            )
        if cmd == "root":
            return "рџ›ЎпёЏ ROOT: ACCESS GRANTED"
        if cmd == "nfc":
            return "рџ“Ў NFC: РјРѕРґСѓР»СЊ Р°РєС‚РёРІРµРЅ"
        if cmd == "bt":
            return "рџ”µ BT: Bluetooth РІРєР»СЋС‡С‘РЅ"
        return f"[AI] Received: {cmd}"


# [FIX-7] РћР±С‘СЂС‚РєР°-СЃРѕРІРјРµСЃС‚РёРјРѕСЃС‚СЊ: Р·Р°РјРµРЅСЏРµС‚ ArgosDB() в†’ РІС‹Р·РѕРІ init_db()
class ArgosDB:
    """РЎРѕРІРјРµСЃС‚РёРјР°СЏ РѕР±С‘СЂС‚РєР° РЅР°Рґ src.db_init.init_db."""

    def __init__(self):
        from src.db_init import init_db as _init_db

        _init_db()


class ArgosOrchestrator:

    def __init__(self):
        import amd_gpu_patch  # noqa: F401
        from src.admin import ArgosAdmin
        from src.argos_integrator import ArgosIntegrator
        from src.connectivity.spatial import SpatialAwareness
        from src.core import ArgosCore
        from src.factory.flasher import AirFlasher
        from src.security.encryption import ArgosShield
        from src.security.git_guard import GitGuard
        from src.security.root_manager import RootManager

        log.info("в”Ѓ" * 48)
        log.info(" ARGOS UNIVERSAL OS v2.1.3 вЂ” BOOT")
        log.info("в”Ѓ" * 48)

        self._stop_event = threading.Event()

        # --- [FIX-2] РєР°Р¶РґС‹Р№ РЅРµРєСЂРёС‚РёС‡РЅС‹Р№ С€Р°Рі РёР·РѕР»РёСЂРѕРІР°РЅ ---

        # 1. Р‘РµР·РѕРїР°СЃРЅРѕСЃС‚СЊ
        try:
            GitGuard().check_security()
            self.shield = ArgosShield()
            log.info("[SHIELD] AES-256 Р°РєС‚РёРІРёСЂРѕРІР°РЅ")
        except Exception as e:
            log.warning("[SHIELD] РРЅРёС†РёР°Р»РёР·Р°С†РёСЏ Р·Р°С‰РёС‚С‹ СЃ РѕС€РёР±РєРѕР№: %s", e)
            self.shield = None

        # 2. РџСЂР°РІР°
        try:
            self.root = RootManager()
            log.info("[ROOT] %s", self.root.status().split("\n")[0])
        except Exception as e:
            log.warning("[ROOT] RootManager РЅРµРґРѕСЃС‚СѓРїРµРЅ: %s", e)
            self.root = None

        # 3. Р‘Р°Р·Р° РґР°РЅРЅС‹С…
        try:
            self.db = ArgosDB()
            log.info("[DB] SQLite ready в†’ data/argos.db")
        except Exception as e:
            log.error("[DB] РћС€РёР±РєР° РёРЅРёС†РёР°Р»РёР·Р°С†РёРё Р‘Р”: %s вЂ” СЂР°Р±РѕС‚Р°СЋ Р±РµР· РїРµСЂСЃРёСЃС‚РµРЅС‚РЅРѕСЃС‚Рё", e)
            self.db = None

        # 4. Р“РµРѕР»РѕРєР°С†РёСЏ
        try:
            self.spatial = SpatialAwareness(db=self.db)
            self.location = self.spatial.get_location()
            log.info("[GEO] %s", self.location)
        except Exception as e:
            log.warning("[GEO] Р“РµРѕР»РѕРєР°С†РёСЏ РЅРµРґРѕСЃС‚СѓРїРЅР°: %s", e)
            self.location = "РЅРµРёР·РІРµСЃС‚РЅРѕ"

        # 5. Admin + Flasher
        try:
            self.admin = ArgosAdmin()
            self.flasher = AirFlasher()
            log.info("[ADMIN] Р¤Р°Р№Р»РѕРІС‹Р№ РјРµРЅРµРґР¶РµСЂ Рё flasher РіРѕС‚РѕРІС‹")
        except Exception as e:
            log.warning("[ADMIN] РћС€РёР±РєР° РёРЅРёС†РёР°Р»РёР·Р°С†РёРё admin/flasher: %s", e)
            self.admin = None
            self.flasher = None

        # 6. РЇРґСЂРѕ
        try:
            self.core = ArgosCore()
            log.info("[CORE] ArgosCore РіРѕС‚РѕРІ")
        except Exception as e:
            log.error("[CORE] РљСЂРёС‚РёС‡РµСЃРєР°СЏ РѕС€РёР±РєР° СЏРґСЂР°: %s", e)
            raise

        # 6.2. [FIX-P2P-1] РђРІС‚Рѕ-СЃС‚Р°СЂС‚ P2P РїСЂРё Р·Р°РіСЂСѓР·РєРµ.
        # Р’ Р±Р°РЅРµСЂРµ Р±С‹Р»Рѕ "P2P: вќЊ", РїРѕС‚РѕРјСѓ С‡С‚Рѕ self.core.p2p РѕСЃС‚Р°РІР°Р»СЃСЏ None,
        # РїРѕРєР° РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ РЅРµ РІРІРµРґС‘С‚ РєРѕРјР°РЅРґСѓ "Р·Р°РїСѓСЃС‚Рё p2p". РўРµРїРµСЂСЊ РІРєР»СЋС‡Р°РµС‚СЃСЏ
        # РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ; РѕС‚РєР»СЋС‡РёС‚СЊ РјРѕР¶РЅРѕ ARGOS_P2P_AUTOSTART=0.
        if os.getenv("ARGOS_P2P_AUTOSTART", "1") == "1":
            try:
                result = self.core.start_p2p()
                log.info("[P2P] РђРІС‚РѕР·Р°РїСѓСЃРє: %s", str(result).splitlines()[0] if result else "ok")
            except Exception as e:
                log.warning("[P2P] РђРІС‚РѕР·Р°РїСѓСЃРє РЅРµ СѓРґР°Р»СЃСЏ (Р±Р°РЅРµСЂ РїРѕРєР°Р¶РµС‚ вќЊ): %s", e)
        else:
            log.info("[P2P] РђРІС‚РѕР·Р°РїСѓСЃРє РѕС‚РєР»СЋС‡С‘РЅ (ARGOS_P2P_AUTOSTART=0)")

        # 6.3. [HEADROOM] Запуск proxy + learn scheduler
        if os.getenv("ARGOS_HEADROOM_ENABLED", "1") == "1":
            try:
                from scripts.headroom.headroom_autostart import autostart as _headroom_start
                _hr_result = _headroom_start()
                log.info("[HEADROOM] %s", _hr_result)
            except Exception as _e:
                log.debug("[HEADROOM] autostart пропущен: %s", _e)

        # 6.5. [INTEGRATOR] РЈРЅРёС„РёС†РёСЂРѕРІР°РЅРЅР°СЏ РёРЅС‚РµРіСЂР°С†РёСЏ РїРѕРґСЃРёСЃС‚РµРј
        try:
            self.integrator = ArgosIntegrator(self.core)
            self.registry = self.integrator.integrate_all()
            log.info("[INTEGRATOR] РџРѕРґРєР»СЋС‡РµРЅРѕ РїРѕРґСЃРёСЃС‚РµРј: %d", len(self.registry))
        except Exception as e:
            log.warning("[INTEGRATOR] РћС€РёР±РєР° РёРЅС‚РµРіСЂР°С†РёРё: %s", e)
            self.integrator = None
            self.registry = {}

        # 6.7. [BRAIN] ARGOS AI Brain вЂ” Р°РІС‚РѕР·Р°РїСѓСЃРє + РєР»РёРµРЅС‚.
        # РџСЂРё ARGOS_BRAIN_ENABLED=1 СЃРЅР°С‡Р°Р»Р° РїСЂРѕР±СѓРµС‚ РїРѕРґРєР»СЋС‡РёС‚СЊСЃСЏ Рє СѓР¶Рµ Р·Р°РїСѓС‰РµРЅРЅРѕРјСѓ СЃРµСЂРІРµСЂСѓ.
        # Р•СЃР»Рё /health РЅРµ РѕС‚РІРµС‡Р°РµС‚ вЂ” Р·Р°РїСѓСЃРєР°РµС‚ argos_brain_api.py РІ С„РѕРЅРѕРІРѕРј РїСЂРѕС†РµСЃСЃРµ,
        # Р¶РґС‘С‚ 4 СЃРµРєСѓРЅРґС‹ Рё РїРѕРІС‚РѕСЂСЏРµС‚ РїРѕРїС‹С‚РєСѓ РїРѕРґРєР»СЋС‡РµРЅРёСЏ.
        self.brain = None
        self._brain_proc = None
        if os.getenv("ARGOS_BRAIN_ENABLED", "0") == "1":
            try:
                from argos_brain_examples import ARGOSBrainClient
                _brain_url = os.getenv("ARGOS_BRAIN_API_URL", "http://localhost:5001")
                _client = ARGOSBrainClient(_brain_url)

                def _start_brain_server():
                    """Р—Р°РїСѓСЃРєР°РµС‚ argos_brain_api.py РєР°Рє С„РѕРЅРѕРІС‹Р№ РїСЂРѕС†РµСЃСЃ."""
                    _brain_script = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)), "argos_brain_api.py"
                    )
                    if not os.path.exists(_brain_script):
                        log.warning("[BRAIN] argos_brain_api.py РЅРµ РЅР°Р№РґРµРЅ: %s", _brain_script)
                        return None
                    _venv_py = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)), ".venv",
                        "Scripts" if os.name == "nt" else "bin", "python"
                    )
                    _py = _venv_py if os.path.exists(_venv_py) else sys.executable
                    log.info("[BRAIN] Р—Р°РїСѓСЃРєР°СЋ Brain API: %s %s", _py, _brain_script)
                    try:
                        proc = subprocess.Popen(
                            [_py, _brain_script],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            cwd=os.path.dirname(os.path.abspath(__file__)),
                        )
                        return proc
                    except Exception as _pe:
                        log.warning("[BRAIN] РќРµ СѓРґР°Р»РѕСЃСЊ Р·Р°РїСѓСЃС‚РёС‚СЊ Brain: %s", _pe)
                        return None

                if _client.health_check():
                    self.brain = _client
                    log.info("[BRAIN] вњ… Brain API СѓР¶Рµ Р·Р°РїСѓС‰РµРЅ: %s", _brain_url)
                else:
                    log.info("[BRAIN] Brain API РЅРµ РѕС‚РІРµС‡Р°РµС‚ вЂ” Р·Р°РїСѓСЃРєР°СЋ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё...")
                    self._brain_proc = _start_brain_server()
                    if self._brain_proc:
                        time.sleep(4)  # Р¶РґС‘Рј РїРѕРєР° Flask РїРѕРґРЅРёРјРµС‚СЃСЏ
                        if _client.health_check():
                            self.brain = _client
                            log.info("[BRAIN] вњ… Brain API Р·Р°РїСѓС‰РµРЅ (PID %d): %s",
                                     self._brain_proc.pid, _brain_url)
                        else:
                            log.warning("[BRAIN] Brain API Р·Р°РїСѓС‰РµРЅ РЅРѕ /health РЅРµ РѕС‚РІРµС‡Р°РµС‚ вЂ” "
                                        "РїСЂРѕРІРµСЂСЊ РїРѕСЂС‚ %s", _brain_url)
            except Exception as e:
                log.warning("[BRAIN] РќРµ СѓРґР°Р»РѕСЃСЊ РёРЅРёС†РёР°Р»РёР·РёСЂРѕРІР°С‚СЊ РјРѕР·Рі: %s", e)
        else:
            log.info("[BRAIN] РћС‚РєР»СЋС‡С‘РЅ (ARGOS_BRAIN_ENABLED != 1)")

        # 6b. РџСЂРѕРіСЂРµРІ вЂ” GPU СЃРµСЂРІРµСЂС‹ РїСЂРёРѕСЂРёС‚РµС‚, Ollama fallback
        def _warmup_ollama():
            import time, urllib.request as _ur, json as _json
            _ai_mode = os.getenv("ARGOS_AI_MODE", "auto")

            # в”Ђв”Ђ РџСЂРѕРіСЂРµРІ GPU СЃРµСЂРІРµСЂРѕРІ (local-gpu mode) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
            _gpu_warmed = False
            if _ai_mode in ("local-gpu", "gpu", "lg", "auto"):
                _active_gpu_slots = []
                for _idx in ("0", "1", "2"):
                    _enabled = os.getenv(f"GPU_SERVER_{_idx}_ENABLED", "1").strip().lower()
                    if _enabled in ("0", "false", "off", "no", "нет", "выкл"):
                        continue
                    _gh = os.getenv(f"GPU_SERVER_{_idx}_HOST", "localhost")
                    _gp = os.getenv(f"GPU_SERVER_{_idx}_PORT", "")
                    _gn = os.getenv(f"GPU_SERVER_{_idx}_NAME", f"GPU{_idx}")
                    if _gp:
                        _active_gpu_slots.append(f"GPU{_idx} {_gn} ({_gh}:{_gp})")
                if _active_gpu_slots:
                    log.info("[WARMUP] Active GPU slots: %s", " | ".join(_active_gpu_slots))
                else:
                    log.warning("[WARMUP] Нет активных GPU_SERVER_* слотов для прогрева")
                for _idx in ("0", "1", "2"):
                    _enabled = os.getenv(f"GPU_SERVER_{_idx}_ENABLED", "1").strip().lower()
                    if _enabled in ("0", "false", "off", "no", "нет", "выкл"):
                        continue
                    _gh = os.getenv(f"GPU_SERVER_{_idx}_HOST", "localhost")
                    _gp = os.getenv(f"GPU_SERVER_{_idx}_PORT", "")
                    _gm = os.getenv(f"GPU_SERVER_{_idx}_MODEL", f"GPU{_idx}")
                    _gn = os.getenv(f"GPU_SERVER_{_idx}_NAME", f"GPU{_idx}")
                    if not _gp:
                        continue
                    try:
                        # Health check
                        _hreq = _ur.Request(f"http://{_gh}:{_gp}/health")
                        with _ur.urlopen(_hreq, timeout=3) as _hr:
                            if _hr.status != 200:
                                continue
                        # Warmup ping
                        _payload = _json.dumps({"prompt": "ping", "n_predict": 1, "stream": False}).encode()
                        _wreq = _ur.Request(
                            f"http://{_gh}:{_gp}/completion", data=_payload,
                            headers={"Content-Type": "application/json"}, method="POST",
                        )
                        with _ur.urlopen(_wreq, timeout=30):
                            pass
                        log.info("[WARMUP] вњ… GPU%s %s (%s) РіРѕС‚РѕРІ", _idx, _gn, _gm)
                        _gpu_warmed = True
                    except Exception as _e:
                        log.warning("[WARMUP] GPU%s (%s:%s) РЅРµ РѕС‚РІРµС‚РёР»: %s", _idx, _gh, _gp, _e)

            # в”Ђв”Ђ Ollama fallback (РµСЃР»Рё РЅРµ local-gpu РёР»Рё GPU СЃРµСЂРІРµСЂС‹ РЅРµРґРѕСЃС‚СѓРїРЅС‹) в”Ђ
            _ollama_enabled = os.getenv("OLLAMA_ENABLED", "true").strip().lower()
            _ollama_ok = _ollama_enabled not in ("0", "false", "no", "off")
            _skip_ollama_warmup_after_gpu = os.getenv("ARGOS_SKIP_OLLAMA_WARMUP_WHEN_GPU", "1").strip().lower()
            _skip_ollama_warmup_after_gpu = _skip_ollama_warmup_after_gpu not in ("0", "false", "no", "off")
            if _ollama_ok and (not _gpu_warmed or (_ai_mode not in ("local-gpu", "gpu", "lg") and not _skip_ollama_warmup_after_gpu)):
                try:
                    import requests as _req
                    _host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
                    _model = os.getenv("OLLAMA_MODEL", "tinyllama:latest")
                    log.info("[WARMUP] РџСЂРѕРіСЂРµРІ Ollama РјРѕРґРµР»Рё %s...", _model)
                    r = _req.post(
                        _host.rstrip("/") + "/api/generate",
                        json={"model": _model, "prompt": "ping", "stream": False,
                              "options": {"num_ctx": 512, "num_gpu": -1}},
                        timeout=60,  # 60s вЂ” С…РѕР»РѕРґРЅС‹Р№ СЃС‚Р°СЂС‚ llama3.2:1b ~35-45s
                    )
                    if r.ok:
                        log.info("[WARMUP] вњ… Ollama %s РіРѕС‚РѕРІР° Рє СЂР°Р±РѕС‚Рµ", _model)
                    else:
                        log.warning("[WARMUP] Ollama РІРµСЂРЅСѓР»Р° HTTP %s", r.status_code)
                except Exception as e:
                    log.warning("[WARMUP] Ollama РЅРµ РѕС‚РІРµС‚РёР»Р°: %s", e)
            elif _ollama_ok and _gpu_warmed and _skip_ollama_warmup_after_gpu:
                log.info("[WARMUP] Ollama warmup skipped: GPU llama-server is ready")
            elif not _ollama_ok:
                log.info("[WARMUP] Ollama РїСЂРѕРїСѓС‰РµРЅ (OLLAMA_ENABLED=false)")

        threading.Thread(target=_warmup_ollama, daemon=True, name="OllamaWarmup").start()

        # 6c. OpenClaw Gateway — автозапуск только если явно включен и не отключен флагом ARGOS_DISABLE_OPENCLAW
        _openclaw_enabled = os.getenv("OPENCLAW_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
        _openclaw_disabled = os.getenv("ARGOS_DISABLE_OPENCLAW", "").strip().lower() in ("1", "true", "yes", "on", "да", "вкл")
        if _openclaw_enabled and not _openclaw_disabled:
            def _start_openclaw_gateway():
                import shutil, subprocess as _sp, time as _t, requests as _req
                gw_url = os.getenv("OPENCLAW_BASE_URL", "http://localhost:47392")
                try:
                    r = _req.get(gw_url + "/health", timeout=3)
                    if r.ok:
                        log.info("[OpenClaw] Gateway СѓР¶Рµ Р·Р°РїСѓС‰РµРЅ: %s", gw_url)
                        return
                except Exception:
                    pass
                argoss_dir = os.path.dirname(os.path.abspath(__file__))
                gw_port_str = gw_url.rsplit(":", 1)[-1].split("/")[0]
                # РџСЂРёРѕСЂРёС‚РµС‚ 1: Р»РѕРєР°Р»СЊРЅС‹Р№ node_modules (СЃС‚Р°Р±РёР»СЊРЅРµРµ, РЅРµ Р·Р°РІРёСЃРёС‚ РѕС‚ PATH)
                _local_idx = os.path.join(argoss_dir, "node_modules", "openclaw", "dist", "index.js")
                _node = shutil.which("node") or shutil.which("node.exe")
                if os.path.exists(_local_idx) and _node:
                    log.info("[OpenClaw] Р—Р°РїСѓСЃРєР°СЋ Gateway (local): node dist/index.js gateway --port %s", gw_port_str)
                    proc = _sp.Popen(
                        [_node, _local_idx, "gateway", "--port", gw_port_str],
                        cwd=argoss_dir,
                        stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                    )
                else:
                    # РџСЂРёРѕСЂРёС‚РµС‚ 2: npx (РіР»РѕР±Р°Р»СЊРЅС‹Р№/PATH)
                    npx = shutil.which("npx")
                    if not npx:
                        log.warning("[OpenClaw] РЅРё node+local, РЅРё npx РЅРµ РЅР°Р№РґРµРЅС‹ вЂ” Gateway РЅРµ Р·Р°РїСѓС‰РµРЅ. "
                                    "РЈСЃС‚Р°РЅРѕРІРё: npm install (РІ РїР°РїРєРµ РїСЂРѕРµРєС‚Р°)")
                        return
                    log.info("[OpenClaw] Р—Р°РїСѓСЃРєР°СЋ Gateway (npx): npx openclaw gateway start --port %s", gw_port_str)
                    proc = _sp.Popen(
                        [npx, "openclaw", "gateway", "start", "--port", gw_port_str],
                        cwd=argoss_dir,
                        stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                    )
                _t.sleep(5)
                try:
                    r = _req.get(gw_url + "/health", timeout=3)
                    if r.ok:
                        log.info("[OpenClaw] Gateway Р·Р°РїСѓС‰РµРЅ (PID %d): %s", proc.pid, gw_url)
                    else:
                        log.warning("[OpenClaw] Gateway Р·Р°РїСѓС‰РµРЅ РЅРѕ /health РІРµСЂРЅСѓР» %s", r.status_code)
                except Exception as e:
                    log.warning("[OpenClaw] Gateway РЅРµ РѕС‚РІРµС‡Р°РµС‚: %s", e)
            threading.Thread(target=_start_openclaw_gateway, daemon=True, name="OpenClawGateway").start()
        elif _openclaw_disabled:
            log.info("[OpenClaw] disabled by ARGOS_DISABLE_OPENCLAW")

        # 6d. Pi Coding Agent вЂ” Р°РІС‚РѕР·Р°РїСѓСЃРє РµСЃР»Рё PI_ENABLED=true
        if os.getenv("PI_ENABLED", "true").strip().lower() in ("1", "true", "yes"):
            def _start_pi_agent():
                import shutil, subprocess as _sp, time as _t, requests as _req
                _pi_url = os.getenv("PI_BASE_URL", "http://localhost:18765")
                try:
                    r = _req.get(_pi_url + "/health", timeout=3)
                    if r.ok:
                        log.info("[Pi] Agent СѓР¶Рµ Р·Р°РїСѓС‰РµРЅ: %s", _pi_url)
                        return
                except Exception:
                    pass
                try:
                    import psutil

                    stale = []
                    for proc_info in psutil.process_iter(["pid", "cmdline"]):
                        try:
                            cmdline = " ".join(proc_info.info.get("cmdline") or []).lower()
                        except Exception:
                            continue
                        if "pi-coding-agent" in cmdline and " server" in cmdline:
                            stale.append(str(proc_info.info.get("pid")))
                    if stale:
                        log.warning(
                            "[Pi] Found existing pi-coding-agent process(es) without healthy %s: %s. "
                            "Not starting another copy.",
                            _pi_url,
                            ", ".join(stale[:8]),
                        )
                        return
                except Exception:
                    pass
                # РџСѓС‚СЊ Рє Pi
                _pi_cmd = os.getenv("PI_CMD", "pi")
                _pi_path = shutil.which(_pi_cmd) or _pi_cmd
                if not os.path.exists(_pi_path) and not shutil.which(_pi_cmd):
                    # Windows npm path
                    _win_pi = r"C:\Users\AvA\AppData\Roaming\npm\pi.cmd"
                    if os.path.exists(_win_pi):
                        _pi_path = _win_pi
                argoss_dir = os.path.dirname(os.path.abspath(__file__))
                log.info("[Pi] Р—Р°РїСѓСЃРєР°СЋ Pi Coding Agent...")
                # Р—Р°РїСѓСЃРє РІ СЂРµР¶РёРјРµ СЃРµСЂРІРµСЂР° (background)
                proc = _sp.Popen(
                    [_pi_path, "server"],
                    cwd=argoss_dir,
                    stdout=_sp.DEVNULL,
                    stderr=_sp.DEVNULL,
                    creationflags=_sp.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    env={**os.environ, "PI_CWD": argoss_dir},
                )
                _t.sleep(3)
                # РЎРѕС…СЂР°РЅСЏРµРј РїР°РјСЏС‚СЊ Pi
                _pi_mem_path = os.path.join(argoss_dir, "AGENTS.md")
                _now = _t.strftime("%Y-%m-%d %H:%M")
                _pi_mem = f"""\n## Pi Session вЂ” {_now}
- ARGOS: {os.getenv('ARGOS_VERSION', '2.1.3')}
- Mode: server
- PID: {proc.pid}
- URL: {_pi_url}
"""
                try:
                    with open(_pi_mem_path, "a", encoding="utf-8") as _f:
                        _f.write(_pi_mem)
                    log.info("[Pi] РџР°РјСЏС‚СЊ СЃРѕС…СЂР°РЅРµРЅР°: %s", _pi_mem_path)
                except Exception as _me:
                    log.warning("[Pi] РќРµ СѓРґР°Р»РѕСЃСЊ СЃРѕС…СЂР°РЅРёС‚СЊ РїР°РјСЏС‚СЊ: %s", _me)
                    try:
                        r = _req.get(_pi_url + "/health", timeout=5)
                        if r.ok:
                            log.info("[Pi] вњ… Pi Agent Р·Р°РїСѓС‰РµРЅ (PID %d): %s", proc.pid, _pi_url)
                        else:
                            log.warning("[Pi] Agent Р·Р°РїСѓС‰РµРЅ РЅРѕ /health РІРµСЂРЅСѓР» %s", r.status_code)
                    except Exception as _pe:
                        log.warning("[Pi] Agent РЅРµ РѕС‚РІРµС‡Р°РµС‚: %s", _pe)
                except Exception as _e:
                    log.warning("[Pi] РќРµ СѓРґР°Р»РѕСЃСЊ Р·Р°РїСѓСЃС‚РёС‚СЊ: %s", _e)
            threading.Thread(target=_start_pi_agent, daemon=True, name="PiAgent").start()
        else:
            log.info("[Pi] РћС‚РєР»СЋС‡С‘РЅ (PI_ENABLED != 1)")

        # 6e. Autonomous Entity Bus sidecar — отдельный контур сущностей/внешнего сознания.
        self.entity_bus_proc = _start_entity_bus_sidecar()

        # 7. Telegram
        self.tg = None  # [FIX-4]

    # --- [FIX-4] _start_telegram СЃРѕС…СЂР°РЅСЏРµС‚ СЃСЃС‹Р»РєСѓ РЅР° РїРѕС‚РѕРє ---
    def _start_telegram(self):
        try:
            from src.connectivity.telegram_bot import ArgosTelegram

            tg = ArgosTelegram(self.core, self.admin, self.flasher)
            ok, reason = tg.can_start()
            if not ok:
                log.warning("[TG] Telegram не запущен: %s", reason)
                self.tg = None
                return
            t = threading.Thread(target=tg.run, daemon=True, name="ArgosTelegram")
            t.start()
            self.tg = t
            self.tg_bot = tg
            log.info("[TG] Telegram Р±РѕС‚ Р·Р°РїСѓС‰РµРЅ")
        except Exception as e:
            log.warning("[TG] Telegram РЅРµРґРѕСЃС‚СѓРїРµРЅ: %s", e)
            self.tg = None

    def shutdown(self):
        log.info("РђСЂРіРѕСЃ Р·Р°РІРµСЂС€Р°РµС‚ СЂР°Р±РѕС‚Сѓ...")
        try:
            if self.core:
                if hasattr(self.core, "p2p") and self.core.p2p:
                    self.core.p2p.stop()
                if hasattr(self.core, "alerts") and self.core.alerts:
                    self.core.alerts.stop()
        except Exception as e:
            log.warning("РћС€РёР±РєР° РїСЂРё shutdown: %s", e)
        # РЎРѕС…СЂР°РЅСЏРµРј РїР°РјСЏС‚СЊ Pi РїСЂРё Р·Р°РІРµСЂС€РµРЅРёРё
        try:
            import time as _t
            argoss_dir = os.path.dirname(os.path.abspath(__file__))
            _pi_mem_path = os.path.join(argoss_dir, "AGENTS.md")
            _now = _t.strftime("%Y-%m-%d %H:%M")
            _pi_mem = f"\n## Pi Shutdown вЂ” {_now}\n"
            with open(_pi_mem_path, "a", encoding="utf-8") as _f:
                _f.write(_pi_mem)
            log.info("[Pi] РџР°РјСЏС‚СЊ СЃРѕС…СЂР°РЅРµРЅР° РїСЂРё shutdown")
        except Exception:
            pass

    def boot_desktop(self):
        # [FIX-GUI-KIVY] Desktop-СЂРµР¶РёРј РІСЃРµРіРґР° СЂР°Р±РѕС‚Р°РµС‚ С‚РѕР»СЊРєРѕ С‡РµСЂРµР· customtkinter.
        # РќР° desktop Р·Р°РїСЂРµС‰Р°РµРј fallback РЅР° Kivy, С‡С‚РѕР±С‹ РЅРµ РїРѕРґРЅРёРјР°Р»РёСЃСЊ Kivy/SDL Р»РѕРіРё Рё РѕРєРЅРѕ.
        try:
            from src.interface.gui import ArgosGUI
        except Exception as e:
            raise RuntimeError(
                "Desktop GUI С‚СЂРµР±СѓРµС‚ customtkinter. "
                "Kivy fallback РѕС‚РєР»СЋС‡РµРЅ. РЈСЃС‚Р°РЅРѕРІРё customtkinter РёР»Рё Р·Р°РїСѓСЃС‚Рё --mobile."
            ) from e

        self._start_telegram()

        try:
            from src.obsidian_mempalace_sync import get_sync
            _mp_sync = get_sync()
            _mp_sync.start()
        except Exception:
            pass

        is_root = self.root.is_root if self.root else False
        app = ArgosGUI(self.core, self.admin, self.flasher, self.location)
        app._append(
            f"рџ‘ЃпёЏ ARGOS UNIVERSAL OS v2.1.3\n"
            f"в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ\n"
            f"РЎРѕР·РґР°С‚РµР»СЊ: Р’СЃРµРІРѕР»РѕРґ\n"
            f"Р“РµРѕ: {self.location}\n"
            f"РџСЂР°РІР°: {'ROOT вњ…' if is_root else 'User вљ пёЏ'}\n"
            f"РР: {self.core.ai_mode_label()}\n"
            f"РџР°РјСЏС‚СЊ: {'вњ…' if self.core.memory else 'вќЊ'}\n"
            f"Vision: {'вњ…' if self.core.vision else 'вќЊ'}\n"
            f"РђР»РµСЂС‚С‹: {'вњ…' if self.core.alerts else 'вќЊ'}\n"
            f"P2P: {'вњ…' if self.core.p2p else 'вќЊ'}\n"
            f"в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ\n"
            f"РќР°РїРµС‡Р°С‚Р°Р№ 'РїРѕРјРѕС‰СЊ' РґР»СЏ СЃРїРёСЃРєР° РєРѕРјР°РЅРґ.\n\n",
            "#00FF88",
        )
        if "--wake" in sys.argv:
            ww = self.core.start_wake_word(self.admin, self.flasher)
            app._append(f"{ww}\n", "#00ffff")
        app.mainloop()

    def boot_mobile(self):
        from src.interface.mobile_ui import ArgosMobileUI

        ArgosMobileUI(core=self.core, admin=self.admin, flasher=self.flasher).run()

    def boot_shell(self):
        """РРЅС‚РµСЂР°РєС‚РёРІРЅР°СЏ РѕР±РѕР»РѕС‡РєР° Argos (Р·Р°РјРµРЅР° bash/cmd)."""
        log.info("[SHELL] Low-level REPL mode activated.")
        print("\n--- [ Argos System Shell ] ---\n")
        from src.interface.argos_shell import ArgosShell

        try:
            ArgosShell().cmdloop()
        except KeyboardInterrupt:
            print("\nShell terminated.")

    # --- [FIX-3] graceful shutdown С‡РµСЂРµР· threading.Event + SIGTERM ---
    def boot_server(self):
        log.info("[SERVER] Headless СЂРµР¶РёРј вЂ” С‚РѕР»СЊРєРѕ Telegram + P2P")
        _mcp_host = os.getenv("ARGOS_MCP_HOST", "0.0.0.0")
        _mcp_port = int(os.getenv("ARGOS_MCP_PORT", "8000") or "8000")
        _primary_alive, _primary_pid = _primary_runtime_already_active(_mcp_host, _mcp_port)
        if _primary_alive:
            log.warning(
                "[GUARD] Existing ARGOS MCP is alive on port %d (PID %s); secondary server exits before dashboard/TG",
                _mcp_port,
                _primary_pid,
            )
            return

        # Dashboard вЂ” РІСЃРµРіРґР° Р·Р°РїСѓСЃРєР°РµС‚СЃСЏ РІ server-СЂРµР¶РёРјРµ
        _dash_port = int(os.getenv("ARGOS_DASHBOARD_PORT", "8080") or "8080")
        try:
            from src.interface.web_engine import run_web_sync
            _dash_thread = threading.Thread(
                target=run_web_sync,
                kwargs={"core": self.core, "host": "0.0.0.0", "port": _dash_port},
                daemon=True,
                name="argos-dashboard",
            )
            _dash_thread.start()
            log.info("[SERVER] Dashboard: http://localhost:%d", _dash_port)
        except Exception as _e:
            log.warning("[SERVER] Dashboard РЅРµ Р·Р°РїСѓС‰РµРЅ: %s", _e)

        # Master Dashboard (web_server.py) вЂ” РµРґРёРЅР°СЏ С‚РѕС‡РєР° РІС…РѕРґР° РЅР° РїРѕСЂС‚Сѓ 18789
        _master_port = int(os.getenv("ARGOS_WEB_PORT", "18789") or "18789")
        _master_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.py")
        if os.path.exists(_master_script):
            try:
                _venv_py = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    ".venv", "Scripts" if os.name == "nt" else "bin", "python",
                )
                _py = _venv_py if os.path.exists(_venv_py) else sys.executable
                _master_proc = subprocess.Popen(
                    [_py, _master_script],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=os.path.dirname(os.path.abspath(__file__)),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                log.info("[WEB] Master Dashboard Р·Р°РїСѓС‰РµРЅ (PID %d): http://localhost:%d/",
                         _master_proc.pid, _master_port)
            except Exception as _we:
                log.warning("[WEB] РќРµ СѓРґР°Р»РѕСЃСЊ Р·Р°РїСѓСЃС‚РёС‚СЊ web_server.py: %s", _we)
        else:
            log.warning("[WEB] web_server.py РЅРµ РЅР°Р№РґРµРЅ, РїСЂРѕРїСѓСЃРєР°СЋ")

        # MCP API + watchdog
        _mcp_ok = _start_mcp_with_guard(self.core, self.admin, _mcp_host, _mcp_port)
        if _mcp_ok:
            log.info("[MCP] Endpoint active: http://%s:%d/mcp", _mcp_host, _mcp_port)
        else:
            log.warning("[MCP] Endpoint not started: port %d is busy or unavailable", _mcp_port)
        _start_mcp_watchdog(self.core, self.admin, _mcp_host, _mcp_port)

        self._start_telegram()

        try:
            from src.obsidian_mempalace_sync import get_sync
            _mp_sync = get_sync()
            _mp_sync.start()
            if _mp_sync._thread:
                log.info("[OBSIDIAN_MP] Obsidian→MemPalace sync active")
        except Exception as _mp_e:
            log.debug("[OBSIDIAN_MP] sync not started: %s", _mp_e)

        stop_event = threading.Event()

        def _handle_signal(signum, frame):
            log.info("РџРѕР»СѓС‡РµРЅ СЃРёРіРЅР°Р» %s вЂ” Р·Р°РІРµСЂС€Р°СЋ...", signum)
            stop_event.set()

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT,  _handle_signal)
        _start_control_stop_watcher(stop_event, log)

        log.info("[SERVER] Argos running. Press Ctrl+C to stop.")
        stop_event.wait()
        self.shutdown()


def main():
    argv = normalize_launch_args(sys.argv[1:])
    # argv вЂ” СЌС‚Рѕ list[str], РЅРµ dict
    if "--mobile" in argv:
        mode = "mobile"
    elif "--desktop" in argv:
        mode = "desktop"
    else:
        mode = "server"
    if mode == "server" and os.getenv("ARGOS_ALLOW_SECONDARY", "0").strip().lower() not in ("1", "true", "yes", "on"):
        _mcp_host = os.getenv("ARGOS_MCP_HOST", "0.0.0.0")
        _mcp_port = int(os.getenv("ARGOS_MCP_PORT", "8000") or "8000")
        _primary_alive, _primary_pid = _primary_runtime_already_active(_mcp_host, _mcp_port)
        if _primary_alive:
            log.warning(
                "[GUARD] Existing ARGOS MCP is alive on port %d (PID %s); secondary startup exits",
                _mcp_port,
                _primary_pid,
            )
            return
    orchestrator = ArgosOrchestrator()

    try:
        if mode == "desktop":
            orchestrator.boot_desktop()
        elif mode == "mobile":
            orchestrator.boot_mobile()
        else:
            orchestrator.boot_server()
    except Exception as e:
        log.error('Fatal startup error: %s', e)
        raise


if __name__ == '__main__':
    main()
