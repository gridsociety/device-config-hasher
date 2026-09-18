import hashlib
from pathlib import Path

import pytest

from device_config_hasher.profile import (
    ProfileError,
    bundled_profiles_dir,
    find_profile,
    list_profiles,
    load_profile,
    normalize_lf,
    parse_profile,
)

MINIMAL = """\
schema: dch-profile/1
name: demo-device
version: 3
title: Demo device
protocol:
  type: modbus_tcp
  addressing: modicon
parameters:
  - name: watchdog
    register: holding
    address: 42208
    type: u16
    unit: ms
  - name: settle
    register: holding
    address: 42204
    type: u16
    scale: 0.001
    unit: s
  - name: battery
    register: holding
    address: 42218
    type: u16
    enum: {1: "No contactor", 2: "With contactor"}
  - name: flag
    register: holding
    address: 42212
    type: bit
    bit: 3
  - name: insulation
    register: input
    address: 30061
    type: f32
  - name: relay
    register: coil
    address: 17
    type: bit
excluded:
  - address: 42201
    name: start_stop
    reason: command
"""


def test_parse_minimal_profile() -> None:
    p = parse_profile(MINIMAL)
    assert p.name == "demo-device"
    assert p.version == 3
    assert p.protocol.addressing == "modicon"
    assert p.protocol.word_order == "big"
    assert p.protocol.default_unit_id == 1
    assert [x.name for x in p.parameters] == [
        "watchdog",
        "settle",
        "battery",
        "flag",
        "insulation",
        "relay",
    ]
    assert p.parameters[2].enum == {1: "No contactor", 2: "With contactor"}
    assert p.excluded[0].reason == "command"


def test_source_and_sha256_are_lf_normalized() -> None:
    crlf = MINIMAL.replace("\n", "\r\n")
    p_lf, p_crlf = parse_profile(MINIMAL), parse_profile(crlf)
    assert p_lf.source == MINIMAL
    assert p_crlf.source == MINIMAL
    assert p_lf.sha256 == p_crlf.sha256 == hashlib.sha256(MINIMAL.encode()).hexdigest()
    assert normalize_lf("a\r\nb\rc\n") == "a\nb\nc\n"


def test_load_profile_from_file(tmp_path: Path) -> None:
    path = tmp_path / "demo.yaml"
    path.write_bytes(MINIMAL.replace("\n", "\r\n").encode())
    p = load_profile(path)
    assert p.sha256 == hashlib.sha256(MINIMAL.encode()).hexdigest()


def test_pdu_address_modicon() -> None:
    p = parse_profile(MINIMAL)
    by_name = {x.name: x for x in p.parameters}
    assert p.pdu_address(by_name["watchdog"]) == 2207
    assert p.pdu_address(by_name["insulation"]) == 60
    assert p.pdu_address(by_name["relay"]) == 16


def _single(addressing: str, register: str, address: int) -> str:
    type_ = "bit" if register in ("coil", "discrete") else "u16"
    return f"""\
schema: dch-profile/1
name: x
version: 1
protocol: {{type: modbus_tcp, addressing: {addressing}}}
parameters:
  - {{name: p, register: {register}, address: {address}, type: {type_}}}
"""


@pytest.mark.parametrize(
    ("addressing", "register", "address", "expected"),
    [
        ("pdu", "holding", 5, 5),
        ("pdu", "coil", 0, 0),
        ("one_based", "holding", 103, 102),
        ("one_based", "input", 61, 60),
        ("modicon", "holding", 40001, 0),
        ("modicon", "input", 30001, 0),
        ("modicon", "coil", 1, 0),
        ("modicon", "discrete", 10001, 0),
    ],
)
def test_pdu_address_modes(addressing: str, register: str, address: int, expected: int) -> None:
    text = _single(addressing, register, address)
    p = parse_profile(text)
    assert p.pdu_address(p.parameters[0]) == expected


@pytest.mark.parametrize(
    ("addressing", "register", "address"),
    [
        ("pdu", "holding", -1),
        ("pdu", "holding", 65536),
        ("one_based", "holding", 0),
        ("modicon", "holding", 30001),
        ("modicon", "input", 40001),
        ("modicon", "coil", 10001),
        ("modicon", "holding", 50000),
    ],
)
def test_address_out_of_range(addressing: str, register: str, address: int) -> None:
    text = _single(addressing, register, address)
    with pytest.raises(ProfileError, match="p"):
        parse_profile(text)


def _with(params: str, extra: str = "") -> str:
    return f"""\
schema: dch-profile/1
name: x
version: 1
protocol: {{type: modbus_tcp, addressing: pdu}}
parameters:
{params}
{extra}"""


