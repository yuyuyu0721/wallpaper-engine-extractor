"""Detect the real type of a texture payload.

Texture headers are not always trustworthy: numerous wallpapers store a
complete MP4, PNG or JPEG inside the container while declaring an unrelated
pixel format.  Sniffing the payload magic is far more reliable, so callers
should prefer it over the declared format whenever it matches.
"""

from __future__ import annotations

from typing import Optional, Tuple

__all__ = ["PayloadKind", "sniff", "sniff_extension"]

PayloadKind = str

# (magic, offset_of_magic, kind, extension)
_MAGIC_TABLE = (
    (b"\x89PNG\r\n\x1a\n", 0, "png", "png"),
    (b"\xff\xd8\xff", 0, "jpeg", "jpg"),
    (b"DDS ", 0, "dds", "dds"),
    (b"GIF87a", 0, "gif", "gif"),
    (b"GIF89a", 0, "gif", "gif"),
    (b"BM", 0, "bmp", "bmp"),
    (b"II*\x00", 0, "tiff", "tif"),
    (b"MM\x00*", 0, "tiff", "tif"),
    (b"8BPS", 0, "psd", "psd"),
    (b"ftyp", 4, "mp4", "mp4"),        # ISO base media file format
    (b"WEBP", 8, "webp", "webp"),      # RIFF....WEBP, checked with RIFF
)

#: FreeImage ids that describe a complete embedded file.
FIF_TO_EXTENSION = {
    0: "bmp", 1: "ico", 2: "jpg", 13: "png", 17: "tga", 18: "tif",
    20: "psd", 24: "dds", 25: "gif", 26: "hdr", 35: "webp",
}


def sniff(data: bytes) -> Tuple[Optional[PayloadKind], Optional[str]]:
    """Return ``(kind, extension)`` for a payload, or ``(None, None)``.

    The payload is treated as a complete file when its leading bytes match a
    known container signature.
    """
    if len(data) < 12:
        return None, None

    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "webp"

    for magic, offset, kind, extension in _MAGIC_TABLE:
        end = offset + len(magic)
        if data[offset:end] == magic:
            # "ftyp" is only meaningful inside a well-formed ISO box.
            if kind == "mp4" and data[4:8] != b"ftyp":
                continue
            return kind, extension

    return None, None


def sniff_extension(data: bytes) -> Optional[str]:
    """Convenience wrapper returning just the extension."""
    return sniff(data)[1]
