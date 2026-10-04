"""Builders for synthetic ``.pkg`` and ``.tex`` streams.

These let the test suite run anywhere without shipping real wallpaper data:
every container version and pixel format can be generated deterministically.
"""

from __future__ import annotations

import struct
from typing import Iterable, List, Sequence, Tuple

# -- .pkg --------------------------------------------------------------------

def build_pkg(entries: Sequence[Tuple[str, bytes]], version: str = "PKGV0021") -> bytes:
    """Assemble a PKGV package from ``(name, payload)`` pairs."""
    encoded_version = version.encode("ascii")
    out = bytearray()
    out += struct.pack("<i", len(encoded_version))
    out += encoded_version
    out += struct.pack("<i", len(entries))

    offset = 0
    blobs = bytearray()
    for name, payload in entries:
        raw_name = name.encode("utf-8")
        out += struct.pack("<i", len(raw_name))
        out += raw_name
        out += struct.pack("<ii", offset, len(payload))
        blobs += payload
        offset += len(payload)

    out += blobs
    return bytes(out)


# -- .tex --------------------------------------------------------------------

def _header(tex_format: int, flags: int, width: int, height: int,
            image_width: int, image_height: int) -> bytes:
    out = bytearray()
    out += b"TEXV0005\x00"
    out += b"TEXI0001\x00"
    out += struct.pack("<6I", tex_format, flags, width, height, image_width, image_height)
    out += struct.pack("<I", 0)          # unknown constant
    return bytes(out)


def build_tex(container: str, images: Sequence[Sequence[dict]], *,
              tex_format: int = 0, flags: int = 0,
              texture_size: Tuple[int, int] = (0, 0),
              image_size: Tuple[int, int] = (0, 0),
              free_image_format: int = -1,
              trailing: bytes = b"") -> bytes:
    """Assemble a texture.

    ``images`` is a sequence of sub-images; each sub-image is a sequence of
    mipmap dicts with keys ``width``, ``height`` and ``payload`` and optional
    ``lz4`` / ``decompressed``.
    """
    if container not in ("TEXB0001", "TEXB0002", "TEXB0003", "TEXB0004"):
        raise ValueError(container)

    first = images[0][0] if images and images[0] else {"width": 0, "height": 0}
    tex_w, tex_h = texture_size if texture_size != (0, 0) else (first["width"], first["height"])
    img_w, img_h = image_size if image_size != (0, 0) else (first["width"], first["height"])

    out = bytearray(_header(tex_format, flags, tex_w, tex_h, img_w, img_h))
    out += container.encode("ascii") + b"\x00"

    # Byte layout after the container magic mirrors the reader:
    #   TEXB0001: imageCount, mipCount, records
    #   TEXB0002: imageCount, 0, mipCount, records
    #   TEXB0003: imageCount, fif, mipCount, records
    #   TEXB0004: imageCount, fif, 0, mipCount, records
    if container == "TEXB0001":
        out += struct.pack("<I", len(images))
    elif container == "TEXB0002":
        out += struct.pack("<I", len(images))
        out += struct.pack("<i", free_image_format)
    elif container == "TEXB0003":
        out += struct.pack("<I", len(images))
        out += struct.pack("<i", free_image_format)
    else:  # TEXB0004
        out += struct.pack("<I", len(images))
        out += struct.pack("<i", free_image_format)
        out += struct.pack("<I", 0)

    for image in images:
        out += struct.pack("<I", len(image))
        for level in image:
            payload = level["payload"]
            width = level["width"]
            height = level["height"]
            lz4 = 1 if level.get("lz4") else 0
            decompressed = level.get("decompressed", 0)
            size = len(payload)

            if container == "TEXB0001":
                out += struct.pack("<3I", width, height, size)
            else:
                # w h comp decompressed size
                out += struct.pack("<5I", width, height, lz4, decompressed, size)
            out += payload

    out += trailing
    return bytes(out)


# -- pixel payload helpers ---------------------------------------------------

def solid_bgra(width: int, height: int, bgr: Tuple[int, int, int], alpha: int = 255) -> bytes:
    """A tightly packed BGRA buffer filled with one colour."""
    blue, green, red = bgr
    return bytes((blue, green, red, alpha)) * (width * height)


def solid_bgr(width: int, height: int, bgr: Tuple[int, int, int]) -> bytes:
    """A tightly packed BGR buffer filled with one colour."""
    blue, green, red = bgr
    return bytes((blue, green, red)) * (width * height)


def dxt1_solid(width: int, height: int, rgb: Tuple[int, int, int]) -> bytes:
    """A DXT1 image whose every block decodes to one colour."""
    red, green, blue = rgb
    c0 = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
    c1 = c0
    block = struct.pack("<HHI", c0, c1, 0)      # all indices point at entry 0
    blocks = ((width + 3) // 4) * ((height + 3) // 4)
    return block * blocks


def dxt5_solid(width: int, height: int, rgb: Tuple[int, int, int], alpha: int = 255) -> bytes:
    """A DXT5 image with one colour and one alpha value."""
    red, green, blue = rgb
    c0 = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
    c1 = c0
    alpha_block = bytes((alpha, alpha, 0, 0, 0, 0, 0, 0))
    colour_block = struct.pack("<HHI", c0, c1, 0)
    block = alpha_block + colour_block
    blocks = ((width + 3) // 4) * ((height + 3) // 4)
    return block * blocks


def dxt3_solid(width: int, height: int, rgb: Tuple[int, int, int], alpha_nibble: int = 0xF) -> bytes:
    """A DXT3 image with one colour and one alpha value."""
    red, green, blue = rgb
    c0 = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
    c1 = c0
    packed_alpha = int(f"{alpha_nibble:X}" * 16, 16)
    block = struct.pack("<QHHI", packed_alpha, c0, c1, 0)
    blocks = ((width + 3) // 4) * ((height + 3) // 4)
    return block * blocks


PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)

JPEG_HEAD = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
MINIMAL_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42mp41" + b"\x00\x00\x00\x08free"
