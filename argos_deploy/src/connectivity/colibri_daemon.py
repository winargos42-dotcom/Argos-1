#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
colibri_daemon.py — Фоновый демон для узла Argos (Колибри).

ColibriDaemon запускает WhisperNode в фоновом потоке, управляет его
жизненным циклом и предоставляет статус.

Включает ColibriAsmEngine — движок ассемблирования/дизассемблирования
в режиме реального времени (Keystone + Capstone, мульти-арх).

Запуск:
  python colibri_daemon.py                    # запуск в переднем плане
  python colibri_daemon.py --light-mode       # лёгкий режим (только слушает)
  python colibri_daemon.py --node-id MyNode --port 5010
  python colibri_daemon.py --asm-watch src/asm/main.s --arch arm_thumb

Интегрируется с main.py через команды: colibri start / colibri stop / colibri status
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("argos.colibri")

# ── Keystone (ассемблер) ─────────────────────────────────────────────────
try:
    from keystone import (
        Ks,
        KS_ARCH_X86,
        KS_MODE_32,
        KS_MODE_64,
        KS_ARCH_ARM,
        KS_MODE_ARM,
        KS_MODE_THUMB,
        KS_ARCH_ARM64,
        KS_MODE_LITTLE_ENDIAN,
        KS_ARCH_MIPS,
        KS_MODE_MIPS32,
        KsError,
    )

    HAVE_KS = True
except ImportError:
    HAVE_KS = False
    KsError = Exception  # type: ignore[misc,assignment]

# ── Capstone (дизассемблер) ───────────────────────────────────────────────
try:
    import capstone as cs_mod

    HAVE_CS = True
except ImportError:
    cs_mod = None  # type: ignore[assignment]
    HAVE_CS = False

# ── Таблицы архитектур ───────────────────────────────────────────────────
_KS_ARCHS: dict[str, tuple] = {}
if HAVE_KS:
    _KS_ARCHS = {
        "x86": (KS_ARCH_X86, KS_MODE_32),
        "x86_64": (KS_ARCH_X86, KS_MODE_64),
        "arm": (KS_ARCH_ARM, KS_MODE_ARM),
        "arm_thumb": (KS_ARCH_ARM, KS_MODE_THUMB),
        "arm64": (KS_ARCH_ARM64, KS_MODE_LITTLE_ENDIAN),
        "mips": (KS_ARCH_MIPS, KS_MODE_MIPS32),
    }
    # AVR есть только в сборках Keystone из исходников, в PyPI 0.9.2 его нет
    import keystone as _ks_mod
    if hasattr(_ks_mod, "KS_ARCH_AVR"):
        _KS_ARCHS["avr"] = (_ks_mod.KS_ARCH_AVR, _ks_mod.KS_MODE_AVR32)

_CS_ARCHS: dict[str, tuple] = {}
if HAVE_CS:
    _CS_ARCHS = {
        "x86": (cs_mod.CS_ARCH_X86, cs_mod.CS_MODE_32),
        "x86_64": (cs_mod.CS_ARCH_X86, cs_mod.CS_MODE_64),
        "arm": (cs_mod.CS_ARCH_ARM, cs_mod.CS_MODE_ARM),
        "arm_thumb": (cs_mod.CS_ARCH_ARM, cs_mod.CS_MODE_THUMB),
        "arm64": (cs_mod.CS_ARCH_ARM64, cs_mod.CS_MODE_ARM),
        "mips": (cs_mod.CS_ARCH_MIPS, cs_mod.CS_MODE_MIPS32 | cs_mod.CS_MODE_BIG_ENDIAN),
    }
    # AVR удалён в Capstone 5.0 — проверяем наличие
    if hasattr(cs_mod, 'CS_ARCH_AVR'):
        _CS_ARCHS["avr"] = (cs_mod.CS_ARCH_AVR, cs_mod.CS_MODE_AVR)


