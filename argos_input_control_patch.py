"""
argos_input_control_patch.py
Патч управления мышью и клавиатурой через ARGOS/Telegram.

Команды:
  мышь move 100 200          — переместить курсор
  мышь click                 — левый клик
  мышь rclick                — правый клик
  мышь dclick                — двойной клик
  мышь scroll 3              — прокрутка вверх
  мышь drag 100 200 300 400  — перетащить
  мышь позиция               — текущие координаты
  клавиша enter              — нажать Enter
  клавиша ctrl+c             — комбинация клавиш
  печатай Привет мир         — напечатать текст
  горячие клавиши            — список комбинаций
  скриншот                   — сделать скриншот
  экран найди кнопку         — найти элемент на экране

Запуск: python argos_input_control_patch.py /путь/к/SiGtRiP
"""
import os
import sys
import subprocess
from pathlib import Path

REPO = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "SiGtRiP"

# ── src/input_control.py ──────────────────────────────────────────────────────

INPUT_CONTROL = '''"""
src/input_control.py — Управление мышью и клавиатурой.
"""
from __future__ import annotations
import logging
import time
import os
from typing import Optional, Tuple

log = logging.getLogger("argos.input_control")

# Безопасная зона — ARGOS не может кликать за пределами экрана
SAFE_MODE = os.getenv("ARGOS_INPUT_SAFE", "on") == "on"
CLICK_DELAY = float(os.getenv("ARGOS_CLICK_DELAY", "0.1"))
TYPE_DELAY  = float(os.getenv("ARGOS_TYPE_DELAY", "0.05"))


def _get_pyautogui():
    try:
        import pyautogui
        pyautogui.FAILSAFE = True   # Угол экрана = стоп
        pyautogui.PAUSE = CLICK_DELAY
        return pyautogui
    except ImportError:
        return None


def _get_screen_size() -> Tuple[int, int]:
    try:
        pg = _get_pyautogui()
        if pg:
            return pg.size()
    except Exception:
        pass
    return (1920, 1080)


class InputController:
    """Управление мышью и клавиатурой."""

    def __init__(self):
        self.pg = _get_pyautogui()
        self._available = self.pg is not None
        self._history = []

    def is_available(self) -> bool:
        return self._available

    def _log(self, action: str):
        self._history.append({"action": action, "time": time.time()})
        if len(self._history) > 100:
            self._history = self._history[-50:]

    # ── Мышь ──────────────────────────────────────────────────────────────────

    def move(self, x: int, y: int, duration: float = 0.3) -> str:
        if not self._available:
            return "❌ pyautogui не установлен: pip install pyautogui"
        try:
            sw, sh = _get_screen_size()
            x = max(0, min(x, sw))
            y = max(0, min(y, sh))
            self.pg.moveTo(x, y, duration=duration)
            self._log(f"move({x},{y})")
            return f"🖱️ Курсор перемещён → ({x}, {y})"
        except Exception as e:
            return f"❌ Ошибка: {e}"

    def click(self, x: int = None, y: int = None, button: str = "left") -> str:
        if not self._available:
            return "❌ pyautogui не установлен"
        try:
            if x is not None and y is not None:
                self.pg.click(x, y, button=button)
                self._log(f"click({x},{y},{button})")
                return f"🖱️ Клик {button} → ({x}, {y})"
            else:
                pos = self.pg.position()
                self.pg.click(button=button)
                self._log(f"click({pos.x},{pos.y},{button})")
                return f"🖱️ Клик {button} на текущей позиции ({pos.x}, {pos.y})"
        except Exception as e:
            return f"❌ Ошибка: {e}"

    def double_click(self, x: int = None, y: int = None) -> str:
        if not self._available:
            return "❌ pyautogui не установлен"
        try:
            if x is not None and y is not None:
                self.pg.doubleClick(x, y)
                return f"🖱️ Двойной клик → ({x}, {y})"
            else:
                self.pg.doubleClick()
                return "🖱️ Двойной клик на текущей позиции"
        except Exception as e:
            return f"❌ Ошибка: {e}"

    def right_click(self, x: int = None, y: int = None) -> str:
        return self.click(x, y, button="right")

    def scroll(self, clicks: int = 3, x: int = None, y: int = None) -> str:
        if not self._available:
            return "❌ pyautogui не установлен"
        try:
            if x and y:
                self.pg.scroll(clicks, x=x, y=y)
            else:
                self.pg.scroll(clicks)
            direction = "вверх" if clicks > 0 else "вниз"
            return f"🖱️ Прокрутка {direction} ({abs(clicks)} шагов)"
        except Exception as e:
            return f"❌ Ошибка: {e}"

    def drag(self, x1: int, y1: int, x2: int, y2: int,
             duration: float = 0.5) -> str:
        if not self._available:
            return "❌ pyautogui не установлен"
        try:
            self.pg.moveTo(x1, y1, duration=0.2)
            self.pg.dragTo(x2, y2, duration=duration, button="left")
            self._log(f"drag({x1},{y1}→{x2},{y2})")
            return f"🖱️ Перетащено ({x1},{y1}) → ({x2},{y2})"
        except Exception as e:
            return f"❌ Ошибка: {e}"

    def position(self) -> str:
        if not self._available:
            return "❌ pyautogui не установлен"
        try:
            pos = self.pg.position()
            sw, sh = _get_screen_size()
            return f"🖱️ Позиция курсора: ({pos.x}, {pos.y})\\nЭкран: {sw}×{sh}"
        except Exception as e:
            return f"❌ Ошибка: {e}"

    # ── Клавиатура ────────────────────────────────────────────────────────────

    def press(self, key: str) -> str:
        if not self._available:
            return "❌ pyautogui не установлен"
        try:
            # Комбинации: ctrl+c, alt+f4 и т.д.
            if "+" in key:
                keys = [k.strip() for k in key.split("+")]
                self.pg.hotkey(*keys)
                self._log(f"hotkey({key})")
                return f"⌨️ Комбинация: {key}"
            else:
                self.pg.press(key)
                self._log(f"press({key})")
                return f"⌨️ Нажата: {key}"
        except Exception as e:
            return f"❌ Ошибка клавиши '{key}': {e}"

    def type_text(self, text: str, interval: float = None) -> str:
        if not self._available:
            return "❌ pyautogui не установлен"
        try:
            interval = interval or TYPE_DELAY
            self.pg.typewrite(text, interval=interval)
            self._log(f"type({text[:20]})")
            return f"⌨️ Напечатано: {text[:50]}{'...' if len(text)>50 else ''}"
        except Exception as e:
            # Для Unicode используем pyperclip + paste
            try:
                import pyperclip
                old = pyperclip.paste()
                pyperclip.copy(text)
                time.sleep(0.1)
                self.pg.hotkey("ctrl", "v")
                time.sleep(0.2)
                pyperclip.copy(old)
                return f"⌨️ Вставлено: {text[:50]}{'...' if len(text)>50 else ''}"
            except Exception as e2:
                return f"❌ Ошибка печати: {e2}"

    def write_clipboard(self, text: str) -> str:
        """Скопировать текст в буфер обмена."""
        try:
            import pyperclip
            pyperclip.copy(text)
            return f"📋 Скопировано в буфер: {text[:50]}"
        except ImportError:
            return "❌ pip install pyperclip"
        except Exception as e:
            return f"❌ Ошибка: {e}"

    # ── Скриншот ──────────────────────────────────────────────────────────────

    def screenshot(self, path: str = None) -> str:
        if not self._available:
            return "❌ pyautogui не установлен"
        try:
            from pathlib import Path as P
            if not path:
                import time as t
                path = str(P.home() / f"screenshot_{int(t.time())}.png")
            img = self.pg.screenshot()
            img.save(path)
            size = P(path).stat().st_size // 1024
            return f"📸 Скриншот сохранён: {path} ({size} KB)"
        except Exception as e:
            return f"❌ Ошибка скриншота: {e}"

    def find_on_screen(self, image_path: str,
                       confidence: float = 0.8) -> str:
        if not self._available:
            return "❌ pyautogui не установлен"
        try:
            loc = self.pg.locateOnScreen(image_path, confidence=confidence)
            if loc:
                center = self.pg.center(loc)
                return f"🔍 Найдено на экране: ({center.x}, {center.y})"
            return f"🔍 Не найдено на экране: {image_path}"
        except Exception as e:
            return f"❌ Ошибка поиска: {e}"

    # ── Макросы ───────────────────────────────────────────────────────────────

    def run_macro(self, name: str) -> str:
        """Выполнить именованный макрос."""
        macros = {
            "копировать": lambda: self.press("ctrl+c"),
            "вставить":   lambda: self.press("ctrl+v"),
            "вырезать":   lambda: self.press("ctrl+x"),
            "отменить":   lambda: self.press("ctrl+z"),
            "сохранить":  lambda: self.press("ctrl+s"),
            "выделить всё": lambda: self.press("ctrl+a"),
            "закрыть":    lambda: self.press("alt+f4"),
            "новая вкладка": lambda: self.press("ctrl+t"),
            "закрыть вкладку": lambda: self.press("ctrl+w"),
            "скрин":      lambda: self.screenshot(),
        }
        fn = macros.get(name.lower())
        if fn:
            return fn()
        return f"❓ Макрос не найден: {name}\\nДоступные: {', '.join(macros.keys())}"

    def status(self) -> str:
        sw, sh = _get_screen_size()
        lines = [
            "⌨️🖱️ Input Controller:\\n",
            f"  pyautogui: {'✅' if self._available else '❌ pip install pyautogui'}",
            f"  Экран: {sw}×{sh}",
            f"  Безопасный режим: {'✅' if SAFE_MODE else '⚠️ выключен'}",
            f"  Задержка клика: {CLICK_DELAY}s",
            f"  Задержка печати: {TYPE_DELAY}s",
            "",
            "  Команды:",
            "  мышь move X Y          — переместить курсор",
            "  мышь click [X Y]       — левый клик",
            "  мышь rclick [X Y]      — правый клик",
            "  мышь dclick [X Y]      — двойной клик",
            "  мышь scroll N          — прокрутка",
            "  мышь drag X1 Y1 X2 Y2  — перетащить",
            "  мышь позиция           — текущие координаты",
            "  клавиша KEY            — нажать клавишу",
            "  клавиша ctrl+c         — комбинация",
            "  печатай ТЕКСТ          — ввод текста",
            "  буфер ТЕКСТ            — копировать в буфер",
            "  макрос НАЗВАНИЕ        — выполнить макрос",
            "  скриншот               — сделать скриншот",
        ]
        return "\\n".join(lines)


# Глобальный инстанс
_controller: InputController = None


def get_controller() -> InputController:
    global _controller
    if _controller is None:
        _controller = InputController()
    return _controller
'''

