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
import signal
import threading
import datetime
import uuid
import socket
import time
import urllib.request
import subprocess

# [FIX-8] Отключаем перехват аргументов командной строки Kivy.
# Без этого Kivy ловит --dashboard, --no-gui и т.д. и падает с ошибкой
# "option --dashboard not recognized". Должно быть ДО любого импорта Kivy.
os.environ.setdefault("KIVY_NO_ARGS", "1")

# [FIX-10] Принудительно переходим в директорию проекта.
# Без этого os.getcwd() и find_dotenv(usecwd=True) могут вернуть
# C:\Users\...\AppData\Local\Temp или любой другой CWD запустившего процесса,
# что ломает поиск .env, data/, src/ и любых относительных путей.
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(_PROJECT_ROOT)
# Добавляем корень проекта в sys.path, если его там нет
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# [FIX-9] Подавляем окно Kivy при не-mobile запуске
if "--mobile" not in sys.argv:
    os.environ.setdefault("KIVY_NO_ENV_CONFIG", "1")
    os.environ.setdefault("KIVY_HEADLESS", "1")

from dotenv import load_dotenv

# Всегда грузим .env из папки проекта — CWD уже правильный благодаря FIX-10
_env_path = os.path.join(_PROJECT_ROOT, ".env")
load_dotenv(_env_path, override=False)

from src.argos_logger import get_logger
from src.launch_config import normalize_launch_args

log = get_logger("argos.main")


def _ensure_venv_bootstrap() -> bool:
    """
    Автопереход в .venv при обычном запуске Argos.
    Возвращает True, если выполнен re-exec в venv (текущий процесс должен завершиться).
    """
    enabled = os.getenv("ARGOS_AUTO_VENV", "on").strip().lower() in ("1", "on", "true", "yes", "да")
    if not enabled:
        return False

    # Уже внутри виртуального окружения
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
            log.info("[VENV] Создаю .venv...")
            subprocess.check_call([sys.executable, "-m", "venv", venv_dir], cwd=project_root)

        log.info("[VENV] Обновляю pip и зависимости...")
        subprocess.check_call([venv_python, "-m", "pip", "install", "--upgrade", "pip"], cwd=project_root)
        subprocess.check_call([venv_python, "-m", "pip", "install", "-r", "requirements.txt"], cwd=project_root)

        install_arc = os.getenv("ARGOS_AUTO_ARC", "on").strip().lower() in ("1", "on", "true", "yes", "да")
        if install_arc:
            try:
                # arc-agi v0.0.7 — датасет-пакет (ARC1/ARC2), arcengine требует Python>=3.12
                # Устанавливаем только arc-agi; arcengine — для .venv_arc (ARC-AGI-3 игровой движок)
                subprocess.check_call([venv_python, "-m", "pip", "install", "arc-agi"], cwd=project_root)
            except Exception as e:
                # Не роняем запуск Argos из-за опционального пакета
                log.warning("[VENV] Не удалось установить arc-agi: %s", e)

        log.info("[VENV] Перезапуск Argos из .venv...")
        os.execv(venv_python, [venv_python, __file__, *sys.argv[1:]])
    except Exception as e:
        log.warning("[VENV] Автопереход в .venv не выполнен: %s", e)
        return False

    return True


def _mcp_http_alive(host: str, port: int, timeout: float = 1.5) -> bool:
    # 0.0.0.0 — это bind-адрес, не destination; для проверки используем 127.0.0.1
    check_host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    url = f"http://{check_host}:{port}/mcp"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return int(getattr(resp, "status", 0)) == 200
    except Exception:
        return False


def _start_mcp_with_guard(core, admin, host: str, port: int) -> bool:
    try:
        # Для проверки занятости порта используем 127.0.0.1 (0.0.0.0 не connectable)
        check_host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            busy = s.connect_ex((check_host, port)) == 0
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
    enabled = os.getenv("ARGOS_MCP_WATCHDOG", "on").strip().lower() in ("1", "on", "true", "yes", "да")
    if not enabled:
        return None
    try:
        interval = max(3, int(os.getenv("ARGOS_MCP_WATCHDOG_INTERVAL", "10")))
    except ValueError:
        interval = 10

    def _loop():
        log.info("[MCP] Watchdog активен: check каждые %ss", interval)
        while True:
            try:
                if not _mcp_http_alive(host, port):
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


