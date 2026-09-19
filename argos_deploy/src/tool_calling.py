"""
tool_calling.py — ArgosToolCallingEngine
Реальное выполнение инструментов через прямой вызов функций.
НЕ использует LLM для выбора инструмента — сопоставление по ключевым словам.
"""

from __future__ import annotations
import os
import re
import requests
from src.ollama_input_policy import generate_payload, context_limit_message
from src.execution_outcome import classify_execution
from src.argos_logger import get_logger

log = get_logger("argos.tool_calling")


class ArgosToolCallingEngine:
    """
    Движок инструментов Аргоса.
    try_handle() сопоставляет текст с реальными функциями напрямую.
    Никаких LLM-вызовов внутри — только детерминированный роутинг.
    """

    def __init__(self, core):
        self.core = core

    def _plan_calls(
        self,
        query: str,
        context_text: str = "",
        previous_outputs: list | None = None,
    ) -> dict | None:
        """Запрашивает Ollama для планирования вызовов инструментов.

        Возвращает dict:
          {"confidence": float, "tool_calls": [...], "final_answer": str}
        или None при ошибке.
        """
        ollama_timeout = int(os.getenv("OLLAMA_TIMEOUT", "600"))
        ollama_url = getattr(self.core, "ollama_url", "http://localhost:11434/api/generate")
        ensure_fn = getattr(self.core, "_ensure_ollama_running", None)
        if callable(ensure_fn):
            ensure_fn()

        schemas_text = "\n".join(
            f"- {s['name']}: {s['description']}" for s in self.tool_schemas()
        )
        if not context_text:
            try:
                context_text = self.core.context.get_prompt_context(query)
            except Exception:
                try:
                    context_text = self.core.context.get_prompt_context()
                except Exception:
                    pass

        prev_str = ""
        if previous_outputs:
            import json as _json2
            prev_str = f"\nПредыдущие результаты: {_json2.dumps(previous_outputs, ensure_ascii=False)}"

        prompt = (
            f"Инструменты:\n{schemas_text}\n\n"
            f"Контекст: {context_text}{prev_str}\n\n"
            f"Запрос: {query}\n\n"
            "Ответь JSON-объектом: "
            '{"confidence": 0.0..1.0, "tool_calls": [{"name": "...", "arguments": {...}}], "final_answer": ""}. '
            "confidence >= 0.8 означает что можно дать финальный ответ."
        )
        try:
            resp = requests.post(
                ollama_url,
                json=generate_payload(os.getenv("OLLAMA_MODEL", "poilopr57/Argoss"), prompt),
                timeout=ollama_timeout,
            )
            overflow_message = context_limit_message(resp)
            if overflow_message:
                return {"error": "context_overflow", "message": overflow_message, "tool_calls": []}
            text = resp.json().get("response", "")
            import json as _json
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                return _json.loads(m.group(0))
        except Exception as e:
            log.debug("[ToolCalling] _plan_calls error: %s", e)
        return None

    def _execute_tool(
        self, name: str, arguments: dict, admin, flasher
    ) -> str:
        """Выполняет один вызов инструмента и возвращает строку-результат."""
        if admin is None:
            admin = getattr(self.core, "_internal_admin", None)
        try:
            if name in ("get_system_stats", "get_stats"):
                if admin and hasattr(admin, "get_stats"):
                    return self._tool_result(name, admin.get_stats())
                try:
                    from src.connectivity.system_health import format_full_report
                    return self._tool_result(name, format_full_report())
                except Exception:
                    return "stats unavailable"
            if name == "list_dir" and admin:
                return self._tool_result(name, admin.list_dir(arguments.get("path", ".")))
            if name == "read_file" and admin:
                return self._tool_result(name, admin.read_file(arguments.get("path", "")))
            if name == "create_file" and admin:
                return self._tool_result(name, admin.create_file(
                    arguments.get("path", "new.txt"),
                    arguments.get("content", ""),
                ))
            if name == "delete_item" and admin:
                return self._tool_result(name, admin.delete_item(arguments.get("path", "")))
            if name == "run_cmd" and admin:
                return self._tool_result(name, admin.run_cmd(arguments.get("cmd", ""), user="planner"))
            return f"[Tool {name}: not implemented]"
        except Exception as e:
            return f"[Tool {name} error: {e}]"

    @staticmethod
    def _tool_result(name: str, result: object) -> str:
        answer, status = classify_execution(result)
        if not answer.strip():
            return f"❌ Инструмент {name} не вернул подтверждения результата."
        if status == "failed" and not answer.startswith("❌"):
            return "❌ " + answer
        return answer

    def _synthesize_answer(self, text: str, outputs: list) -> str:
        """Синтезирует ответ из результатов выполненных инструментов."""
        if not outputs:
            return "Нет данных"
        parts = [f"[{o['tool']}]: {o['result']}" for o in outputs]
        return "\n".join(parts)

    def tool_schemas(self) -> list:
        return [
            {"name": "create_file", "description": "Создать файл", "trigger": "создай файл"},
            {"name": "read_file", "description": "Прочитать файл", "trigger": "прочитай файл"},
            {"name": "list_dir", "description": "Список файлов", "trigger": "покажи файлы"},
            {"name": "delete_item", "description": "Удалить файл", "trigger": "удали файл"},
            {"name": "run_cmd", "description": "Команда в терминале", "trigger": "консоль"},
            {"name": "get_stats", "description": "Статус системы", "trigger": "статус системы"},
        ]

    def try_handle(self, text: str, admin, flasher) -> str | None:
        """
        Возвращает строку-результат если команда распознана и выполнена,
        иначе None (тогда управление переходит к AI).
        """
        t = text.lower().strip()

        # Гарантируем admin
        if admin is None:
            admin = getattr(self.core, "_internal_admin", None)
        if admin is None:
            try:
                from src.admin import ArgosAdmin

                admin = ArgosAdmin()
                if hasattr(self.core, "_internal_admin"):
                    self.core._internal_admin = admin
            except Exception:
                pass

        if admin is None:
            return None  # не можем выполнить файловые команды

        # ── ФАЙЛЫ ─────────────────────────────────────────────────────────────
        if any(t.startswith(k) for k in ("создай файл", "напиши файл")):
            body = text
            for k in ("создай файл", "напиши файл"):
                body = body.replace(k, "").strip()
            parts = body.split(maxsplit=1)
            fname = parts[0] if parts else "note.txt"
            fcontent = parts[1] if len(parts) > 1 else ""
            log.info("tool: create_file(%s)", fname)
            return admin.create_file(fname, fcontent)

        if any(t.startswith(k) for k in ("прочитай файл", "открой файл")):
            path = text
            for k in ("прочитай файл", "открой файл"):
                path = path.replace(k, "").strip()
            log.info("tool: read_file(%s)", path)
            return admin.read_file(path)

        if any(t.startswith(k) for k in ("покажи файлы", "список файлов")):
            path = text
            for k in ("покажи файлы", "список файлов"):
                path = path.replace(k, "").strip()
            return admin.list_dir(path or ".")

        if t.startswith("файлы "):
            path = text[6:].strip()
            return admin.list_dir(path or ".")

        if any(t.startswith(k) for k in ("удали файл", "удали папку")):
            path = text
            for k in ("удали файл", "удали папку"):
                path = path.replace(k, "").strip()
            return admin.delete_item(path)

        if any(t.startswith(k) for k in ("добавь в файл", "допиши в файл", "дополни файл")):
            tail = text
            for k in ("добавь в файл", "допиши в файл", "дополни файл"):
                if k in t:
                    tail = text.split(k, 1)[-1].strip()
                    break
            parts = tail.split(maxsplit=1)
            if len(parts) >= 2:
                return admin.append_file(parts[0], parts[1])
            return "Формат: добавь в файл [путь] [текст]"

        if any(t.startswith(k) for k in ("отредактируй файл", "измени файл", "замени в файле")):
            tail = text
            for k in ("отредактируй файл", "измени файл", "замени в файле"):
                if k in t:
                    tail = text.split(k, 1)[-1].strip()
                    break
            parts = tail.split("→", 1) if "→" in tail else tail.split("->", 1)
            if len(parts) == 2:
                path_and_old = parts[0].strip().split(maxsplit=1)
                if len(path_and_old) == 2:
                    return admin.edit_file(path_and_old[0], path_and_old[1], parts[1].strip())
            return "Формат: отредактируй файл [путь] [старый текст] → [новый текст]"

        if t.startswith("скопируй файл"):
            tail = text.replace("скопируй файл", "").strip()
            parts = tail.split(maxsplit=1)
            if len(parts) == 2:
                return admin.copy_file(parts[0], parts[1])
            return "Формат: скопируй файл [откуда] [куда]"

        if t.startswith("переименуй файл"):
            tail = text.replace("переименуй файл", "").strip()
            parts = tail.split(maxsplit=1)
            if len(parts) == 2:
                return admin.rename_file(parts[0], parts[1])
            return "Формат: переименуй файл [старое] [новое]"

        # ── ТЕРМИНАЛ ──────────────────────────────────────────────────────────
        if t.startswith("консоль ") or t.startswith("терминал "):
            cmd = text.split(None, 1)[1].strip() if len(text.split()) > 1 else ""
            if cmd:
                return admin.run_cmd(cmd, user="telegram")
            return "Формат: консоль [команда]"

        # ── ПРОЦЕССЫ ──────────────────────────────────────────────────────────
        if t.startswith("список процессов"):
            return admin.list_processes()

        if any(t.startswith(k) for k in ("убей процесс", "завершить процесс")):
            name = text.split(None, 2)[-1].strip()
            return admin.kill_process(name) if name else "Укажи имя процесса"

        # ── СИСТЕМА ───────────────────────────────────────────────────────────
        if any(t.startswith(k) for k in ("статус системы", "чек-ап", "состояние здоровья")):
            try:
                from src.connectivity.system_health import format_full_report

                return format_full_report()
            except Exception:
                return admin.get_stats()

        # ── ПРОШИВКА / FIRMWARE ───────────────────────────────────────────────
        FLASH_TRIGGERS = (
            "прошивай", "прошить", "прошивка", "flash",
            "залить прошивку", "загрузить прошивку", "прошить esp",
            "начинай мигать", "мигать", "загружай прошивку",
        )
        if any(k in t for k in FLASH_TRIGGERS):
            import re as _re
            # Извлекаем порт (COM5, COM3, /dev/ttyUSB0 и т.п.)
            port_match = _re.search(r"(com\d+|/dev/tty\S+)", t)
            port = port_match.group(1).upper() if port_match else None
            # Извлекаем путь к файлу (.bin или .ino)
            file_match = _re.search(r"([a-zA-Z]:[/\\][^\s]+\.(bin|ino)|/[^\s]+\.(bin|ino))", text)
            fw_path = file_match.group(1) if file_match else None

            try:
                from src.firmware_builder import FirmwareBuilder
                fb = FirmwareBuilder()

                # Авто-определение порта если не указан
                if not port:
                    try:
                        import serial.tools.list_ports as _lp
                        ESP_KW = ["ch340", "ch341", "cp210", "usb-serial", "nodemcu"]
                        for _p in _lp.comports():
                            if any(k in (_p.description or "").lower() for k in ESP_KW):
                                port = _p.device
                                break
                        if not port:
                            ports = list(_lp.comports())
                            if ports:
                                port = ports[0].device
                    except Exception:
                        port = "COM5"

                if not port:
                    return "❌ ESP8266 не найден. Подключи устройство к USB."

                # Если .ino — компилируем через arduino-cli
                if fw_path and fw_path.endswith(".ino"):
                    import subprocess as _sp, os as _os
                    build_dir = _os.path.join(_os.path.dirname(fw_path), "build")
                    _os.makedirs(build_dir, exist_ok=True)
                    r = _sp.run(
                        ["arduino-cli", "compile", "--fqbn", "esp8266:esp8266:nodemcuv2",
                         "--output-dir", build_dir, fw_path],
                        capture_output=True, text=True
                    )
                    if r.returncode != 0:
                        return f"❌ Ошибка компиляции:\n{r.stderr[:500]}"
                    for f in _os.listdir(build_dir):
                        if f.endswith(".bin"):
                            fw_path = _os.path.join(build_dir, f)
                            break

                # Если файл не найден — ищем последний .bin в стандартных путях
                if not fw_path:
                    import os as _os
                    candidates = [
                        r"C:\ARGOS\firmware\argos_display\build",
                        r"C:\argoss\firmware",
                        _os.path.join(_os.path.dirname(__file__), "..", "firmware"),
                    ]
                    for cand in candidates:
                        if _os.path.isdir(cand):
                            for f in _os.listdir(cand):
                                if f.endswith(".bin"):
                                    fw_path = _os.path.join(cand, f)
                                    break
                        if fw_path:
                            break

                if not fw_path:
                    return (
                        f"⚠️ Порт {port} найден, но укажи файл прошивки.\n"
                        "Пример: прошивай COM5 C:\\путь\\к\\firmware.bin"
                    )

                result = fb.flash(fw_path, port=port, target="esp8266")
                return f"✅ Прошивка завершена!\nПорт: {port}\nФайл: {fw_path}\n\n{result}"

            except Exception as e:
                return f"❌ Ошибка прошивки: {e}"

        # -- IMAGE GENERATION -------------------------------------------------
        image_prefixes = (
            "нарисуй ",
            "сгенерируй изображение ",
            "создай изображение ",
            "generate image ",
            "draw ",
        )
        if any(t.startswith(p) for p in image_prefixes):
            prompt = text
            for p in image_prefixes:
                if t.startswith(p):
                    prompt = text[len(p):].strip()
                    break
            if not prompt:
                return "Укажи промпт. Пример: нарисуй futuristic ARGOS control center"
            try:
                from src.tools.image_generator import ArgosImageGenerator

                model_name = os.getenv("ARGOS_IMAGE_MODEL", "") or os.getenv("HF_TXT2IMG_MODEL", "")
                gen = ArgosImageGenerator(model_name=model_name or None)
                path = gen.generate(
                    prompt=prompt,
                    negative_prompt="blurry, low quality, text artifacts, deformed",
                    steps=24,
                    width=1024,
                    height=1024,
                )
                return f"🖼 Изображение создано: {path}"
            except Exception as e:
                return f"❌ Генерация изображения не удалась: {e}"

        # ── ПЛАНИРОВЩИК (мульти-шаговый) ─────────────────────────────────────
        ctx = ""
        try:
            ctx = self.core.context.get_prompt_context(text)
        except Exception:
            try:
                ctx = self.core.context.get_prompt_context()
            except Exception:
                pass

        outputs: list[dict] = []
        executed_keys: set = set()
        MAX_ROUNDS = 5

        for _ in range(MAX_ROUNDS):
            plan = self._plan_calls(text, context_text=ctx, previous_outputs=outputs)
            if plan is None:
                break
            if plan.get("error") == "context_overflow":
                message = plan["message"]
                if outputs:
                    return self._synthesize_answer(text, outputs) + "\n" + message
                return message
            tool_calls = plan.get("tool_calls") or []
            new_calls = False
            for call in tool_calls:
                call_name = call.get("name", "")
                call_args = call.get("arguments") or {}
                key = (call_name, str(sorted(call_args.items())))
                if key in executed_keys:
                    continue
                executed_keys.add(key)
                new_calls = True
                result = self._execute_tool(call_name, call_args, admin, flasher)
                outputs.append({"tool": call_name, "arguments": call_args, "result": result})
            if not new_calls:
                break

        if not outputs:
            return None  # Ничего не выполнено — пусть AI отвечает

        return self._synthesize_answer(text, outputs)
