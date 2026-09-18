"""Pin the exact parameter set of every bundled profile.

Any change to a bundled profile changes hashes for every operator using it,
so it must show up here as an explicit, reviewed diff.
"""

from __future__ import annotations

from device_config_hasher.profile import bundled_profiles_dir, find_profile, list_profiles

INGETEAM = {
    ("operation_mode_request", "holding", 42202, "u16"),
    ("reactive_power_control_mode", "holding", 42203, "u16"),
    ("active_power_increase_settling_time", "holding", 42204, "u16"),
    ("active_power_decrease_settling_time", "holding", 42205, "u16"),
    ("reactive_power_increase_settling_time", "holding", 42206, "u16"),
    ("reactive_power_decrease_settling_time", "holding", 42207, "u16"),
    ("communication_watchdog_timeout", "holding", 42208, "u16"),
    ("voltage_ramp", "holding", 42209, "u16"),
    ("frequency_ramp", "holding", 42210, "u16"),
    ("strategy_mode_bits_1", "holding", 42212, "u16"),
    ("type_of_battery", "holding", 42218, "u16"),
    ("maximum_battery_voltage", "holding", 42219, "u16"),
    ("minimum_battery_voltage", "holding", 42220, "u16"),
    ("maximum_battery_charge_current", "holding", 42221, "u16"),
    ("maximum_battery_discharge_current", "holding", 42222, "u16"),
    ("grid_forming_voltage_droop", "holding", 42250, "u16"),
    ("grid_forming_frequency_droop", "holding", 42252, "u16"),
    ("grid_forming_connection_mode", "holding", 42253, "u16"),
}
INGETEAM_EXCLUDED = {
    42201,
    42213,
    42217,
    42223,
    42231,
    42232,
    42233,
    42234,
    42235,
    42242,
    42249,
    42251,
    42261,
}


def _params(name: str) -> set[tuple[str, str, int, str]]:
    p = find_profile(name)
    return {(x.name, x.register, x.address, x.type) for x in p.parameters}


def test_bundled_directory_lists_exactly_three_profiles() -> None:
    names = sorted(p.name for p in list_profiles())
    assert names == ["ingeteam-sun-storage-3power-c", "jinko-scu-bank", "jinko-scu-rack"]
    files = sorted(f.name for f in bundled_profiles_dir().iterdir() if f.suffix == ".yaml")
    assert files == [f"{n}.yaml" for n in names]


def test_ingeteam_profile() -> None:
    p = find_profile("ingeteam-sun-storage-3power-c")
    assert p.version == 1
    assert p.protocol.addressing == "modicon"
    assert _params(p.name) == INGETEAM
    assert {e.address for e in p.excluded} == INGETEAM_EXCLUDED
    assert all(e.reason for e in p.excluded)


def test_jinko_bank_profile() -> None:
    p = find_profile("jinko-scu-bank")
    assert p.version == 1
    assert p.protocol.addressing == "one_based"
    assert _params(p.name) == {
        ("number_of_racks", "input", 103, "f32"),
        ("number_of_cells", "input", 105, "f32"),
        ("number_of_temperature_sensors", "input", 107, "f32"),
        ("number_of_packs", "input", 109, "f32"),
    }
    assert {e.name for e in p.excluded} >= {"local_remote_status", "heartbeat", "rtc"}


def test_jinko_rack_profile() -> None:
    p = find_profile("jinko-scu-rack")
    assert p.version == 1
    assert _params(p.name) == {("insulation_enabled", "input", 61, "f32")}
    assert p.pdu_address(p.parameters[0]) == 60


def test_bundled_profiles_have_unit_id_and_documentation() -> None:
    for p in list_profiles():
        assert p.protocol.default_unit_id == 1
        assert p.documentation
        assert p.sha256 and p.source.endswith("\n")
