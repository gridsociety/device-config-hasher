import hashlib
import json
import random
from pathlib import Path

import pytest

from device_config_hasher.canonical import (
    ALGORITHM,
    ParameterMaterial,
    canonical_json,
    device_hash,
    device_material,
    plant_hash,
    plant_material,
    sha256_hex,
)

VECTORS = Path(__file__).parent / "vectors"


def test_algorithm_identifier() -> None:
    assert ALGORITHM == "sha256-jcs-v1"


def test_canonical_json_sorts_keys_and_strips_whitespace() -> None:
    assert canonical_json({"b": 1, "a": [1, 2]}) == b'{"a":[1,2],"b":1}'


def test_canonical_json_sorts_keys_by_code_point() -> None:
    # "Z" (0x5A) sorts before "a" (0x61)
    assert canonical_json({"a": 1, "Z": 2}) == b'{"Z":2,"a":1}'


def test_canonical_json_keeps_non_ascii_raw() -> None:
    assert canonical_json({"k": "é"}) == '{"k":"é"}'.encode()


def test_canonical_json_escapes_control_chars_minimally() -> None:
    assert canonical_json({"k": 'a\n"b"\\'}) == b'{"k":"a\\n\\"b\\"\\\\"}'


def test_canonical_json_rejects_floats() -> None:
    with pytest.raises(TypeError):
        canonical_json({"k": 1.5})


def test_canonical_json_rejects_bool_and_none() -> None:
    with pytest.raises(TypeError):
        canonical_json({"k": True})
    with pytest.raises(TypeError):
        canonical_json({"k": None})


def test_sha256_hex() -> None:
    assert sha256_hex(b"abc") == hashlib.sha256(b"abc").hexdigest()


def _params() -> list[ParameterMaterial]:
    return [
        ParameterMaterial("b_param", "holding", 10, [1]),
        ParameterMaterial("a_param", "holding", 10, [1]),
        ParameterMaterial("z", "input", 0, [0x3F80, 0]),
        ParameterMaterial("c", "coil", 5, [1]),
        ParameterMaterial("y", "holding", 2, [7]),
    ]


def test_device_material_sorted_by_register_address_name() -> None:
    m = device_material("dev", "prof", 1, "p" * 64, "t" * 64, _params())
    assert m["id"] == "dev"
    assert m["profile"] == {"name": "prof", "version": 1, "sha256": "p" * 64}
    assert m["tool"] == {"source_sha256": "t" * 64}
    order = [(p["r"], p["a"], p["n"]) for p in m["parameters"]]  # type: ignore[index]
    assert order == [
        ("coil", 5, "c"),
        ("holding", 2, "y"),
        ("holding", 10, "a_param"),
        ("holding", 10, "b_param"),
        ("input", 0, "z"),
    ]
    assert m["parameters"][3] == {"n": "b_param", "r": "holding", "a": 10, "w": [1]}  # type: ignore[index]


def test_device_hash_is_order_independent() -> None:
    params = _params()
    shuffled = params[:]
    random.Random(42).shuffle(shuffled)
    h1 = device_hash(device_material("dev", "prof", 1, "p" * 64, "t" * 64, params))
    h2 = device_hash(device_material("dev", "prof", 1, "p" * 64, "t" * 64, shuffled))
    assert h1 == h2
    assert len(h1) == 64 and h1 == h1.lower()


def test_device_hash_changes_when_one_word_changes() -> None:
    params = _params()
    h1 = device_hash(device_material("dev", "prof", 1, "p" * 64, "t" * 64, params))
    params[0] = ParameterMaterial("b_param", "holding", 10, [2])
    h2 = device_hash(device_material("dev", "prof", 1, "p" * 64, "t" * 64, params))
    assert h1 != h2


def test_device_hash_changes_with_profile_or_tool_hash() -> None:
    base = device_hash(device_material("dev", "prof", 1, "p" * 64, "t" * 64, _params()))
    assert base != device_hash(device_material("dev", "prof", 1, "q" * 64, "t" * 64, _params()))
    assert base != device_hash(device_material("dev", "prof", 1, "p" * 64, "u" * 64, _params()))
    assert base != device_hash(device_material("dev", "prof", 2, "p" * 64, "t" * 64, _params()))


def test_plant_material_sorted_by_id() -> None:
    m = plant_material("arizzi", {"inverter": "a" * 64, "bms": "b" * 64})
    assert m == {
        "plant": "arizzi",
        "devices": [{"id": "bms", "sha256": "b" * 64}, {"id": "inverter", "sha256": "a" * 64}],
    }
    assert len(plant_hash(m)) == 64


@pytest.mark.parametrize("vector", sorted(VECTORS.glob("*.json")), ids=lambda p: p.stem)
def test_vectors(vector: Path) -> None:
    data = json.loads(vector.read_text(encoding="utf-8"))
    canonical = canonical_json(data["material"])
    assert canonical.decode("utf-8") == data["canonical"]
    assert sha256_hex(canonical) == data["sha256"]
    assert hashlib.sha256(data["canonical"].encode("utf-8")).hexdigest() == data["sha256"]


def test_vectors_exist() -> None:
    assert len(list(VECTORS.glob("*.json"))) >= 2
