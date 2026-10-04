"""Turn parsed textures and packages into files on disk."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import ffmpeg as _ffmpeg
from .pixels import decode
from .pkg import PkgReader
from .sniff import FIF_TO_EXTENSION, sniff
from .tex import TexError, TexInfo, parse_tex

__all__ = ["ExtractResult", "extract_pkg", "extract_tex", "safe_name"]

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(name: str, *, fallback: str = "output") -> str:
    """Make ``name`` usable as a single file-system path component.

    Windows forbids a number of characters and reserving trailing dots or
    spaces, and texture names frequently come from content packs that contain
    them.  CJK characters are preserved as-is.
    """
    cleaned = _INVALID_CHARS.sub("_", name).strip().strip(".")
    cleaned = cleaned.rstrip(". ")
    if not cleaned:
        cleaned = fallback
    if os.name == "nt":
        reserved = {
            "CON", "PRN", "AUX", "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
        if cleaned.upper() in reserved:
            cleaned = "_" + cleaned
    return cleaned


@dataclass
class ExtractResult:
    """Summary of one extraction run."""

    written: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing failed."""
        return not self.failed

    def add_written(self, path: str) -> None:
        """Record a file that was written."""
        self.written.append(path)

    def add_failed(self, message: str) -> None:
        """Record a failure."""
        self.failed.append(message)

    def add_skipped(self, message: str) -> None:
        """Record something intentionally not extracted."""
        self.skipped.append(message)


def _write(path: str, data: bytes) -> int:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return len(data)


def _save_image(mode: str, payload: bytes, width: int, height: int, destination: str) -> None:
    """Write decoded pixels to ``destination``, fixing channel order."""
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise TexError(
            "writing PNG output needs Pillow:\n    pip install Pillow"
        ) from exc

    if mode == "RG":            # RG88: G is luminance, R is alpha
        image = Image.frombytes("LA", (width, height), payload).convert("RGBA")
    elif mode == "BGRA":        # ARGB8888 is stored BGRA
        image = Image.frombytes("RGBA", (width, height), payload)
        red, green, blue, alpha = image.split()
        image = Image.merge("RGBA", (blue, green, red, alpha))
    elif mode == "BGR":
        image = Image.frombytes("RGB", (width, height), payload)
        red, green, blue = image.split()
        image = Image.merge("RGB", (blue, green, red))
    else:
        image = Image.frombytes(mode, (width, height), payload)

    image.save(destination)


def extract_tex(blob: bytes, base_name: str, outdir: str, *, poster: bool = False,
                ffmpeg_path: Optional[str] = None) -> str:
    """Extract the main mipmap of a ``.tex`` blob.

    Returns a human-readable description of what was produced.  Raises
    :class:`TexError` if the texture cannot be parsed.
    """
    info = parse_tex(blob)
    main = info.main_mipmap()
    if main is None:
        raise TexError("texture has no mipmap levels")

    base = safe_name(base_name)
    payload = main.data()
    width, height = main.width, main.height

    kind, extension = sniff(payload)
    is_video = info.is_video or kind == "mp4"

    # 1) videos (usually a complete MP4 stored inside the texture)
    if is_video and kind in (None, "mp4"):
        destination = os.path.join(outdir, f"{base}.mp4")
        _write(destination, payload)
        note = "video (MP4)"
        if poster:
            ffmpeg_exe = ffmpeg_path or _ffmpeg.ensure()
            if ffmpeg_exe and _ffmpeg.extract_poster(
                ffmpeg_exe, destination, os.path.join(outdir, f"{base}-preview.png")
            ):
                note += " + preview"
        return f"{base}.mp4  {width}x{height}  {note}"

    # 2) any other complete embedded file (PNG / JPEG / WebP / DDS / GIF ...)
    if kind and extension:
        destination = os.path.join(outdir, f"{base}.{extension}")
        _write(destination, payload)
        return f"{base}.{extension}  {width}x{height}  embedded {kind.upper()}"

    # 3) raw pixels or block-compressed data
    decoded = decode(info.tex_format, width, height, payload)
    if decoded is not None:
        mode, raw = decoded
        destination = os.path.join(outdir, f"{base}.png")
        os.makedirs(outdir, exist_ok=True)
        _save_image(mode, raw, width, height, destination)
        return f"{base}.png  {width}x{height}  raw {info.tex_format_name}"

    # 4) unsupported: keep the bytes so nothing is lost
    declared = FIF_TO_EXTENSION.get(info.free_image_format)
    suffix = declared or info.tex_format_name.lower()
    destination = os.path.join(outdir, f"{base}.{suffix}.bin")
    _write(destination, payload)
    return (
        f"{base}: {info.tex_format_name} is not decodable by this tool; "
        f"dumped raw payload ({width}x{height})"
    )


def extract_pkg(path: str, outdir: str, *, assets: bool = True,
                textures_only: bool = False, poster: bool = False,
                progress=None) -> ExtractResult:
    """Extract every entry of a package into ``outdir``.

    Files keep their original directory structure.  ``.tex`` entries are
    converted, everything else is copied verbatim unless ``textures_only``.
    """
    reader = PkgReader(open(path, "rb").read())
    if not reader.is_plausible():
        raise ValueError(
            f"{path}: package index does not line up with the data "
            "(unsupported or corrupt file)"
        )

    result = ExtractResult()
    os.makedirs(outdir, exist_ok=True)

    for entry in reader.entries:
        relative = entry.name.replace("\\", "/")
        target = os.path.join(outdir, *[safe_name(part) for part in relative.split("/")])

        if entry.extension == ".tex":
            try:
                summary = extract_tex(
                    reader.payload(entry),
                    os.path.splitext(entry.basename)[0],
                    os.path.dirname(target),
                    poster=poster,
                )
            except (TexError, OSError, ValueError) as exc:
                result.add_failed(f"{entry.name}: {type(exc).__name__}: {exc}")
                continue
            result.add_written(summary)
            if progress:
                progress(summary)
            continue

        if textures_only:
            result.add_skipped(entry.name)
            continue

        if not assets and not entry.extension == ".json":
            result.add_skipped(entry.name)
            continue

        try:
            _write(target, reader.payload(entry))
        except OSError as exc:
            result.add_failed(f"{entry.name}: {exc}")
            continue
        result.add_written(entry.name)

    return result


def describe_pkg(path: str) -> Dict[str, object]:
    """Inspect a package without extracting anything."""
    reader = PkgReader(open(path, "rb").read())
    textures = []
    for entry in reader.by_extension(".tex"):
        try:
            info = parse_tex(reader.payload(entry))
        except TexError as exc:
            textures.append({"name": entry.name, "error": str(exc)})
            continue
        main = info.main_mipmap()
        kind = None
        if main is not None:
            kind = sniff(main.data())[0] or "pixels"
        textures.append({
            "name": entry.name,
            "container": info.container,
            "format": info.tex_format_name,
            "free_image_format": info.free_image_format_name,
            "flags": info.flags_name,
            "size": (main.width, main.height) if main else None,
            "payload": kind,
            "mipmaps": len(info.images[0]) if info.images else 0,
        })
    return {
        "version": reader.version,
        "entry_count": len(reader.entries),
        "plausible": reader.is_plausible(),
        "textures": textures,
    }