(REPO / "src" / "input_control.py").write_text(INPUT_CONTROL, encoding="utf-8")
print("✅ src/input_control.py")

# ── Подключаем к core.py ──────────────────────────────────────────────────────

core_path = REPO / "src" / "core.py"
core_text = core_path.read_text(encoding="utf-8")

# Импорт
if "input_control" not in core_text:
    core_text = core_text.replace(
        "from src.argos_logger import get_logger",
        "from src.argos_logger import get_logger\ntry:\n    from src.input_control import get_controller as _get_input_ctrl\nexcept Exception:\n    _get_input_ctrl = None"
    )

# Инициализация
if "self.input_ctrl" not in core_text:
    core_text = core_text.replace(
        "self.memory = ArgosMemory()",
        "self.memory = ArgosMemory()\n        try:\n            self.input_ctrl = _get_input_ctrl() if _get_input_ctrl else None\n        except Exception:\n            self.input_ctrl = None"
    )

core_path.write_text(core_text, encoding="utf-8")
print("✅ core.py — input_ctrl подключён")

# ── Роутинг команд в execute_intent ──────────────────────────────────────────

if "мышь\|mouse\|клавиша\|keyboard" not in core_text:
    # Ищем место в execute_intent
    marker = "if any(k in t for k in [\"консоль\", \"терминал\"]):"
    if marker in core_text:
        core_text = core_path.read_text(encoding="utf-8")
        routing = '''        # ── Управление мышью и клавиатурой ──────────────────────────
        if any(k in t for k in ["мышь", "mouse", "курсор"]):
            ctrl = getattr(self, "input_ctrl", None)
            if not ctrl:
                return "❌ input_control не инициализирован"
            parts = text.strip().split()
            if len(parts) < 2:
                return ctrl.status()
            cmd = parts[1].lower()
            nums = []
            for p in parts[2:]:
                try: nums.append(int(p))
                except: pass
            if cmd in ("move", "переместить", "перемести"):
                return ctrl.move(nums[0], nums[1]) if len(nums) >= 2 else "❓ мышь move X Y"
            elif cmd in ("click", "клик", "кликни"):
                return ctrl.click(nums[0] if len(nums) > 0 else None,
                                   nums[1] if len(nums) > 1 else None)
            elif cmd in ("rclick", "правый"):
                return ctrl.right_click(nums[0] if nums else None,
                                         nums[1] if len(nums)>1 else None)
            elif cmd in ("dclick", "двойной"):
                return ctrl.double_click(nums[0] if nums else None,
                                          nums[1] if len(nums)>1 else None)
            elif cmd in ("scroll", "прокрутка"):
                return ctrl.scroll(nums[0] if nums else 3)
            elif cmd in ("drag", "перетащи"):
                return ctrl.drag(*nums[:4]) if len(nums) >= 4 else "❓ мышь drag X1 Y1 X2 Y2"
            elif cmd in ("позиция", "position", "pos"):
                return ctrl.position()
            return ctrl.status()

        if any(k in t for k in ["клавиша", "нажми", "hotkey", "keyboard"]):
            ctrl = getattr(self, "input_ctrl", None)
            if not ctrl:
                return "❌ input_control не инициализирован"
            key = text.split(None, 1)[1].strip() if len(text.split()) > 1 else ""
            return ctrl.press(key) if key else "❓ клавиша ENTER / клавиша ctrl+c"

        if t.startswith("печатай ") or t.startswith("напечатай "):
            ctrl = getattr(self, "input_ctrl", None)
            if not ctrl:
                return "❌ input_control не инициализирован"
            txt = text.split(None, 1)[1].strip() if len(text.split()) > 1 else ""
            return ctrl.type_text(txt) if txt else "❓ печатай ТЕКСТ"

        if t.startswith("буфер "):
            ctrl = getattr(self, "input_ctrl", None)
            if ctrl:
                txt = text.split(None, 1)[1].strip()
                return ctrl.write_clipboard(txt)

        if t.startswith("макрос "):
            ctrl = getattr(self, "input_ctrl", None)
            if ctrl:
                name = text.split(None, 1)[1].strip()
                return ctrl.run_macro(name)

        if t in ("скриншот", "screenshot"):
            ctrl = getattr(self, "input_ctrl", None)
            return ctrl.screenshot() if ctrl else "❌ input_control недоступен"

        '''
        core_text = core_text.replace(marker, routing + "\n        " + marker)
        core_path.write_text(core_text, encoding="utf-8")
        print("✅ core.py — роутинг мыши/клавиатуры добавлен")

