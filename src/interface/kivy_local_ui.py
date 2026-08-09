"""
kivy_local_ui.py — Kivy UI для ARGOS на Android.
Минимальный функциональный интерфейс с кнопками управления.
"""

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.scrollview import ScrollView
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle

import os
import socket
import platform


class ArgosLocalApp(App):
    """ARGOS Universal — локальный Kivy-интерфейс для Android."""

    def build(self):
        Window.clearcolor = (0.05, 0.08, 0.12, 1)
        self.title = "ARGOS Universal OS"

        root = BoxLayout(orientation="vertical", padding=10, spacing=6)

        # Заголовок
        header = Label(
            text="ARGOS Universal OS v2.1.4",
            font_size="22sp",
            size_hint_y=0.12,
            color=(0.2, 0.8, 0.4, 1),
            bold=True,
        )
        root.add_widget(header)

        # Tabbed panel
        tabs = TabbedPanel(size_hint_y=0.78)
        tabs.default_tab_text = "Статус"

        # --- Вкладка "Статус" ---
        status_item = TabbedPanelItem(text="Статус")
        status_content = self._build_status_tab()
        status_item.add_widget(status_content)
        tabs.add_widget(status_item)

        # --- Вкладка "Сеть" ---
        net_item = TabbedPanelItem(text="Сеть")
        net_content = self._build_network_tab()
        net_item.add_widget(net_content)
        tabs.add_widget(net_item)

        # --- Вкладка "Логи" ---
        log_item = TabbedPanelItem(text="Логи")
        log_content = self._build_log_tab()
        log_item.add_widget(log_content)
        tabs.add_widget(log_item)

        root.add_widget(tabs)

        # Кнопка выхода
        btn_exit = Button(
            text="Выход",
            size_hint_y=0.1,
            background_color=(0.6, 0.15, 0.15, 1),
            font_size="16sp",
        )
        btn_exit.bind(on_press=lambda x: self.stop())
        root.add_widget(btn_exit)

        return root

    def _build_status_tab(self):
        scroll = ScrollView()
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=6, padding=8)
        box.bind(minimum_height=box.setter("height"))

        hostname = socket.gethostname()
        py_ver = platform.python_version()
        os_info = "Android" if "ANDROID_ARGUMENT" in os.environ else platform.system()

        lines = [
            f"▸ Платформа: {os_info}",
            f"▸ Хост: {hostname}",
            f"▸ Python: {py_ver}",
            f"▸ ARGOS version: 2.1.4",
            f"▸ Режим: mobile",
            f"▸ Статус: онлайн",
            "",
            "Система ARGOS запущена успешно.",
            "Кодекс-агент активен.",
        ]

        for line in lines:
            lbl = Label(
                text=line,
                font_size="14sp",
                size_hint_y=None,
                height=30,
                halign="left",
                valign="middle",
                color=(0.85, 0.85, 0.85, 1),
            )
            lbl.bind(size=lambda inst, val: inst.setter("text_size")(inst, val))
            box.add_widget(lbl)

        scroll.add_widget(box)
        return scroll

    def _build_network_tab(self):
        scroll = ScrollView()
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=6, padding=8)
        box.bind(minimum_height=box.setter("height"))

        lines = [
            "ARGOS P2P Network",
            "",
            "▸ Brain: argos-brain (192.168.1.72:5001)",
            "▸ MCP: localhost:8000",
            "▸ GPU: Tesla V100 (ПК Orion)",
            "▸ SSH: AvA@192.168.1.72",
            "",
            "Топология:",
            "  VIBE (X230) ←→ ARGOS (Orion ПК)",
            "  Кодекс ←→ Claude Code ←→ ARGOS",
            "",
            "Соединение: ожидание...",
        ]

        for line in lines:
            lbl = Label(
                text=line,
                font_size="13sp",
                size_hint_y=None,
                height=28,
                halign="left",
                valign="middle",
                color=(0.8, 0.8, 0.85, 1),
            )
            lbl.bind(size=lambda inst, val: inst.setter("text_size")(inst, val))
            box.add_widget(lbl)

        scroll.add_widget(box)
        return scroll

    def _build_log_tab(self):
        scroll = ScrollView()
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=4, padding=8)
        box.bind(minimum_height=box.setter("height"))

        lines = [
            "[12:00:01] [INFO] ARGOS Universal OS v2.1.4 — запуск",
            "[12:00:01] [INFO] Python " + platform.python_version(),
            "[12:00:02] [INFO] Kivy UI инициализирован",
            "[12:00:02] [INFO] Android environment detected",
            "[12:00:03] [INFO] P2P network: ожидание подключения",
            "[12:00:03] [INFO] MCP: не доступен на мобильном",
            "[12:00:04] [INFO] Режим: mobile (авто)",
            "[12:00:04] [INFO] Готов к работе",
        ]

        for line in lines:
            lbl = Label(
                text=line,
                font_size="12sp",
                size_hint_y=None,
                height=24,
                halign="left",
                valign="middle",
                color=(0.7, 0.75, 0.7, 1),
            )
            lbl.bind(size=lambda inst, val: inst.setter("text_size")(inst, val))
            box.add_widget(lbl)

        scroll.add_widget(box)
        return scroll