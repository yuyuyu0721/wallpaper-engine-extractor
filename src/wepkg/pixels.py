"""Pixel decoding for the raw and block-compressed texture formats.

Supported:

============  ====================================================
Format        Handling
============  ====================================================
ARGB8888      stored as BGRA, swapped back to RGBA on output
RGB888        stored as BGR, swapped back to RGB
RGB565        16-bit packed, expanded to RGB
RGBa1010102   10:10:10:2 packed, expanded to RGBA
RG88          G is luminance, R is alpha
R8            single channel luminance
DXT1/3/5      S3TC block decode (a self-contained implementation)
============  ====================================================

Other formats (``BC7``, half-float variants) are reported but not decoded;
callers should fall back to dumping the raw payload rather than guessing.
"""

from __future__ import annotations

import struct
from typing import Optional, Tuple

__all__ = ["DecodedImage", "decode", "decode_dxt", "is_decodable", "rgb565_to_rgb"]

#: (PIL mode, bytes-per-pixel) for the formats we decode straight through.
RAW_FORMATS = {
    0: ("BGRA", 4),   # ARGB8888
    1: ("BGR", 3),    # RGB888
    8: ("RG", 2),     # RG88
    9: ("L", 1),      # R8
}

DXT_FORMATS = {4: "DXT5", 6: "DXT3", 7: "DXT1"}

DecodedImage = Tuple[str, bytes]


def rgb565_to_rgb(value: int) -> Tuple[int, int, int]:
    """Expand a 16-bit RGB565 value to 8-bit channels.

    Each field is scaled with rounding so that the midpoints land on the
    expected values (e.g. 5-bit 16 and 6-bit 32 both give 128).
    """
    red = (((value >> 11) & 0x1F) * 255 + 15) // 31
    green = (((value >> 5) & 0x3F) * 255 + 31) // 63
    blue = ((value & 0x1F) * 255 + 15) // 31
    return red, green, blue


def is_decodable(tex_format: int) -> bool:
    """True when :func:`decode` can handle this format."""
    return tex_format in RAW_FORMATS or tex_format in DXT_FORMATS or tex_format in (2, 13)


# -- S3TC / DXT --------------------------------------------------------------

def _dxt_color_palette(c0: int, c1: int, dxt1: bool):
    """Build the four-entry DXT colour palette for one block."""
    first = rgb565_to_rgb(c0)
    second = rgb565_to_rgb(c1)
    palette = [first, second]
    if not dxt1 or c0 > c1:
        palette.append(tuple((2 * first[i] + second[i]) // 3 for i in range(3)))
        palette.append(tuple((first[i] + 2 * second[i]) // 3 for i in range(3)))
    else:
        # DXT1 with c0 <= c1 reserves entry 3 for transparent black.
        palette.append(tuple((first[i] + second[i]) // 2 for i in range(3)))
        palette.append(None)
    return palette


def _dxt_alpha_palette(a0: int, a1: int):
    """Build the eight-entry DXT5 alpha palette."""
    palette = [a0, a1]
    if a0 > a1:
        palette += [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    else:
        palette += [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)]
        palette += [0, 255]
    return palette


def decode_dxt(data: bytes, width: int, height: int, mode: str) -> Optional[bytes]:
    """Decode a DXT1/DXT3/DXT5 image to raw RGBA bytes."""
    if mode not in ("DXT1", "DXT3", "DXT5"):
        raise ValueError(f"unknown DXT mode {mode!r}")

    block_size = 8 if mode == "DXT1" else 16
    blocks_x = (width + 3) // 4
    blocks_y = (height + 3) // 4
    needed = blocks_x * blocks_y * block_size
    if len(data) < needed:
        return None

    out = bytearray(width * height * 4)
    pos = 0
    for block_y in range(blocks_y):
        for block_x in range(blocks_x):
            block = data[pos:pos + block_size]
            pos += block_size

            if mode == "DXT1":
                c0, c1 = struct.unpack_from("<HH", block, 0)
                palette = _dxt_color_palette(c0, c1, True)
                indices = struct.unpack_from("<I", block, 4)[0]
                alphas = None
            elif mode == "DXT3":
                c0, c1 = struct.unpack_from("<HH", block, 8)
                palette = _dxt_color_palette(c0, c1, False)
                indices = struct.unpack_from("<I", block, 12)[0]
                packed_alpha = struct.unpack_from("<Q", block, 0)[0]
                alphas = [((packed_alpha >> (4 * i)) & 0xF) * 17 for i in range(16)]
            else:  # DXT5
                alpha_palette = _dxt_alpha_palette(block[0], block[1])
                alpha_code = int.from_bytes(block[2:8], "little")
                alphas = [alpha_palette[(alpha_code >> (3 * i)) & 0x7] for i in range(16)]
                c0, c1 = struct.unpack_from("<HH", block, 8)
                palette = _dxt_color_palette(c0, c1, False)
                indices = struct.unpack_from("<I", block, 12)[0]

            for pixel_y in range(4):
                y = block_y * 4 + pixel_y
                if y >= height:
                    break
                for pixel_x in range(4):
                    x = block_x * 4 + pixel_x
                    if x >= width:
                        continue
                    slot = pixel_y * 4 + pixel_x
                    colour = palette[(indices >> (2 * slot)) & 0x3]
                    offset = (y * width + x) * 4
                    if colour is None:            # DXT1 transparent texel
                        continue                  # already zeroed
                    red, green, blue = colour
                    alpha = 255 if alphas is None else alphas[slot]
                    out[offset] = red
                    out[offset + 1] = green
                    out[offset + 2] = blue
                    out[offset + 3] = alpha

    return bytes(out)


# -- entry point -------------------------------------------------------------

def decode(tex_format: int, width: int, height: int, data: bytes) -> Optional[DecodedImage]:
    """Decode ``data`` into ``(PIL mode, raw bytes)``, or ``None`` if unsupported."""
    if width <= 0 or height <= 0:
        return None

    raw = RAW_FORMATS.get(tex_format)
    if raw is not None:
        mode, bytes_per_pixel = raw
        needed = width * height * bytes_per_pixel
        if len(data) < needed:
            return None
        return mode, data[:needed]

    if tex_format in DXT_FORMATS:
        rgba = decode_dxt(data, width, height, DXT_FORMATS[tex_format])
        return ("RGBA", rgba) if rgba is not None else None

    if tex_format == 2:  # RGB565
        needed = width * height * 2
        if len(data) < needed:
            return None
        out = bytearray(width * height * 3)
        for i in range(width * height):
            red, green, blue = rgb565_to_rgb(struct.unpack_from("<H", data, i * 2)[0])
            out[i * 3:i * 3 + 3] = bytes((red, green, blue))
        return "RGB", bytes(out)

    if tex_format == 13:  # RGBa1010102
        needed = width * height * 4
        if len(data) < needed:
            return None
        out = bytearray(width * height * 4)
        for i in range(width * height):
            value = struct.unpack_from("<I", data, i * 4)[0]
            out[i * 4] = (value & 0x3FF) * 255 // 1023
            out[i * 4 + 1] = ((value >> 10) & 0x3FF) * 255 // 1023
            out[i * 4 + 2] = ((value >> 20) & 0x3FF) * 255 // 1023
            out[i * 4 + 3] = (value >> 30) * 85
        return "RGBA", bytes(out)

    return None
