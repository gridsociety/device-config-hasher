# Hashing recipe (`sha256-jcs-v1`)

This page restates §7 of the design specification as a standalone normative
reference. Any independent implementation that follows it produces the same
hashes as `device-config-hasher`.

## 1. Parameter material

For each parameter of a device:

| Key | Value |
|-----|-------|
| `n` | parameter name, as written in the profile |
| `r` | register type: `"holding"`, `"input"`, `"coil"` or `"discrete"` |
| `a` | 0-based PDU address (integer), after converting from the profile's `addressing` |
| `w` | list of raw unsigned 16-bit words as returned by the device, in device order: 1 word for `u16`/`i16`/`bit`, 2 for `u32`/`i32`/`f32`. For `bit` on a register the **whole word** is used. For `coil`/`discrete`, `[0]` or `[1]`. |

`scale`, `unit`, `enum`, `description` and `bit` are display-only and not part
of the material.

## 2. Device material

```json
{
  "id": "<device id>",
  "profile": { "name": "<profile name>", "version": <int>, "sha256": "<hex>" },
  "tool": { "source_sha256": "<hex>" },
  "parameters": [ <parameter material>, ... ]
}
```

- `profile.sha256` is the SHA-256 of the profile file text with `CRLF` and
  `CR` line endings normalized to `LF`, encoded as UTF-8.
- `tool.source_sha256` is defined in §5 below.
- `parameters` is sorted by `(r, a, n)`, comparing `r` and `n` as strings and
  `a` as an integer.

## 3. Plant material

```json
{ "plant": "<plant id>", "devices": [ { "id": "<device id>", "sha256": "<device hash>" }, ... ] }
```

`devices` is sorted by `id`.

## 4. Canonical JSON and hashes

The material is serialized following RFC 8785 (JSON Canonicalization Scheme):

- UTF-8, no insignificant whitespace;
- object keys sorted by Unicode code point;
- integers rendered as plain decimal digits;
- strings escaped minimally: `"` → `\"`, `\` → `\\`, control characters
  `\b \f \n \r \t` by their short escapes, other characters below U+0020 as
  `\u00XX`, everything else (including non-ASCII) written raw.

Only strings, integers, lists and objects appear in the material. Floats,
booleans and `null` are forbidden and must make an implementation fail.

```
device_sha256 = hex( SHA-256( canonical_json(device material) ) )
plant_sha256  = hex( SHA-256( canonical_json(plant material)  ) )
```

Hex digests are lowercase.

## 5. Tool source hash

`tool.source_sha256` binds a hash to the exact software build that produced
it. It is the SHA-256 of the tool's own Python sources:

- the file set is every `*.py` file under the installed `device_config_hasher`
  package directory, recursively, excluding anything under `__pycache__`.
  Nothing else is included: no bundled profiles, tests, docs or stray files;
- files are ordered by their POSIX path relative to the package directory,
  compared as UTF-8 byte strings;
- for each file, in order, feed to one SHA-256: the relative path as UTF-8,
  one `0x00` byte, the file content with `CRLF`/`CR` normalized to `LF`, one
  `0x00` byte;
- the digest is rendered as lowercase hex.

## 6. Fail-closed rule

If any parameter of a device has a quality other than `good`, no device
material is built, `device_sha256` is `null`, and `plant_sha256` is `null`.
The snapshot is still written so that the failure is visible.

## 7. Verifying a snapshot

Given only the snapshot file:

1. For every device, check `sha256(LF-normalize(profile.source)) == profile.sha256`.
2. For every device with `complete == true`, rebuild the device material from
   the `parameters` entries (`name`, `register`, `pdu_address`, `raw`) plus
   `profile.{name,version,sha256}` and the snapshot's `tool.source_sha256`,
   and compare its hash with `device_sha256`.
3. Rebuild the plant material from the **recomputed** device hashes and
   compare with `plant_sha256`.
4. Independently, compare `tool.source_sha256` with the source hash of the
   build you are running; a difference means the snapshot was produced by a
   different build, which is informational, not a failure.

`examples/verify_stdlib.py` implements these steps with the Python standard
library only and is tested against the package.

## 8. Worked example

Test vectors with the material, its canonical form and the expected digest are
published under `tests/vectors/`. For instance `tests/vectors/plant-basic.json`:

```json
{"devices":[{"id":"bms","sha256":"cc…"},{"id":"inverter","sha256":"dd…"}],"plant":"arizzi"}
```

hashes to the `sha256` value recorded in that file.
