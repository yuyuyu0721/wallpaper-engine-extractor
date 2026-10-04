"""Tests for the TEX reader, covering every container version."""

import struct

import pytest

from samples import JPEG_HEAD, MINIMAL_MP4, PNG_1X1, build_tex, solid_bgra
from wepkg.tex import (
    FLAG_VIDEO,
    TexError,
    flag_names,
    parse_tex,
)


def _level(width, height, payload, lz4=False, decompressed=0):
    return {
        "width": width, "height": height, "payload": payload,
        "lz4": lz4, "decompressed": decompressed,
    }


@pytest.mark.parametrize("container", ["TEXB0001", "TEXB0002", "TEXB0003", "TEXB0004"])
def test_single_level_roundtrip(container):
    payload = solid_bgra(4, 4, (10, 20, 30))
    blob = build_tex(container, [[_level(4, 4, payload)]], tex_format=0)

    info = parse_tex(blob)

    assert info.container == container
    assert info.tex_format == 0
    assert info.tex_format_name == "ARGB8888"
    assert info.level_count == 1
    assert info.consumed == len(blob)
    assert info.main_mipmap().width == 4
    assert info.main_mipmap().height == 4
    assert info.main_mipmap().data() == payload


@pytest.mark.parametrize("container", ["TEXB0002", "TEXB0003", "TEXB0004"])
def test_multiple_levels_are_read_in_order(container):
    payloads = [bytes([i]) * (4 * 4 * 4) for i in range(1, 4)]
    sizes = [(8, 8), (4, 4), (2, 2)]
    blob = build_tex(
        container,
        [[_level(w, h, p) for (w, h), p in zip(sizes, payloads)]],
        tex_format=0,
    )

    info = parse_tex(blob)

    assert info.level_count == 3
    assert [m.data() for m in info.images[0]] == payloads
    assert [(m.width, m.height) for m in info.images[0]] == sizes
    assert info.consumed == len(blob)

@pytest.mark.parametrize("container", ["TEXB0003", "TEXB0004"])
def test_free_image_format_field_is_parsed(container):
    blob = build_tex(container, [[_level(2, 2, PNG_1X1)]], free_image_format=13)
    info = parse_tex(blob)

    assert info.free_image_format == 13
    assert info.free_image_format_name == "PNG"
    assert info.has_embedded_file


def test_texi0001_and_texi0002_have_no_free_image_format_field():
    """Regression: reading a fif field for TEXB0002 desynchronises the stream."""
    payload = solid_bgra(2, 2, (1, 2, 3))
    blob = build_tex("TEXB0002", [[_level(2, 2, payload), _level(1, 1, b"\x00" * 4)]],
                     tex_format=0)

    info = parse_tex(blob)

    assert info.free_image_format == -1
    assert info.level_count == 2
    assert info.consumed == len(blob)


def test_records_are_twenty_bytes_not_sixteen():
    """Regression: a 16-byte stride parses level 0 then drifts."""
    payloads = [b"A" * 16, b"B" * 8, b"C" * 4]
    sizes = [(4, 4), (2, 2), (1, 1)]
    blob = build_tex(
        "TEXB0003",
        [[_level(w, h, p) for (w, h), p in zip(sizes, payloads)]],
    )

    info = parse_tex(blob)

    assert [m.data() for m in info.images[0]] == payloads
    assert info.consumed == len(blob)


def test_texb0004_has_an_extra_field_before_records():
    payload = solid_bgra(4, 4, (9, 9, 9))
    blob = build_tex("TEXB0004", [[_level(4, 4, payload)]])

    info = parse_tex(blob)

    assert info.main_mipmap().data() == payload
    assert info.consumed == len(blob)


def test_video_flag_and_mp4_payload():
    blob = build_tex(
        "TEXB0003",
        [[_level(1920, 1080, MINIMAL_MP4)]],
        tex_format=0,
        flags=FLAG_VIDEO | 0x2,
    )

    info = parse_tex(blob)

    assert info.is_video
    assert "Video" in info.flags_name
    assert info.main_mipmap().data().startswith(b"\x00\x00\x00\x18ftyp")


