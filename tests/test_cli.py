from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from device_config_hasher.cli import main
from device_config_hasher.sourcehash import tool_source_sha256
from tests.conftest import Simulator

PROFILE = """\
schema: dch-profile/1
name: sim-device
version: 1
title: Simulated device
protocol: {type: modbus_tcp, addressing: pdu}
parameters:
  - {name: watchdog, register: holding, address: 10, type: u16, unit: ms}
  - {name: ramp, register: holding, address: 11, type: u16, scale: 0.1, unit: "%/s"}
  - {name: level, register: input, address: 5, type: f32}
  - {name: relay, register: coil, address: 17, type: bit}
"""


@pytest.fixture
def profile_path(tmp_path: Path) -> Path:
    path = tmp_path / "sim-device.yaml"
    path.write_text(PROFILE)
    return path


def _snapshot_args(sim: Simulator, profile: Path, out: Path, *extra: str) -> list[str]:
    return [
        "snapshot",
        "--profile",
        str(profile),
        "--host",
        sim.host,
        "--port",
        str(sim.port),
        "--unit-id",
        str(sim.unit_id),
        "--timeout",
        "1",
        "--retries",
        "0",
        "-o",
        str(out),
        *extra,
    ]


def test_snapshot_then_verify(simulator: Simulator, profile_path: Path, tmp_path: Path) -> None:
    simulator.holding.update({10: 30000, 11: 50})
    simulator.input.update({5: 0x3F80, 6: 0})
    simulator.coils[17] = 1
    out = tmp_path / "snap.json"
    runner = CliRunner()

    result = runner.invoke(
        main, _snapshot_args(simulator, profile_path, out, "--id", "inv1", "--plant", "arizzi")
    )
    assert result.exit_code == 0, result.output
    assert "inv1" in result.output and "device_sha256" in result.output
    doc = json.loads(out.read_text())
    assert doc["complete"] is True
    assert doc["plant"] == "arizzi"
    assert doc["devices"][0]["id"] == "inv1"
    assert doc["devices"][0]["profile"]["source"] == PROFILE
    assert doc["tool"]["source_sha256"] == tool_source_sha256()
    params = {p["name"]: p for p in doc["devices"][0]["parameters"]}
    assert params["watchdog"]["raw"] == [30000]
    assert params["ramp"]["decoded"] == 5.0
    assert params["level"]["decoded"] == 1.0
    assert params["relay"]["decoded"] is True

    result = runner.invoke(main, ["verify", str(out)])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output and "FAIL" not in result.output
    assert "same build" in result.output

    result = runner.invoke(main, ["verify", "--json", str(out)])
    assert result.exit_code == 0
    report = json.loads(result.output)
    assert report["ok"] is True and report["same_tool_build"] is True
    assert len(report["checks"]) == 3


def test_snapshot_defaults_and_json_output(
    simulator: Simulator, profile_path: Path, tmp_path: Path
) -> None:
    out = tmp_path / "snap.json"
    result = CliRunner().invoke(main, _snapshot_args(simulator, profile_path, out, "--json"))
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    doc = json.loads(out.read_text())
    assert doc["plant"] == "default"
    assert doc["devices"][0]["id"] == "sim-device"
    assert summary["complete"] is True
    assert summary["device_sha256"] == doc["devices"][0]["device_sha256"]
    assert summary["plant_sha256"] == doc["plant_sha256"]
    assert summary["output"] == str(out)


def test_register_change_changes_hash(
    simulator: Simulator, profile_path: Path, tmp_path: Path
) -> None:
    runner = CliRunner()
    a, b, c = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "c.json"
    simulator.holding[10] = 1
    assert runner.invoke(main, _snapshot_args(simulator, profile_path, a)).exit_code == 0
    assert runner.invoke(main, _snapshot_args(simulator, profile_path, b)).exit_code == 0
    simulator.holding[10] = 2
    assert runner.invoke(main, _snapshot_args(simulator, profile_path, c)).exit_code == 0
    ha, hb, hc = (json.loads(p.read_text())["plant_sha256"] for p in (a, b, c))
    assert ha == hb != hc