# ══════════════════════════════════════════════════════════════════════════
# COLIBRI ASM ENGINE — Движок микрокода в режиме реального времени
# ══════════════════════════════════════════════════════════════════════════


class ColibriAsmEngine:
    """
    Ассемблирование и дизассемблирование микрокода в режиме реального времени.

    Возможности:
      • Немедленная компиляция ASM → машинный код (Keystone)
      • Немедленный дизассемблер bytes/hex → листинг (Capstone)
      • Watch-режим: слежение за .s/.asm файлом и авто-компиляция при изменении
      • Очередь заданий: поддерживает фоновые задания от ColibriDaemon
      • Мульти-арх: x86_64, ARM Thumb (Cortex-M), AVR, ARM64, MIPS

    Архитектуры для носимых (wearables):
      arm_thumb — STM32, nRF52, RP2040, SAMD21/51
      avr       — Arduino (ATmega328p и др.)
      arm64     — ESP32-S3, Raspberry Pi (64-bit)
    """

    def __init__(self, default_arch: str = "arm_thumb") -> None:
        self.default_arch = default_arch
        self._watch_thread: Optional[threading.Thread] = None
        self._watch_running = False
        self._job_queue: queue.Queue = queue.Queue()
        self._results: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._worker_thread: Optional[threading.Thread] = None

    # ── Ассемблирование ───────────────────────────────────────────────────
    def assemble(self, source: str, arch: str | None = None) -> dict:
        """
        Компилирует ASM-исходник в машинный код немедленно.

        Returns:
            {ok, arch, hex, bytes, count, listing, error}
        """
        arch = arch or self.default_arch
        out: dict = {
            "ok": False,
            "arch": arch,
            "hex": "",
            "bytes": b"",
            "count": 0,
            "listing": "",
            "error": "",
        }

        if not HAVE_KS:
            out["error"] = "Keystone не установлен: pip install keystone-engine"
            log.warning(out["error"])
            return out

        if arch not in _KS_ARCHS:
            out["error"] = (
                f"Архитектура '{arch}' не поддерживается Keystone.\n" f"Доступны: {list(_KS_ARCHS)}"
            )
            return out

        ks_arch, ks_mode = _KS_ARCHS[arch]
        try:
            ks = Ks(ks_arch, ks_mode)
            encoding, count = ks.asm(source)
            code = bytes(encoding)
            out.update(
                {
                    "ok": True,
                    "hex": code.hex(),
                    "bytes": code,
                    "count": count,
                    "listing": self._make_listing(source, code, arch),
                }
            )
            log.info("[ColibriAsm] %s: %d инструкций, %d байт", arch, count, len(code))
        except KsError as e:
            out["error"] = f"Ошибка ассемблирования ({arch}): {e}"
            log.error(out["error"])
        except Exception as e:
            out["error"] = str(e)
        return out

    def assemble_str(self, source: str, arch: str | None = None) -> str:
        """Текстовый результат ассемблирования для вывода пользователю."""
        r = self.assemble(source, arch)
        if not r["ok"]:
            return f"❌ {r['error']}"
        lines = [
            f"✅ Ассемблирование [{r['arch']}]:  {r['count']} инструкций  {len(r['bytes'])} байт",
            f"   HEX: {r['hex'][:64]}{'…' if len(r['hex']) > 64 else ''}",
        ]
        if r["listing"]:
            lines.append(r["listing"])
        return "\n".join(lines)

    # ── Дизассемблирование ────────────────────────────────────────────────
    def disassemble(self, code: bytes, arch: str | None = None, base_addr: int = 0) -> str:
        """Дизассемблирует байты через Capstone."""
        arch = arch or self.default_arch

        if not HAVE_CS:
            return "⚠️ Capstone не установлен: pip install capstone"

        if arch not in _CS_ARCHS:
            return f"⚠️ Архитектура '{arch}' не поддерживается Capstone."

        cs_arch, cs_mode = _CS_ARCHS[arch]
        try:
            md = cs_mod.Cs(cs_arch, cs_mode)
            md.detail = False
            lines = [f"; Дизассемблирование [{arch}]  {len(code)} байт\n"]
            for ins in md.disasm(code, base_addr):
                hx = " ".join(f"{b:02x}" for b in ins.bytes)
                lines.append(f"  0x{ins.address:08x}:  {hx:<24}  {ins.mnemonic} {ins.op_str}")
            return "\n".join(lines) if len(lines) > 1 else "; (нет инструкций)"
        except Exception as e:
            return f"❌ Ошибка дизассемблирования: {e}"

    def disassemble_hex(self, hex_str: str, arch: str | None = None) -> str:
        """Дизассемблирует hex-строку ('e0 2e 00 00' или 'e02e0000')."""
        try:
            code = bytes.fromhex(hex_str.replace(" ", "").replace("\n", ""))
            return self.disassemble(code, arch)
        except Exception as e:
            return f"❌ Некорректный hex: {e}"

    # ── Watch-режим: авто-компиляция при изменении файла ─────────────────
    def watch_file(self, path: str, arch: str | None = None, on_result=None) -> str:
        """
        Запускает слежение за файлом .s/.asm в реальном времени.
        При изменении — немедленно компилирует и вызывает on_result(result).

        Args:
            path:      путь к файлу ASM
            arch:      архитектура (default_arch если None)
            on_result: callback(dict) — вызывается при каждой компиляции

        Returns:
            str: статусное сообщение
        """
        if self._watch_running:
            return "⚠️ Watch уже активен. Останови командой: colibri asm stop-watch"

        fp = Path(path)
        if not fp.exists():
            return f"❌ Файл не найден: {path}"

        arch = arch or self.default_arch
        self._watch_running = True
        self._watch_thread = threading.Thread(
            target=self._watch_loop,
            args=(fp, arch, on_result),
            daemon=True,
            name="ColibriAsmWatch",
        )
        self._watch_thread.start()
        return (
            f"👁️ Watch запущен: {fp.name}  [{arch}]\n"
            f"  Изменяй файл — авто-компиляция в реальном времени."
        )

    def stop_watch(self) -> str:
        self._watch_running = False
        return "⏹️ ColibriAsmWatch остановлен"

    def _watch_loop(self, fp: Path, arch: str, on_result) -> None:
        last_mtime = 0.0
        log.info("[ColibriAsmWatch] Слежение за %s [%s]", fp, arch)
        while self._watch_running:
            try:
                mtime = fp.stat().st_mtime
                if mtime != last_mtime:
                    last_mtime = mtime
                    source = fp.read_text(encoding="utf-8")
                    result = self.assemble(source, arch)
                    result["file"] = str(fp)
                    result["ts"] = time.time()
                    with self._lock:
                        self._results["last"] = result
                    log.info(
                        "[ColibriAsmWatch] %s: ok=%s, %d байт",
                        fp.name,
                        result["ok"],
                        len(result.get("bytes", b"")),
                    )
                    if on_result:
                        try:
                            on_result(result)
                        except Exception as cb_e:
                            log.warning("on_result callback: %s", cb_e)
            except Exception as e:
                log.error("[ColibriAsmWatch] %s", e)
            time.sleep(0.5)

    # ── Фоновые задания ───────────────────────────────────────────────────
    def submit(self, job: dict) -> str:
        """
        Добавляет задание в очередь фоновой компиляции.
        job = {'id': 'job1', 'source': '...', 'arch': 'arm_thumb'}
        """
        self._job_queue.put(job)
        if not (self._worker_thread and self._worker_thread.is_alive()):
            self._worker_thread = threading.Thread(
                target=self._worker_loop, daemon=True, name="ColibriAsmWorker"
            )
            self._worker_thread.start()
        return f"📬 Задание '{job.get('id', '?')}' добавлено в очередь"

    def get_result(self, job_id: str) -> dict | None:
        with self._lock:
            return self._results.get(job_id)

    def _worker_loop(self) -> None:
        while True:
            try:
                job = self._job_queue.get(timeout=5)
            except queue.Empty:
                break
            job_id = job.get("id", f"job_{int(time.time())}")
            result = self.assemble(job.get("source", ""), job.get("arch"))
            result["id"] = job_id
            with self._lock:
                self._results[job_id] = result
            log.info("[ColibriAsmWorker] %s: ok=%s", job_id, result["ok"])
            self._job_queue.task_done()

    # ── Листинг ──────────────────────────────────────────────────────────
    def _make_listing(self, source: str, code: bytes, arch: str) -> str:
        hex_rows = [code[i : i + 16].hex(" ") for i in range(0, len(code), 16)]
        lines = ["; Машинный код (hex):"]
        lines += [f"  {r}" for r in hex_rows]
        if HAVE_CS and arch in _CS_ARCHS:
            lines.append("\n; Дизассемблирование:")
            lines.append(self.disassemble(code, arch))
        return "\n".join(lines)

    def status(self) -> str:
        arch_list = list(_KS_ARCHS) if HAVE_KS else []
        return (
            f"⚙️  ColibriAsmEngine:\n"
            f"  Keystone:    {'✅' if HAVE_KS else '❌ pip install keystone-engine'}\n"
            f"  Capstone:    {'✅' if HAVE_CS else '❌ pip install capstone'}\n"
            f"  Watch:       {'✅ активен' if self._watch_running else '⏹️ остановлен'}\n"
            f"  Arch по умол.: {self.default_arch}\n"
            f"  Доступные арх: {', '.join(arch_list) or 'нет (установи keystone)'}"
        )


