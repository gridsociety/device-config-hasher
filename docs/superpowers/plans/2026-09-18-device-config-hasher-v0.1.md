# device-config-hasher v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `dch` CLI that reads one Modbus TCP device according to a profile, writes a self-contained snapshot (raw words, decoded values, embedded profile, tool source hash, device and plant hashes), and verifies such a snapshot offline.

**Architecture:** A `device_config_hasher` package with small pure modules (`canonical`, `sourcehash`, `decode`, `profile`) and two I/O modules (`modbus`, `reader`) feeding a `snapshot` builder/verifier; `cli` is a thin click layer. Hashing follows spec §7 byte for byte; the hashing core has zero third-party dependencies.

**Tech Stack:** Python ≥ 3.11, uv, pydantic 2, PyYAML, pymodbus 3.15 (client only; its simulator is used in tests), click 8, pytest, ruff, mypy --strict.

**Spec:** `docs/specs/2026-09-18-device-config-hasher-design.md` (revised 2026-09-18).

## Global Constraints

- Only Modbus function codes 1, 2, 3, 4 are ever sent; no write path exists (spec §1.1, §3.1).
- Hash material contains only strings, integers, lists, objects — never floats (§7.4).
- Canonical JSON = RFC 8785 subset: sorted keys by code point, no whitespace, minimal escaping (§7.4).
- Algorithm identifier `"sha256-jcs-v1"`, snapshot schema `"dch-snapshot/1"`, profile schema `"dch-profile/1"`.
- Fail closed: any non-good parameter ⇒ `device_sha256 = null`, `plant_sha256 = null`, exit 2 (§7.6).
- Source hash covers exactly the `*.py` files under the package dir (§7.7).
- Exit codes: snapshot 0/2/3; verify 0/1/3.
- Tooling: `uv`, `ruff`, `mypy --strict`, `pytest`; layout per §13.

---

## File structure

```
pyproject.toml                       project metadata, deps, tool config, `dch` entry point
LICENSE                              MIT
README.md                            purpose, quick start, guarantees, gaps (§11)
docs/hashing.md                      §7 restated standalone
src/device_config_hasher/
  __init__.py                        __version__
  canonical.py                       canonical_json, sha256_hex, material builders, hashes   (no deps)
  sourcehash.py                      compute_source_sha256, tool_source_sha256              (no deps)
  decode.py                          decode_words                                            (no deps)
  profile.py                         pydantic models, load_profile, find_profile, list_profiles
  modbus.py                          ReadOnlyModbusClient (fc 1-4 only), ModbusReadError
  reader.py                          plan_reads, read_device → ParameterReading list
  snapshot.py                        build_snapshot, dump/load, verify_snapshot
  cli.py                             click group: snapshot, verify, profile list
  profiles/*.yaml                    bundled profiles
tests/
  conftest.py                        simulator fixture (pymodbus SimDevice server in a thread)
  vectors/*.json                     canonical-form test vectors
  test_canonical.py test_sourcehash.py test_decode.py test_profile.py
  test_reader.py test_snapshot.py test_cli.py test_bundled_profiles.py
examples/profile.example.yaml
.github/workflows/ci.yml
```

---

### Task 1: Project scaffold

**Files:** Create `pyproject.toml`, `LICENSE`, `src/device_config_hasher/__init__.py`, `tests/__init__.py`, `.gitignore`.

- [ ] `uv init --lib` style pyproject with `[project.scripts] dch = "device_config_hasher.cli:main"`, deps `pymodbus>=3.15`, `pydantic>=2`, `PyYAML>=6`, `click>=8`; dev deps `pytest`, `ruff`, `mypy`, `types-PyYAML`.
- [ ] `[tool.ruff] line-length = 100`, `[tool.mypy] strict = true`, `[tool.pytest.ini_options] testpaths = ["tests"]`.
- [ ] `__init__.py` exposes `__version__ = "0.1.0"`.
- [ ] `uv sync`, `uv run pytest` (0 tests collected, exit 5 acceptable), commit `chore: project scaffold`.

### Task 2: canonical

**Files:** Create `src/device_config_hasher/canonical.py`, `tests/test_canonical.py`, `tests/vectors/device-basic.json`, `tests/vectors/plant-basic.json`.

