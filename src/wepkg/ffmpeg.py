"""Locate or fetch an ``ffmpeg`` binary.

Only needed for the optional ``--poster`` feature, which grabs a still frame
from video wallpapers.  When no system ffmpeg is present a static build is
downloaded once and cached in the user's temp directory.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from typing import List, Optional

__all__ = ["available", "ensure", "run"]

CACHE_DIR = os.path.join(tempfile.gettempdir(), "wepkg-ffmpeg")
_EXE = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"


def _wheel_tag() -> str:
    """Best-guess platform tag for the imageio-ffmpeg wheels."""
    if os.name == "nt":
        return "win_amd64" if platform.machine().endswith("64") else "win32"
    if sys.platform == "darwin":
        return "macosx_11_0_arm64" if platform.machine() == "arm64" else "macosx_10_9_x86_64"
    return "manylinux2014_x86_64"


def _find_cached() -> Optional[str]:
    if not os.path.isdir(CACHE_DIR):
        return None
    for root, _dirs, files in os.walk(CACHE_DIR):
        if _EXE in files:
            return os.path.join(root, _EXE)
    return None


def available() -> Optional[str]:
    """Return a path to ffmpeg, or ``None`` when it is not usable.

    Checks ``$FFMPEG``, then ``PATH``, then the download cache.  Never touches
    the network.
    """
    override = os.environ.get("FFMPEG")
    if override and os.path.exists(override):
        return override

    found = shutil.which(_EXE) or shutil.which("ffmpeg")
    if found:
        return found

    return _find_cached()


def ensure(timeout: int = 300) -> Optional[str]:
    """Like :func:`available`, but downloads a static build when missing."""
    found = available()
    if found:
        return found

    try:
        import urllib.request

        print("  ffmpeg not found - downloading a static build (one time) ...")
        os.makedirs(CACHE_DIR, exist_ok=True)

        with urllib.request.urlopen(
            "https://pypi.org/pypi/imageio-ffmpeg/json", timeout=30
        ) as response:
            meta = json.load(response)

        version = meta["info"]["version"]
        tag = _wheel_tag()
        candidates = [
            f for f in meta["releases"][version]
            if tag in f["filename"] and f["filename"].endswith(".whl")
        ]
        if not candidates:
            print(f"  no ffmpeg wheel for platform tag {tag!r}")
            return None

        wheel = candidates[0]
        wheel_path = os.path.join(CACHE_DIR, wheel["filename"])
        if not os.path.exists(wheel_path):
            with urllib.request.urlopen(wheel["url"], timeout=timeout) as response, \
                    open(wheel_path, "wb") as handle:
                shutil.copyfileobj(response, handle)

        with zipfile.ZipFile(wheel_path) as archive:
            archive.extractall(os.path.join(CACHE_DIR, "x"))

        return _find_cached()
    except Exception as exc:  # network unavailable, blocked, disk full, ...
        print(f"  (ffmpeg unavailable: {exc})")
        return None


def run(ffmpeg: str, args: List[str]) -> int:
    """Run ffmpeg with ``args``, returning its exit status."""
    try:
        completed = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", *args],
            check=False,
        )
        return completed.returncode
    except OSError as exc:
        print(f"  (failed to run ffmpeg: {exc})")
        return 1


def extract_poster(ffmpeg: str, video: str, destination: str, at_seconds: float = 2.0,
                   width: int = 1920) -> bool:
    """Write a single frame of ``video`` to ``destination`` as PNG."""
    args = [
        "-ss", str(at_seconds),
        "-i", video,
        "-frames:v", "1",
        "-vf", f"scale={width}:-2",
        "-y", destination,
    ]
    if run(ffmpeg, args) != 0:
        # short clips may not have a frame exactly at the requested time
        args[1] = "0"
        if run(ffmpeg, args) != 0:
            return False
    return os.path.exists(destination)
