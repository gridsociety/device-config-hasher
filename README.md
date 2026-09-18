# device-config-hasher

`dch` reads the configuration parameters of a field device (inverter, BMS,
relay, PLC) over Modbus TCP and writes a **self-contained snapshot** that
holds the raw register words, their decoded values, the device profile that
selected them, and a deterministic SHA-256 hash over all of it. Two snapshots
with the same hash prove that the configuration has not changed; any snapshot
can be re-verified later from the file alone.

The tool is **read-only** (only Modbus function codes 1 to 4 exist in the code),
**deterministic** (spec §7 is normative and reproducible by hand) and
**fails closed** (no hash is produced over partial data).

Design specification: [`docs/specs/2026-09-18-device-config-hasher-design.md`](docs/specs/2026-09-18-device-config-hasher-design.md).
Hashing recipe: [`docs/hashing.md`](docs/hashing.md).

## Install

```sh
uv tool install .            # or: pip install .
dch --version
```

Development:

```sh
uv sync
uv run pytest
uv run ruff check . && uv run mypy
```

## Usage

Take a snapshot of one device, passing the connection parameters directly:

```sh
dch snapshot --profile ingeteam-sun-storage-3power-c \
             --host 10.10.100.10 --port 502 --unit-id 1 \
             --id inverter --plant arizzi \
             -o inverter-2026-09-18.json
```

Verify a snapshot file offline, with no device and no network:

```sh
dch verify inverter-2026-09-18.json
```

```
OK   inverter: profile sha256
OK   inverter: device sha256
OK   plant sha256
tool: same build (recorded 3a1f…, current 3a1f…)
result: CONSISTENT
```

List the available profiles (bundled and, optionally, from your own directory):

```sh
dch profile list --profile-path ./profiles
```

`--profile` accepts either a bundled profile name or a path to a YAML file;
see [`examples/profile.example.yaml`](examples/profile.example.yaml) for the
format. Every command accepts `--json` for machine-readable output.

### Exit codes

| Command | 0 | 1 | 2 | 3 |
|---------|---|---|---|---|
| `snapshot` | written, complete | | written, **incomplete** (no hash) | profile or connection error |
| `verify` | consistent | **inconsistent** | | not a valid snapshot |

## What is in a snapshot

```json
{
  "schema": "dch-snapshot/1",
  "tool": { "name": "device-config-hasher", "version": "0.1.0", "source_sha256": "…" },
  "algorithm": "sha256-jcs-v1",
  "plant": "arizzi",
  "captured_at": "2026-09-18T09:41:12Z",
  "complete": true,
  "plant_sha256": "…",
  "devices": [{
    "id": "inverter",
    "profile": { "name": "ingeteam-sun-storage-3power-c", "version": 1, "sha256": "…", "source": "…full YAML…" },
    "connection": { "host": "10.10.100.10", "port": 502, "unit_id": 1 },
    "complete": true,
    "device_sha256": "…",
    "parameters": [
      { "name": "communication_watchdog_timeout", "register": "holding", "address": 42208,
        "pdu_address": 2207, "type": "u16", "raw": [30000], "decoded": 30000, "unit": "ms",
        "quality": "good" }
    ]
  }]
}
```

- **Hashed:** device id, profile name/version/sha256, the tool's source hash,
  and for each parameter its name, register type, PDU address and raw words.
- **Not hashed (display only):** timestamps, connection, decoded values, units,
  quality flags, tool name and version.
- **`profile.source`** is the full text of the profile that was used, so the
  file can be audited and re-verified without access to the profile repository.
- **`tool.source_sha256`** is the SHA-256 of the tool's own Python sources
  (spec §7.7). The hash therefore identifies the exact software build that
  produced it. `verify` uses the value recorded in the file, so snapshots made
  by older builds stay verifiable, and reports whether the current build is
  the same.

## Verifying without this tool

The recipe is small enough to reimplement in any language.
[`examples/verify_stdlib.py`](examples/verify_stdlib.py) is a standalone
verifier using only the Python standard library; the test suite checks that
it agrees with the package. It is the reference for a future web verifier.

```sh
python3 examples/verify_stdlib.py inverter-2026-09-18.json
```

## Guarantees and limits

- The tool never writes to a device. There is no write function in the code
  base and the tests assert that the simulator only ever receives function
  codes 1 to 4.
- The hash covers the raw 16-bit words, not decoded values, so float
  formatting and rounding are outside the trust chain.
- A snapshot is **not** a signature. Sign or timestamp the JSON file with an
  external tool if you need non-repudiation.
- A snapshot proves the register contents the device reported at one moment.
  It does not prove firmware integrity, and it cannot cover settings a device
  does not expose over Modbus. For the bundled profiles:
  - the Jinko SCU exposes only topology over Modbus; cell and rack protection
    thresholds and balancing parameters need a vendor export;
  - the Ingeteam inverter exposes the PPC-writable parameters, but internal
    protection settings (grid protection thresholds, LVRT curves) are only
    reachable through the vendor's service tools.

## Bundled profiles

| Name | Parameters | Notes |
|------|-----------|-------|
| `ingeteam-sun-storage-3power-c` | 18 holding registers | commands and dispatch setpoints listed under `excluded` |
| `jinko-scu-bank` | 4 input f32 | the SCU "System Configuration" section |
| `jinko-scu-rack` | 1 input f32 | insulation monitoring enable |

Any change to a bundled profile changes hashes for everyone using it, so the
exact parameter set is pinned by a test and every change is an explicit diff.

## License

MIT, see [`LICENSE`](LICENSE).