**Interfaces (produces):**
```python
Json = str | int | list["Json"] | dict[str, "Json"]
def canonical_json(value: Json) -> bytes
def sha256_hex(data: bytes) -> str
@dataclass(frozen=True)
class ParameterMaterial: name: str; register: str; pdu_address: int; words: list[int]
def device_material(device_id: str, profile_name: str, profile_version: int,
                    profile_sha256: str, tool_source_sha256: str,
                    parameters: Iterable[ParameterMaterial]) -> dict[str, Json]
def device_hash(material: dict[str, Json]) -> str
def plant_material(plant: str, device_hashes: Mapping[str, str]) -> dict[str, Json]
def plant_hash(material: dict[str, Json]) -> str
ALGORITHM = "sha256-jcs-v1"
```

- [ ] Tests: `canonical_json({"b":1,"a":[1,2]}) == b'{"a":[1,2],"b":1}'`; non-ASCII kept raw (`"é"` → UTF-8 bytes, not `é`); control char escaped `\n`; floats raise `TypeError`; keys sorted by code point (`"Z" < "a"`); parameter sort by `(r, a, n)`; devices sorted by id; hash of hand-computed vector equals `hashlib.sha256(expected_bytes).hexdigest()` stored in the vector file; shuffled input → same hash; one word changed → different hash; different tool source hash → different hash.
- [ ] Vector file format: `{"material": {...}, "canonical": "<string>", "sha256": "<hex>"}`; test loads every file in `tests/vectors/` and checks both.
- [ ] Commit `feat: canonical serialization and hashing`.

### Task 3: sourcehash

**Files:** Create `src/device_config_hasher/sourcehash.py`, `tests/test_sourcehash.py`.

**Interfaces:**
```python
def iter_source_files(package_dir: Path) -> list[Path]   # sorted *.py, no __pycache__
def compute_source_sha256(package_dir: Path) -> str      # §7.7 recipe
def tool_source_sha256() -> str                          # for the installed package, cached
```

- [ ] Tests in `tmp_path`: two files hashed in path order regardless of creation order; `a/b.py` sorts as `"a/b.py"`; `.DS_Store`, `x.yaml`, `__pycache__/x.pyc` ignored; CRLF content hashes equal to LF content; expected digest computed by hand in the test with `hashlib` over `path\0content\0`; `tool_source_sha256()` is 64 hex chars and equals `compute_source_sha256(Path(device_config_hasher.__file__).parent)`.
- [ ] Commit `feat: tool source hash`.

### Task 4: decode

**Files:** Create `src/device_config_hasher/decode.py`, `tests/test_decode.py`.

**Interfaces:**
```python
WORDS_PER_TYPE = {"u16":1,"i16":1,"bit":1,"u32":2,"i32":2,"f32":2}
def decode_words(words: list[int], type_: str, *, word_order: str = "big",
                 bit: int | None = None, scale: float | None = None,
                 enum: Mapping[int, str] | None = None) -> int | float | bool | str
```
- [ ] Tests: u16 65535; i16 `[0xFFFF]` → -1; u32 big `[1, 0]` → 65536, little → 1; i32 negative; f32 big `[0x3F80, 0]` → 1.0, little swapped; bit 3 of `0b1000` → True; scale 0.001 on 30000 → 30.0; enum 2 → label, unknown enum value → `"2 (unknown)"`; coil `[1]` type bit no `bit` → True; wrong word count raises `ValueError`.
- [ ] Commit `feat: display decoder`.

### Task 5: profile

**Files:** Create `src/device_config_hasher/profile.py`, `tests/test_profile.py`, `examples/profile.example.yaml`.

