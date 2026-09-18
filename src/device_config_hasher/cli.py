"""``dch`` command-line interface (spec §9, v0.1 scope)."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click

from device_config_hasher import __version__
from device_config_hasher.modbus import Connection, ModbusReadError, ReadOnlyModbusClient
from device_config_hasher.profile import ProfileError, find_profile, list_profiles
from device_config_hasher.reader import read_device
from device_config_hasher.snapshot import (
    SnapshotError,
    build_snapshot,
    dump_snapshot,
    load_snapshot,
    verify_snapshot,
)
from device_config_hasher.sourcehash import tool_source_sha256

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_INCOMPLETE = 2
EXIT_CONFIG = 3


def _fail(message: str, code: int) -> None:
    click.echo(f"error: {message}", err=False)
    sys.exit(code)


@click.group()
@click.version_option(__version__, prog_name="dch")
def main() -> None:
    """Read-only, deterministic hashing of field-device configuration."""


@main.command()
@click.option("--profile", "profile_ref", required=True, help="Bundled profile name or YAML path.")
@click.option("--host", required=True, help="Modbus TCP host.")
@click.option("--port", default=502, show_default=True, type=int)
@click.option("--unit-id", default=None, type=int, help="Modbus unit id (default: from profile).")
@click.option("--id", "device_id", default=None, help="Device id (default: profile name).")
@click.option(
    "--plant", default="default", show_default=True, help="Plant id recorded in the file."
)
@click.option("--timeout", default=3.0, show_default=True, type=float, help="Seconds per request.")
@click.option("--retries", default=2, show_default=True, type=int, help="Retries per read block.")
@click.option(
    "--profile-path",
    "profile_paths",
    multiple=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Extra directory searched for profiles by name (repeatable).",
)
@click.option("-o", "--output", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Machine-readable summary on stdout.")
def snapshot(
    profile_ref: str,
    host: str,
    port: int,
    unit_id: int | None,
    device_id: str | None,
    plant: str,
    timeout: float,
    retries: int,
    profile_paths: tuple[Path, ...],
    output: Path,
    as_json: bool,
) -> None:
    """Read one device and write a self-contained snapshot with its hashes."""
    try:
        profile = find_profile(profile_ref, extra_dirs=list(profile_paths))
    except ProfileError as exc:
        _fail(str(exc), EXIT_CONFIG)
        return
    conn = Connection(
        host=host,
        port=port,
        unit_id=unit_id if unit_id is not None else profile.protocol.default_unit_id,
        timeout_s=timeout,
        retries=retries,
    )
    device_id = device_id or profile.name
    try:
        with ReadOnlyModbusClient(conn) as client:
            readings = read_device(profile, client)
    except ModbusReadError as exc:
        _fail(str(exc), EXIT_CONFIG)
        return
    captured_at = datetime.now(tz=UTC)
    doc = build_snapshot(
        plant=plant,
        device_id=device_id,
        profile=profile,
        connection=conn,
        readings=readings,
        captured_at=captured_at,
        tool_source_sha256=tool_source_sha256(),
    )
    dump_snapshot(doc, output)

    dev = doc["devices"][0]
    failed = [p for p in dev["parameters"] if p["quality"] != "good"]
    if as_json:
        click.echo(
            json.dumps(
                {
                    "output": str(output),
                    "plant": plant,
                    "device_id": device_id,
                    "profile": {"name": profile.name, "version": profile.version},
                    "captured_at": doc["captured_at"],
                    "complete": doc["complete"],
                    "parameters": len(dev["parameters"]),
                    "failed": [p["name"] for p in failed],
                    "device_sha256": dev["device_sha256"],
                    "plant_sha256": doc["plant_sha256"],
                    "tool_source_sha256": doc["tool"]["source_sha256"],
                },
                indent=2,
            )
        )
    else:
        click.echo(f"device:        {device_id}  ({profile.name} v{profile.version})")
        click.echo(f"connection:    {conn.host}:{conn.port} unit {conn.unit_id}")
        click.echo(f"captured_at:   {doc['captured_at']}")
        click.echo(f"parameters:    {len(dev['parameters'])} ({len(failed)} failed)")
        click.echo(f"written:       {output}")
        if doc["complete"]:
            click.echo(f"device_sha256: {dev['device_sha256']}")
            click.echo(f"plant_sha256:  {doc['plant_sha256']}")
        else:
            click.echo("device_sha256: (none, incomplete)")
            for p in failed:
                click.echo(f"  FAIL {p['name']}: {p.get('error', 'unknown error')}")
    if not doc["complete"]:
        sys.exit(EXIT_INCOMPLETE)


@main.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Machine-readable report on stdout.")
def verify(file: Path, as_json: bool) -> None:
    """Recompute the hashes in a snapshot file and check them against the recorded ones."""
    try:
        doc = load_snapshot(file)
    except SnapshotError as exc:
        _fail(str(exc), EXIT_CONFIG)
        return
    result = verify_snapshot(doc, current_source_sha256=tool_source_sha256())
    if as_json:
        report: dict[str, Any] = {
            "file": str(file),
            "ok": result.ok,
            "same_tool_build": result.same_tool_build,
            "recorded_source_sha256": result.recorded_source_sha256,
            "current_source_sha256": result.current_source_sha256,
            "checks": [asdict(c) for c in result.checks],
        }
        click.echo(json.dumps(report, indent=2))
    else:
        for c in result.checks:
            status = "OK  " if c.ok else "FAIL"
            line = f"{status} {c.subject}"
            if not c.ok and c.expected is not None:
                line += f"\n       recorded: {c.expected}\n       computed: {c.actual}"
            click.echo(line)
        build = "same build" if result.same_tool_build else "different build"
        click.echo(
            f"tool: {build} (recorded {result.recorded_source_sha256[:12]}…, "
            f"current {result.current_source_sha256[:12]}…)"
        )
        click.echo("result: " + ("CONSISTENT" if result.ok else "INCONSISTENT"))
    sys.exit(EXIT_OK if result.ok else EXIT_MISMATCH)


@main.group()
def profile() -> None:
    """Inspect device profiles."""


@profile.command("list")
@click.option(
    "--profile-path",
    "profile_paths",
    multiple=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Extra directory to list (repeatable).",
)
@click.option("--json", "as_json", is_flag=True)
def profile_list(profile_paths: tuple[Path, ...], as_json: bool) -> None:
    """List bundled profiles and those in extra directories."""
    try:
        profiles = list_profiles(extra_dirs=list(profile_paths))
    except ProfileError as exc:
        _fail(str(exc), EXIT_CONFIG)
        return
    rows = [
        {
            "name": p.name,
            "version": p.version,
            "title": p.title,
            "parameters": len(p.parameters),
            "sha256": p.sha256,
            "path": p.path,
        }
        for p in profiles
    ]
    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    width = max((len(p.name) for p in profiles), default=4)
    for p in profiles:
        click.echo(
            f"{p.name.ljust(width)}  v{p.version:<3} {len(p.parameters):>3} params  {p.title or ''}"
        )


@main.command("source-hash")
@click.argument("profiles", nargs=-1)
@click.option(
    "--profile-path",
    "profile_paths",
    multiple=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Extra directory searched for profiles by name (repeatable).",
)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable report on stdout.")
def source_hash(profiles: tuple[str, ...], profile_paths: tuple[Path, ...], as_json: bool) -> None:
    """Print the hashes that bind a snapshot to this build: the tool's own
    source hash and the hash of each profile.

    Without arguments every bundled profile (plus those found under
    --profile-path) is listed. PROFILES restricts the output to the given
    profile names or YAML paths.
    """
    try:
        if profiles:
            selected = [find_profile(ref, extra_dirs=list(profile_paths)) for ref in profiles]
        else:
            selected = list_profiles(extra_dirs=list(profile_paths))
    except ProfileError as exc:
        _fail(str(exc), EXIT_CONFIG)
        return
    tool = {
        "name": "device-config-hasher",
        "version": __version__,
        "source_sha256": tool_source_sha256(),
    }
    rows = [
        {"name": p.name, "version": p.version, "sha256": p.sha256, "path": p.path} for p in selected
    ]
    if as_json:
        click.echo(json.dumps({"tool": tool, "profiles": rows}, indent=2))
        return
    click.echo(f"tool     device-config-hasher {__version__}  {tool['source_sha256']}")
    for r in rows:
        click.echo(f"profile  {r['name']} v{r['version']}  {r['sha256']}")
