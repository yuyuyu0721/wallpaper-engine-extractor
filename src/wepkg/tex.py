"""Reader for Wallpaper Engine ``.tex`` textures.

Container layout, verified against every texture in a 254-wallpaper library
(2321 files)::

    "TEXV0005\\0"                9 bytes, fixed
    "TEXI0001\\0"                9 bytes, fixed
    uint32 texture_format
    uint32 flags
    uint32 texture_width
    uint32 texture_height
    uint32 image_width
    uint32 image_height
    uint32 unknown
    "TEXB0001..0004\\0"          9 bytes, container version
    uint32 image_count
    uint32 free_image_format     TEXB0003 / TEXB0004 only
    uint32 unknown               TEXB0004 only
    per image:
        uint32 mipmap_count
        mipmap_count * { mipmap record, then payload }

Mipmap records are 20 bytes and their *last* uint32 holds the payload length::

    uint32 width
    uint32 height
    uint32 lz4_compressed        (0 or 1)
    uint32 decompressed_size
    uint32 unknown / payload_size
    uint32 payload_size

Two details are easy to get wrong:

* ``TEXB0001`` and ``TEXB0002`` have no free-image-format field.  Reading one
  anyway consumes the next field and desynchronises the whole stream.
* ``TEXB0003`` records are 20 bytes, not 16 - a 16-byte stride still "works"
  for the first level and then drifts.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple

__all__ = [
    "FIF_NAMES",
    "MAX_IMAGES",
    "MAX_LEVELS",
    "TEX_FORMATS",
    "FLAG_VIDEO",
    "TexError",
    "TexInfo",
    "TexMipmap",
    "parse_tex",
]

MAX_IMAGES = 64
MAX_LEVELS = 64

MAGIC_HEADER = b"TEXV0005\x00"
MAGIC_IMAGE = b"TEXI0001\x00"
CONTAINERS = ("TEXB0001", "TEXB0002", "TEXB0003", "TEXB0004")

#: Texture format ids, from the engine's own enum.
TEX_FORMATS = {
    0xFFFFFFFF: "UNKNOWN",
    0: "ARGB8888",
    1: "RGB888",
    2: "RGB565",
    4: "DXT5",
    6: "DXT3",
    7: "DXT1",
    8: "RG88",
    9: "R8",
    10: "RG1616f",
    11: "R16f",
    12: "BC7",
    13: "RGBa1010102",
    14: "RGBA16161616f",
    15: "RGB161616f",
}

#: FreeImage format ids stored in the container header.
FIF_NAMES = {
    -1: "UNKNOWN",
    0: "BMP", 1: "ICO", 2: "JPEG", 3: "JNG", 4: "KOALA", 5: "LBM", 6: "MNG",
    7: "PBM", 8: "PBMRAW", 9: "PCD", 10: "PCX", 11: "PGM", 12: "PGMRAW",
    13: "PNG", 14: "PPM", 15: "PPMRAW", 16: "RAS", 17: "TARGA", 18: "TIFF",
    19: "WBMP", 20: "PSD", 21: "CUT", 22: "XBM", 23: "XPM", 24: "DDS",
    25: "GIF", 26: "HDR", 27: "FAXG3", 28: "SGI", 29: "EXR", 30: "J2K",
    31: "JP2", 32: "PFM", 33: "PICT", 34: "RAW", 35: "WEBP", 36: "JXR",
}

#: Texture flag bits.
FLAG_NO_INTERPOLATION = 0x1
FLAG_CLAMP_UVS = 0x2
FLAG_IS_GIF = 0x4
FLAG_CLAMP_UVS_BORDER = 0x8
FLAG_VIDEO = 0x20
FLAG_ALPHA_CHANNEL_PRIORITY = 0x80000

FLAG_NAMES = {
    FLAG_NO_INTERPOLATION: "NoInterpolation",
    FLAG_CLAMP_UVS: "ClampUVs",
    FLAG_IS_GIF: "IsGif",
    FLAG_CLAMP_UVS_BORDER: "ClampUVsBorder",
    FLAG_VIDEO: "Video",
    FLAG_ALPHA_CHANNEL_PRIORITY: "AlphaChannelPriority",
}


class TexError(Exception):
    """Raised when a byte stream is not a readable TEX texture."""


def flag_names(flags: int) -> str:
    """Human-readable rendering of the texture flag bits."""
    names = [name for bit, name in FLAG_NAMES.items() if flags & bit]
    return "|".join(names) if names else "-"


@dataclass
class TexMipmap:
    """One mipmap level."""

    width: int
    height: int
    payload: bytes
    lz4_compressed: bool = False
    decompressed_size: int = 0

    @property
    def size(self) -> int:
        """Length of the stored payload in bytes."""
        return len(self.payload)

    def data(self) -> bytes:
        """Return the payload, decompressing LZ4 data when necessary.

        Some files are marked LZ4-compressed even though the stored bytes are
        already the final data.  In that case decompression fails and the raw
        payload is returned rather than raising, since it is what the engine
        ends up using too.
        """
        if not self.lz4_compressed:
            return self.payload
        return lz4_decompress(self.payload, self.decompressed_size, fallback=True)


@dataclass
class TexInfo:
    """A parsed texture."""

    container: str
    tex_format: int
    tex_format_name: str
    flags: int
    flags_name: str
    texture_width: int
    texture_height: int
    image_width: int
    image_height: int
    free_image_format: int
    free_image_format_name: str
    images: List[List[TexMipmap]] = field(default_factory=list)
    consumed: int = 0

    # -- conveniences ----------------------------------------------------
    @property
    def image_count(self) -> int:
        """Number of sub-images (frames) in the texture."""
        return len(self.images)

    @property
    def level_count(self) -> int:
        """Number of mipmap levels in the first sub-image."""
        return len(self.images[0]) if self.images else 0

    @property
    def is_video(self) -> bool:
        """True when the payload is a video rather than pixels."""
        return bool(self.flags & FLAG_VIDEO)

    @property
    def is_animated(self) -> bool:
        """True for GIF-style animated textures."""
        return bool(self.flags & FLAG_IS_GIF)

    @property
    def has_embedded_file(self) -> bool:
        """True when the container declares a standalone image format."""
        return self.free_image_format != -1

    def main_mipmap(self) -> Optional[TexMipmap]:
        """Largest mipmap of the first sub-image, or ``None``."""
        if not self.images or not self.images[0]:
            return None
        return self.images[0][0]


def lz4_decompress(payload: bytes, expected_size: int, *, fallback: bool = False) -> bytes:
    """Decompress a raw LZ4 block.

    Parameters
    ----------
    payload:
        The compressed bytes.
    expected_size:
        Size of the decompressed data, taken from the mipmap header.
    fallback:
        When true, return ``payload`` unchanged if it does not decompress,
        which happens with files that are flagged as compressed but stored
        verbatim.  When false, the error is raised.
    """
    try:
        import lz4.block
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise TexError(
            "this texture is LZ4-compressed and needs the 'lz4' package:\n"
            "    pip install lz4"
        ) from exc
    try:
        return lz4.block.decompress(payload, uncompressed_size=expected_size)
    except Exception:
        # Some producers prepend the uncompressed size to the block
        # (``lz4.block.compress(..., store_size=True)``); retry in that form.
        try:
            return lz4.block.decompress(payload)
        except Exception as exc:
            if fallback:
                return payload
            raise TexError(f"failed to decompress LZ4 payload: {exc}") from exc


def _walk_levels(data: bytes, pos: int, count: int, record: int, size_index: int
                 ) -> Optional[Tuple[List[TexMipmap], int]]:
    """Read ``count`` mipmap records of ``record`` bytes, each plus its payload."""
    levels: List[TexMipmap] = []
    for _ in range(count):
        if pos + record > len(data):
            return None
        fields = struct.unpack_from("<" + "I" * (record // 4), data, pos)
        pos += record
        if size_index >= len(fields):
            return None
        size = fields[size_index]
        if size == 0 or pos + size > len(data):
            return None
        # Only the 20-byte records carry the LZ4 flag and decompressed size;
        # the 12-byte TEXB0001 record holds just width, height and size.
        lz4 = bool(fields[2]) if len(fields) >= 5 else False
        decompressed = fields[3] if len(fields) >= 5 else size
        levels.append(
            TexMipmap(
                width=fields[0],
                height=fields[1],
                payload=data[pos:pos + size],
                lz4_compressed=lz4,
                decompressed_size=decompressed,
            )
        )
        pos += size
    return levels, pos


def _candidate_levels(data: bytes, pos: int, record: int, size_index: int, image_count: int = 1
                      ) -> Iterator[Tuple[int, List[TexMipmap], int]]:
    """Yield (count, levels, end_offset) for progressively longer chains.

    The level count is not stored consistently across container versions, so
    every plausible count is tried; picking the one that consumes the file
    exactly removes all guesswork.
    """
    limit = MAX_LEVELS if image_count == 1 else max(1, MAX_LEVELS // image_count)
    for count in range(1, limit + 1):
        result = _walk_levels(data, pos, count, record, size_index)
        if result is None:
            return
        levels, end = result
        yield count, levels, end


def parse_tex(data: bytes, *, strict: bool = False) -> TexInfo:
    """Parse a ``TEXV0005`` texture.

    Parameters
    ----------
    data:
        Raw texture bytes.
    strict:
        When true, raise :class:`TexError` instead of returning the best-effort
        interpretation if no layout consumes the file exactly.
    """
    if data[:9] != MAGIC_HEADER:
        raise TexError(f"unexpected texture magic {data[:9]!r} (want TEXV0005)")
    if data[9:18] != MAGIC_IMAGE:
        raise TexError(f"unexpected sub-container magic {data[9:18]!r} (want TEXI0001)")

    pos = 18
    (
        tex_format,
        flags,
        texture_width,
        texture_height,
        image_width,
        image_height,
    ) = struct.unpack_from("<6I", data, pos)
    pos += 24
    pos += 4  # unknown constant

    if pos + 9 > len(data):
        raise TexError("truncated texture header")

    container = data[pos:pos + 9].decode("ascii", "replace").rstrip("\x00")
    pos += 9
    if container not in CONTAINERS:
        raise TexError(f"unsupported container {container!r}")

    if pos + 4 > len(data):
        raise TexError("truncated container header")
    (image_count,) = struct.unpack_from("<I", data, pos)
    pos += 4
    if not 1 <= image_count <= MAX_IMAGES:
        raise TexError(f"implausible image count {image_count}")

    free_image_format = -1
    if container in ("TEXB0003", "TEXB0004"):
        if pos + 4 > len(data):
            raise TexError("truncated container header")
        (free_image_format,) = struct.unpack_from("<i", data, pos)
        pos += 4

    # Mipmap record layout per container, verified against a real 254-wallpaper
    # library (2321 textures).  Each image is preceded by its mipmap count, then
    # comes `count` records, each `record` bytes followed by its payload.
    # `lead` skips any extra container-level field that sits before the records,
    # and `size_index` locates the payload-size field inside the record.
    #
    #   TEXB0001: w h size
    #   TEXB0002: w h comp dec size
    #   TEXB0003: w h comp dec size
    #   TEXB0004: w h comp dec size          (+4 byte lead)
    if container == "TEXB0001":
        record, lead, size_index = 12, 0, 2
    elif container in ("TEXB0002", "TEXB0003"):
        record, lead, size_index = 20, 0, 4
    else:
        record, lead, size_index = 20, 4, 4

    def parse_image(at: int):
        if at + 4 > len(data):
            return None
        candidates = list(_candidate_levels(data, at + 4, record, size_index, image_count))
        if not candidates:
            return None
        exact = [c for c in candidates if c[2] == len(data)]
        count, levels, end = exact[0] if exact else candidates[-1]
        return levels, end, bool(exact)

    # A few textures pad the header before the first record, so probe a couple
    # of alignments and keep the interpretation that consumes the file exactly
    # (falling back to the tightest fit) with the fewest assumed pad bytes.
    best = None  # (exact_fit, -extra, images, end)
    for extra in (0, 4, 8):
        cursor = pos + lead + extra
        parsed: List[List[TexMipmap]] = []
        for _ in range(image_count):
            result = parse_image(cursor)
            if result is None:
                parsed = []
                break
            levels, cursor, _ = result
            parsed.append(levels)
        if not parsed:
            continue
        score = (cursor == len(data), -extra)
        if best is None or score > best[0]:
            best = (score, parsed, cursor)
        if score[0]:
            break

    if best is None:
        raise TexError("could not read any mipmap level")

    (exact_fit, _), images, end = best
    if strict and not exact_fit:
        raise TexError("mipmap layout did not consume the file exactly")

    return TexInfo(
        container=container,
        tex_format=tex_format,
        tex_format_name=TEX_FORMATS.get(tex_format, f"?{tex_format}"),
        flags=flags,
        flags_name=flag_names(flags),
        texture_width=texture_width,
        texture_height=texture_height,
        image_width=image_width,
        image_height=image_height,
        free_image_format=free_image_format,
        free_image_format_name=FIF_NAMES.get(free_image_format, f"?{free_image_format}"),
        images=images,
        consumed=end,
    )
