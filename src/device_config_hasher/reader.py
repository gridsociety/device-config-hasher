"""Plan and execute the reads for one device according to its profile."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from device_config_hasher.decode import WORDS_PER_TYPE
from device_config_hasher.modbus import ModbusReadError, ReadOnlyModbusClient
from device_config_hasher.profile import Parameter, Profile

Quality = Literal["good", "error"]


@dataclass(frozen=True)
class ReadBlock:
    register: str
    start: int
    count: int

    @property
    def end(self) -> int:
        """Exclusive end address."""
        return self.start + self.count


@dataclass
class ParameterReading:
    parameter: Parameter
    pdu_address: int
    raw: list[int] | None
    quality: Quality
    error: str | None = None


def _width(p: Parameter) -> int:
    return 1 if p.register in ("coil", "discrete") else WORDS_PER_TYPE[p.type]


def plan_reads(
    profile: Profile, *, max_gap: int = 0, max_words: int = 100, max_bits: int = 2000
) -> list[ReadBlock]:
    """Group parameters into contiguous read blocks per register type.

    Blocks are sorted by ``(register, start)``. Two parameters are merged into
    one block when the gap between them is at most *max_gap* addresses and the
    resulting block does not exceed *max_words* (or *max_bits* for coils and
    discrete inputs).
    """
    spans: dict[str, list[tuple[int, int]]] = {}
    for p in profile.parameters:
        start = profile.pdu_address(p)
        spans.setdefault(p.register, []).append((start, start + _width(p)))

    blocks: list[ReadBlock] = []
    for register in sorted(spans):
        limit = max_bits if register in ("coil", "discrete") else max_words
        current: tuple[int, int] | None = None
        for start, end in sorted(spans[register]):
            if current is not None and start <= current[1] + max_gap and end - current[0] <= limit:
                current = (current[0], max(current[1], end))
            else:
                if current is not None:
                    blocks.append(ReadBlock(register, current[0], current[1] - current[0]))
                current = (start, end)
        if current is not None:
            blocks.append(ReadBlock(register, current[0], current[1] - current[0]))
    return blocks


def read_device(
    profile: Profile,
    client: ReadOnlyModbusClient,
    *,
    max_gap: int = 0,
    max_words: int = 100,
    max_bits: int = 2000,
) -> list[ParameterReading]:
    """Read every parameter of *profile* through *client*.

    Readings are returned in profile order. A block that fails marks every
    parameter it covers as ``quality="error"``; the other blocks are still
    read so the operator sees everything that could be obtained.
    """
    blocks = plan_reads(profile, max_gap=max_gap, max_words=max_words, max_bits=max_bits)
    results: dict[tuple[str, int], list[int] | str] = {}
    for block in blocks:
        try:
            if block.register in ("coil", "discrete"):
                values = client.read_bits(block.register, block.start, block.count)  # type: ignore[arg-type]
            else:
                values = client.read_words(block.register, block.start, block.count)  # type: ignore[arg-type]
        except ModbusReadError as exc:
            for addr in range(block.start, block.end):
                results[(block.register, addr)] = str(exc)
            continue
        for offset, value in enumerate(values):
            results[(block.register, block.start + offset)] = [value]

    readings: list[ParameterReading] = []
    for p in profile.parameters:
        pdu = profile.pdu_address(p)
        words: list[int] = []
        error: str | None = None
        for addr in range(pdu, pdu + _width(p)):
            got = results.get((p.register, addr))
            if isinstance(got, list):
                words.extend(got)
            else:
                error = got if isinstance(got, str) else "address not read"
                break
        if error is None:
            readings.append(ParameterReading(p, pdu, words, "good"))
        else:
            readings.append(ParameterReading(p, pdu, None, "error", error))
    return readings
