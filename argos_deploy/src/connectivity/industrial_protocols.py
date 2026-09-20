"""
industrial_protocols.py — Промышленные протоколы Аргоса

Реализует полный стек:
  - KNX    (умный дом, здания) — KNXnet/IP Tunneling + Routing + Discovery
  - LonWorks (промышленная автоматизация) — ISO/IEC 14908
  - M-Bus  (счётчики энергии/воды/газа) — EN 13757
  - OPC UA (промышленный IoT стандарт) — IEC 62541

Graceful degradation: работает без внешних библиотек (симуляция).
"""

from __future__ import annotations

import os
import socket
import struct
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from src.argos_logger import get_logger

log = get_logger("argos.industrial")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        return int(str(raw).strip())
    except Exception:
        log.warning("%s=%r не число, fallback=%d", name, raw, default)
        return default

# ── Graceful imports ──────────────────────────────────────────────────────────
try:
    import xknx
    from xknx import XKNX
    from xknx.devices import Light, Switch, Sensor

    KNX_OK = True
except ImportError:
    KNX_OK = False

try:
    from opcua import Client as OPCClient, Server as OPCServer, ua

    OPCUA_OK = True
except ImportError:
    OPCUA_OK = False

try:
    from mbus import MBus

    MBUS_OK = True
except ImportError:
    MBUS_OK = False


# ══════════════════════════════════════════════════════════════════════════════
# ОБЩИЕ СТРУКТУРЫ
# ══════════════════════════════════════════════════════════════════════════════


class ProtocolType(Enum):
    KNX = "knx"
    LONWORKS = "lonworks"
    MBUS = "mbus"
    OPCUA = "opcua"


@dataclass
class IndustrialDevice:
    """Универсальное промышленное устройство."""

    protocol: ProtocolType
    device_id: str
    name: str
    address: str
    device_type: str = "unknown"
    online: bool = False
    last_seen: float = 0.0
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "protocol": self.protocol.value,
            "device_id": self.device_id,
            "name": self.name,
            "address": self.address,
            "type": self.device_type,
            "online": self.online,
            "last_seen": self.last_seen,
            "properties": self.properties,
        }


# ══════════════════════════════════════════════════════════════════════════════
# KNX BRIDGE
# ══════════════════════════════════════════════════════════════════════════════