@pytest.mark.parametrize(
    ("text", "match"),
    [
        (MINIMAL.replace("dch-profile/1", "dch-profile/9"), "schema"),
        (MINIMAL.replace("name: demo-device", "name: Demo_Device"), "name"),
        (_with("  - {name: Bad, register: holding, address: 1, type: u16}"), "Bad"),
        (
            _with(
                "  - {name: a, register: holding, address: 1, type: u16}\n"
                "  - {name: a, register: holding, address: 2, type: u16}"
            ),
            "duplicate",
        ),
        (
            _with(
                "  - {name: a, register: holding, address: 1, type: u16}\n"
                "  - {name: b, register: holding, address: 1, type: u16}"
            ),
            "same",
        ),
        (
            _with(
                "  - {name: a, register: holding, address: 1, type: bit, bit: 0}\n"
                "  - {name: b, register: holding, address: 1, type: bit, bit: 0}"
            ),
            "same",
        ),
        (_with("  - {name: a, register: holding, address: 1, type: bit}"), "bit"),
        (_with("  - {name: a, register: holding, address: 1, type: bit, bit: 16}"), "bit"),
        (_with("  - {name: a, register: coil, address: 1, type: bit, bit: 0}"), "bit"),
        (_with("  - {name: a, register: coil, address: 1, type: u16}"), "coil"),
        (
            _with(
                "  - {name: a, register: holding, address: 1, type: u16}",
                "excluded:\n  - {address: 1, reason: command}",
            ),
            "excluded",
        ),
        (_with("  - {name: a, register: holding, address: 1, type: u16, bogus: 1}"), "bogus"),
        ("just a string", "profile"),
    ],
)
def test_validation_errors(text: str, match: str) -> None:
    with pytest.raises(ProfileError, match=match):
        parse_profile(text)


def test_two_bits_on_same_word_allowed() -> None:
    p = parse_profile(
        _with(
            "  - {name: a, register: holding, address: 1, type: bit, bit: 0}\n"
            "  - {name: b, register: holding, address: 1, type: bit, bit: 1}"
        )
    )
    assert len(p.parameters) == 2


def test_bundled_profiles_dir_exists() -> None:
    assert bundled_profiles_dir().is_dir()


def test_find_profile_by_path_and_name(tmp_path: Path) -> None:
    path = tmp_path / "demo.yaml"
    path.write_text(MINIMAL)
    assert find_profile(str(path)).name == "demo-device"
    assert find_profile("demo-device", extra_dirs=[tmp_path]).name == "demo-device"
    with pytest.raises(ProfileError, match="no-such"):
        find_profile("no-such-profile")


def test_list_profiles_includes_extra_dirs(tmp_path: Path) -> None:
    (tmp_path / "demo.yaml").write_text(MINIMAL)
    names = [p.name for p in list_profiles(extra_dirs=[tmp_path])]
    assert names[-1] == "demo-device"
    assert names == [*sorted(names[:-1]), "demo-device"]


def _offset_profile(addressing: str, offset: int, register: str, address: int) -> str:
    return f"""\
schema: dch-profile/1
name: offset-device
version: 1
protocol: {{type: modbus_tcp, addressing: {addressing}, address_offset: {offset}}}
parameters:
  - {{name: p, register: {register}, address: {address}, type: u16}}
"""


@pytest.mark.parametrize(
    ("addressing", "offset", "register", "address", "expected"),
    [
        ("pdu", 0, "input", 103, 103),
        ("pdu", 1, "input", 103, 104),
        ("one_based", 2, "input", 103, 104),
        ("one_based", 2, "input", 61, 62),
        ("one_based", -1, "holding", 5, 3),
        ("modicon", 1, "holding", 42208, 2208),
    ],
)
def test_address_offset_is_added_after_conversion(
    addressing: str, offset: int, register: str, address: int, expected: int
) -> None:
    p = parse_profile(_offset_profile(addressing, offset, register, address))
    assert p.pdu_address(p.parameters[0]) == expected


def test_address_offset_defaults_to_zero() -> None:
    p = parse_profile(_single("one_based", "input", 103))
    assert p.protocol.address_offset == 0
    assert p.pdu_address(p.parameters[0]) == 102


@pytest.mark.parametrize(
    ("addressing", "offset", "register", "address"),
    [
        ("one_based", -1, "input", 1),
        ("pdu", 1, "holding", 0xFFFF),
    ],
)
def test_address_offset_out_of_range_is_rejected(
    addressing: str, offset: int, register: str, address: int
) -> None:
    with pytest.raises(ProfileError, match="out of range"):
        parse_profile(_offset_profile(addressing, offset, register, address))