class ColibriDaemon:
    """
    Фоновый сервис, управляющий WhisperNode.

    Методы:
      start()   — запускает узел в daemon-потоке
      stop()    — останавливает узел
      status()  — возвращает словарь с текущим состоянием
    """

    def __init__(
        self,
        node_id: Optional[str] = None,
        port: int = 5000,
        hidden_size: int = 5,
        light_mode: bool = False,
        enable_budding: bool = True,
        soil_search_interval: int = 60,
        work_dir: str = "data/colibri",
    ) -> None:
        self.node_id = node_id or f"Colibri-{_hostname()}"
        self.port = port
        self.hidden_size = hidden_size
        self.light_mode = light_mode
        self.enable_budding = enable_budding
        self.soil_search_interval = soil_search_interval
        self.work_dir = work_dir

        self._node = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        os.makedirs(work_dir, exist_ok=True)

        # Логирование в файл
        fh = logging.FileHandler(os.path.join(work_dir, "colibri.log"))
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
        log.addHandler(fh)

    # ── жизненный цикл ────────────────────────────────────────────────────

    def start(self) -> str:
        if self._running:
            return f"⚡ COLIBRI: узел {self.node_id} уже запущен"
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="ColibriThread")
        self._thread.start()
        log.info("Колибри запущен: node_id=%s, port=%d", self.node_id, self.port)
        return f"🐦 COLIBRI: узел {self.node_id} запущен на порту {self.port}"

    def stop(self) -> str:
        if not self._running:
            return "🐦 COLIBRI: узел не запущен"
        self._running = False
        if self._node:
            self._node.stop()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Колибри остановлен: %s", self.node_id)
        return f"🐦 COLIBRI: узел {self.node_id} остановлен"

    def status(self) -> dict:
        base = {
            "node_id": self.node_id,
            "port": self.port,
            "running": self._running,
            "light_mode": self.light_mode,
        }
        if self._node and self._running:
            try:
                base.update(self._node.get_status())
            except Exception:
                pass
        return base

    def status_str(self) -> str:
        return "🐦 COLIBRI:\n" + json.dumps(self.status(), indent=2, ensure_ascii=False)

    # ── внутренний запуск узла ────────────────────────────────────────────

    def _run(self) -> None:
        try:
            try:
                from whisper_node import WhisperNode
            except ImportError:
                from src.connectivity.whisper_node import WhisperNode
            self._node = WhisperNode(
                node_id=self.node_id,
                port=self.port,
                hidden_size=self.hidden_size,
                light_mode=self.light_mode,
                enable_budding=self.enable_budding,
                soil_search_interval=self.soil_search_interval,
            )
            self._node.start()
            while self._running:
                time.sleep(1)
        except ImportError as exc:
            log.error("whisper_node.py не найден: %s", exc)
            self._running = False
        except Exception as exc:
            log.exception("Ошибка в ColibriDaemon: %s", exc)
            self._running = False
        finally:
            if self._node:
                self._node.stop()


