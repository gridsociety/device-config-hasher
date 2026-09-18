"""The stdlib-only reference verifier must agree with the package on real snapshots."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from device_config_hasher.cli import main
from tests.conftest import Simulator

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "examples" / "verify_stdlib.py"


def _run(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True, check=False
    )


def test_reference_verifier_agrees(simulator: Simulator, tmp_path: Path) -> None:
    simulator.holding.update({2201: 1, 2203: 30, 2207: 30000, 2217: 2})
    out = tmp_path / "snap.json"
    result = CliRunner().invoke(
        main,
        [
            "snapshot",
            "--profile",
            "ingeteam-sun-storage-3power-c",
            "--host",
            simulator.host,
            "--port",
            str(simulator.port),
            "--id",
            "inverter",
            "--plant",
            "arizzi",
            "--retries",
            "0",
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output

    proc = _run(out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("OK") == 3

    doc = json.loads(out.read_text())
    doc["devices"][0]["parameters"][3]["raw"] = [31]
    out.write_text(json.dumps(doc))
    proc = _run(out)
    assert proc.returncode == 1
    assert "FAIL inverter: device sha256" in proc.stdout
    assert "FAIL plant sha256" in proc.stdout
