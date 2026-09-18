"""Device profiles: loading, validation, identity (spec §5)."""

from __future__ import annotations

import hashlib
import re
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

SCHEMA = "dch-profile/1"

RegisterType = Literal["holding", "input", "coil", "discrete"]
ValueType = Literal["u16", "i16", "u32", "i32", "f32", "bit"]
Addressing = Literal["pdu", "one_based", "modicon"]

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_PARAM_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Modicon offsets: address ranges per register type, mapped to PDU 0-based.
_MODICON: dict[str, tuple[int, int]] = {
    "coil": (1, 9999),
    "discrete": (10001, 19999),
    "input": (30001, 39999),
    "holding": (40001, 49999),
}


class ProfileError(ValueError):
    """Raised when a profile cannot be loaded or is invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# "register" is the natural name for the Modbus table and is what profile
# authors write; pydantic warns because it shadows ABCMeta.register, which
# models never use.
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message='Field name "register"')

    class Parameter(_Strict):
        name: str
        register: RegisterType
        address: int
        type: ValueType
        bit: int | None = None
        scale: float | None = None
        unit: str | None = None
        enum: dict[int, str] | None = None
        description: str | None = None

    class Excluded(_Strict):
        address: int
        name: str | None = None
        reason: str
        register: RegisterType = "holding"


class Protocol(_Strict):
    type: Literal["modbus_tcp"]
    addressing: Addressing
    word_order: Literal["big", "little"] = "big"
    default_unit_id: int = 1


class Profile(_Strict):
    schema_: str = Field(alias="schema")
    name: str
    version: int
    title: str | None = None
    vendor: str | None = None
    documentation: list[str] | None = None
    protocol: Protocol
    parameters: list[Parameter]
    excluded: list[Excluded] = Field(default_factory=list)

    # Filled by parse_profile, not by the YAML author.
    source: str = Field(default="", exclude=True)
    sha256: str = Field(default="", exclude=True)
    path: str | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _validate(self) -> Profile:
        if self.schema_ != SCHEMA:
            raise ValueError(f"unsupported schema {self.schema_!r}, expected {SCHEMA!r}")
        if not _NAME_RE.match(self.name):
            raise ValueError(f"profile name {self.name!r} must match {_NAME_RE.pattern}")
        seen_names: set[str] = set()
        seen_words: set[tuple[str, int, int | None]] = set()
        param_addresses: set[tuple[str, int]] = set()
        for p in self.parameters:
            if not _PARAM_RE.match(p.name):
                raise ValueError(f"parameter name {p.name!r} must match {_PARAM_RE.pattern}")
            if p.name in seen_names:
                raise ValueError(f"duplicate parameter name {p.name!r}")
            seen_names.add(p.name)
            if p.register in ("coil", "discrete"):
                if p.type != "bit":
                    raise ValueError(f"parameter {p.name!r}: {p.register} requires type bit")
                if p.bit is not None:
                    raise ValueError(f"parameter {p.name!r}: bit is not allowed on {p.register}")
            elif p.type == "bit":
                if p.bit is None or not 0 <= p.bit <= 15:
                    raise ValueError(f"parameter {p.name!r}: type bit requires bit in 0..15")
            elif p.bit is not None:
                raise ValueError(f"parameter {p.name!r}: bit is only allowed with type bit")
            self.pdu_address(p)  # range check
            key = (p.register, p.address, p.bit)
            if key in seen_words:
                raise ValueError(f"parameter {p.name!r} maps to the same word as another parameter")
            seen_words.add(key)
            param_addresses.add((p.register, p.address))
        for e in self.excluded:
            if (e.register, e.address) in param_addresses:
                raise ValueError(
                    f"excluded address {e.address} collides with a parameter; "
                    "a register cannot be both read and excluded"
                )
        return self

    def pdu_address(self, p: Parameter) -> int:
        """Convert the profile address of *p* to a 0-based PDU address."""
        mode = self.protocol.addressing
        if mode == "pdu":
            pdu = p.address
        elif mode == "one_based":
            pdu = p.address - 1
        else:
            lo, hi = _MODICON[p.register]
            if not lo <= p.address <= hi:
                raise ValueError(
                    f"parameter {p.name!r}: modicon address {p.address} is outside the "
                    f"{p.register} range {lo}..{hi}"
                )
            pdu = p.address - lo
        if not 0 <= pdu <= 0xFFFF:
            raise ValueError(f"parameter {p.name!r}: address {p.address} is out of range")
        return pdu


def normalize_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_profile(text: str) -> Profile:
    """Parse profile YAML *text*; the LF-normalized text and its SHA-256 are recorded."""
    source = normalize_lf(text)
    try:
        data: Any = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise ProfileError(f"invalid YAML in profile: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError("profile must be a YAML mapping")
    try:
        profile = Profile.model_validate(data)
    except ValidationError as exc:
        raise ProfileError(_format_validation_error(exc)) from exc
    profile.source = source
    profile.sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return profile


def _format_validation_error(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"])
        lines.append(f"{loc}: {err['msg']}" if loc else err["msg"])
    return "invalid profile: " + "; ".join(lines)


def load_profile(path: Path) -> Profile:
    try:
        text = path.read_bytes().decode("utf-8")
    except OSError as exc:
        raise ProfileError(f"cannot read profile {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ProfileError(f"profile {path} is not UTF-8: {exc}") from exc
    try:
        profile = parse_profile(text)
    except ProfileError as exc:
        raise ProfileError(f"{path}: {exc}") from exc
    profile.path = str(path)
    return profile


def bundled_profiles_dir() -> Path:
    return Path(__file__).resolve().parent / "profiles"


def _profile_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.y*ml") if p.is_file())


def list_profiles(extra_dirs: Sequence[Path] = ()) -> list[Profile]:
    """All bundled profiles (sorted by file name), then those in *extra_dirs* in order."""
    profiles: list[Profile] = []
    for directory in [bundled_profiles_dir(), *extra_dirs]:
        profiles.extend(load_profile(p) for p in _profile_files(directory))
    return profiles


def find_profile(name_or_path: str, extra_dirs: Sequence[Path] = ()) -> Profile:
    """Resolve a bundled/local profile by name, or load a profile from a path."""
    path = Path(name_or_path)
    if path.suffix.lower() in (".yaml", ".yml") or path.is_file():
        return load_profile(path)
    for profile in list_profiles(extra_dirs):
        if profile.name == name_or_path:
            return profile
    raise ProfileError(f"profile {name_or_path!r} not found (not a file, not a known profile name)")
