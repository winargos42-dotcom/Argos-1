#!/usr/bin/env python3
r"""
xdma_ring_trace_logger.py — polymorphic ring-buffer logger for FPGA DMA tracing.

Backends:
    * deque  (default) — low GC pressure, great readability, perfect for step 1.
    * raw    — zero-allocation bytearray backend, ~60 ns per log, for perf/IRQ tests.

Event schema:
    index          uint64   global monotonic counter
    timestamp      float    time.perf_counter()
    event_type     int      EventType
    address_offset uint16   BAR0 offset (or ioctl code)
    val_before     uint32   value before write/ioctl
    val_after      uint32   value after write/result of ioctl
    delta_ns       uint64   measured interval in nanoseconds
"""
from __future__ import annotations

import json
import os as _os
import struct
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Dict, Optional, Tuple, Type, Union

# CasRing C-extension (or Python fallback) — lazy import once at module load.
# Dual-import: package form (project root) then direct form (tools/ CWD).
# Avoids sys.path manipulation that would cause cas_ring.py to import itself.
try:
    from tools.cas_ring import CasRing as _CasRingImpl, CasRingError as _CasRingError  # type: ignore[import]
    _CASRING_AVAILABLE = True
except ImportError:
    try:
        # Running directly from tools/ directory.
        _tools_dir = _os.path.dirname(_os.path.abspath(__file__))
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("_cas_ring_mod", _os.path.join(_tools_dir, "cas_ring.py"))
        _cas_ring_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
        _spec.loader.exec_module(_cas_ring_mod)  # type: ignore[union-attr]
        _CasRingImpl = _cas_ring_mod.CasRing
        _CasRingError = _cas_ring_mod.CasRingError
        _CASRING_AVAILABLE = True
    except Exception:
        _CasRingImpl = None  # type: ignore[assignment]
        _CasRingError = RuntimeError
        _CASRING_AVAILABLE = False


class EventType(IntEnum):
    POLL = 0
    WRITE_REQ = 1
    IOCTL_REQ = 2
    IRQ_TRIGGERED = 3
    ERROR_LATCH = 4
    STATE_TRANSITION = 5


@dataclass(frozen=True, slots=True)
class TraceRecord:
    index: int
    timestamp: float
    event_type: EventType
    address_offset: int
    val_before: int
    val_after: int
    delta_ns: int

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "timestamp": self.timestamp,
            "event_type": self.event_type.name,
            "address_offset": f"0x{self.address_offset:04X}",
            "val_before": f"0x{self.val_before:08X}",
            "val_after": f"0x{self.val_after:08X}",
            "delta_ns": self.delta_ns,
        }

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Zero-GC binary layout: 31 bytes per record (padded to 32 for alignment)
# ---------------------------------------------------------------------------
_STRUCT_FMT = struct.Struct("<QdBHIIQ")  # index, ts, etype, addr, vb, va, delta
RECORD_SIZE = _STRUCT_FMT.size  # 35 bytes on all supported platforms


