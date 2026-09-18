# device-config-hasher — Design Specification

**Status:** draft for review (revised 2026-09-18: tool source hash §7.7, self-contained snapshot §8, v0.1 CLI scope §9)
**Date:** 2026-09-18
**Authors:** Grid Society

## 1. Purpose

Plant operators are increasingly required to demonstrate that the *active
configuration* of field devices (inverters, battery management systems,
protection relays, PLCs) has not been altered between two points in time.

`device-config-hasher` is a small, read-only, device-agnostic tool that:

1. reads the configuration parameters of one or more field devices over
   Modbus TCP, according to a declarative **device profile**;
2. stores them in a self-describing **snapshot** file;
3. computes a **deterministic cryptographic hash** of those parameters, so
   that two snapshots taken at different times can be compared with a single
   value, and any discrepancy can be traced to the individual parameter that
   changed.

If the hash of today's snapshot equals the hash of the baseline snapshot, the
configuration is unaltered. If it differs, the tool shows exactly which
parameters differ and how.

The tool is intended to be published as open-source software (MIT license) so
that operators, integrators and certifying bodies can inspect how the hash is
computed and reproduce it independently.

### 1.1 Non-goals

- The tool never writes to a device. Only Modbus function codes 1, 2, 3 and 4
  are used. This is a hard guarantee, enforced by the code structure (there is
  no write path) and by tests.
- The tool does not decide *what* is configuration. That knowledge lives in
  the device profiles, which are reviewable data files. The tool ships with
  profiles for the devices Grid Society operates and accepts profiles from
  third parties.
- The tool does not implement a continuous monitoring daemon, a database of
  historical snapshots, alarms, or a web UI. It is a command-line tool and a
  library. Scheduling and storage are delegated to the operator's existing
  infrastructure (cron, CI, document management). These may become separate
  projects.
- The tool does not sign snapshots. Snapshot files are plain JSON and can be
  signed with any external tool (GPG, a qualified electronic signature
  service, a timestamping authority). A future version may integrate signing.
- Parameters that a device does not expose over Modbus cannot be covered.
  The tool reports exactly which parameters it covers; gaps must be handled
  by other means (vendor export files, paper records). See §11.

## 2. Terminology

| Term | Meaning |
|------|---------|
| **Device profile** | A YAML file describing one *type* of device: protocol details and the ordered list of configuration parameters to read. Versioned. |
| **Parameter** | One configuration value on a device, mapped to one or more Modbus registers or a discrete/coil bit. |
| **Plant manifest** | A YAML file describing one *installation*: the device instances present, which profile each uses, and how to reach them. |
| **Snapshot** | A JSON file produced by reading every parameter of every device in a manifest at one point in time. Contains raw and decoded values, metadata, and hashes. |
| **Device hash** | SHA-256 over the canonical form of one device's parameters. |
| **Plant hash** | SHA-256 over the canonical form of all device hashes in a manifest. |
| **Baseline** | A snapshot the operator has designated as the reference configuration. |

## 3. Design principles

1. **Read-only.** No code path can issue a Modbus write.
2. **Deterministic.** Given identical register contents and identical profile
   files, the hash is byte-for-byte identical regardless of host, operating
   system, Python version, read order, network timing or float formatting.
3. **No time-dependent inputs.** Nothing that changes during normal operation
   is allowed into the hash: no timestamps, no measurements, no counters, no
   setpoints that the control system rewrites, no heartbeats. Profile authors
   are bound by this rule (§5.4) and the shipped profiles document, per
   excluded register, why it was excluded.
4. **Hash the raw words, display the decoded values.** The hash is computed
   over the unsigned 16-bit register words exactly as returned by the device.
   Decoding (signedness, scaling, floats, enums) is applied only for human
   display and for the diff report. This removes float-formatting and
   rounding from the trust chain.
