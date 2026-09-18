#!/usr/bin/env python3
"""Reference snapshot verifier using only the Python standard library.

This script re-implements spec §7 independently of the ``device_config_hasher``
package. It exists so that a third party (or a future web verifier) can check
a snapshot without installing the tool. Usage::

    python3 examples/verify_stdlib.py snapshot.json

Exit code 0 when every recorded hash is consistent with the raw contents,
1 otherwise.
"""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any


def canonical(value: Any) -> str:
    """RFC 8785 subset: strings, ints, lists, objects only."""
    if isinstance(value, bool) or value is None or isinstance(value, float):
        raise TypeError("only str/int/list/dict allowed in hashed material")
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ",".join(canonical(v) for v in value) + "]"
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                f"{json.dumps(k, ensure_ascii=False)}:{canonical(value[k])}" for k in sorted(value)
            )
            + "}"
        )
    raise TypeError(type(value).__name__)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify(doc: dict[str, Any]) -> list[tuple[str, bool]]:
    tool_sha = doc["tool"]["source_sha256"]
    results: list[tuple[str, bool]] = []
    recomputed_devices: dict[str, str] = {}
    for dev in doc["devices"]:
        prof = dev["profile"]
        source = prof["source"].replace("\r\n", "\n").replace("\r", "\n")
        results.append((f"{dev['id']}: profile sha256", sha256(source) == prof["sha256"]))
        if dev.get("device_sha256") is None or any(
            p["quality"] != "good" for p in dev["parameters"]
        ):
            results.append((f"{dev['id']}: incomplete, no hash", False))
            continue
        params = sorted(
            (
                {"n": p["name"], "r": p["register"], "a": p["pdu_address"], "w": list(p["raw"])}
                for p in dev["parameters"]
            ),
            key=lambda m: (m["r"], m["a"], m["n"]),
        )
        material = {
            "id": dev["id"],
            "profile": {"name": prof["name"], "version": prof["version"], "sha256": prof["sha256"]},
            "tool": {"source_sha256": tool_sha},
            "parameters": params,
        }
        actual = sha256(canonical(material))
        recomputed_devices[dev["id"]] = actual
        results.append((f"{dev['id']}: device sha256", actual == dev["device_sha256"]))
    if len(recomputed_devices) == len(doc["devices"]):
        plant = {
            "plant": doc["plant"],
            "devices": [
                {"id": i, "sha256": recomputed_devices[i]} for i in sorted(recomputed_devices)
            ],
        }
        results.append(("plant sha256", sha256(canonical(plant)) == doc["plant_sha256"]))
    return results


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        doc = json.load(fh)
    if doc.get("schema") != "dch-snapshot/1" or doc.get("algorithm") != "sha256-jcs-v1":
        print("unsupported snapshot schema/algorithm")
        return 2
    results = verify(doc)
    for subject, ok in results:
        print(("OK   " if ok else "FAIL ") + subject)
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
