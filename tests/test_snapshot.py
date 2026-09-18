from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from device_config_hasher import __version__
from device_config_hasher.canonical import ALGORITHM
from device_config_hasher.modbus import Connection
from device_config_hasher.profile import parse_profile
from device_config_hasher.reader import ParameterReading
from device_config_hasher.snapshot import (
    SCHEMA,
    SnapshotError,
    build_snapshot,
    dump_snapshot,
    load_snapshot,
    verify_snapshot,
)

PROFILE = """\
schema: dch-profile/1
name: demo
version: 2
title: Demo
protocol: {type: modbus_tcp, addressing: modicon}
parameters:
  - {name: watchdog, register: holding, address: 42208, type: u16, unit: ms}
  - {name: settle, register: holding, address: 42204, type: u16, scale: 0.001, unit: s}
  - {name: battery, register: holding, address: 42218, type: u16, enum: {1: "No", 2: "Yes"}}
  - {name: flag, register: holding, address: 42212, type: bit, bit: 3}
  - {name: insulation, register: input, address: 30061, type: f32}
  - {name: relay, register: coil, address: 17, type: bit}
"""
TOOL = "t" * 64
WHEN = datetime(2026, 9, 18, 9, 41, 12, tzinfo=UTC)


def _readings(profile: Any, *, fail: str | None = None) -> list[ParameterReading]:
    raw = {
        "watchdog": [30000],
        "settle": [500],
        "battery": [2],
        "flag": [0b1000],
        "insulation": [0x3F80, 0],
        "relay": [1],
    }
    out = []
    for p in profile.parameters:
        if p.name == fail:
            out.append(ParameterReading(p, profile.pdu_address(p), None, "error", "boom"))
        else:
            out.append(ParameterReading(p, profile.pdu_address(p), raw[p.name], "good"))
    return out


def _doc(fail: str | None = None) -> dict[str, Any]:
    profile = parse_profile(PROFILE)
    return build_snapshot(
        plant="arizzi",
        device_id="inverter",
        profile=profile,
        connection=Connection(host="10.0.0.1", port=502, unit_id=1),
        readings=_readings(profile, fail=fail),
        captured_at=WHEN,
        tool_source_sha256=TOOL,
    )


def test_build_snapshot_document_shape() -> None:
    doc = _doc()
    assert doc["schema"] == SCHEMA == "dch-snapshot/1"
    assert doc["tool"] == {
        "name": "device-config-hasher",
        "version": __version__,
        "source_sha256": TOOL,
    }
    assert doc["algorithm"] == ALGORITHM
    assert doc["plant"] == "arizzi"
    assert doc["captured_at"] == "2026-09-18T09:41:12Z"
    assert doc["complete"] is True
    assert len(doc["plant_sha256"]) == 64
    dev = doc["devices"][0]
    assert dev["id"] == "inverter"
    assert dev["profile"]["name"] == "demo"
    assert dev["profile"]["version"] == 2
    assert dev["profile"]["sha256"] == hashlib.sha256(PROFILE.encode()).hexdigest()
    assert dev["profile"]["source"] == PROFILE
    assert dev["connection"] == {"host": "10.0.0.1", "port": 502, "unit_id": 1}
    assert dev["complete"] is True
    assert len(dev["device_sha256"]) == 64
    params = {p["name"]: p for p in dev["parameters"]}
    assert params["watchdog"] == {
        "name": "watchdog",
        "register": "holding",
        "address": 42208,
        "pdu_address": 2207,
        "type": "u16",
        "raw": [30000],
        "decoded": 30000,
        "unit": "ms",
        "quality": "good",
    }
    assert params["settle"]["decoded"] == 0.5
    assert params["battery"]["decoded"] == "Yes"
    assert params["flag"]["decoded"] is True
    assert params["flag"]["bit"] == 3
    assert params["insulation"]["decoded"] == 1.0
    assert params["relay"]["decoded"] is True
    assert "unit" not in params["battery"]


def test_build_snapshot_incomplete_has_no_hashes() -> None:
    doc = _doc(fail="settle")
    assert doc["complete"] is False
    assert doc["plant_sha256"] is None
    dev = doc["devices"][0]
    assert dev["complete"] is False
    assert dev["device_sha256"] is None
    bad = next(p for p in dev["parameters"] if p["name"] == "settle")
    assert bad["raw"] is None and bad["decoded"] is None
    assert bad["quality"] == "error" and bad["error"] == "boom"


def test_dump_load_round_trip(tmp_path: Path) -> None:
    doc = _doc()
    path = tmp_path / "snap.json"
    dump_snapshot(doc, path)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert json.loads(text) == doc
    assert load_snapshot(path) == doc


def test_load_rejects_bad_documents(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"schema": "x"}')
    with pytest.raises(SnapshotError, match="schema"):
        load_snapshot(path)
    path.write_text("not json")
    with pytest.raises(SnapshotError):
        load_snapshot(path)
    path.write_text("[]")
    with pytest.raises(SnapshotError):
        load_snapshot(path)


def test_verify_ok() -> None:
    result = verify_snapshot(_doc(), current_source_sha256=TOOL)
    assert result.ok is True
    assert result.same_tool_build is True
    assert result.recorded_source_sha256 == TOOL
    subjects = [c.subject for c in result.checks]
    assert subjects == ["inverter: profile sha256", "inverter: device sha256", "plant sha256"]
    assert all(c.ok and c.expected == c.actual for c in result.checks)


def test_verify_detects_tampered_word() -> None:
    doc = _doc()
    doc["devices"][0]["parameters"][0]["raw"] = [30001]
    result = verify_snapshot(doc, current_source_sha256=TOOL)
    assert result.ok is False
    by = {c.subject: c for c in result.checks}
    assert by["inverter: profile sha256"].ok is True
    assert by["inverter: device sha256"].ok is False
    assert by["plant sha256"].ok is False
    assert by["inverter: device sha256"].expected == _doc()["devices"][0]["device_sha256"]


def test_verify_detects_tampered_profile_source() -> None:
    doc = _doc()
    doc["devices"][0]["profile"]["source"] = doc["devices"][0]["profile"]["source"].replace(
        "42208", "42209"
    )
    result = verify_snapshot(doc, current_source_sha256=TOOL)
    by = {c.subject: c for c in result.checks}
    assert by["inverter: profile sha256"].ok is False
    assert by["inverter: device sha256"].ok is True  # recorded sha256 still consistent
    assert result.ok is False


def test_verify_detects_tampered_tool_hash() -> None:
    doc = _doc()
    doc["tool"]["source_sha256"] = "u" * 64
    result = verify_snapshot(doc, current_source_sha256=TOOL)
    by = {c.subject: c for c in result.checks}
    assert by["inverter: device sha256"].ok is False


def test_verify_with_different_tool_build_is_ok_but_flagged() -> None:
    result = verify_snapshot(_doc(), current_source_sha256="u" * 64)
    assert result.ok is True
    assert result.same_tool_build is False
    assert result.current_source_sha256 == "u" * 64


def test_verify_incomplete_snapshot_fails() -> None:
    result = verify_snapshot(_doc(fail="relay"), current_source_sha256=TOOL)
    assert result.ok is False
    assert any("incomplete" in c.subject for c in result.checks)


def test_verify_detects_tampered_device_id() -> None:
    doc = _doc()
    doc["devices"][0]["id"] = "other"
    result = verify_snapshot(doc, current_source_sha256=TOOL)
    assert result.ok is False