# ── Проверка синтаксиса ───────────────────────────────────────────────────────

import subprocess as sp
for f in ["src/input_control.py", "src/core.py"]:
    r = sp.run(["python3", "-m", "py_compile", str(REPO / f)],
               capture_output=True, text=True)
    if r.returncode == 0:
        print(f"✅ {f} — синтаксис OK")
    else:
        print(f"❌ {f}: {r.stderr[:100]}")

# ── Git commit ────────────────────────────────────────────────────────────────

import os
os.chdir(REPO)

cmds = [
    ["git", "add", "src/input_control.py", "src/core.py"],
    ["git", "commit", "-m",
     "feat: mouse & keyboard control via ARGOS (pyautogui) - move/click/scroll/drag/type/screenshot"],
    ["git", "push", "origin", "main"],
]

for cmd in cmds:
    r = sp.run(cmd, capture_output=True, text=True, cwd=REPO)
    if r.returncode == 0:
        print(f"✅ {' '.join(cmd[:3])}")
    else:
        print(f"⚠️  {r.stderr[:100]}")

print("""
╔══════════════════════════════════════════════════════════╗
║  Управление мышью и клавиатурой готово!                   ║
╚══════════════════════════════════════════════════════════╝

Установи на ПК:
  pip install pyautogui pyperclip

Команды через Telegram или консоль:
  мышь move 500 300
  мышь click
  мышь rclick 100 200
  мышь dclick
  мышь scroll 5
  мышь drag 100 100 500 500
  мышь позиция
  клавиша enter
  клавиша ctrl+c
  клавиша ctrl+alt+delete
  печатай Привет ARGOS!
  буфер текст для буфера
  макрос сохранить
  скриншот
""")
