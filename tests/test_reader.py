from __future__ import annotations

import pytest

from device_config_hasher.modbus import Connection, ModbusReadError, ReadOnlyModbusClient
from device_config_hasher.profile import parse_profile
from device_config_hasher.reader import ReadBlock, plan_reads, read_device
from tests.conftest import Simulator

PROFILE = """\
schema: dch-profile/1
name: sim-device
version: 1
protocol: {type: modbus_tcp, addressing: pdu}
parameters:
  - {name: h_b, register: holding, address: 12, type: u16}
  - {name: h_a, register: holding, address: 10, type: u16}
  - {name: h_c, register: holding, address: 13, type: i16}
  - {name: h_f, register: holding, address: 20, type: f32}
  - {name: h_bit3, register: holding, address: 30, type: bit, bit: 3}
  - {name: h_bit4, register: holding, address: 30, type: bit, bit: 4}
  - {name: i_a, register: input, address: 5, type: u32}
  - {name: c_a, register: coil, address: 17, type: bit}
  - {name: c_b, register: coil, address: 18, type: bit}
  - {name: d_a, register: discrete, address: 40, type: bit}
"""


def _client(sim: Simulator, **kw: object) -> ReadOnlyModbusClient:
    return ReadOnlyModbusClient(
        Connection(host=sim.host, port=sim.port, unit_id=sim.unit_id, timeout_s=1.0, **kw)  # type: ignore[arg-type]
    )


def test_plan_reads_groups_contiguous_and_sorts() -> None:
    profile = parse_profile(PROFILE)
    blocks = plan_reads(profile)
    assert blocks == [
        ReadBlock("coil", 17, 2),
        ReadBlock("discrete", 40, 1),
        ReadBlock("holding", 10, 1),
        ReadBlock("holding", 12, 2),
        ReadBlock("holding", 20, 2),
        ReadBlock("holding", 30, 1),
        ReadBlock("input", 5, 2),
    ]


def test_plan_reads_with_gap() -> None:
    profile = parse_profile(PROFILE)
    holding = [b for b in plan_reads(profile, max_gap=1) if b.register == "holding"]
    assert holding == [
        ReadBlock("holding", 10, 4),
        ReadBlock("holding", 20, 2),
        ReadBlock("holding", 30, 1),
    ]
    holding = [b for b in plan_reads(profile, max_gap=100) if b.register == "holding"]
    assert holding == [ReadBlock("holding", 10, 21)]


def test_plan_reads_respects_max_words() -> None:
    profile = parse_profile(PROFILE)
    holding = [b for b in plan_reads(profile, max_gap=100, max_words=12) if b.register == "holding"]
    assert holding == [ReadBlock("holding", 10, 12), ReadBlock("holding", 30, 1)]


def test_read_device_good(simulator: Simulator) -> None:
    simulator.holding.update({10: 1000, 12: 1200, 13: 0xFFFF, 20: 0x3F80, 21: 0, 30: 0b11000})
    simulator.input.update({5: 1, 6: 2})
    simulator.coils.update({17: 1, 18: 0})
    simulator.discrete.update({40: 1})
    profile = parse_profile(PROFILE)
    with _client(simulator) as client:
        readings = read_device(profile, client)
    by_name = {r.parameter.name: r for r in readings}
    assert [r.parameter.name for r in readings] == [p.name for p in profile.parameters]
    assert all(r.quality == "good" and r.error is None for r in readings)
    assert by_name["h_a"].raw == [1000]
    assert by_name["h_b"].raw == [1200]
    assert by_name["h_c"].raw == [0xFFFF]
    assert by_name["h_f"].raw == [0x3F80, 0]
    assert by_name["h_bit3"].raw == [0b11000]
    assert by_name["h_bit4"].raw == [0b11000]
    assert by_name["i_a"].raw == [1, 2]
    assert by_name["c_a"].raw == [1]
    assert by_name["c_b"].raw == [0]
    assert by_name["d_a"].raw == [1]
    assert by_name["c_a"].pdu_address == 17
    assert set(simulator.seen_function_codes) <= {1, 2, 3, 4}
    assert set(simulator.seen_function_codes) == {1, 2, 3, 4}


def test_read_device_partial_failure(simulator: Simulator) -> None:
    simulator.invalid_holding.add(12)
    profile = parse_profile(PROFILE)
    with _client(simulator, retries=0) as client:
        readings = read_device(profile, client)
    by_name = {r.parameter.name: r for r in readings}
    assert by_name["h_b"].quality == "error" and by_name["h_b"].raw is None
    assert by_name["h_c"].quality == "error"
    assert by_name["h_b"].error is not None and "ILLEGAL_ADDRESS" in by_name["h_b"].error
    assert by_name["h_a"].quality == "good"
    assert by_name["i_a"].quality == "good"


def test_read_device_retries_transient_error(simulator: Simulator) -> None:
    simulator.flaky_holding.add(10)
    simulator.holding[10] = 5
    profile = parse_profile(PROFILE)
    with _client(simulator, retries=1) as client:
        readings = read_device(profile, client)
    assert readings[1].parameter.name == "h_a"
    assert readings[1].quality == "good" and readings[1].raw == [5]
    assert simulator.seen_function_codes.count(3) == 5  # 4 holding blocks + 1 retry


def test_read_device_no_retries_reports_transient_error(simulator: Simulator) -> None:
    simulator.flaky_holding.add(10)
    profile = parse_profile(PROFILE)
    with _client(simulator, retries=0) as client:
        readings = read_device(profile, client)
    assert readings[1].quality == "error"


def test_client_refuses_unreachable_host() -> None:
    conn = Connection(host="127.0.0.1", port=1, timeout_s=0.5, retries=0)
    with pytest.raises(ModbusReadError), ReadOnlyModbusClient(conn):
        pass


def test_client_has_no_write_path() -> None:
    names = [n for n in dir(ReadOnlyModbusClient) if not n.startswith("_")]
    assert not any("write" in n for n in names)
    assert sorted(names) == ["read_bits", "read_words"]


def test_read_out_of_range_raises(simulator: Simulator) -> None:
    with _client(simulator, retries=0) as client, pytest.raises(ModbusReadError):
        client.read_words("holding", 60000, 2)


OFFSET_PROFILE = """\
schema: dch-profile/1
name: offset-device
version: 1
protocol: {type: modbus_tcp, addressing: one_based, address_offset: 2}
parameters:
  - {name: racks, register: input, address: 103, type: f32}
"""


def test_address_offset_reaches_the_wire(simulator: Simulator) -> None:
    """Document address 103 with offset 2 must read PDU words 104 and 105."""
    simulator.input[102] = 0xDEAD
    simulator.input[103] = 0xBEEF
    simulator.input[104] = 0x4140  # 12.0 as float32, high word
    simulator.input[105] = 0x0000
    profile = parse_profile(OFFSET_PROFILE)
    with _client(simulator) as client:
        readings = read_device(profile, client)
    (r,) = readings
    assert r.pdu_address == 104
    assert r.raw == [0x4140, 0x0000]
    assert r.quality == "good"
