"""Shared fixtures: an in-process Modbus TCP simulator.

The simulator runs a pymodbus ``ModbusTcpServer`` in a background thread for
the whole test session. Each test gets a fresh :class:`Simulator` handle whose
dictionaries (``holding``, ``input``, ``coils``, ``discrete``) are overlaid on
the server's registers at request time, so tests can mutate register contents
without restarting the server. Every request's function code is recorded so
tests can assert that only read functions are ever used.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from pymodbus.constants import ExcCodes
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

HOLDING_SIZE = 3000
INPUT_SIZE = 1000
BIT_WORDS = 64  # 64 words of coils/discrete inputs = 1024 bits


@dataclass
class Simulator:
    host: str
    port: int
    unit_id: int
    holding: dict[int, int] = field(default_factory=dict)
    input: dict[int, int] = field(default_factory=dict)
    coils: dict[int, int] = field(default_factory=dict)
    discrete: dict[int, int] = field(default_factory=dict)
    #: PDU addresses of holding registers that answer ILLEGAL_ADDRESS.
    invalid_holding: set[int] = field(default_factory=set)
    #: Holding addresses that fail once and are removed on the first request.
    flaky_holding: set[int] = field(default_factory=set)
    seen_function_codes: list[int] = field(default_factory=list)

    def reset(self) -> None:
        self.holding.clear()
        self.input.clear()
        self.coils.clear()
        self.discrete.clear()
        self.invalid_holding.clear()
        self.flaky_holding.clear()
        self.seen_function_codes.clear()

    async def action(
        self,
        function_code: int,
        start: int,
        address: int,
        count: int,
        regs: list[int],
        set_values: Any,
    ) -> ExcCodes | None:
        if function_code == 3:
            requested = set(range(address, address + count))
            if requested & self.invalid_holding:
                return ExcCodes.ILLEGAL_ADDRESS
            flaky = requested & self.flaky_holding
            if flaky:
                self.flaky_holding -= flaky
                return ExcCodes.DEVICE_BUSY
            self._overlay_words(self.holding, start, regs)
        elif function_code == 4:
            self._overlay_words(self.input, start, regs)
        elif function_code == 1:
            self._overlay_bits(self.coils, start, regs)
        elif function_code == 2:
            self._overlay_bits(self.discrete, start, regs)
        return None

    @staticmethod
    def _overlay_words(values: dict[int, int], start: int, regs: list[int]) -> None:
        for addr, value in values.items():
            i = addr - start
            if 0 <= i < len(regs):
                regs[i] = value

    @staticmethod
    def _overlay_bits(values: dict[int, int], start: int, regs: list[int]) -> None:
        for addr, value in values.items():
            i = addr // 16 - start
            if 0 <= i < len(regs):
                mask = 1 << (addr % 16)
                regs[i] = (regs[i] | mask) if value else (regs[i] & ~mask)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="session")
def _simulator_server() -> Iterator[Simulator]:
    port = _free_port()
    sim = Simulator(host="127.0.0.1", port=port, unit_id=1)

    def trace_pdu(sending: bool, pdu: Any) -> Any:
        if not sending:
            sim.seen_function_codes.append(int(pdu.function_code))
        return pdu

    device = SimDevice(
        id=sim.unit_id,
        simdata=(
            [SimData(0, count=BIT_WORDS * 16, values=False, datatype=DataType.BITS)],
            [SimData(0, count=BIT_WORDS * 16, values=False, datatype=DataType.BITS)],
            [SimData(0, count=HOLDING_SIZE, values=0, datatype=DataType.REGISTERS)],
            [SimData(0, count=INPUT_SIZE, values=0, datatype=DataType.REGISTERS)],
        ),
        action=sim.action,
    )

    loop = asyncio.new_event_loop()
    ready = threading.Event()
    holder: dict[str, ModbusTcpServer] = {}

    async def serve() -> None:
        holder["server"] = ModbusTcpServer(
            device, address=(sim.host, sim.port), trace_pdu=trace_pdu
        )
        ready.set()
        await holder["server"].serve_forever()

    thread = threading.Thread(target=lambda: loop.run_until_complete(serve()), daemon=True)
    thread.start()
    assert ready.wait(5)
    for _ in range(100):
        try:
            socket.create_connection((sim.host, sim.port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    else:
        raise RuntimeError("simulator did not start")

    yield sim

    asyncio.run_coroutine_threadsafe(holder["server"].shutdown(), loop).result(5)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)
    loop.close()


@pytest.fixture
def simulator(_simulator_server: Simulator) -> Simulator:
    _simulator_server.reset()
    return _simulator_server