5. **Fail closed.** If any parameter of a device cannot be read with good
   quality, that device gets no hash, the plant gets no hash, and the process
   exits non-zero. A hash must never be computed over partial or stale data.
6. **The profile is part of the hashed material.** The hash of the profile
   file itself is mixed into the device hash, so a modified profile (for
   example one that silently drops a parameter) cannot reproduce a baseline
   hash.
7. **Everything an auditor needs is in the snapshot.** A snapshot alone,
   together with the published tool, is enough to re-verify its own hashes
   without network access (`verify --offline`).

## 4. Architecture

```
┌──────────────┐    ┌───────────────┐    ┌──────────────┐    ┌───────────────┐
│ plant.yaml   │───▶│  Manifest     │───▶│  Reader      │───▶│  Snapshot     │
│ profiles/*.y │    │  + Profile    │    │  (Modbus TCP │    │  builder      │
└──────────────┘    │  loader       │    │   read-only) │    │  + Canonical  │
                    └───────────────┘    └──────────────┘    │  hasher       │
                                                             └──────┬────────┘
                                                                    ▼
                                                           snapshot.json
                                                                    │
                                              ┌─────────────────────┴────────┐
                                              ▼                              ▼
                                        verify / diff                 external signing
                                        (offline or live)             (out of scope)
```

Python package `device_config_hasher` with these modules, each independently
testable:

| Module | Responsibility | Depends on |
|--------|----------------|------------|
| `profile` | Load and validate device profiles; compute the profile content hash. | PyYAML, pydantic |
| `manifest` | Load and validate plant manifests; expand instance ranges; resolve profile references. | `profile` |
| `modbus` | Thin read-only client: connect, read a contiguous block of a given register type, disconnect. Retries and timeouts. | pymodbus |
| `reader` | For one device instance, plan reads (grouping contiguous registers), execute them via `modbus`, produce `ParameterReading`s with quality flags. | `profile`, `modbus` |
| `decode` | Turn raw words into decoded values for display: integers, floats, bits, scaling, enum labels. Never used for hashing. | none |
| `canonical` | Canonical serialization and hashing. Pure functions. Documented byte-for-byte (§7). | none |
| `snapshot` | Build, serialize, load and validate snapshot documents. | `canonical`, `decode` |
| `compare` | Diff two snapshots parameter by parameter. | `snapshot` |
| `cli` | `dch` command-line entry point. | all |

The CLI is a thin layer; everything is usable as a library so that a PPC or
SCADA can embed the same logic and produce identical hashes.

## 5. Device profiles

### 5.1 Location and identity

Profiles are YAML files. The package ships a `profiles/` directory with the
profiles Grid Society maintains; users can point to their own files or
directories. A profile is identified by `name` and `version`; its **content
hash** (SHA-256 of the file bytes, after normalizing line endings to LF) is
recorded in every snapshot and mixed into the device hash.

### 5.2 Schema

```yaml
schema: dch-profile/1          # profile schema version, for forward compatibility
name: ingeteam-sun-storage-3power-c
version: 1                     # bump on any change to the parameter list
title: Ingeteam INGECON SUN STORAGE 3Power HV C Series
vendor: Ingeteam
documentation:                 # optional, free text / references for auditors
  - "Modbus TCP register map, document ABH2010IQM01, rev. 02"

protocol:
  type: modbus_tcp
  addressing: modicon          # how addresses in this file are written:
                               #   pdu       = 0-based protocol address
                               #   one_based = 1-based as in many vendor docs
                               #   modicon   = 30001/40001 style
  word_order: big              # for 32-bit types: big = high word first
  default_unit_id: 1
  address_offset: 0            # optional, default 0: added to every converted
                               # PDU address, for firmware that serves its
                               # documented registers shifted by a constant
                               # number of words (see the Jinko profiles)

parameters:
  - name: active_power_increase_settling_time
    register: holding          # holding | input | coil | discrete
    address: 42204
    type: u16                  # u16 | i16 | u32 | i32 | f32 | bit
    scale: 0.001               # decoded = raw * scale  (optional, default 1)
    unit: s
    description: First-order filter settling time for active power increase
  - name: type_of_battery
    register: holding
    address: 42218
    type: u16
    enum: { 1: "No contactor (internal precharge)", 2: "With contactor" }
  - name: insulation_enabled
    register: input
    address: 61
    type: f32                  # occupies two words
  - name: some_flag
    register: holding
    address: 42212
    type: bit
    bit: 3                     # required when type is bit on a 16-bit register

excluded:                      # documentation only, not read, not hashed
  - address: 42201
    name: start_stop_command
    reason: command
  - address: 42213
    name: external_temperature_measure
    reason: time-dependent measurement written by the controller
```