def test_flag_names_rendering():
    assert flag_names(0) == "-"
    assert flag_names(0x2) == "ClampUVs"
    assert flag_names(0x2 | 0x20) == "ClampUVs|Video"
    assert flag_names(0x4) == "IsGif"


def test_lz4_level_is_flagged_and_decompresses():
    lz4_block = pytest.importorskip("lz4.block")
    raw = solid_bgra(64, 64, (5, 6, 7))          # highly compressible
    compressed = lz4_block.compress(raw)
    assert len(compressed) < len(raw)
    blob = build_tex(
        "TEXB0003",
        [[_level(64, 64, compressed, lz4=True, decompressed=len(raw))]],
        tex_format=0,
    )

    info = parse_tex(blob)

    assert info.main_mipmap().lz4_compressed
    assert info.main_mipmap().data() == raw


def test_compressed_flag_with_uncompressed_data_falls_back():
    """Some files claim LZ4 but store the bytes verbatim."""
    raw = solid_bgra(4, 4, (1, 2, 3))
    blob = build_tex(
        "TEXB0003",
        [[_level(4, 4, raw, lz4=True, decompressed=len(raw))]],
        tex_format=0,
    )

    info = parse_tex(blob)

    assert info.main_mipmap().lz4_compressed
    assert info.main_mipmap().data() == raw         # no exception raised


def test_multiple_sub_images():
    first = solid_bgra(4, 4, (1, 1, 1))
    second = solid_bgra(2, 2, (2, 2, 2))
    blob = build_tex(
        "TEXB0003",
        [[_level(4, 4, first)], [_level(2, 2, second)]],
        tex_format=0,
        flags=0x4,
    )

    info = parse_tex(blob)

    assert info.image_count == 2
    assert info.is_animated
    assert info.images[0][0].data() == first
    assert info.images[1][0].data() == second
    assert info.consumed == len(blob)


def test_trailing_bytes_are_tolerated():
    """Some textures carry padding after the last payload."""
    payload = solid_bgra(4, 4, (3, 3, 3))
    blob = build_tex("TEXB0003", [[_level(4, 4, payload)]], trailing=b"JUNKJUNK")

    info = parse_tex(blob)

    assert info.main_mipmap().data() == payload
    assert info.consumed == len(blob) - 8


def test_strict_mode_rejects_inexact_layout():
    payload = solid_bgra(4, 4, (3, 3, 3))
    blob = build_tex("TEXB0003", [[_level(4, 4, payload)]], trailing=b"JUNK")

    parse_tex(blob)                                  # default is lenient
    with pytest.raises(TexError, match="exactly"):
        parse_tex(blob, strict=True)


@pytest.mark.parametrize("blob", [b"", b"TEXV0005\x00", b"X" * 64])
def test_rejects_bad_magic(blob):
    with pytest.raises(TexError):
        parse_tex(blob)


def test_rejects_unknown_container():
    blob = bytearray(build_tex("TEXB0003", [[_level(1, 1, b"\x00")]]))
    blob[46:55] = b"TEXB0009\x00"
    with pytest.raises(TexError, match="unsupported container"):
        parse_tex(bytes(blob))


def test_rejects_implausible_image_count():
    blob = bytearray(build_tex("TEXB0003", [[_level(1, 1, b"\x00")]]))
    blob[55:59] = struct.pack("<I", 0)
    with pytest.raises(TexError, match="image count"):
        parse_tex(bytes(blob))


def test_texture_dimensions_are_reported():
    blob = build_tex("TEXB0003", [[_level(16, 8, b"\x00" * 16)]],
                     texture_size=(2560, 1440), image_size=(16, 8))
    info = parse_tex(blob)

    assert (info.texture_width, info.texture_height) == (2560, 1440)
    assert (info.image_width, info.image_height) == (16, 8)


def test_jpeg_payload_is_recognisable():
    blob = build_tex("TEXB0003", [[_level(100, 50, JPEG_HEAD + b"\x00" * 32)]],
                     free_image_format=2)
    info = parse_tex(blob)

    assert info.free_image_format_name == "JPEG"
    assert info.main_mipmap().data().startswith(b"\xff\xd8\xff")
