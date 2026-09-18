"""Decode raw register words into display values.

Decoding is for humans and diff reports only. It is never part of the
hashed material (spec §3.4): the hash covers the raw words.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping, Sequence

WORDS_PER_TYPE: dict[str, int] = {"u16": 1, "i16": 1, "bit": 1, "u32": 2, "i32": 2, "f32": 2}


def _to_bytes(words: Sequence[int], word_order: str) -> bytes:
    ordered = list(words) if word_order == "big" else list(reversed(words))
    return b"".join(struct.pack(">H", w) for w in ordered)


def decode_words(
    words: Sequence[int],
    type_: str,
    *,
    word_order: str = "big",
    bit: int | None = None,
    scale: float | None = None,
    enum: Mapping[int, str] | None = None,
) -> int | float | bool | str:
    """Decode *words* according to *type_*.

    For ``bit`` with ``bit=None`` the single word is a coil/discrete value
    (0 or 1). ``scale`` multiplies numeric results; ``enum`` maps the integer
    result to a label, falling back to ``"<value> (unknown)"``.
    """
    if type_ not in WORDS_PER_TYPE:
        raise ValueError(f"unknown type {type_!r}")
    if len(words) != WORDS_PER_TYPE[type_]:
        raise ValueError(f"type {type_} needs {WORDS_PER_TYPE[type_]} word(s), got {len(words)}")
    for w in words:
        if not 0 <= w <= 0xFFFF:
            raise ValueError(f"word {w} out of 16-bit range")
    if word_order not in ("big", "little"):
        raise ValueError(f"unknown word order {word_order!r}")

    if type_ == "bit":
        word = words[0]
        return bool((word >> bit) & 1) if bit is not None else bool(word)

    value: int | float
    if type_ == "u16":
        value = words[0]
    elif type_ == "i16":
        value = struct.unpack(">h", struct.pack(">H", words[0]))[0]
    elif type_ == "u32":
        value = struct.unpack(">I", _to_bytes(words, word_order))[0]
    elif type_ == "i32":
        value = struct.unpack(">i", _to_bytes(words, word_order))[0]
    else:  # f32
        value = struct.unpack(">f", _to_bytes(words, word_order))[0]

    if enum is not None and isinstance(value, int):
        return enum.get(value, f"{value} (unknown)")
    if scale is not None:
        return value * scale
    return value
