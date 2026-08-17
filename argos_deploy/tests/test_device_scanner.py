"""
tests/test_device_scanner.py — Тесты автономного сканера и адаптивного сборщика
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from src.device_scanner import (
    DeviceScanner,
    AdaptiveImageBuilder,
    PROFILES,
    _GLOBAL_EXCLUDE,
)


# ── DeviceScanner ──────────────────────────────────────────────────────────

class TestDeviceScanner:
    def test_scan_returns_required_keys(self):
        s = DeviceScanner()
        info = s.scan()
        for key in ("os", "cpu", "ram", "storage", "network",
                    "peripherals", "tools", "packages", "firmware", "profile"):
            assert key in info, f"Ключ '{key}' отсутствует в scan()"

    def test_scan_os_has_system(self):
        s = DeviceScanner()
        info = s.scan()
        assert "system" in info["os"]
        assert info["os"]["system"] in ("Linux", "Windows", "Darwin", "Android")

    def test_scan_cpu_has_arch_and_cores(self):
        s = DeviceScanner()
        info = s.scan()
        assert "arch" in info["cpu"]
        assert "cores" in info["cpu"]
        assert info["cpu"]["cores"] >= 1

    def test_scan_ram_non_negative(self):
        s = DeviceScanner()
        info = s.scan()
        assert info["ram"]["total_mb"] >= 0
        assert info["ram"]["available_mb"] >= 0

    def test_scan_profile_valid_key(self):
        s = DeviceScanner()
        info = s.scan()
        assert info["profile"]["key"] in PROFILES

    def test_report_contains_hostname(self):
        s = DeviceScanner()
        info = s.scan()
        report = s.report(info)
        assert "ARGOS DEVICE SCAN" in report
        assert info["profile"]["key"].upper() in report

    def test_report_without_arg_calls_scan(self):
        s = DeviceScanner()
        report = s.report()
        assert "ПРОФИЛЬ" in report

    def test_firmware_type_is_string(self):
        s = DeviceScanner()
        info = s.scan()
        assert info["firmware"]["type"] in ("UEFI", "BIOS")

    def test_network_has_internet_field(self):
        s = DeviceScanner()
        info = s.scan()
        assert isinstance(info["network"]["internet"], bool)

    def test_tools_dict_contains_adb(self):
        s = DeviceScanner()
        info = s.scan()
        assert "adb" in info["tools"]
        assert isinstance(info["tools"]["adb"], bool)

    def test_packages_dict_contains_requests(self):
        s = DeviceScanner()
        info = s.scan()
        assert "requests" in info["packages"]

    def test_high_ram_gives_full_or_server_profile(self, monkeypatch):
        s = DeviceScanner()
        monkeypatch.setattr(s, "_scan_ram", lambda: {"total_mb": 32768, "available_mb": 16384})
        monkeypatch.setattr(s, "_scan_os", lambda: {"system": "Linux", "release": "x",
                                                     "version": "x", "python": "3.12",
                                                     "is_android": False, "hostname": "test"})
        monkeypatch.setattr(s, "_scan_cpu", lambda: {"arch": "x86_64", "cores": 8,
                                                      "model": "Test", "freq_mhz": 3000,
                                                      "is_arm": False, "is_x86": True,
                                                      "is_riscv": False})
        monkeypatch.setattr(s, "_scan_storage",    lambda: [])
        monkeypatch.setattr(s, "_scan_network",    lambda: {"interfaces": [], "has_wifi": False,
                                                             "has_ethernet": True, "has_bluetooth": False,
                                                             "internet": False})
        monkeypatch.setattr(s, "_scan_peripherals",lambda: {"serial_ports": [], "has_audio": False,
                                                             "has_gpu": False, "gpu_name": "",
                                                             "has_display": True, "has_gpio": False})
        monkeypatch.setattr(s, "_scan_tools",      lambda: {})
        monkeypatch.setattr(s, "_scan_packages",   lambda: {})
        monkeypatch.setattr(s, "_scan_firmware_type", lambda: {"type": "UEFI", "is_efi": True})
        info = s.scan()
        assert info["profile"]["key"] in ("full", "server")

    def test_low_ram_gives_lite_or_micro_profile(self, monkeypatch):
        s = DeviceScanner()
        monkeypatch.setattr(s, "_scan_ram", lambda: {"total_mb": 256, "available_mb": 128})
        monkeypatch.setattr(s, "_scan_os", lambda: {"system": "Linux", "release": "x",
                                                     "version": "x", "python": "3.12",
                                                     "is_android": False, "hostname": "test"})
        monkeypatch.setattr(s, "_scan_cpu", lambda: {"arch": "armv7l", "cores": 1,
                                                      "model": "ARM", "freq_mhz": 700,
                                                      "is_arm": True, "is_x86": False,
                                                      "is_riscv": False})
        monkeypatch.setattr(s, "_scan_storage",    lambda: [])
        monkeypatch.setattr(s, "_scan_network",    lambda: {"interfaces": [], "has_wifi": True,
                                                             "has_ethernet": False, "has_bluetooth": False,
                                                             "internet": False})
        monkeypatch.setattr(s, "_scan_peripherals",lambda: {"serial_ports": [], "has_audio": False,
                                                             "has_gpu": False, "gpu_name": "",
                                                             "has_display": False, "has_gpio": True})
        monkeypatch.setattr(s, "_scan_tools",      lambda: {})
        monkeypatch.setattr(s, "_scan_packages",   lambda: {})
        monkeypatch.setattr(s, "_scan_firmware_type", lambda: {"type": "BIOS", "is_efi": False})
        info = s.scan()
        assert info["profile"]["key"] in ("micro", "lite")


# ── AdaptiveImageBuilder ───────────────────────────────────────────────────

class TestAdaptiveImageBuilder:
    @pytest.fixture(autouse=True)
    def _minimal_source_tree(self, tmp_path, monkeypatch):
        """Build test archives from a tiny source tree, not the whole checkout."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "main.py").write_text("print('argos')\n", encoding="utf-8")
        (source_dir / "requirements.txt").write_text(
            "requests\npython-dotenv\npsutil\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(source_dir)

    def test_build_for_this_device_creates_zip(self, tmp_path):
        builder = AdaptiveImageBuilder(output_dir=str(tmp_path))
        result  = builder.build_for_this_device(version="0.0.1")
        zips    = list(tmp_path.glob("*.zip"))
        assert zips, f"ZIP не создан. Результат: {result[:200]}"
        assert "✅" in result or "Образ" in result

    def test_build_for_this_device_zip_has_profile_json(self, tmp_path):
        builder = AdaptiveImageBuilder(output_dir=str(tmp_path))
        builder.build_for_this_device(version="0.0.1")
        zips = list(tmp_path.glob("*.zip"))
        assert zips
        with zipfile.ZipFile(zips[0]) as zf:
            names = zf.namelist()
        assert any("device_profile.json" in n for n in names)

    def test_build_for_this_device_zip_has_requirements(self, tmp_path):
        builder = AdaptiveImageBuilder(output_dir=str(tmp_path))
        builder.build_for_this_device(version="0.0.1")
        zips = list(tmp_path.glob("*.zip"))
        with zipfile.ZipFile(zips[0]) as zf:
            names = zf.namelist()
        assert any("requirements.txt" in n for n in names)

    def test_build_for_this_device_excludes_secrets(self, tmp_path):
        builder = AdaptiveImageBuilder(output_dir=str(tmp_path))
        builder.build_for_this_device(version="0.0.1")
        zips = list(tmp_path.glob("*.zip"))
        with zipfile.ZipFile(zips[0]) as zf:
            names = zf.namelist()
        assert not any(n.endswith("/.env") or n == ".env" for n in names)
        assert not any("master.key" in n for n in names)

    def test_build_for_target_windows(self, tmp_path):
        builder = AdaptiveImageBuilder(output_dir=str(tmp_path))
        result  = builder.build_for_target("windows", version="0.0.1")
        assert "✅" in result

    def test_build_for_target_android(self, tmp_path):
        builder = AdaptiveImageBuilder(output_dir=str(tmp_path))
        result  = builder.build_for_target("android", version="0.0.1")
        assert "✅" in result

    def test_build_for_target_rpi(self, tmp_path):
        builder = AdaptiveImageBuilder(output_dir=str(tmp_path))
        result  = builder.build_for_target("rpi", version="0.0.1")
        assert "✅" in result

    def test_build_for_target_invalid(self, tmp_path):
        builder = AdaptiveImageBuilder(output_dir=str(tmp_path))
        result  = builder.build_for_target("mars_colony")
        assert "❌" in result

    def test_build_for_target_profile_keys(self, tmp_path):
        builder = AdaptiveImageBuilder(output_dir=str(tmp_path))
        for pkey in ("micro", "lite", "standard"):
            result = builder.build_for_target(pkey, version="0.0.1")
            assert "✅" in result, f"Профиль {pkey}: {result[:100]}"

    def test_status(self, tmp_path):
        builder = AdaptiveImageBuilder(output_dir=str(tmp_path))
        status  = builder.status()
        assert "AdaptiveImageBuilder" in status
        assert "Профили" in status

    def test_device_profile_json_content(self, tmp_path):
        builder = AdaptiveImageBuilder(output_dir=str(tmp_path))
        builder.build_for_this_device(version="0.0.1")
        zips = list(tmp_path.glob("*.zip"))
        with zipfile.ZipFile(zips[0]) as zf:
            profile_data = json.loads(zf.read(
                next(n for n in zf.namelist() if "device_profile.json" in n)
            ))
        assert "profile" in profile_data
        assert profile_data["profile"] in PROFILES
        assert "os" in profile_data


# ── Watson code-only filter ────────────────────────────────────────────────

class TestWatsonCodeFilter:
    def test_code_request_python(self):
        from src.quantum.watson_bridge import is_code_request
        assert is_code_request("напиши функцию на python") is True

    def test_code_request_asm(self):
        from src.quantum.watson_bridge import is_code_request
        assert is_code_request("напиши asm код для ARM") is True

    def test_code_request_fix(self):
        from src.quantum.watson_bridge import is_code_request
        assert is_code_request("исправь ошибку в src/core.py") is True

    def test_non_code_request_weather(self):
        from src.quantum.watson_bridge import is_code_request
        assert is_code_request("какая погода завтра") is False

    def test_non_code_request_chat(self):
        from src.quantum.watson_bridge import is_code_request
        assert is_code_request("привет как дела") is False

    def test_watson_bridge_import(self):
        from src.quantum.watson_bridge import WatsonXBridge
        bridge = WatsonXBridge()
        # Без API ключа ask() должен вернуть None для любого запроса
        result = bridge.ask("system", "привет как дела")
        assert result is None

    def test_watson_status_contains_policy(self):
        from src.quantum.watson_bridge import WatsonXBridge
        bridge = WatsonXBridge()
        status = bridge.status()
        assert "Политика" in status or "только для кода" in status