def test_verify_detects_tampering(simulator: Simulator, profile_path: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    out = tmp_path / "snap.json"
    assert runner.invoke(main, _snapshot_args(simulator, profile_path, out)).exit_code == 0
    doc = json.loads(out.read_text())
    doc["devices"][0]["parameters"][0]["raw"] = [4242]
    out.write_text(json.dumps(doc))
    result = runner.invoke(main, ["verify", str(out)])
    assert result.exit_code == 1, result.output
    assert "FAIL" in result.output
    assert "device sha256" in result.output


def test_verify_reports_different_build(
    simulator: Simulator, profile_path: Path, tmp_path: Path
) -> None:
    runner = CliRunner()
    out = tmp_path / "snap.json"
    assert runner.invoke(main, _snapshot_args(simulator, profile_path, out)).exit_code == 0
    doc = json.loads(out.read_text())
    # Pretend the snapshot was produced by another build: rewrite the tool hash
    # and recompute the device/plant hashes consistently with it.
    from device_config_hasher.snapshot import verify_snapshot

    doc["tool"]["source_sha256"] = "f" * 64
    result_obj = verify_snapshot(doc, current_source_sha256="f" * 64)
    by = {c.subject: c for c in result_obj.checks}
    doc["devices"][0]["device_sha256"] = by["sim-device: device sha256"].actual
    result_obj = verify_snapshot(doc, current_source_sha256="f" * 64)
    by = {c.subject: c for c in result_obj.checks}
    doc["plant_sha256"] = by["plant sha256"].actual
    out.write_text(json.dumps(doc))

    result = runner.invoke(main, ["verify", str(out)])
    assert result.exit_code == 0, result.output
    assert "different build" in result.output


def test_verify_invalid_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema": "nope"}')
    result = CliRunner().invoke(main, ["verify", str(bad)])
    assert result.exit_code == 3
    assert "schema" in result.output


def test_snapshot_incomplete_exits_2(
    simulator: Simulator, profile_path: Path, tmp_path: Path
) -> None:
    simulator.invalid_holding.add(11)
    out = tmp_path / "snap.json"
    result = CliRunner().invoke(main, _snapshot_args(simulator, profile_path, out))
    assert result.exit_code == 2, result.output
    assert "ramp" in result.output
    doc = json.loads(out.read_text())
    assert doc["complete"] is False and doc["plant_sha256"] is None
    verify = CliRunner().invoke(main, ["verify", str(out)])
    assert verify.exit_code == 1


def test_snapshot_unreachable_host_exits_3(profile_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "snap.json"
    result = CliRunner().invoke(
        main,
        [
            "snapshot",
            "--profile",
            str(profile_path),
            "--host",
            "127.0.0.1",
            "--port",
            "1",
            "--timeout",
            "0.5",
            "--retries",
            "0",
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 3, result.output
    assert "connect" in result.output
    assert not out.exists()


def test_snapshot_unknown_profile_exits_3(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        ["snapshot", "--profile", "no-such", "--host", "127.0.0.1", "-o", str(tmp_path / "x.json")],
    )
    assert result.exit_code == 3
    assert "no-such" in result.output


def test_profile_list(profile_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["profile", "list", "--profile-path", str(profile_path.parent)])
    assert result.exit_code == 0, result.output
    assert "sim-device" in result.output
    assert "ingeteam-sun-storage-3power-c" in result.output
    result = runner.invoke(
        main, ["profile", "list", "--json", "--profile-path", str(profile_path.parent)]
    )
    entries = json.loads(result.output)
    sim = next(e for e in entries if e["name"] == "sim-device")
    assert sim == {
        "name": "sim-device",
        "version": 1,
        "title": "Simulated device",
        "parameters": 4,
        "sha256": json.loads(result.output)[-1]["sha256"],
        "path": str(profile_path),
    }