Validation rules enforced at load time (loading fails on violation):

- `schema` must be a supported value.
- `name` matches `^[a-z0-9][a-z0-9-]*$`; parameter names match
  `^[a-z][a-z0-9_]*$` and are unique within the profile.
- `address` is within the range implied by `addressing` and `register`, and
  the PDU address after adding `address_offset` is within 0..65535.
- `type: bit` requires `bit` (0–15) on `holding`/`input`, and forbids it on
  `coil`/`discrete`.
- No two parameters map to the same (register, address, bit) tuple.
- `excluded` entries are informational and are not validated against the
  device, but their addresses must not collide with `parameters`.

### 5.3 What the tool does with a profile

- Reads every entry in `parameters`, grouping contiguous addresses of the same
  register type into single Modbus requests where possible (configurable
  maximum gap, default 0 = strict, and maximum block length, default 100
  words / 2000 bits).
- Records, per parameter, the raw words (or the raw bit) and a quality flag.
- Ignores `excluded` entirely except for the `dch profile show` report.

### 5.4 Authoring rule for profiles (normative for shipped profiles)

A parameter belongs in a profile only if all of the following hold:

1. It configures device behaviour and is expected to stay constant during
   normal operation.
2. It is not rewritten automatically by any controller in the loop with a
   value that may vary (a controller may rewrite it with the *same* value,
   e.g. a watchdog timeout re-armed at each kick).
3. It is not a measurement, a counter, a clock, a heartbeat, a status word or
   a power/voltage/current setpoint used for dispatch.
4. It is readable by the tool (function codes 1–4).

Every register in the vendor map that is writable but does not meet these
rules should be listed under `excluded` with a one-line `reason`, so that an
auditor can see the decision rather than infer it from absence.

### 5.5 Shipped profiles (initial)

**`ingeteam-sun-storage-3power-c` v2** — 16 holding registers, plus one open
decision (see §10). v1 also read 42250 and 42252 (grid-forming voltage and
frequency droop); the vendor map marks both read-only (R, not R/W) and 42252
was observed changing between reads (114, 98, 107 within 50 minutes at Arizzi
on 2026-09-18), so they are reported values, not configuration, and are now
excluded:

| Address | Parameter |
|---------|-----------|
| 42202 | operation_mode_request |
| 42203 | reactive_power_control_mode |
| 42204 | active_power_increase_settling_time |
| 42205 | active_power_decrease_settling_time |
| 42206 | reactive_power_increase_settling_time |
| 42207 | reactive_power_decrease_settling_time |
| 42208 | communication_watchdog_timeout |
| 42209 | voltage_ramp |
| 42210 | frequency_ramp |
| 42212 | strategy_mode_bits_1 |
| 42218 | type_of_battery |
| 42219 | maximum_battery_voltage |
| 42220 | minimum_battery_voltage |
| 42221 | maximum_battery_charge_current |
| 42222 | maximum_battery_discharge_current |
| 42253 | grid_forming_connection_mode |