**Interfaces:**
```python
class Parameter(BaseModel): name, register: Literal["holding","input","coil","discrete"], address: int,
    type: Literal["u16","i16","u32","i32","f32","bit"], bit: int|None, scale: float|None,
    unit: str|None, enum: dict[int,str]|None, description: str|None
class Excluded(BaseModel): address: int; name: str|None; reason: str
class Protocol(BaseModel): type: Literal["modbus_tcp"]; addressing: Literal["pdu","one_based","modicon"];
    word_order: Literal["big","little"] = "big"; default_unit_id: int = 1
class Profile(BaseModel): schema_: str (alias "schema"), name, version: int, title, vendor, documentation,
    protocol, parameters, excluded
    source: str            # LF-normalized text
    sha256: str            # of source
    def pdu_address(self, p: Parameter) -> int
def normalize_lf(text: str) -> str
def load_profile(path: Path) -> Profile
def parse_profile(text: str) -> Profile
class ProfileError(ValueError)
def bundled_profiles_dir() -> Path
def list_profiles(extra_dirs: Sequence[Path] = ()) -> list[Profile]   # bundled first, then extras
def find_profile(name_or_path: str, extra_dirs: Sequence[Path] = ()) -> Profile
```
- [ ] Address conversion: `pdu` → same; `one_based` → a-1; `modicon` → holding 4xxxx-40001, input 3xxxx-30001, coil 0xxxx-1, discrete 1xxxx-10001; out-of-range raises `ProfileError`.
- [ ] Validation tests: bad `schema`; bad name regex; duplicate parameter names; duplicate (register, address, bit); `type: bit` without `bit` on holding; `bit` given on coil; excluded address colliding with a parameter; each raises `ProfileError` with a message naming the parameter.
- [ ] `sha256` equals `hashlib.sha256(normalize_lf(text).encode()).hexdigest()`; CRLF file gives same sha256 as LF file.
- [ ] `find_profile("ingeteam-sun-storage-3power-c")` finds the bundled one; a path works; unknown name raises `ProfileError`.
- [ ] Commit `feat: device profile loading and validation`.

### Task 6: modbus + reader + simulator fixture

**Files:** Create `src/device_config_hasher/modbus.py`, `src/device_config_hasher/reader.py`, `tests/conftest.py`, `tests/test_reader.py`.

**Interfaces:**
```python
# modbus.py
class ModbusReadError(Exception)
@dataclass class Connection: host: str; port: int = 502; unit_id: int = 1; timeout_s: float = 3.0; retries: int = 2
class ReadOnlyModbusClient:
    def __init__(self, conn: Connection) -> None
    def __enter__/__exit__
    def read_words(self, register: Literal["holding","input"], pdu_address: int, count: int) -> list[int]
    def read_bits(self, register: Literal["coil","discrete"], pdu_address: int, count: int) -> list[int]
# reader.py
@dataclass(frozen=True) class ReadBlock: register: str; start: int; count: int
def plan_reads(profile: Profile, *, max_gap: int = 0, max_words: int = 100, max_bits: int = 2000) -> list[ReadBlock]
@dataclass class ParameterReading: parameter: Parameter; pdu_address: int; raw: list[int] | None; quality: Literal["good","error"]; error: str | None
def read_device(profile: Profile, client: ReadOnlyModbusClient) -> list[ParameterReading]
```
- [ ] Fixture `simulator` (session-scoped server on a free port, per-test mutable dicts `holding`, `input`, `coils`, `discrete` applied through the `SimDevice.action` callback, `seen_function_codes` list filled by `trace_pdu`). Pattern validated in a probe: build `ModbusTcpServer` inside the loop thread, `serve_forever()`, `shutdown()` via `run_coroutine_threadsafe`.
- [ ] Tests: contiguous holding parameters become one block, a gap splits with `max_gap=0` and merges with `max_gap=1`; a 2-word f32 counts 2; blocks never exceed `max_words`; `read_device` returns good readings with expected words; an unreadable address yields `quality="error"` for exactly the parameters in that block and good for the rest; retries: a block that fails once then succeeds gives good; after all reads `seen_function_codes ⊆ {1,2,3,4}`; the client class has no method whose name starts with `write`.
- [ ] Commit `feat: read-only Modbus client and device reader`.

### Task 7: snapshot build + verify

**Files:** Create `src/device_config_hasher/snapshot.py`, `tests/test_snapshot.py`.

**Interfaces:**
```python
SCHEMA = "dch-snapshot/1"
def build_snapshot(*, plant: str, device_id: str, profile: Profile, connection: Connection,
                   readings: list[ParameterReading], captured_at: datetime,
                   tool_source_sha256: str) -> dict[str, Any]
def dump_snapshot(doc, path: Path) -> None           # json indent 2, ensure_ascii False, sort_keys False
def load_snapshot(path: Path) -> dict[str, Any]     # raises SnapshotError on bad schema/shape
@dataclass class Check: subject: str; expected: str | None; actual: str | None; ok: bool
@dataclass class VerifyResult: ok: bool; checks: list[Check]; recorded_source_sha256: str; current_source_sha256: str
    @property same_tool_build -> bool
def verify_snapshot(doc: dict[str, Any], *, current_source_sha256: str) -> VerifyResult
```
- [ ] `build_snapshot` writes the §8 document: `complete` true only if all readings good; `device_sha256`/`plant_sha256` null when incomplete; `profile.source` embedded; `decoded` via `decode_words` (None when raw is None); `address` as written in the profile and `pdu_address`.
- [ ] `verify_snapshot` checks: `sha256(profile.source) == profile.sha256` per device; recomputed `device_sha256` per complete device; recomputed `plant_sha256`; an incomplete snapshot verifies `ok=False` with a check saying `"incomplete snapshot has no hash"`.
- [ ] Tests: round trip build → dump → load → verify ok; tamper one raw word → device and plant checks fail, profile check ok; tamper one char of `profile.source` → profile check fails; tamper `tool.source_sha256` → device check fails; `same_tool_build` False when current differs, `ok` still True; `load_snapshot` on `{"schema":"x"}` raises `SnapshotError`.
- [ ] Commit `feat: snapshot builder and offline verifier`.

