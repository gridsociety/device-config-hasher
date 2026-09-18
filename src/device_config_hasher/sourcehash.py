"""Hash of the tool's own source code (spec §7.7).

The digest covers exactly the ``*.py`` files below the package directory,
sorted by their POSIX relative path (compared as UTF-8 bytes), each fed as
``path 0x00 content 0x00`` with line endings normalized to LF. Nothing else
(bundled profiles, caches, stray files) is included.
"""

from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path


def _normalize_lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def iter_source_files(package_dir: Path) -> list[Path]:
    """Return the ``*.py`` files below *package_dir*, sorted by relative POSIX path."""
    files = [
        p
        for p in package_dir.rglob("*.py")
        if p.is_file() and "__pycache__" not in p.relative_to(package_dir).parts
    ]
    return sorted(files, key=lambda p: p.relative_to(package_dir).as_posix().encode("utf-8"))


def compute_source_sha256(package_dir: Path) -> str:
    h = hashlib.sha256()
    for path in iter_source_files(package_dir):
        h.update(path.relative_to(package_dir).as_posix().encode("utf-8"))
        h.update(b"\x00")
        h.update(_normalize_lf(path.read_bytes()))
        h.update(b"\x00")
    return h.hexdigest()


@cache
def tool_source_sha256() -> str:
    """Source hash of the installed ``device_config_hasher`` package."""
    return compute_source_sha256(Path(__file__).resolve().parent)
