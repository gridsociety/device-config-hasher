import pytest

from device_config_hasher.decode import WORDS_PER_TYPE, decode_words


def test_words_per_type() -> None:
    assert WORDS_PER_TYPE == {"u16": 1, "i16": 1, "bit": 1, "u32": 2, "i32": 2, "f32": 2}


def test_u16_and_i16() -> None:
    assert decode_words([65535], "u16") == 65535
    assert decode_words([0xFFFF], "i16") == -1
    assert decode_words([0x7FFF], "i16") == 32767


def test_u32_word_order() -> None:
    assert decode_words([1, 0], "u32", word_order="big") == 65536
    assert decode_words([1, 0], "u32", word_order="little") == 1
    assert decode_words([0xFFFF, 0xFFFF], "i32") == -1


def test_f32() -> None:
    assert decode_words([0x3F80, 0x0000], "f32") == 1.0
    assert decode_words([0x0000, 0x3F80], "f32", word_order="little") == 1.0
    assert decode_words([0xC000, 0x0000], "f32") == -2.0


def test_bit_on_register() -> None:
    assert decode_words([0b1000], "bit", bit=3) is True
    assert decode_words([0b0111], "bit", bit=3) is False


def test_bit_on_coil() -> None:
    assert decode_words([1], "bit") is True
    assert decode_words([0], "bit") is False


def test_scale() -> None:
    assert decode_words([30000], "u16", scale=0.001) == 30.0
    assert decode_words([0xFFFF], "i16", scale=0.1) == -0.1


def test_enum() -> None:
    labels = {1: "No contactor", 2: "With contactor"}
    assert decode_words([2], "u16", enum=labels) == "With contactor"
    assert decode_words([9], "u16", enum=labels) == "9 (unknown)"


def test_wrong_word_count() -> None:
    with pytest.raises(ValueError):
        decode_words([1], "u32")
    with pytest.raises(ValueError):
        decode_words([1, 2], "u16")


def test_word_out_of_range() -> None:
    with pytest.raises(ValueError):
        decode_words([70000], "u16")