def _hostname() -> str:
    import socket

    try:
        return socket.gethostname()
    except Exception:
        return "local"


# ─────────────────────────────────────────────────────────────────────────────
# Глобальный синглтон (для main.py)
# ─────────────────────────────────────────────────────────────────────────────

_daemon: Optional[ColibriDaemon] = None


def colibri_start(
    node_id: Optional[str] = None,
    port: int = 5000,
    light_mode: bool = False,
) -> str:
    global _daemon
    if _daemon and _daemon._running:
        return f"🐦 COLIBRI: уже запущен ({_daemon.node_id})"
    _daemon = ColibriDaemon(node_id=node_id, port=port, light_mode=light_mode)
    return _daemon.start()


def colibri_stop() -> str:
    if not _daemon:
        return "🐦 COLIBRI: демон не создан"
    return _daemon.stop()


def colibri_status() -> str:
    if not _daemon:
        return "🐦 COLIBRI: демон не создан. Команда: colibri start"
    return _daemon.status_str()


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Argos ColibriDaemon")
    parser.add_argument("--node-id", default=None)
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--hidden-size", type=int, default=5)
    parser.add_argument("--light-mode", action="store_true")
    parser.add_argument("--no-budding", action="store_true")
    parser.add_argument("--work-dir", default="data/colibri")
    parser.add_argument("--pid-file", default="data/colibri/colibri.pid")
    parser.add_argument("--daemon", action="store_true", help="Запустить как Unix-демон")
    return parser.parse_args(argv)