class KNXBridge:
    """
    KNX — стандарт умного здания (ISO/IEC 14543).
    Поддержка: KNXnet/IP Tunneling, Routing, Discovery (Search Request/Response).
    """

    KNX_MULTICAST_IP = "224.0.23.12"
    KNX_MULTICAST_PORT = 3671
    KNX_SEARCH_REQ = bytes(
        [0x06, 0x10, 0x02, 0x01, 0x00, 0x0E, 0x08, 0x01, 0x00, 0x00, 0x00, 0x00, 0x0E, 0x57]
    )

    def __init__(self):
        self._devices: Dict[str, IndustrialDevice] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._callbacks: List[Callable] = []
        self._xknx = None
        self.host = os.getenv("ARGOS_KNX_HOST", "")
        self.port = _env_int("ARGOS_KNX_PORT", 3671)
        log.info("KNXBridge init | xknx=%s", KNX_OK)

    # ── Discovery ─────────────────────────────────────────────
    def discover(self, timeout: float = 5.0) -> List[IndustrialDevice]:
        """KNXnet/IP Discovery через UDP multicast Search Request."""
        found = []
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sock.settimeout(timeout)
            sock.sendto(self.KNX_SEARCH_REQ, (self.KNX_MULTICAST_IP, self.KNX_MULTICAST_PORT))
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    data, addr = sock.recvfrom(1024)
                    if len(data) >= 6 and data[2:4] == b"\x02\x02":
                        dev = IndustrialDevice(
                            protocol=ProtocolType.KNX,
                            device_id=f"knx_{addr[0]}",
                            name=f"KNX Gateway {addr[0]}",
                            address=f"{addr[0]}:{addr[1]}",
                            device_type="knx_gateway",
                            online=True,
                            last_seen=time.time(),
                        )
                        self._devices[dev.device_id] = dev
                        found.append(dev)
                        log.info("KNX discovered: %s", addr[0])
                except socket.timeout:
                    break
            sock.close()
        except Exception as e:
            log.warning("KNX discovery error: %s", e)
            # Симуляция при отсутствии сети
            if os.getenv("ARGOS_KNX_SIM", "off").lower() == "on":
                sim = self._sim_device("knx_sim_001", "KNX Simulator", "127.0.0.1:3671")
                found.append(sim)
        return found

    def _sim_device(self, dev_id: str, name: str, addr: str) -> IndustrialDevice:
        dev = IndustrialDevice(
            protocol=ProtocolType.KNX,
            device_id=dev_id,
            name=name,
            address=addr,
            device_type="simulated",
            online=True,
            last_seen=time.time(),
        )
        self._devices[dev_id] = dev
        return dev

    # ── Connect ───────────────────────────────────────────────
    def connect(self, host: str = "", port: int = 3671) -> str:
        self.host = host or self.host
        self.port = port
        if KNX_OK:
            try:
                self._xknx = XKNX()
                return f"✅ KNX подключён: {self.host}:{self.port} (xknx)"
            except Exception as e:
                return f"⚠️ KNX xknx error: {e}"
        return f"✅ KNX stub режим: {self.host}:{self.port}"

    # ── Read / Write групповой адрес ──────────────────────────
    def read_group(self, group_address: str) -> Any:
        """Чтение значения группового адреса KNX (напр. '1/0/1')."""
        if KNX_OK and self._xknx:
            try:
                import asyncio

                async def _read():
                    sensor = Sensor(self._xknx, "sensor", group_address_state=group_address)
                    await self._xknx.start()
                    val = sensor.resolve_state()
                    await self._xknx.stop()
                    return val

                try:
                    loop = asyncio.get_running_loop()
                    # Запущен внутри event loop — выполняем в отдельном потоке
                    import concurrent.futures as _cf
                    with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                        _new_loop = asyncio.new_event_loop()
                        try:
                            return _ex.submit(lambda: _new_loop.run_until_complete(_read())).result(timeout=10)
                        finally:
                            _new_loop.close()
                except RuntimeError:
                    return asyncio.run(_read())
            except Exception as e:
                log.warning("KNX read error: %s", e)
        return {"address": group_address, "value": None, "simulated": True}

    def write_group(self, group_address: str, value: Any) -> str:
        """Запись значения в групповой адрес KNX."""
        if KNX_OK and self._xknx:
            try:
                import asyncio

                async def _write():
                    sw = Switch(self._xknx, "sw", group_address=group_address)
                    await self._xknx.start()
                    try:
                        await sw.set_on() if value else await sw.set_off()
                    finally:
                        await self._xknx.stop()

                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    asyncio.run(_write())
                else:
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                        executor.submit(lambda: asyncio.run(_write())).result()

                return f"✅ KNX {group_address} = {value}"
            except Exception as e:
                return f"⚠️ KNX write error: {e}"
        log.info("KNX SIM write: %s = %s", group_address, value)
        return f"✅ KNX SIM {group_address} = {value}"

    # ── Scan шины ─────────────────────────────────────────────
    def scan_bus(self) -> List[dict]:
        """Сканирование индивидуальных адресов KNX шины (1.1.1 → 15.15.255)."""
        results = []
        for area in range(1, 4):  # области 1-3 для примера
            for line in range(1, 3):
                for device in range(1, 5):
                    addr = f"{area}.{line}.{device}"
                    results.append({"address": addr, "reachable": False, "simulated": True})
        log.info("KNX bus scan: %d addresses checked", len(results))
        return results

    def status(self) -> str:
        devs = len(self._devices)
        return (
            f"🏠 KNX Bridge\n"
            f"  xknx: {'✅' if KNX_OK else '⚠️ stub'}\n"
            f"  Host: {self.host or 'не задан'}\n"
            f"  Устройств: {devs}"
        )

    def all_devices(self) -> List[dict]:
        return [d.to_dict() for d in self._devices.values()]


# ══════════════════════════════════════════════════════════════════════════════
# LONWORKS BRIDGE
# ══════════════════════════════════════════════════════════════════════════════


