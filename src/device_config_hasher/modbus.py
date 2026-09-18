"""Thin, read-only Modbus TCP client.

Only function codes 1 (read coils), 2 (read discrete inputs), 3 (read holding
registers) and 4 (read input registers) are reachable from this module. There
is deliberately no write method (spec §1.1, §3.1).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Literal

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

WordRegister = Literal["holding", "input"]
BitRegister = Literal["coil", "discrete"]


class ModbusReadError(Exception):
    """A read could not be completed (connection, timeout or Modbus exception)."""


@dataclass
class Connection:
    host: str
    port: int = 502
    unit_id: int = 1
    timeout_s: float = 3.0
    retries: int = 2
    retry_delay_s: float = 0.2


_EXC_NAMES = {
    1: "ILLEGAL_FUNCTION",
    2: "ILLEGAL_ADDRESS",
    3: "ILLEGAL_VALUE",
    4: "DEVICE_FAILURE",
    5: "ACKNOWLEDGE",
    6: "DEVICE_BUSY",
    8: "MEMORY_PARITY_ERROR",
    10: "GATEWAY_PATH_UNAVAILABLE",
    11: "GATEWAY_NO_RESPONSE",
}


class ReadOnlyModbusClient:
    """Context-managed client exposing only read operations."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        # pymodbus' own retry loop is disabled; retries are handled here so
        # that their count and behaviour are explicit and testable.
        self._client = ModbusTcpClient(conn.host, port=conn.port, timeout=conn.timeout_s, retries=0)

    def __enter__(self) -> ReadOnlyModbusClient:
        connected: bool = self._client.connect()  # type: ignore[no-untyped-call]
        if not connected:
            raise ModbusReadError(f"cannot connect to {self._conn.host}:{self._conn.port}")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._client.close()  # type: ignore[no-untyped-call]

    def read_words(self, register: WordRegister, pdu_address: int, count: int) -> list[int]:
        """Read *count* 16-bit words (function code 3 or 4)."""
        fn = (
            self._client.read_holding_registers
            if register == "holding"
            else self._client.read_input_registers
        )
        response = self._request(fn, pdu_address, count)
        words = [int(w) for w in response.registers[:count]]
        if len(words) != count:
            raise ModbusReadError(f"short response: expected {count} words, got {len(words)}")
        return words

    def read_bits(self, register: BitRegister, pdu_address: int, count: int) -> list[int]:
        """Read *count* bits (function code 1 or 2), returned as 0/1 integers."""
        fn = self._client.read_coils if register == "coil" else self._client.read_discrete_inputs
        response = self._request(fn, pdu_address, count)
        bits = [1 if b else 0 for b in response.bits[:count]]
        if len(bits) != count:
            raise ModbusReadError(f"short response: expected {count} bits, got {len(bits)}")
        return bits

    def _request(self, fn: Any, pdu_address: int, count: int) -> Any:
        attempts = self._conn.retries + 1
        last_error = "unknown error"
        for attempt in range(attempts):
            if attempt:
                time.sleep(self._conn.retry_delay_s)
            try:
                response = fn(pdu_address, count=count, device_id=self._conn.unit_id)
            except ModbusException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            if response.isError():
                code = getattr(response, "exception_code", None)
                name = _EXC_NAMES.get(code, str(code)) if code is not None else "no response"
                last_error = f"modbus exception {name}"
                continue
            return response
        raise ModbusReadError(
            f"read {pdu_address}+{count} failed after {attempts} attempt(s): {last_error}"
        )
