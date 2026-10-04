"""wepkg - extract assets from Wallpaper Engine ``.pkg`` / ``.tex`` files."""

from .pkg import PkgEntry, PkgError, read_pkg
from .tex import TexError, TexInfo, TexMipmap, parse_tex

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "PkgEntry",
    "PkgError",
    "read_pkg",
    "TexError",
    "TexInfo",
    "TexMipmap",
    "parse_tex",
]