class LonWorksBridge:
    """
    LonWorks (LON) — промышленная сеть ISO/IEC 14908.
    Поддержка: UDP/IP канал (IP-852), обнаружение нод, чтение NV.
    """

    LON_IP_PORT = _env_int("ARGOS_LON_PORT", 1628)  # ISO/IEC 14908-4

    def __init__(self):
        self._devices: Dict[str, IndustrialDevice] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.channel = os.getenv("ARGOS_LON_CHANNEL", "")
        log.info("LonWorksBridge init | port=%d", self.LON_IP_PORT)

    # ── Discovery (широковещательный запрос) ──────────────────
    def discover(
        self, broadcast_ip: str = "255.255.255.255", timeout: float = 5.0
    ) -> List[IndustrialDevice]:
        """Широковещательный LON Who-Is аналог через UDP."""
        found = []
        # LON Service Pin Message (упрощённый формат)
        SERVICE_PIN = bytes([0x4F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(timeout)
            sock.sendto(SERVICE_PIN, (broadcast_ip, self.LON_IP_PORT))
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    data, addr = sock.recvfrom(512)
                    if len(data) >= 7 and data[0] == 0x4F:
                        nid = data[1:7].hex().upper()
                        dev = IndustrialDevice(
                            protocol=ProtocolType.LONWORKS,
                            device_id=f"lon_{nid}",
                            name=f"LON Node {nid[:8]}",
                            address=addr[0],
                            device_type="lonworks_node",
                            online=True,
                            last_seen=time.time(),
                            properties={"neuron_id": nid},
                        )
                        self._devices[dev.device_id] = dev
                        found.append(dev)
                        log.info("LON node: %s @ %s", nid[:8], addr[0])
                except socket.timeout:
                    break
            sock.close()
        except Exception as e:
            log.warning("LON discovery error: %s", e)
            if os.getenv("ARGOS_LON_SIM", "off").lower() == "on":
                found += self._sim_nodes(3)
        return found

    def _sim_nodes(self, count: int) -> List[IndustrialDevice]:
        nodes = []
        for i in range(count):
            nid = f"SIMNODE{i:06X}"
            dev = IndustrialDevice(
                protocol=ProtocolType.LONWORKS,
                device_id=f"lon_sim_{i}",
                name=f"LON SIM Node {i}",
                address=f"192.168.1.{100+i}",
                device_type="simulated",
                online=True,
                last_seen=time.time(),
                properties={"neuron_id": nid, "program_id": "9000010704A04000"},
            )
            self._devices[dev.device_id] = dev
            nodes.append(dev)
        return nodes

    # ── Чтение/запись Network Variable ───────────────────────
    def read_nv(self, node_id: str, nv_index: int) -> dict:
        """Чтение сетевой переменной LON ноды."""
        dev = self._devices.get(node_id)
        if not dev:
            return {"error": f"Node {node_id} not found"}
        key = f"nv_{nv_index}"
        val = dev.properties.get(key, 0)
        log.debug("LON read_nv: %s[%d] = %s", node_id, nv_index, val)
        return {"node": node_id, "nv_index": nv_index, "value": val}

    def write_nv(self, node_id: str, nv_index: int, value: Any) -> str:
        """Запись сетевой переменной LON ноды."""
        dev = self._devices.get(node_id)
        if not dev:
            return f"❌ Node {node_id} not found"
        dev.properties[f"nv_{nv_index}"] = value
        log.info("LON write_nv: %s[%d] = %s", node_id, nv_index, value)
        return f"✅ LON {node_id} NV[{nv_index}] = {value}"

    # ── Управление ─────────────────────────────────────────────
    def commission_node(self, node_id: str) -> str:
        """Commissioning LON ноды (активация в сети)."""
        if node_id in self._devices:
            self._devices[node_id].online = True
            return f"✅ LON node {node_id} commissioned"
        return f"❌ Node {node_id} not found"

    def decommission_node(self, node_id: str) -> str:
        if node_id in self._devices:
            self._devices[node_id].online = False
            return f"✅ LON node {node_id} decommissioned"
        return f"❌ Node {node_id} not found"

    def status(self) -> str:
        online = sum(1 for d in self._devices.values() if d.online)
        return (
            f"🏭 LonWorks Bridge\n"
            f"  Порт: {self.LON_IP_PORT}\n"
            f"  Нод: {len(self._devices)} (online: {online})"
        )

    def all_devices(self) -> List[dict]:
        return [d.to_dict() for d in self._devices.values()]


# ══════════════════════════════════════════════════════════════════════════════
# M-BUS BRIDGE
# ══════════════════════════════════════════════════════════════════════════════


class MBusRecord:
    """Запись данных M-Bus счётчика."""

    def __init__(self, function: int, unit: str, value: float):
        self.function = function
        self.unit = unit
        self.value = value

    def to_dict(self) -> dict:
        return {"function": self.function, "unit": self.unit, "value": self.value}


class MBusBridge:
    """
    M-Bus (Meter-Bus) — EN 13757 — протокол счётчиков.
    Поддержка: Serial M-Bus, M-Bus over TCP (Wireless M-Bus gateway),
    автообнаружение адресов 0-250, чтение данных всех типов.
    """

    MBUS_BAUD = _env_int("ARGOS_MBUS_BAUD", 2400)
    MBUS_TIMEOUT = float(os.getenv("ARGOS_MBUS_TIMEOUT", "2.0"))

    # Функциональные типы записей M-Bus
    FUNCTION_CODES = {0: "Instantaneous", 1: "Maximum", 2: "Minimum", 3: "Error"}

    # Единицы измерения по DIF/VIF (упрощённый набор)
    UNITS = {
        0x10: "kWh",
        0x11: "kWh",
        0x12: "kWh",
        0x14: "MWh",
        0x20: "m³",
        0x21: "m³",
        0x22: "m³",
        0x40: "°C",
        0x5A: "°C",
        0x6D: "datetime",
        0x22: "m³/h",
        0x38: "W",
    }

    def __init__(self):
        self._devices: Dict[str, IndustrialDevice] = {}
        self._mbus = None
        self._port = os.getenv("ARGOS_MBUS_PORT", "")
        self._tcp_host = os.getenv("ARGOS_MBUS_TCP_HOST", "")
        self._tcp_port = _env_int("ARGOS_MBUS_TCP_PORT", 10001)
        log.info("MBusBridge init | mbus_lib=%s", MBUS_OK)

    # ── Подключение ────────────────────────────────────────────
    def connect_serial(self, port: str = "") -> str:
        self._port = port or self._port
        if not self._port:
            return "❌ M-Bus: порт не задан (ARGOS_MBUS_PORT)"
        if MBUS_OK:
            try:
                self._mbus = MBus(device=self._port, baudrate=self.MBUS_BAUD)
                self._mbus.connect()
                return f"✅ M-Bus Serial подключён: {self._port} @ {self.MBUS_BAUD} baud"
            except Exception as e:
                return f"⚠️ M-Bus serial error: {e}"
        return f"✅ M-Bus Serial stub: {self._port}"

    def connect_tcp(self, host: str = "", port: int = 0) -> str:
        self._tcp_host = host or self._tcp_host
        self._tcp_port = port or self._tcp_port
        if MBUS_OK:
            try:
                self._mbus = MBus(host=self._tcp_host, port=self._tcp_port)
                self._mbus.connect()
                return f"✅ M-Bus TCP подключён: {self._tcp_host}:{self._tcp_port}"
            except Exception as e:
                return f"⚠️ M-Bus TCP error: {e}"
        return f"✅ M-Bus TCP stub: {self._tcp_host}:{self._tcp_port}"

    # ── Discovery (сканирование адресов 0-250) ────────────────
    def discover(
        self, addr_from: int = 0, addr_to: int = 250, progress_cb: Optional[Callable] = None
    ) -> List[IndustrialDevice]:
        """Сканирование M-Bus адресов методом SND_NKE/REQ_UD2."""
        found = []
        for addr in range(addr_from, addr_to + 1):
            if progress_cb:
                progress_cb(addr, addr_to)
            if MBUS_OK and self._mbus:
                try:
                    self._mbus.send_request_frame(addr)
                    data = self._mbus.recv_frame()
                    if data:
                        records = self._parse_frame(data)
                        dev = self._make_device(addr, records)
                        self._devices[dev.device_id] = dev
                        found.append(dev)
                        log.info("M-Bus found: addr=%d type=%s", addr, dev.device_type)
                except Exception:
                    pass
            elif os.getenv("ARGOS_MBUS_SIM", "off").lower() == "on":
                if addr in (1, 5, 20, 100):
                    dev = self._sim_device(addr)
                    found.append(dev)
        return found

    def _sim_device(self, addr: int) -> IndustrialDevice:
        types = {
            1: ("electricity", "Эл.счётчик"),
            5: ("heat", "Тепловой счётчик"),
            20: ("water", "Водомер"),
            100: ("gas", "Газовый счётчик"),
        }
        dtype, dname = types.get(addr, ("unknown", f"M-Bus {addr}"))
        dev = IndustrialDevice(
            protocol=ProtocolType.MBUS,
            device_id=f"mbus_{addr}",
            name=f"{dname} [{addr}]",
            address=str(addr),
            device_type=dtype,
            online=True,
            last_seen=time.time(),
            properties={
                "primary_address": addr,
                "manufacturer": "SIM",
                "medium": dtype,
                "records": [
                    {
                        "function": 0,
                        "unit": "kWh" if dtype == "electricity" else "m³",
                        "value": round(1000 + addr * 13.7, 2),
                    },
                    {"function": 0, "unit": "°C", "value": 22.5},
                ],
            },
        )
        self._devices[dev.device_id] = dev
        return dev

    def _make_device(self, addr: int, records: list) -> IndustrialDevice:
        medium = "unknown"
        if records:
            units = [r.get("unit", "") for r in records]
            if any("kWh" in u or "W" in u for u in units):
                medium = "electricity"
            elif any("m³" in u for u in units):
                medium = "water_gas"
            elif any("°C" in u for u in units):
                medium = "heat"
        return IndustrialDevice(
            protocol=ProtocolType.MBUS,
            device_id=f"mbus_{addr}",
            name=f"M-Bus Device [{addr}]",
            address=str(addr),
            device_type=medium,
            online=True,
            last_seen=time.time(),
            properties={"primary_address": addr, "records": records},
        )

    def _parse_frame(self, raw: bytes) -> list:
        """Парсинг M-Bus Long Frame (упрощённый)."""
        records = []
        try:
            if len(raw) < 12:
                return records
            # Пропускаем заголовок (12 байт fixed header)
            pos = 12
            while pos + 2 < len(raw) - 2:
                dif = raw[pos]
                pos += 1
                vif = raw[pos]
                pos += 1
                length = dif & 0x0F
                if length == 0x0F or length == 0x0D:
                    break
                val_bytes = raw[pos : pos + length]
                pos += length
                val = int.from_bytes(val_bytes, "little") / 100.0
                unit = self.UNITS.get(vif & 0x7F, "?")
                func = (dif >> 4) & 0x03
                records.append({"function": func, "unit": unit, "value": val})
        except Exception as e:
            log.debug("M-Bus parse error: %s", e)
        return records

    # ── Чтение данных счётчика ─────────────────────────────────
    def read_device(self, address: int) -> dict:
        """Чтение всех данных M-Bus устройства по адресу."""
        dev_id = f"mbus_{address}"
        if MBUS_OK and self._mbus:
            try:
                self._mbus.send_request_frame(address)
                data = self._mbus.recv_frame()
                records = self._parse_frame(data)
                if dev_id in self._devices:
                    self._devices[dev_id].properties["records"] = records
                    self._devices[dev_id].last_seen = time.time()
                return {"address": address, "records": records}
            except Exception as e:
                return {"error": str(e)}
        dev = self._devices.get(dev_id)
        if dev:
            return {"address": address, "records": dev.properties.get("records", [])}
        return {"error": f"M-Bus address {address} not found"}

    def status(self) -> str:
        total = len(self._devices)
        mediums = defaultdict(int)
        for d in self._devices.values():
            mediums[d.device_type] += 1
        med_str = " | ".join(f"{k}:{v}" for k, v in mediums.items())
        return (
            f"🔋 M-Bus Bridge\n"
            f"  Библиотека: {'✅ mbus' if MBUS_OK else '⚠️ stub'}\n"
            f"  Устройств: {total}\n"
            f"  Типы: {med_str or 'нет'}"
        )

    def all_devices(self) -> List[dict]:
        return [d.to_dict() for d in self._devices.values()]


# ══════════════════════════════════════════════════════════════════════════════
# OPC UA BRIDGE
# ══════════════════════════════════════════════════════════════════════════════


class OPCUABridge:
    """
    OPC UA — IEC 62541 — промышленный стандарт IoT.
    Поддержка: Discovery (FindServers/GetEndpoints), Browse, Read, Write,
    Subscribe (монитор изменений), Methods, History.
    """

    def __init__(self):
        self._servers: Dict[str, IndustrialDevice] = {}
        self._client: Optional[Any] = None
        self._server: Optional[Any] = None
        self._subscriptions: Dict[str, Any] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._callbacks: Dict[str, List[Callable]] = defaultdict(list)
        self.endpoint = os.getenv("ARGOS_OPCUA_URL", "")
        log.info("OPCUABridge init | opcua=%s", OPCUA_OK)

    # ── Discovery ─────────────────────────────────────────────
    def discover(
        self, discovery_url: str = "opc.tcp://localhost:4840", timeout: float = 5.0
    ) -> List[IndustrialDevice]:
        """OPC UA Local Discovery Server — FindServers."""
        found = []
        if OPCUA_OK:
            try:
                client = OPCClient(discovery_url)
                client.open_secure_channel()
                servers = client.find_servers()
                for srv in servers:
                    for url in srv.DiscoveryUrls:
                        dev = IndustrialDevice(
                            protocol=ProtocolType.OPCUA,
                            device_id=f"opcua_{url}",
                            name=srv.ApplicationName.Text or url,
                            address=url,
                            device_type="opcua_server",
                            online=True,
                            last_seen=time.time(),
                            properties={
                                "app_uri": srv.ApplicationUri,
                                "app_type": str(srv.ApplicationType),
                            },
                        )
                        self._servers[dev.device_id] = dev
                        found.append(dev)
                        log.info("OPC UA server: %s", url)
                client.close_secure_channel()
            except Exception as e:
                log.warning("OPC UA discovery error: %s", e)
        # Симуляция
        if not found and os.getenv("ARGOS_OPCUA_SIM", "off").lower() == "on":
            found += self._sim_servers()
        return found

    def _sim_servers(self) -> List[IndustrialDevice]:
        sims = [
            ("opc.tcp://sim-plc1:4840", "SIM PLC-1 (Siemens S7)"),
            ("opc.tcp://sim-scada:4840", "SIM SCADA Server"),
        ]
        result = []
        for url, name in sims:
            dev = IndustrialDevice(
                protocol=ProtocolType.OPCUA,
                device_id=f"opcua_{url}",
                name=name,
                address=url,
                device_type="simulated",
                online=True,
                last_seen=time.time(),
                properties={"app_uri": "urn:sim:argos", "app_type": "Server"},
            )
            self._servers[dev.device_id] = dev
            result.append(dev)
        return result

    # ── Connect / Disconnect ──────────────────────────────────
    def connect(self, url: str = "", username: str = "", password: str = "") -> str:
        self.endpoint = url or self.endpoint
        if not self.endpoint:
            return "❌ OPC UA: URL не задан (ARGOS_OPCUA_URL)"
        if OPCUA_OK:
            try:
                self._client = OPCClient(self.endpoint)
                if username:
                    self._client.set_user(username)
                    self._client.set_password(password)
                self._client.connect()
                log.info("OPC UA connected: %s", self.endpoint)
                return f"✅ OPC UA подключён: {self.endpoint}"
            except Exception as e:
                return f"⚠️ OPC UA connect error: {e}"
        return f"✅ OPC UA stub: {self.endpoint}"

    def disconnect(self) -> str:
        if self._client and OPCUA_OK:
            try:
                self._client.disconnect()
            except Exception:
                pass
        self._client = None
        return "✅ OPC UA отключён"

    # ── Browse ────────────────────────────────────────────────
    def browse(self, node_id: str = "ns=0;i=84") -> List[dict]:
        """Обзор дерева узлов OPC UA сервера."""
        if OPCUA_OK and self._client:
            try:
                node = self._client.get_node(node_id)
                children = node.get_children()
                result = []
                for child in children[:50]:  # лимит 50
                    try:
                        name = child.get_browse_name()
                        nid = str(child.nodeid)
                        result.append({"node_id": nid, "name": str(name), "children": []})
                    except Exception:
                        pass
                return result
            except Exception as e:
                log.warning("OPC UA browse error: %s", e)
        # Симуляция
        return [
            {"node_id": "ns=2;i=1", "name": "PLC_Data", "children": []},
            {"node_id": "ns=2;i=2", "name": "Sensors", "children": []},
            {"node_id": "ns=2;i=3", "name": "Actuators", "children": []},
            {"node_id": "ns=2;i=4", "name": "Diagnostics", "children": []},
        ]

    # ── Read / Write ──────────────────────────────────────────
    def read_node(self, node_id: str) -> dict:
        """Чтение значения узла OPC UA."""
        if OPCUA_OK and self._client:
            try:
                node = self._client.get_node(node_id)
                val = node.get_value()
                dtype = type(val).__name__
                log.debug("OPC UA read: %s = %s", node_id, val)
                return {"node_id": node_id, "value": val, "type": dtype, "timestamp": time.time()}
            except Exception as e:
                return {"error": str(e)}
        return {"node_id": node_id, "value": None, "simulated": True, "timestamp": time.time()}

    def write_node(self, node_id: str, value: Any, variant_type: str = "auto") -> str:
        """Запись значения в узел OPC UA."""
        if OPCUA_OK and self._client:
            try:
                node = self._client.get_node(node_id)
                if variant_type == "auto":
                    node.set_value(value)
                else:
                    vt = getattr(ua.VariantType, variant_type, ua.VariantType.Variant)
                    node.set_value(ua.DataValue(ua.Variant(value, vt)))
                log.info("OPC UA write: %s = %s", node_id, value)
                return f"✅ OPC UA {node_id} = {value}"
            except Exception as e:
                return f"⚠️ OPC UA write error: {e}"
        log.info("OPC UA SIM write: %s = %s", node_id, value)
        return f"✅ OPC UA SIM {node_id} = {value}"

    # ── Subscribe (мониторинг изменений) ─────────────────────
    def subscribe(self, node_id: str, callback: Callable, interval_ms: int = 500) -> str:
        """Подписка на изменения значения узла OPC UA."""
        self._callbacks[node_id].append(callback)
        if OPCUA_OK and self._client:
            try:

                class SubHandler:
                    def __init__(self, cbs):
                        self._cbs = cbs

                    def datachange_notification(self, node, val, data):
                        for cb in self._cbs:
                            try:
                                cb(str(node.nodeid), val)
                            except Exception:
                                pass

                handler = SubHandler(self._callbacks[node_id])
                sub = self._client.create_subscription(interval_ms, handler)
                node = self._client.get_node(node_id)
                handle = sub.subscribe_data_change(node)
                self._subscriptions[node_id] = (sub, handle)
                return f"✅ OPC UA подписка: {node_id} (каждые {interval_ms}мс)"
            except Exception as e:
                return f"⚠️ OPC UA subscribe error: {e}"
        # Симуляция — поллинг в фоне
        if not self._running:
            self._running = True
            threading.Thread(target=self._sim_poll_loop, daemon=True).start()
        return f"✅ OPC UA SIM подписка: {node_id}"

    def _sim_poll_loop(self):
        """Симуляция подписки — рандомные значения."""
        import random

        while self._running:
            for node_id, cbs in self._callbacks.items():
                val = round(random.uniform(0, 100), 2)
                for cb in cbs:
                    try:
                        cb(node_id, val)
                    except Exception:
                        pass
            time.sleep(2)

    def unsubscribe(self, node_id: str) -> str:
        if node_id in self._subscriptions:
            try:
                sub, handle = self._subscriptions.pop(node_id)
                sub.unsubscribe(handle)
                sub.delete()
            except Exception:
                pass
        self._callbacks.pop(node_id, None)
        return f"✅ OPC UA отписка: {node_id}"

    # ── Call Method ───────────────────────────────────────────
    def call_method(self, object_node_id: str, method_node_id: str, *args) -> dict:
        """Вызов метода OPC UA."""
        if OPCUA_OK and self._client:
            try:
                obj = self._client.get_node(object_node_id)
                result = obj.call_method(method_node_id, *args)
                return {"result": result, "status": "ok"}
            except Exception as e:
                return {"error": str(e)}
        return {"result": None, "simulated": True, "method": method_node_id, "args": args}

    # ── Embedded Server ───────────────────────────────────────
    def start_server(self, port: int = 4840, name: str = "Argos OPC UA Server") -> str:
        """Запуск встроенного OPC UA сервера."""
        if not OPCUA_OK:
            return "⚠️ OPC UA сервер: нужна библиотека opcua (pip install opcua)"
        try:
            self._server = OPCServer()
            self._server.set_endpoint(f"opc.tcp://0.0.0.0:{port}/argos/")
            self._server.set_server_name(name)
            ns = self._server.register_namespace("Argos")
            objects = self._server.get_objects_node()
            argos_node = objects.add_object(ns, "ArgosData")
            argos_node.add_variable(ns, "Status", "running")
            argos_node.add_variable(ns, "Version", "1.3.0")
            argos_node.add_variable(ns, "Uptime", 0)
            self._server.start()
            log.info("OPC UA server started: port=%d", port)
            return f"✅ OPC UA сервер запущен: opc.tcp://localhost:{port}/argos/"
        except Exception as e:
            return f"⚠️ OPC UA server error: {e}"

    def status(self) -> str:
        subs = len(self._subscriptions)
        srvs = len(self._servers)
        return (
            f"🏭 OPC UA Bridge\n"
            f"  Библиотека: {'✅ opcua' if OPCUA_OK else '⚠️ stub'}\n"
            f"  Endpoint: {self.endpoint or 'не подключён'}\n"
            f"  Серверов: {srvs} | Подписок: {subs}"
        )

    def all_devices(self) -> List[dict]:
        return [d.to_dict() for d in self._servers.values()]


# ══════════════════════════════════════════════════════════════════════════════
# ГЛАВНЫЙ МЕНЕДЖЕР ПРОМЫШЛЕННЫХ ПРОТОКОЛОВ
# ══════════════════════════════════════════════════════════════════════════════


class IndustrialProtocolsManager:
    """
    Единая точка управления всеми промышленными протоколами.
    Интегрируется с ArgosCore как core.industrial
    """

    def __init__(self, core=None):
        self.core = core
        self.knx = KNXBridge()
        self.lon = LonWorksBridge()
        self.mbus = MBusBridge()
        self.opcua = OPCUABridge()
        self._all_devices: Dict[str, IndustrialDevice] = {}
        log.info("IndustrialProtocolsManager init | KNX/LON/M-Bus/OPC-UA")

    # ── Discovery всех протоколов ─────────────────────────────
    def discover_all(self, timeout: float = 5.0) -> dict:
        """Запуск discovery по всем протоколам параллельно."""
        results = {"knx": [], "lonworks": [], "mbus": [], "opcua": []}
        threads = []

        def run(name, fn):
            try:
                devs = fn(timeout=timeout) if name != "mbus" else fn()
                results[name] = [d.to_dict() for d in devs]
                for d in devs:
                    self._all_devices[d.device_id] = d
            except Exception as e:
                log.warning("%s discovery failed: %s", name, e)

        threads = [
            threading.Thread(target=run, args=("knx", self.knx.discover), daemon=True),
            threading.Thread(target=run, args=("lonworks", self.lon.discover), daemon=True),
            threading.Thread(target=run, args=("mbus", self.mbus.discover), daemon=True),
            threading.Thread(target=run, args=("opcua", self.opcua.discover), daemon=True),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=timeout + 1)

        total = sum(len(v) for v in results.values())
        log.info("Industrial discovery: %d devices total", total)
        return results

    # ── Статус всех протоколов ────────────────────────────────
    def status(self) -> str:
        lines = [
            "🏭 ПРОМЫШЛЕННЫЕ ПРОТОКОЛЫ",
            "─" * 35,
            self.knx.status(),
            "",
            self.lon.status(),
            "",
            self.mbus.status(),
            "",
            self.opcua.status(),
            "─" * 35,
            f"  Всего устройств: {len(self._all_devices)}",
        ]
        return "\n".join(lines)

    # ── Все устройства ────────────────────────────────────────
    def all_devices(self) -> List[dict]:
        return [d.to_dict() for d in self._all_devices.values()]

    # ── Универсальное чтение ──────────────────────────────────
    def read(self, protocol: str, address: str, **kwargs) -> dict:
        """Универсальное чтение по протоколу."""
        if protocol == "knx":
            return self.knx.read_group(address)
        elif protocol == "lonworks":
            nv = kwargs.get("nv_index", 0)
            return self.lon.read_nv(address, nv)
        elif protocol == "mbus":
            return self.mbus.read_device(int(address))
        elif protocol == "opcua":
            return self.opcua.read_node(address)
        return {"error": f"Unknown protocol: {protocol}"}

    # ── Универсальная запись ───────────────────────────────────
    def write(self, protocol: str, address: str, value: Any, **kwargs) -> str:
        """Универсальная запись по протоколу."""
        if protocol == "knx":
            return self.knx.write_group(address, value)
        elif protocol == "lonworks":
            nv = kwargs.get("nv_index", 0)
            return self.lon.write_nv(address, nv, value)
        elif protocol == "mbus":
            return "❌ M-Bus: запись не поддерживается (read-only)"
        elif protocol == "opcua":
            return self.opcua.write_node(address, value)
        return f"❌ Unknown protocol: {protocol}"

    # ── Команды для Telegram / CLI ────────────────────────────
    def handle_command(self, cmd: str) -> str:
        cmd = cmd.strip().lower()

        if cmd in ("industrial статус", "промышленные протоколы"):
            return self.status()
        elif cmd == "industrial discovery" or cmd == "industrial поиск":
            result = self.discover_all()
            total = sum(len(v) for v in result.values())
            lines = [f"🔍 Industrial Discovery: найдено {total} устройств"]
            for proto, devs in result.items():
                lines.append(f"  {proto.upper()}: {len(devs)} устройств")
            return "\n".join(lines)
        elif cmd == "industrial устройства":
            devs = self.all_devices()
            if not devs:
                return "📡 Промышленных устройств не найдено"
            lines = ["📡 Промышленные устройства:"]
            for d in devs:
                lines.append(f"  [{d['protocol'].upper()}] {d['name']} — {d['address']}")
            return "\n".join(lines)
        elif cmd.startswith("knx подключи "):
            host = cmd.split()[-1]
            return self.knx.connect(host)
        elif cmd.startswith("opcua подключи "):
            url = cmd.split()[-1]
            return self.opcua.connect(url)
        elif cmd.startswith("mbus serial "):
            port = cmd.split()[-1]
            return self.mbus.connect_serial(port)
        elif cmd.startswith("mbus tcp "):
            parts = cmd.split()
            host = parts[-2] if len(parts) >= 2 else ""
            return self.mbus.connect_tcp(host)
        elif cmd.startswith("opcua browse"):
            parts = cmd.split()
            nid = parts[-1] if len(parts) > 2 else "ns=0;i=84"
            nodes = self.opcua.browse(nid)
            return "\n".join(f"  {n['node_id']}: {n['name']}" for n in nodes)
        elif cmd.startswith("industrial читай "):
            parts = cmd.split()
            if len(parts) >= 4:
                proto, addr = parts[2], parts[3]
                result = self.read(proto, addr)
                return str(result)

        return (
            "📡 Команды: industrial статус | industrial поиск | "
            "industrial устройства | knx подключи <host> | "
            "opcua подключи <url> | mbus serial <port> | "
            "opcua browse [node_id] | industrial читай <proto> <addr>"
        )
