"""Tests for pixel decoding and payload sniffing."""

import pytest

from samples import (
    MINIMAL_MP4,
    PNG_1X1,
    dxt1_solid,
    dxt3_solid,
    dxt5_solid,
    solid_bgr,
    solid_bgra,
)
from wepkg.pixels import decode, decode_dxt, is_decodable, rgb565_to_rgb
from wepkg.sniff import sniff, sniff_extension


# -- sniffing ----------------------------------------------------------------

def test_sniff_recognises_embedded_files():
    assert sniff(PNG_1X1) == ("png", "png")
    assert sniff(b"\xff\xd8\xff\xe0" + b"\x00" * 16)[0] == "jpeg"
    assert sniff(MINIMAL_MP4) == ("mp4", "mp4")
    assert sniff(b"DDS " + b"\x00" * 16) == ("dds", "dds")
    assert sniff(b"GIF89a" + b"\x00" * 16) == ("gif", "gif")
    assert sniff(b"BM" + b"\x00" * 16) == ("bmp", "bmp")
    assert sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == ("webp", "webp")


def test_sniff_ignores_unknown_and_short_data():
    assert sniff(b"") == (None, None)
    assert sniff(b"1234") == (None, None)
    assert sniff(b"\x00" * 64) == (None, None)


def test_sniff_does_not_mistake_arbitrary_ftyp():
    """'ftyp' must sit inside a plausible ISO box, not just appear anywhere."""
    assert sniff(b"ftyp\x00\x00\x00\x00" + b"\x00" * 8)[0] is None


def test_sniff_extension_helper():
    assert sniff_extension(PNG_1X1) == "png"
    assert sniff_extension(b"\x00" * 32) is None


# -- colour helpers ----------------------------------------------------------

def test_rgb565_expansion():
    assert rgb565_to_rgb(0x0000) == (0, 0, 0)
    assert rgb565_to_rgb(0xFFFF) == (255, 255, 255)
    red, green, blue = rgb565_to_rgb(0xF800)
    assert red == 255 and green == 0 and blue == 0
    # 6-bit green 32 is the closest representable value to 128, and scaling it
    # lands one step above; allow the quantisation step rather than exact 128.
    assert abs(rgb565_to_rgb(0x0400)[1] - 128) <= 2


# -- raw formats -------------------------------------------------------------

def test_argb8888_decodes_as_bgra():
    """ARGB8888 is stored BGRA, so the decoder must hand back mode 'BGRA'."""
    payload = solid_bgra(4, 2, (30, 20, 10), alpha=200)
    mode, raw = decode(0, 4, 2, payload)

    assert mode == "BGRA"
    assert len(raw) == 4 * 2 * 4
    assert raw[0:4] == bytes((30, 20, 10, 200))


def test_rgb888_decodes_as_bgr():
    mode, raw = decode(1, 2, 2, solid_bgr(2, 2, (9, 8, 7)))
    assert mode == "BGR"
    assert raw[0:3] == bytes((9, 8, 7))


def test_r8_is_single_channel():
    mode, raw = decode(9, 4, 4, bytes([7]) * 16)
    assert mode == "L"
    assert raw == bytes([7]) * 16


def test_rg88_is_two_channel():
    mode, raw = decode(8, 2, 2, bytes([1, 2]) * 4)
    assert mode == "RG"
    assert raw == bytes([1, 2]) * 4


def test_rgb565_is_expanded():
    import struct as _struct

    payload = _struct.pack("<H", 0xF800) * 4          # pure red
    mode, raw = decode(2, 2, 2, payload)

    assert mode == "RGB"
    assert raw[0:3] == bytes((255, 0, 0))


def test_rgba1010102_is_expanded():
    import struct as _struct

    # alpha = 3 (max), red/green/blue = full scale
    value = (3 << 30) | (1023 << 20) | (1023 << 10) | 1023
    payload = _struct.pack("<I", value)
    mode, raw = decode(13, 1, 1, payload)

    assert mode == "RGBA"
    assert raw == bytes((255, 255, 255, 255))


def test_short_payload_is_rejected():
    assert decode(0, 100, 100, b"\x00" * 16) is None


def test_zero_sized_image_is_rejected():
    assert decode(0, 0, 0, b"") is None


def test_unknown_format_is_not_decodable():
    assert not is_decodable(12)                       # BC7
    assert decode(12, 4, 4, b"\x00" * 64) is None


def test_is_decodable_reports_supported_formats():
    for tex_format in (0, 1, 2, 8, 9, 13, 4, 6, 7):
        assert is_decodable(tex_format), tex_format
    for tex_format in (10, 11, 12, 14, 15):
        assert not is_decodable(tex_format), tex_format


# -- DXT ---------------------------------------------------------------------

@pytest.mark.parametrize("size", [(4, 4), (8, 4), (4, 8), (8, 8), (7, 5)])
def test_dxt1_solid_colour(size):
    width, height = size
    rgba = decode_dxt(dxt1_solid(width, height, (255, 0, 0)), width, height, "DXT1")

    assert rgba is not None
    assert len(rgba) == width * height * 4
    assert rgba[0:4] == bytes((255, 0, 0, 255))
    # every texel, including the partial edge blocks of odd sizes
    assert set(rgba[3::4]) == {255}


@pytest.mark.parametrize("size", [(4, 4), (8, 8), (6, 6)])
def test_dxt5_solid_colour_and_alpha(size):
    width, height = size
    rgba = decode_dxt(dxt5_solid(width, height, (0, 128, 255), alpha=128), width, height, "DXT5")

    assert rgba is not None
    # colours round-trip through RGB565, so allow one quantisation step
    red, green, blue, alpha = rgba[0:4]
    assert abs(red - 0) <= 2 and abs(green - 128) <= 2 and abs(blue - 255) <= 2
    assert alpha == 128
    assert set(rgba[3::4]) == {128}


def test_dxt5_full_alpha():
    rgba = decode_dxt(dxt5_solid(4, 4, (10, 20, 30), alpha=255), 4, 4, "DXT5")
    assert rgba[3::4] == bytes([255]) * 16


def test_dxt3_solid_colour_and_alpha():
    rgba = decode_dxt(dxt3_solid(4, 4, (255, 255, 0)), 4, 4, "DXT3")

    assert rgba is not None
    assert rgba[0:4] == bytes((255, 255, 0, 255))


def test_dxt1_transparent_mode():
    """With c0 <= c1, index 3 is transparent black."""
    import struct as _struct

    c0, c1 = 0x0000, 0xFFFF           # c0 <= c1 -> transparent mode
    indices = 0xFFFFFFFF              # every texel uses index 3
    block = _struct.pack("<HHI", c0, c1, indices)
    rgba = decode_dxt(block, 4, 4, "DXT1")

    assert rgba[0:4] == bytes((0, 0, 0, 0))
    assert set(rgba[3::4]) == {0}


def test_dxt_short_payload_is_rejected():
    assert decode_dxt(b"\x00" * 4, 4, 4, "DXT1") is None


def test_dxt_unknown_mode_raises():
    with pytest.raises(ValueError):
        decode_dxt(b"\x00" * 16, 4, 4, "DXT9")


def test_decode_routes_dxt_formats():
    for tex_format, builder in ((7, dxt1_solid),):
        mode, raw = decode(tex_format, 4, 4, builder(4, 4, (255, 0, 0)))
        assert mode == "RGBA"
        assert raw[0:4] == bytes((255, 0, 0, 255))