def run_daemon_foreground(args: argparse.Namespace) -> None:
    daemon = ColibriDaemon(
        node_id=args.node_id,
        port=args.port,
        hidden_size=args.hidden_size,
        light_mode=args.light_mode,
        enable_budding=not args.no_budding,
        work_dir=args.work_dir,
    )
    print(daemon.start())

    def _sig(sig, _frame):
        print("\n" + daemon.stop())
        sys.exit(0)

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    print("Нажмите Ctrl+C для остановки.\n")
    try:
        while True:
            time.sleep(10)
            log.info(daemon.status_str())
    except KeyboardInterrupt:
        print(daemon.stop())


def run_daemon_background(args: argparse.Namespace) -> None:
    try:
        import daemon as _daemon
        from daemon import pidfile as _pidfile
    except ImportError:
        print("python-daemon не установлен. Установи: pip install python-daemon")
        sys.exit(1)

    context = _daemon.DaemonContext(
        working_directory=args.work_dir,
        pidfile=_pidfile.PIDLockFile(args.pid_file),
        umask=0o002,
        detach_process=True,
    )
    with context:
        daemon = ColibriDaemon(
            node_id=args.node_id,
            port=args.port,
            hidden_size=args.hidden_size,
            light_mode=args.light_mode,
            enable_budding=not args.no_budding,
            work_dir=args.work_dir,
        )
        daemon.start()
        signal.pause()


# ─────────────────────────────────────────────────────────────────────────────
# Точка входа
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    args = _parse_args()
    if args.daemon:
        run_daemon_background(args)
    else:
        run_daemon_foreground(args)