Excluded with reasons: 42201 start/stop (command), 42213 external
temperature (time-dependent measurement), 42217 Idc reference (setpoint),
42223 battery contactor (command), 42231–42235 and 42242 grid-following
P/Q/tanφ/cosφ/sign/Vdc setpoints (dispatch), 42249 and 42251 grid-forming
voltage and frequency setpoints (dispatch), 42250 and 42252 grid-forming
voltage and frequency droop (read-only, reported by the inverter), 42261
grid-forming Idc setpoint (dispatch).

**`jinko-scu-bank` v2** — 4 input registers (float32), the section the Jinko
SCU protocol v1.5 itself titles "System Configuration". Addresses are the
document's 1-based addresses with `address_offset: 2`: on the SunTera G2 SCU
(Arizzi, 2026-09-18) document address A is served at PDU address A+1, as
verified against the topology values, the local/remote flag and the rack
cell voltages. v1 of both Jinko profiles lacked the offset and hashed the
word pair preceding each documented value; the rack profile hashed the rack
voltage.

| Address (1-based) | Parameter |
|-------------------|-----------|
| 103 | number_of_racks |
| 105 | number_of_cells |
| 107 | number_of_temperature_sensors |
| 109 | number_of_packs |

Excluded with reasons: all holding registers (commands or reserved),
max allowable charge/discharge current and power (recomputed continuously
from SoC and temperature), heartbeat, RTC, energy counters, local/remote
status (see §10).

**`jinko-scu-rack` v2** — 1 input register (float32), same `address_offset: 2`:

| Address (1-based) | Parameter |
|-------------------|-----------|
| 61 | insulation_enabled |

## 6. Plant manifest

```yaml
schema: dch-manifest/1
plant: arizzi                  # free identifier, recorded in the snapshot
profile_paths:                 # optional; searched in order after the bundled profiles
  - ./profiles

devices:
  - id: inverter
    profile: ingeteam-sun-storage-3power-c
    connection: { host: 10.10.100.10, port: 502, unit_id: 1 }

  - id: bms_bank
    profile: jinko-scu-bank
    connection: { host: 10.10.100.20, port: 502 }

  - id: bms_rack_{n}           # expanded to bms_rack_1 … bms_rack_12
    profile: jinko-scu-rack
    expand: { var: n, from: 1, to: 12 }
    connection: { host: 10.10.100.20, port: "502 + {n}" }

options:
  timeout_s: 3.0
  retries: 2
  max_gap: 0
```

Rules:

- `id` values must be unique after expansion and match `^[a-z0-9][a-z0-9_-]*$`.
- `expand` substitutes `{var}` in `id` and in any string field of
  `connection`; `port` accepts a simple integer expression using `+`, `-`
  and `{var}`.
- Profiles are resolved by `name`; the manifest may pin `profile_version` to
  fail if the bundled or local profile has a different version.
- Devices are read sequentially by default to avoid overloading devices that
  accept few concurrent Modbus clients. `--parallel N` allows concurrency
  across *different hosts* only.

## 7. Canonical form and hashing

This section is normative. Any independent implementation following it must
produce identical hashes.

### 7.1 Parameter material

For each parameter, the material is:

- `n`: parameter name (string, as in the profile)
- `r`: register type, one of `"holding"`, `"input"`, `"coil"`, `"discrete"`
- `a`: PDU (0-based) address as an integer, after converting from the
  profile's `addressing`
- `w`: for 16-bit register types, the list of raw unsigned 16-bit words in
  the order returned by the device, exactly as many as the type requires
  (1 for u16/i16/bit, 2 for u32/i32/f32); for `bit` on a register, the full
  word is included (not the extracted bit), so the material is independent
  of bit extraction; for `coil`/`discrete`, `w` is `[0]` or `[1]`.

Note that `scale`, `unit`, `enum`, `description` and `bit` are **not** part
of the material: they affect display only.

### 7.2 Device material

```json
{
  "id": "<device id>",
  "profile": {
    "name": "<profile name>",
    "version": <profile version>,
    "sha256": "<hex sha256 of the profile file bytes, LF-normalized>"
  },
  "tool": {
    "source_sha256": "<hex sha256 of the tool source, see §7.7>"
  },
  "parameters": [ <parameter material>, ... ]
}
```