class ArgosAbsolute:
    """Лёгкий публичный фасад ARGOS, не требующий тяжёлых зависимостей.

    Используется в status_report.py и telegram_bot.py для быстрой
    проверки работоспособности ядра без поднятия полного оркестратора.
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
            return "🛡️ ROOT: ACCESS GRANTED"
        if cmd == "nfc":
            return "📡 NFC: модуль активен"
        if cmd == "bt":
            return "🔵 BT: Bluetooth включён"
        return f"[AI] Received: {cmd}"


# [FIX-7] Обёртка-совместимость: заменяет ArgosDB() → вызов init_db()
class ArgosDB:
    """Совместимая обёртка над src.db_init.init_db."""

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

        log.info("━" * 48)
        log.info(" ARGOS UNIVERSAL OS v2.1.3 — BOOT")
        log.info("━" * 48)

        self._stop_event = threading.Event()

        # --- [FIX-2] каждый некритичный шаг изолирован ---

        # 1. Безопасность
        try:
            GitGuard().check_security()
            self.shield = ArgosShield()
            log.info("[SHIELD] AES-256 активирован")
        except Exception as e:
            log.warning("[SHIELD] Инициализация защиты с ошибкой: %s", e)
            self.shield = None

        # 2. Права
        try:
            self.root = RootManager()
            log.info("[ROOT] %s", self.root.status().split("\n")[0])
        except Exception as e:
            log.warning("[ROOT] RootManager недоступен: %s", e)
            self.root = None

        # 3. База данных
        try:
            self.db = ArgosDB()
            log.info("[DB] SQLite ready → data/argos.db")
        except Exception as e:
            log.error("[DB] Ошибка инициализации БД: %s — работаю без персистентности", e)
            self.db = None

        # 4. Геолокация
        try:
            self.spatial = SpatialAwareness(db=self.db)
            self.location = self.spatial.get_location()
            log.info("[GEO] %s", self.location)
        except Exception as e:
            log.warning("[GEO] Геолокация недоступна: %s", e)
            self.location = "неизвестно"

        # 5. Admin + Flasher
        try:
            self.admin = ArgosAdmin()
            self.flasher = AirFlasher()
            log.info("[ADMIN] Файловый менеджер и flasher готовы")
        except Exception as e:
            log.warning("[ADMIN] Ошибка инициализации admin/flasher: %s", e)
            self.admin = None
            self.flasher = None

        # 6. Ядро
        try:
            self.core = ArgosCore()
            log.info("[CORE] ArgosCore готов")
        except Exception as e:
            log.error("[CORE] Критическая ошибка ядра: %s", e)
            raise

        # 6.5. [INTEGRATOR] Унифицированная интеграция подсистем
        try:
            self.integrator = ArgosIntegrator(self.core)
            self.registry = self.integrator.integrate_all()
            log.info("[INTEGRATOR] Подключено подсистем: %d", len(self.registry))
        except Exception as e:
            log.warning("[INTEGRATOR] Ошибка интеграции: %s", e)
            self.integrator = None
            self.registry = {}

        # 7. Telegram
        self.tg = None  # [FIX-4]

    # --- [FIX-4] _start_telegram сохраняет ссылку на поток ---
    def _start_telegram(self):
        try:
            from src.connectivity.telegram_bot import ArgosTelegram

            tg = ArgosTelegram(self.core, self.admin, self.flasher)
            t = threading.Thread(target=tg.run, daemon=True, name="ArgosTelegram")
            t.start()
            self.tg = t
            log.info("[TG] Telegram бот запущен")
        except Exception as e:
            log.warning("[TG] Telegram недоступен: %s", e)
            self.tg = None

    def shutdown(self):
        log.info("Аргос завершает работу...")
        try:
            if self.core:
                if hasattr(self.core, "p2p") and self.core.p2p:
                    self.core.p2p.stop()
                if hasattr(self.core, "alerts") and self.core.alerts:
                    self.core.alerts.stop()
        except Exception as e:
            log.warning("Ошибка при shutdown: %s", e)

    def boot_desktop(self):
        # [FIX-GUI-KIVY] Desktop-режим всегда работает только через customtkinter.
        # На desktop запрещаем fallback на Kivy, чтобы не поднимались Kivy/SDL логи и окно.
        try:
            from src.interface.gui import ArgosGUI
        except Exception as e:
            raise RuntimeError(
                "Desktop GUI требует customtkinter. "
                "Kivy fallback отключен. Установи customtkinter или запусти --mobile."
            ) from e

        self._start_telegram()

        is_root = self.root.is_root if self.root else False
        app = ArgosGUI(self.core, self.admin, self.flasher, self.location)
        app._append(
            f"👁️ ARGOS UNIVERSAL OS v2.1.3\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Создатель: Всеволод\n"
            f"Гео: {self.location}\n"
            f"Права: {'ROOT ✅' if is_root else 'User ⚠️'}\n"
            f"ИИ: {self.core.ai_mode_label()}\n"
            f"Память: {'✅' if self.core.memory else '❌'}\n"
            f"Vision: {'✅' if self.core.vision else '❌'}\n"
            f"Алерты: {'✅' if self.core.alerts else '❌'}\n"
            f"P2P: {'✅' if self.core.p2p else '❌'}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Напечатай 'помощь' для списка команд.\n\n",
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
        """Интерактивная оболочка Argos (замена bash/cmd)."""
        log.info("[SHELL] Low-level REPL mode activated.")
        print("\n--- [ Argos System Shell ] ---\n")
        from src.interface.argos_shell import ArgosShell

        try:
            ArgosShell().cmdloop()
        except KeyboardInterrupt:
            print("\nShell terminated.")

    # --- [FIX-3] graceful shutdown через threading.Event + SIGTERM ---
    def boot_server(self):
        log.info("[SERVER] Headless режим — только Telegram + P2P")
        if "--dashboard" in sys.argv:
            log.info("[SERVER] Dashboard: http://localhost:8080")

        self._start_telegram()

        def _handle_signal(signum, frame):
            log.info("Получен сигнал %s — завершаю работу...", signum)
            self._stop_event.set()

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        log.info("[SERVER] Жду директив. Для остановки: CTRL+C или SIGTERM.")

        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=1.0)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()


# ══════════════════════════════════════════════════════════════
# ТОЧКА ВХОДА
# ══════════════════════════════════════════════════════════════
def main():
    sys.argv = normalize_launch_args(sys.argv)
    _ensure_venv_bootstrap()

    # Content API toggle (safe/free) для dashboard
    try:
        from src.content_api import start_content_api
        api_port = int(os.getenv("CONTENT_API_PORT", "5050"))
        start_content_api(port=api_port)
        log.info(f"[API] Content API запущен: http://127.0.0.1:{api_port}")
    except Exception as e:
        log.warning("[API] Content API не запущен: %s", e)

    # Стартуем win_bridge_host автоматически на Windows, если порт 5000 свободен
    if os.name == "nt":
        bridge_enabled = os.getenv("ARGOS_WIN_BRIDGE", "on").strip().lower() in ("1", "on", "true", "yes", "да")

        if not bridge_enabled:
            log.info("[BRIDGE] ARGOS_WIN_BRIDGE=off, пропуск win_bridge_host")
        else:
            def _port_free(port: int) -> bool:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.5)
                    return s.connect_ex(("127.0.0.1", port)) != 0
            if _port_free(5000):
                try:
                    subprocess.Popen(
                        [sys.executable, "win_bridge_host.py"],
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    log.info("[BRIDGE] win_bridge_host стартован на 5000")
                except Exception as e:
                    log.warning("[BRIDGE] Не удалось запустить win_bridge_host: %s", e)
            else:
                log.info("[BRIDGE] Порт 5000 занят, win_bridge_host не стартуем")

    if "--openai-tools-demo" in sys.argv:
        from src.openai_responses_tools import main as openai_tools_main

        demo_args = [arg for arg in sys.argv[1:] if arg != "--openai-tools-demo"]
        sys.exit(openai_tools_main(demo_args))

    # --- [FIX-6] оборачиваем создание оркестратора ---
    try:
        orch = ArgosOrchestrator()
    except Exception as e:
        print(f"[FATAL] Не удалось запустить ARGOS: {e}")
        sys.exit(1)

    # Автостарт MCP endpoint (http://127.0.0.1:8000/mcp)
    mcp_enabled = os.getenv("ARGOS_MCP_ENABLE", "on").strip().lower() in ("1", "on", "true", "yes", "да")
    if mcp_enabled:
        mcp_host = os.getenv("ARGOS_MCP_HOST", "127.0.0.1").strip() or "127.0.0.1"
        try:
            mcp_port = int(os.getenv("ARGOS_MCP_PORT", "8001"))
        except ValueError:
            mcp_port = 8001
        try:
            if _start_mcp_with_guard(orch.core, orch.admin, mcp_host, mcp_port):
                log.info("[MCP] Endpoint доступен: http://%s:%d/mcp", mcp_host, mcp_port)
            else:
                log.warning("[MCP] Не удалось запустить MCP endpoint на %s:%d", mcp_host, mcp_port)
            _start_mcp_watchdog(orch.core, orch.admin, mcp_host, mcp_port)
        except Exception as e:
            log.warning("[MCP] Ошибка MCP bootstrap: %s", e)

    # Dashboard (фоновый поток)
    if "--dashboard" in sys.argv:
        try:
            from src.interface.web_engine import ArgosWebEngine

            dash = ArgosWebEngine(orch.core)
            threading.Thread(target=dash.run, daemon=True, name="ArgosDashboard").start()
            log.info("[DASH] Веб-панель запущена: http://localhost:8080")
        except Exception as e:
            log.warning("[DASH] Dashboard недоступен: %s", e)

    # --- [FIX-5] режимы через if/elif ---
    try:
        if "--root" in sys.argv:
            if orch.root:
                print(orch.root.request_root())
            else:
                print("RootManager недоступен.")

        elif "--shell" in sys.argv:
            orch.boot_shell()

        elif "--mobile" in sys.argv:
            orch.boot_mobile()

        elif "--no-gui" in sys.argv:
            orch.boot_server()

     
        else:
            orch.boot_desktop()

    except Exception as e:
        log.error("[BOOT] Ошибка запуска: %s", e)
        orch.shutdown()
        sys.exit(1)


if __name__ == "__main__":
    main()