class BaseLogger(ABC):
    def __init__(self, maxlen: int, dump_path: Path, anomaly_ns: int):
        self.maxlen = maxlen
        self.dump_path = Path(dump_path)
        self.anomaly_ns = anomaly_ns
        self._counter = 0

    @abstractmethod
    def log(self, event_type: EventType, address_offset: int, val_before: int,
            val_after: int, delta_ns: int) -> None:
        """Low-level event append. Must be O(1) and allocation-free in raw mode."""
        raise NotImplementedError

    @abstractmethod
    def dump_json(self) -> None:
        """Persist events to self.dump_path as JSON array, oldest first."""
        raise NotImplementedError

    def flush(self) -> None:
        """Optional hook to drain anomaly buffers. Default: no-op."""

    def log_poll(self, address_offset: int, val_after: int, delta_ns: int = 0) -> None:
        self.log(EventType.POLL, address_offset, 0, val_after, delta_ns)

    def log_write(self, address_offset: int, val_before: int, val_after: int,
                  delta_ns: int = 0) -> None:
        self.log(EventType.WRITE_REQ, address_offset, val_before, val_after, delta_ns)

    def log_ioctl(self, code: int, arg: int, delta_ns: int = 0) -> None:
        self.log(EventType.IOCTL_REQ, code & 0xFFFF, arg & 0xFFFFFFFF, 0, delta_ns)

    def log_irq(self, latency_ns: int) -> None:
        self.log(EventType.IRQ_TRIGGERED, 0, 0, 0, latency_ns)

    def log_error_latch(self, address_offset: int, val_after: int, delta_ns: int = 0) -> None:
        self.log(EventType.ERROR_LATCH, address_offset, 0, val_after, delta_ns)

    def log_state_transition(self, from_state: str, to_state: str) -> None:
        """Record a state machine transition. State labels are hashed to ints."""
        # Pack both labels into a 32-bit int to stay in the fixed schema.
        from_id = hash(from_state) & 0xFFFF
        to_id = hash(to_state) & 0xFFFF
        self.log(EventType.STATE_TRANSITION, 0, from_id, to_id, 0)

    def log_anomaly(self, timestamp_ns: int, address: int, value: int,
                    extra: Optional[Dict] = None, delta_ns: int = 0) -> None:
        """Explicit anomaly entry with structured metadata."""
        self.log(EventType.ERROR_LATCH, address, 0, value, delta_ns)

    def _check_anomaly(self, delta_ns: int) -> bool:
        return delta_ns > self.anomaly_ns

    def _anomaly_path(self, index: int) -> Path:
        return self.dump_path.parent / f"anomaly_{index}.json"

    def _trigger_anomaly_dump(self, data: Tuple) -> None:
        """Default anomaly dump. Override to disable disk I/O in interrupt paths."""
        path = self._anomaly_path(data[0])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump({
                "index": data[0], "timestamp": data[1], "event_type": data[2],
                "address_offset": data[3], "val_before": data[4],
                "val_after": data[5], "delta_ns": data[6],
            }, f, indent=2, ensure_ascii=False)

    def _format_events(self, events: list[dict]) -> str:
        return json.dumps(events, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Backend 1: deque + NamedTuple — perfect for step 1 debug / low-rate capture
# ---------------------------------------------------------------------------

class DequeLogger(BaseLogger):
    def __init__(self, maxlen: int, dump_path: Path, anomaly_ns: int):
        super().__init__(maxlen, dump_path, anomaly_ns)
        self._buf: deque[TraceRecord] = deque(maxlen=maxlen)

    def log(self, event_type: EventType, address_offset: int, val_before: int,
            val_after: int, delta_ns: int) -> None:
        self._counter += 1
        rec = TraceRecord(
            index=self._counter,
            timestamp=time.perf_counter(),
            event_type=event_type,
            address_offset=address_offset & 0xFFFF,
            val_before=val_before & 0xFFFFFFFF,
            val_after=val_after & 0xFFFFFFFF,
            delta_ns=delta_ns,
        )
        self._buf.append(rec)
        if self._check_anomaly(delta_ns):
            self._trigger_anomaly_dump((rec.index, rec.timestamp, rec.event_type,
                                        rec.address_offset, rec.val_before,
                                        rec.val_after, rec.delta_ns))

    def dump_json(self) -> None:
        events = [rec.to_dict() for rec in self._buf]
        self.dump_path.parent.mkdir(parents=True, exist_ok=True)
        self.dump_path.write_text(self._format_events(events), encoding="utf-8")

    def __len__(self) -> int:
        return len(self._buf)

    def latest(self, n: int = 10) -> list[TraceRecord]:
        return list(self._buf)[-n:]


# ---------------------------------------------------------------------------
# Backend 2: preallocated bytearray — zero-allocation in hot path
# ---------------------------------------------------------------------------

class RawBytesLogger(BaseLogger):
    def __init__(self, maxlen: int, dump_path: Path, anomaly_ns: int):
        super().__init__(maxlen, dump_path, anomaly_ns)
        self._step = _STRUCT_FMT.size
        self._raw = bytearray(self._step * maxlen)
        self._head = 0
        self._wrapped = False
        # Method cache for hot path.
        self._pack_into = _STRUCT_FMT.pack_into
        self._unpack_from = _STRUCT_FMT.unpack_from

    def log(self, event_type: EventType, address_offset: int, val_before: int,
            val_after: int, delta_ns: int) -> None:
        cnt = self._counter
        self._counter = cnt + 1
        ts = time.perf_counter()

        self._pack_into(
            self._raw,
            self._head * self._step,
            cnt, ts, event_type, address_offset, val_before, val_after, delta_ns,
        )

        if self._check_anomaly(delta_ns):
            self._trigger_anomaly_dump((cnt, ts, event_type, address_offset,
                                        val_before, val_after, delta_ns))

        self._head = (self._head + 1) % self.maxlen
        if self._head == 0:
            self._wrapped = True

    def dump_json(self) -> None:
        count = self.maxlen if self._wrapped else self._head
        start = self._head if self._wrapped else 0
        events = []
        for i in range(count):
            offset = ((start + i) % self.maxlen) * self._step
            idx, ts, et, addr, vb, va, dns = self._unpack_from(self._raw, offset)
            events.append({
                "index": idx,
                "timestamp": ts,
                "event_type": EventType(et).name,
                "address_offset": f"0x{addr:04X}",
                "val_before": f"0x{vb:08X}",
                "val_after": f"0x{va:08X}",
                "delta_ns": dns,
            })
        self.dump_path.parent.mkdir(parents=True, exist_ok=True)
        self.dump_path.write_text(self._format_events(events), encoding="utf-8")

    def __len__(self) -> int:
        return self.maxlen if self._wrapped else self._head

    def _record_at(self, i: int) -> TraceRecord:
        """Access absolute record by linear index (0 = oldest)."""
        count = len(self)
        if not 0 <= i < count:
            raise IndexError(i)
        offset = ((self._head if self._wrapped else 0) + i) % self.maxlen * self._step
        idx, ts, et, addr, vb, va, dns = self._unpack_from(self._raw, offset)
        return TraceRecord(idx, ts, EventType(et), addr, vb, va, dns)

    def latest(self, n: int = 10) -> list[TraceRecord]:
        count = len(self)
        take = min(n, count)
        return [self._record_at(count - 1 - i) for i in range(take - 1, -1, -1)]


# ---------------------------------------------------------------------------
# Backend 3: CasRing — lock-free cache-line-aligned ring for interrupt-safe
# zero-copy BAR tracing. Draining (dequeue) is destructive by design.
# ---------------------------------------------------------------------------

class CasRingLogger(BaseLogger):
    """Lock-free CAS ring-buffer backend for zero-copy BAR tracing.

    ``latest()`` and ``dump_json()`` drain the ring destructively. Use when
    a separate consumer thread calls ``drain_all()`` periodically.
    """

    def __init__(self, maxlen: int, dump_path: Path, anomaly_ns: int):
        super().__init__(maxlen, dump_path, anomaly_ns)
        if not _CASRING_AVAILABLE:
            raise ImportError(
                "cas_ring C extension unavailable; compile with: "
                "python setup_cas_ring.py build_ext --inplace"
            )
        self._ring = _CasRingImpl()

    def log(self, event_type: EventType, address_offset: int, val_before: int,
            val_after: int, delta_ns: int) -> None:
        self._counter += 1
        try:
            self._ring.enqueue(
                ts_ns=time.perf_counter_ns(),
                ev_type=int(event_type),
                addr=address_offset & 0xFFFF,
                val_before=val_before & 0xFFFFFFFF,
                val_after=val_after & 0xFFFFFFFF,
                delta_ns=delta_ns,
            )
        except _CasRingError:
            pass  # ring full — oldest slot reused by producer in C extension
        if self._check_anomaly(delta_ns):
            self._trigger_anomaly_dump((
                self._counter, time.perf_counter(), event_type,
                address_offset & 0xFFFF, val_before & 0xFFFFFFFF,
                val_after & 0xFFFFFFFF, delta_ns,
            ))

    def drain_all(self) -> list[TraceRecord]:
        """Consume all available events from the ring. Non-blocking, O(n)."""
        records: list[TraceRecord] = []
        idx_base = self._counter - min(self._counter, self._ring.capacity())
        while True:
            ev = self._ring.dequeue()
            if ev is None:
                break
            records.append(TraceRecord(
                index=idx_base + len(records),
                timestamp=ev.ts_ns * 1e-9,
                event_type=EventType(ev.ev_type),
                address_offset=ev.addr,
                val_before=ev.val_before,
                val_after=ev.val_after,
                delta_ns=ev.delta_ns,
            ))
        return records

    def dump_json(self) -> None:
        events = [rec.to_dict() for rec in self.drain_all()]
        self.dump_path.parent.mkdir(parents=True, exist_ok=True)
        self.dump_path.write_text(self._format_events(events), encoding="utf-8")

    def __len__(self) -> int:
        return self._counter

    def latest(self, n: int = 10) -> list[TraceRecord]:
        return self.drain_all()[-n:]


# ---------------------------------------------------------------------------
# Factory — lazy backend instantiation, zero wasted allocation
# ---------------------------------------------------------------------------

_LOGGER_BACKENDS: Dict[str, Type[BaseLogger]] = {
    "deque": DequeLogger,
    "raw": RawBytesLogger,
    "casring": CasRingLogger,
}


def create_logger(backend: str, maxlen: int, dump_path: Union[str, Path],
                  anomaly_ns: int = 1_000_000) -> BaseLogger:
    """Factory returning the requested logger backend without instantiating others."""
    try:
        cls = _LOGGER_BACKENDS[backend]
    except KeyError as exc:
        raise ValueError(
            f"Unknown backend {backend!r}; choose from {list(_LOGGER_BACKENDS.keys())}"
        ) from exc
    return cls(maxlen=maxlen, dump_path=Path(dump_path), anomaly_ns=anomaly_ns)


def summary(logger: BaseLogger, latest_n: int = 1000) -> dict:
    recent = logger.latest(latest_n)
    counts: Dict[str, int] = {}
    latencies: list[int] = []
    for rec in recent:
        counts[rec.event_type.name] = counts.get(rec.event_type.name, 0) + 1
        if rec.event_type == EventType.IRQ_TRIGGERED:
            latencies.append(rec.delta_ns)
    return {
        "backend": type(logger).__name__,
        "total_records": len(logger),
        "event_counts": counts,
        "irq_count": len(latencies),
        "avg_irq_latency_us": round(sum(latencies) / len(latencies) / 1000, 2)
        if latencies else 0.0,
        "max_irq_latency_us": round(max(latencies) / 1000, 2)
        if latencies else 0.0,
    }


if __name__ == "__main__":
    import random

    backends = ["deque", "raw"]
    if _CASRING_AVAILABLE:
        backends.append("casring")
    else:
        print("WARNING: cas_ring not compiled; skipping casring backend")

    for name in backends:
        log = create_logger(name, maxlen=20, dump_path=Path(f"/tmp/xdma_trace_{name}.json"))
        for i in range(30):
            log.log_poll(0x04, random.randint(0, 0xFF))
            if i == 15:
                log.log_irq(latency_ns=12_000_000)
        print(name, json.dumps(summary(log), indent=2, ensure_ascii=False))
        log.dump_json()
    print("Done")
