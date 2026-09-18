"""Canonical serialization and hashing (spec §7).

This module is normative: it has no third-party dependencies and implements
the subset of RFC 8785 (JSON Canonicalization Scheme) that the hashed
material exercises. The material contains only strings, integers, lists and
objects; floats, booleans and null are rejected so that they can never enter
the trust chain by accident.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TypeAlias

Json: TypeAlias = str | int | list["Json"] | dict[str, "Json"]

ALGORITHM = "sha256-jcs-v1"

_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _escape_string(value: str) -> str:
    out: list[str] = ['"']
    for ch in value:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _canonical(value: Json, out: list[str]) -> None:
    # bool is a subclass of int in Python: reject it explicitly.
    if isinstance(value, bool) or value is None or isinstance(value, float):
        raise TypeError(f"value of type {type(value).__name__} is not allowed in hashed material")
    if isinstance(value, str):
        out.append(_escape_string(value))
    elif isinstance(value, int):
        out.append(str(value))
    elif isinstance(value, list):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _canonical(item, out)
        out.append("]")
    elif isinstance(value, dict):
        out.append("{")
        # RFC 8785 sorts keys by UTF-16 code units; for the BMP-only keys used
        # here that is identical to sorting by code point, which is what
        # Python's default str ordering does.
        for i, key in enumerate(sorted(value)):
            if i:
                out.append(",")
            if not isinstance(key, str):
                raise TypeError("object keys must be strings")
            out.append(_escape_string(key))
            out.append(":")
            _canonical(value[key], out)
        out.append("}")
    else:
        raise TypeError(f"value of type {type(value).__name__} is not allowed in hashed material")


def canonical_json(value: Json) -> bytes:
    """Serialize *value* to canonical JSON (RFC 8785 subset) as UTF-8 bytes."""
    out: list[str] = []
    _canonical(value, out)
    return "".join(out).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ParameterMaterial:
    """Hashed material of one parameter (spec §7.1)."""

    name: str
    register: str
    pdu_address: int
    words: list[int]

    def to_json(self) -> dict[str, Json]:
        return {"n": self.name, "r": self.register, "a": self.pdu_address, "w": list(self.words)}


def device_material(
    device_id: str,
    profile_name: str,
    profile_version: int,
    profile_sha256: str,
    tool_source_sha256: str,
    parameters: Iterable[ParameterMaterial],
) -> dict[str, Json]:
    """Build the device material (spec §7.2)."""
    ordered = sorted(parameters, key=lambda p: (p.register, p.pdu_address, p.name))
    return {
        "id": device_id,
        "profile": {"name": profile_name, "version": profile_version, "sha256": profile_sha256},
        "tool": {"source_sha256": tool_source_sha256},
        "parameters": [p.to_json() for p in ordered],
    }


def device_hash(material: dict[str, Json]) -> str:
    return sha256_hex(canonical_json(material))


def plant_material(plant: str, device_hashes: Mapping[str, str]) -> dict[str, Json]:
    """Build the plant material (spec §7.3)."""
    devices: list[Json] = [
        {"id": device_id, "sha256": device_hashes[device_id]} for device_id in sorted(device_hashes)
    ]
    return {"plant": plant, "devices": devices}


def plant_hash(material: dict[str, Json]) -> str:
    return sha256_hex(canonical_json(material))
