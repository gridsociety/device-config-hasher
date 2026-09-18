import hashlib
from pathlib import Path

import device_config_hasher
from device_config_hasher.sourcehash import (
    compute_source_sha256,
    iter_source_files,
    tool_source_sha256,
)


def _expected(files: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for rel in sorted(files, key=lambda s: s.encode("utf-8")):
        content = files[rel].replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        h.update(content)
        h.update(b"\x00")
    return h.hexdigest()


def _write(root: Path, files: dict[str, bytes]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def test_iter_source_files_sorted_and_filtered(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "z.py": b"z",
            "a/b.py": b"b",
            "a.py": b"a",
            ".DS_Store": b"junk",
            "profiles/x.yaml": b"yaml",
            "__pycache__/a.cpython-312.pyc": b"pyc",
            "a/__pycache__/b.cpython-312.pyc": b"pyc",
        },
    )
    rel = [p.relative_to(tmp_path).as_posix() for p in iter_source_files(tmp_path)]
    assert rel == ["a.py", "a/b.py", "z.py"]


def test_compute_matches_recipe(tmp_path: Path) -> None:
    files = {
        "__init__.py": b"x = 1\n",
        "cli.py": b"def main():\n    pass\n",
        "sub/mod.py": b"# m\n",
    }
    _write(tmp_path, files)
    assert compute_source_sha256(tmp_path) == _expected(files)


def test_creation_order_and_stray_files_do_not_matter(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    _write(a, {"m.py": b"1", "n.py": b"2"})
    _write(b, {"n.py": b"2"})
    _write(b, {"m.py": b"1", ".DS_Store": b"x", "data.yaml": b"y"})
    assert compute_source_sha256(a) == compute_source_sha256(b)


def test_line_endings_normalized(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    _write(a, {"m.py": b"x = 1\ny = 2\n"})
    _write(b, {"m.py": b"x = 1\r\ny = 2\r\n"})
    assert compute_source_sha256(a) == compute_source_sha256(b)


def test_content_change_changes_hash(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    _write(a, {"m.py": b"x = 1\n"})
    _write(b, {"m.py": b"x = 2\n"})
    assert compute_source_sha256(a) != compute_source_sha256(b)


def test_tool_source_sha256_is_package_hash() -> None:
    value = tool_source_sha256()
    assert len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    pkg = Path(device_config_hasher.__file__).parent
    assert value == compute_source_sha256(pkg)
    assert tool_source_sha256() is value  # cached