`tool.source_sha256` binds the hash to the exact software build that
produced it (§7.7). `parameters` is sorted by `(r, a)` with `r` compared as a string, and then by
`n` for the degenerate case of two parameters on the same word (different
bits).

### 7.3 Plant material

```json
{
  "plant": "<plant id>",
  "devices": [ { "id": "<device id>", "sha256": "<device hash>" }, ... ]
}
```

`devices` is sorted by `id`.

### 7.4 Canonical JSON

Canonical JSON follows RFC 8785 (JSON Canonicalization Scheme): UTF-8, object
keys sorted by code point, no insignificant whitespace, integers rendered
without exponent or fraction, strings escaped minimally. The material contains
only strings, integers, lists and objects — no floats — so the subset of RFC
8785 actually exercised is small and easy to reproduce by hand.

### 7.5 Hashes

- `device_sha256 = SHA-256( canonical_json(device material) )`
- `plant_sha256  = SHA-256( canonical_json(plant material) )`

Both are rendered as lowercase hex. The snapshot also records the algorithm
identifier `"sha256-jcs-v1"` so that a future change to the recipe is
detectable.

### 7.6 Fail-closed rule

If any parameter of a device has quality other than good, the device material
is not built, `device_sha256` is `null`, and `plant_sha256` is `null`. The
snapshot is still written (with the partial readings and the error per
parameter) so the operator can see what failed, and the CLI exits with code 2.

### 7.7 Tool source hash

The software itself is part of the hashed material, so that a hash also
identifies the exact code that computed it. Because the tool is pure Python,
the source hash is computed over the installed package sources:

- the set of files is every `*.py` file below the `device_config_hasher`
  package directory (recursively), and nothing else: no bundled profiles
  (they are hashed per device, §7.2), no `__pycache__`, no tests, no docs,
  no stray files such as `.DS_Store`. This is exactly the set of Python files
  committed to git under `src/device_config_hasher/`;
- files are sorted by their POSIX path relative to the package directory,
  compared as UTF-8 byte strings;
- for each file, in that order, the following bytes are fed to a single
  SHA-256: the relative path as UTF-8, one `0x00` byte, the file content with
  CRLF and CR line endings normalized to LF, one `0x00` byte;
- `source_sha256` is the lowercase hex digest.

The value is recorded in the snapshot as `tool.source_sha256` and enters the
device material (§7.2). When verifying a snapshot offline, the verifier uses
the `tool.source_sha256` **recorded in the snapshot** to rebuild the material,
so that snapshots produced by earlier builds remain verifiable; it reports
separately whether the running build is the same one that produced the file.

## 8. Snapshot document

```json
{
  "schema": "dch-snapshot/1",
  "tool": { "name": "device-config-hasher", "version": "0.1.0", "source_sha256": "…" },
  "algorithm": "sha256-jcs-v1",
  "plant": "arizzi",
  "captured_at": "2026-09-18T09:41:12Z",
  "complete": true,
  "plant_sha256": "…",
  "devices": [
    {
      "id": "inverter",
      "profile": { "name": "ingeteam-sun-storage-3power-c", "version": 1, "sha256": "…",
                   "source": "schema: dch-profile/1\nname: ingeteam-sun-storage-3power-c\n…" },
      "connection": { "host": "10.10.100.10", "port": 502, "unit_id": 1 },
      "complete": true,
      "device_sha256": "…",
      "parameters": [
        {
          "name": "communication_watchdog_timeout",
          "register": "holding",
          "address": 42208,
          "pdu_address": 2207,
          "type": "u16",
          "raw": [30000],
          "decoded": 30000,
          "unit": "ms",
          "quality": "good"
        }
      ]
    }
  ]
}
```

