"""Build, serialize, load and verify snapshot documents (spec §8, §9)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from device_config_hasher import __version__
from device_config_hasher.canonical import (
    ALGORITHM,
    ParameterMaterial,
    device_hash,
    device_material,
    plant_hash,
    plant_material,
)
from device_config_hasher.decode import decode_words
from device_config_hasher.modbus import Connection
from device_config_hasher.profile import Profile, normalize_lf
from device_config_hasher.reader import ParameterReading

SCHEMA = "dch-snapshot/1"
TOOL_NAME = "device-config-hasher"


class SnapshotError(ValueError):
    """Raised when a file is not a valid snapshot document."""


def _parameter_entry(profile: Profile, reading: ParameterReading) -> dict[str, Any]:
    p = reading.parameter
    entry: dict[str, Any] = {
        "name": p.name,
        "register": p.register,
        "address": p.address,
        "pdu_address": reading.pdu_address,
        "type": p.type,
    }
    if p.bit is not None:
        entry["bit"] = p.bit
    entry["raw"] = list(reading.raw) if reading.raw is not None else None
    if reading.raw is not None:
        entry["decoded"] = decode_words(
            reading.raw,
            p.type,
            word_order=profile.protocol.word_order,
            bit=p.bit,
            scale=p.scale,
            enum=p.enum,
        )
    else:
        entry["decoded"] = None
    if p.unit is not None:
        entry["unit"] = p.unit
    entry["quality"] = reading.quality
    if reading.error is not None:
        entry["error"] = reading.error
    return entry


def _device_hash_from_entries(
    device_id: str,
    profile_name: str,
    profile_version: int,
    profile_sha256: str,
    tool_source_sha256: str,
    entries: Sequence[dict[str, Any]],
) -> str:
    material = device_material(
        device_id,
        profile_name,
        profile_version,
        profile_sha256,
        tool_source_sha256,
        [
            ParameterMaterial(
                str(e["name"]),
                str(e["register"]),
                int(e["pdu_address"]),
                [int(w) for w in e["raw"]],
            )
            for e in entries
        ],
    )
    return device_hash(material)


def build_snapshot(
    *,
    plant: str,
    device_id: str,
    profile: Profile,
    connection: Connection,
    readings: list[ParameterReading],
    captured_at: datetime,
    tool_source_sha256: str,
) -> dict[str, Any]:
    """Build a snapshot document for one device (spec §8)."""
    entries = [_parameter_entry(profile, r) for r in readings]
    complete = all(r.quality == "good" for r in readings)
    dev_sha: str | None = None
    if complete:
        dev_sha = _device_hash_from_entries(
            device_id, profile.name, profile.version, profile.sha256, tool_source_sha256, entries
        )
    plant_sha: str | None = None
    if dev_sha is not None:
        plant_sha = plant_hash(plant_material(plant, {device_id: dev_sha}))

    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=UTC)
    stamp = captured_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "schema": SCHEMA,
        "tool": {"name": TOOL_NAME, "version": __version__, "source_sha256": tool_source_sha256},
        "algorithm": ALGORITHM,
        "plant": plant,
        "captured_at": stamp,
        "complete": complete,
        "plant_sha256": plant_sha,
        "devices": [
            {
                "id": device_id,
                "profile": {
                    "name": profile.name,
                    "version": profile.version,
                    "sha256": profile.sha256,
                    "source": profile.source,
                },
                "connection": {
                    "host": connection.host,
                    "port": connection.port,
                    "unit_id": connection.unit_id,
                },
                "complete": complete,
                "device_sha256": dev_sha,
                "parameters": entries,
            }
        ],
    }


def dump_snapshot(doc: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_snapshot(path: Path) -> dict[str, Any]:
    try:
        doc: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"cannot read snapshot {path}: {exc}") from exc
    _validate_shape(doc)
    return doc  # type: ignore[no-any-return]


def _validate_shape(doc: Any) -> None:
    if not isinstance(doc, dict):
        raise SnapshotError("snapshot must be a JSON object")
    if doc.get("schema") != SCHEMA:
        raise SnapshotError(
            f"unsupported snapshot schema {doc.get('schema')!r}, expected {SCHEMA!r}"
        )
    if doc.get("algorithm") != ALGORITHM:
        raise SnapshotError(
            f"unsupported algorithm {doc.get('algorithm')!r}, expected {ALGORITHM!r}"
        )
    tool = doc.get("tool")
    if not isinstance(tool, dict) or not isinstance(tool.get("source_sha256"), str):
        raise SnapshotError("snapshot is missing tool.source_sha256")
    if not isinstance(doc.get("plant"), str):
        raise SnapshotError("snapshot is missing plant")
    devices = doc.get("devices")
    if not isinstance(devices, list) or not devices:
        raise SnapshotError("snapshot has no devices")
    for dev in devices:
        if not isinstance(dev, dict) or not isinstance(dev.get("id"), str):
            raise SnapshotError("device entry is missing id")
        prof = dev.get("profile")
        if not isinstance(prof, dict) or not all(
            k in prof for k in ("name", "version", "sha256", "source")
        ):
            raise SnapshotError(
                f"device {dev.get('id')}: profile is missing name/version/sha256/source"
            )
        params = dev.get("parameters")
        if not isinstance(params, list):
            raise SnapshotError(f"device {dev['id']}: parameters must be a list")
        for p in params:
            if not isinstance(p, dict) or not all(
                k in p for k in ("name", "register", "pdu_address", "raw", "quality")
            ):
                raise SnapshotError(f"device {dev['id']}: malformed parameter entry")


@dataclass
class Check:
    subject: str
    expected: str | None
    actual: str | None
    ok: bool


@dataclass
class VerifyResult:
    ok: bool
    checks: list[Check]
    recorded_source_sha256: str
    current_source_sha256: str

    @property
    def same_tool_build(self) -> bool:
        return self.recorded_source_sha256 == self.current_source_sha256


def verify_snapshot(doc: dict[str, Any], *, current_source_sha256: str) -> VerifyResult:
    """Recompute every hash in *doc* from its raw contents and compare (spec §9).

    The device material is rebuilt with the ``tool.source_sha256`` recorded in
    the snapshot, so snapshots made by other builds stay verifiable; whether
    the running build is the same is reported through ``same_tool_build``.
    """
    _validate_shape(doc)
    recorded_tool = str(doc["tool"]["source_sha256"])
    checks: list[Check] = []
    incomplete = False

    for dev in doc["devices"]:
        device_id = str(dev["id"])
        prof = dev["profile"]
        actual_profile_sha = hashlib.sha256(
            normalize_lf(str(prof["source"])).encode("utf-8")
        ).hexdigest()
        checks.append(
            Check(
                f"{device_id}: profile sha256",
                str(prof["sha256"]),
                actual_profile_sha,
                actual_profile_sha == prof["sha256"],
            )
        )
        good = [p for p in dev["parameters"] if p["quality"] == "good" and p["raw"] is not None]
        if len(good) != len(dev["parameters"]) or dev.get("device_sha256") is None:
            incomplete = True
            checks.append(Check(f"{device_id}: incomplete snapshot has no hash", None, None, False))
            continue
        actual_dev_sha = _device_hash_from_entries(
            device_id,
            str(prof["name"]),
            int(prof["version"]),
            str(prof["sha256"]),
            recorded_tool,
            good,
        )
        expected_dev_sha = str(dev["device_sha256"])
        checks.append(
            Check(
                f"{device_id}: device sha256",
                expected_dev_sha,
                actual_dev_sha,
                actual_dev_sha == expected_dev_sha,
            )
        )

    if not incomplete:
        # The plant hash is recomputed from the *recomputed* device hashes so
        # that a tampered word also surfaces at plant level.
        recomputed = {
            c.subject.removesuffix(": device sha256"): c.actual
            for c in checks
            if c.subject.endswith(": device sha256") and c.actual is not None
        }
        actual_plant = plant_hash(plant_material(str(doc["plant"]), recomputed))
        expected_plant = doc.get("plant_sha256")
        checks.append(
            Check(
                "plant sha256",
                str(expected_plant) if expected_plant is not None else None,
                actual_plant,
                actual_plant == expected_plant,
            )
        )

    return VerifyResult(
        ok=all(c.ok for c in checks),
        checks=checks,
        recorded_source_sha256=recorded_tool,
        current_source_sha256=current_source_sha256,
    )