### Task 8: CLI

**Files:** Create `src/device_config_hasher/cli.py`, `tests/test_cli.py`.

- [ ] `dch snapshot` options per spec §9; connects, reads, builds, writes; human summary (device id, profile, n parameters, device hash, plant hash, or the list of failed parameters); `--json` prints the snapshot summary; exit 0 / 2 (incomplete) / 3 (config or connection error: bad profile, host unreachable).
- [ ] `dch verify FILE`: prints one line per check (`OK`/`FAIL`) plus `tool build: same|different (recorded …, current …)`; `--json` prints `VerifyResult`; exit 0 / 1 / 3.
- [ ] `dch profile list [--profile-path DIR]`: table name, version, title, parameters count; `--json` list of objects.
- [ ] Tests with `click.testing.CliRunner` and the simulator: snapshot → file exists and `verify` exits 0; mutate a register on the simulator → new snapshot has different device hash; tamper file → verify exits 1 with `FAIL` in output; unreachable port → exit 3; unknown profile → exit 3; simulator returning exception for a block → exit 2 and `complete: false` in file.
- [ ] Commit `feat: dch command-line interface`.

### Task 9: bundled profiles

**Files:** Create `src/device_config_hasher/profiles/ingeteam-sun-storage-3power-c.yaml`, `jinko-scu-bank.yaml`, `jinko-scu-rack.yaml`, `tests/test_bundled_profiles.py`.

- [ ] Ingeteam: modicon addressing, 18 holding u16 parameters of §5.5 with the `excluded` table of §5.5; 42204–42207 scale 0.001 unit s (per the §5.2 example); 42218 enum per §5.2. Types beyond what the spec states are left as plain u16 with descriptions, so decoding is conservative; this does not affect hashes.
- [ ] Jinko bank: one_based, 4 input f32 (103,105,107,109); Jinko rack: one_based, 1 input f32 (61). Excluded per §5.5.
- [ ] Test asserts the exact `(name, register, address, type)` set of each bundled profile and that every profile loads with `find_profile`.
- [ ] Commit `feat: bundled profiles`.

### Task 10: docs and CI

**Files:** Create `README.md`, `docs/hashing.md`, `.github/workflows/ci.yml`.

- [ ] README: purpose, install (`uv tool install` / `pip install`), the two commands with examples, guarantees (read-only, deterministic, fail-closed, source hash), gaps (§11), verifying without the tool.
- [ ] `docs/hashing.md`: §7 including §7.7, plus a 20-line pure-stdlib Python snippet that re-verifies a snapshot, as a reference for the future web verifier.
- [ ] CI: `uv sync`, `ruff check`, `ruff format --check`, `mypy`, `pytest` on ubuntu-latest, Python 3.11 and 3.12.
- [ ] Run all: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest`. Commit `docs: README, hashing reference, CI`.

## Self-review

- Spec coverage: §5 profile (T5, T9), §7 hashing incl. §7.7 (T2, T3), §7.6 fail-closed (T7, T8), §8 snapshot (T7), §9 v0.1 CLI (T8), §12 tests (vectors T2, determinism T2, simulator T6, e2e T8, profiles T9), §13 tooling (T1, T10). Manifest/compare/live verify deliberately out of v0.1 per spec §9.
- Names used across tasks: `ParameterMaterial`, `device_material`, `plant_material`, `Profile.pdu_address`, `Connection`, `ReadOnlyModbusClient`, `ParameterReading`, `build_snapshot`, `verify_snapshot`, `tool_source_sha256` — consistent.