- `captured_at`, `connection`, `decoded`, `unit`, `quality`, `tool.name` and
  `tool.version` are informational and outside the hashed material.
  `tool.source_sha256` is hashed (§7.7).
- `profile.source` is the full, LF-normalized text of the profile file that
  was used, so that the snapshot is self-contained: a verifier can check that
  `sha256(profile.source) == profile.sha256` and an auditor can read the
  parameter definitions (including the `excluded` table) without access to
  the profile repository. The snapshot therefore carries everything needed
  to re-verify it with no network and no other file: the raw words, the
  profile, the tool source hash and the recorded hashes. This is what makes a
  future web-based verifier possible with the file alone as input.
- `decoded` is rendered by the tool's decoder; for `f32` it is written with
  Python `repr` (shortest round-trip). It is never re-hashed.
- On a failed read, the parameter has `"raw": null`, `"quality": "error"` and
  an `"error"` string.

## 9. Command-line interface

Entry point: `dch`.

**Scope of v0.1.** The first release ships only the commands needed to
produce a self-contained snapshot of one device and to verify a snapshot
file: `dch snapshot` with connection parameters given on the command line
(no manifest), `dch verify FILE` (offline only) and `dch profile list`.
Manifest support, live `verify`, `diff` and `profile show` are specified
here for later releases; the snapshot format already accommodates them
(`devices` is a list, `plant_sha256` is recorded).

```
dch snapshot --profile NAME_OR_PATH --host HOST [--port 502] [--unit-id 1]
             [--id DEVICE_ID] [--plant PLANT] [--timeout 3.0] [--retries 2]
             -o snapshot.json [--json]
dch verify snapshot.json [--json]
dch profile list [--profile-path DIR]... [--json]
dch source-hash [PROFILE_NAME_OR_PATH]... [--profile-path DIR]... [--json]
```

`--id` defaults to the profile name; `--plant` defaults to `"default"`.
`--profile` accepts a bundled profile name or a path to a YAML file.
`dch verify FILE` recomputes the profile hash from `profile.source`, every
`device_sha256` from the raw words and the recorded `tool.source_sha256`,
and `plant_sha256` from the device hashes, then compares each with the
recorded value. Exit 0 if everything matches, 1 on any mismatch, 3 if the
file is not a valid snapshot. The report states whether the running build's
source hash equals the recorded one; a difference is informational, not a
failure.

`dch source-hash` (added in 1.3.0) prints, without any snapshot, the
running build's `tool.source_sha256` and the `sha256` of every bundled
profile or of the profiles given as arguments, so that the values recorded
in a snapshot can be checked against a clean checkout of the corresponding
tag. Exit 0, or 3 if a named profile does not exist.

| Command | Purpose | Exit codes |
|---------|---------|------------|
| `dch snapshot -m plant.yaml -o snapshot.json` | Read all devices and write a snapshot. `--device ID` restricts to some devices. | 0 ok, 2 incomplete, 3 config error |
| `dch verify -m plant.yaml --baseline base.json` | Take a live snapshot and compare with the baseline. Prints per-parameter differences. `--save new.json` also stores the live snapshot. | 0 match, 1 mismatch, 2 incomplete, 3 config error |
| `dch verify --offline snapshot.json` | Recompute all hashes from the raw values in a snapshot file and check they match the recorded ones. No network. Detects a tampered snapshot file. | 0 ok, 1 hash mismatch |
| `dch diff a.json b.json` | Compare two snapshots parameter by parameter (raw and decoded). | 0 identical, 1 different |
| `dch profile list` / `dch profile show NAME` | Inspect bundled and local profiles, including the `excluded` table. | 0 |
| `dch manifest check plant.yaml` | Validate a manifest and print the expanded device list without connecting. | 0 / 3 |

Output is human-readable by default; `--json` switches every command to
machine-readable output for scripting.

## 10. Open decisions

1. **Pmax setpoint (inverter 40833).** Applied only while the maximum-output
   digital input is active, set rarely. Named a setpoint by the vendor, but
   behaves like a plant parameter. Proposed: **include** in the inverter
   profile. Decision pending.
2. **Local/remote status (BMS bank 97 and cooling unit 163).** Changes only by
   manual intervention on the SCU. Reading them would flag a technician
   switching the BMS to local mode. Proposed: **exclude** for v1; a "mode"
   flag is state, not configuration. Decision pending.
3. **License.** Proposed MIT, consistent with other Grid Society public
   repositories.
4. **Repository name and PyPI name.** `device-config-hasher`, CLI `dch`.

## 11. Gaps this tool cannot close

- The Jinko SCU exposes only topology over Modbus. Cell/rack protection
  thresholds, balancing parameters and firmware settings are not readable.
  Covering them requires a vendor export or a signed commissioning record.
- The Ingeteam inverter exposes the PPC-writable parameters over Modbus, but
  its internal protection settings (grid protection thresholds, LVRT curves,
  etc.) are only accessible through the vendor's service tools.
- The snapshot proves the device's reported register contents at a point in
  time; it does not prove firmware integrity.

These gaps will be stated in the README so that no reader mistakes the plant
hash for a complete attestation of device configuration.

## 12. Testing strategy

- **Unit, pure:** canonical serialization against hand-computed vectors
  (including a fixture set published in `tests/vectors/` so third parties can
  test their own implementation); address conversion for the three
  `addressing` modes; decoder for every type and word order; profile and
  manifest validation errors; manifest expansion.
- **Determinism:** same readings in shuffled order → same hash; one word
  changed → different hash; profile file changed by one byte → different hash;
  parameter missing → no hash.
- **Reader against a Modbus simulator:** pymodbus-based in-process server
  fixture with holding, input, coil and discrete tables; tests for grouping,
  retries, timeouts, partial failure, and the guarantee that no write function
  code is ever sent (server fixture records every request).
- **End-to-end CLI:** `snapshot` → `verify --offline` → mutate a register on
  the simulator → `verify` reports exactly one changed parameter with old and
  new raw and decoded values.
- **Shipped profiles:** a test asserts the exact parameter set of every
  bundled profile, so that any change to a profile is an explicit, reviewed
  diff, and that every writable register of the vendor map appears either in
  `parameters` or in `excluded`.

## 13. Tooling and repository layout

- Python ≥ 3.11, `uv` for environment and lockfile, `ruff` for lint/format,
  `pytest`, `mypy --strict`.
- Dependencies: `pymodbus` (client only), `pydantic`, `PyYAML`, `typer` or
  `click` for the CLI. Kept minimal; the canonical/hashing core has zero
  third-party dependencies so it can be vendored.
- Layout:

```
device-config-hasher/
  README.md            # purpose, quick start, guarantees, gaps (§11)
  LICENSE              # MIT
  pyproject.toml
  src/device_config_hasher/
    profile.py manifest.py modbus.py reader.py decode.py
    canonical.py snapshot.py compare.py cli.py
    profiles/           # bundled YAML profiles
  docs/
    specs/              # this document and successors
    hashing.md          # §7 restated as a standalone normative page
  tests/
    vectors/            # canonical-form test vectors
  examples/
    plant.example.yaml
```

- CI (GitHub Actions): lint, type-check, tests on Linux; publish to PyPI on
  tag.

## 14. Milestones

1. **M1 — Core and vectors.** `canonical`, `decode`, `profile`, `manifest`,
   test vectors, `hashing.md`. No network.
2. **M2 — Reader and snapshot.** `modbus`, `reader`, `snapshot`, simulator
   fixture, `dch snapshot`, `dch verify --offline`.
3. **M3 — Compare and verify.** `compare`, `dch verify`, `dch diff`, exit
   codes, `--json` output.
4. **M4 — Profiles and release.** Three bundled profiles with `excluded`
   tables, README, CI, first tagged release.
