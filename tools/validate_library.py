#!/usr/bin/env python
"""Validate the reader against a real Wallpaper Engine library.

Walks a workshop folder, parses every ``.pkg``, and decodes every ``.tex`` in
memory (nothing is written) so format regressions are caught immediately.

    python tools/validate_library.py "<steam>/steamapps/workshop/content/431960"
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from wepkg.pixels import decode                                        # noqa: E402
from wepkg.pkg import PkgError, PkgReader                              # noqa: E402
from wepkg.sniff import sniff                                          # noqa: E402
from wepkg.tex import TexError, parse_tex                              # noqa: E402


def check_tex(blob: bytes):
    """Return (status, detail) for one texture; status is ok/warn/fail."""
    try:
        info = parse_tex(blob)
    except TexError as exc:
        return "fail", f"{info_container(blob)}: {exc}"

    main = info.main_mipmap()
    if main is None:
        return "fail", f"{info.container}: no mipmaps"

    try:
        payload = main.data()
    except TexError as exc:
        return "fail", f"{info.container}: lz4: {exc}"

    kind, _extension = sniff(payload)
    if kind:
        note = "" if info.consumed >= len(blob) else f" (+{len(blob) - info.consumed}B tail)"
        return "ok", f"embedded {kind}{note}"

    decoded = decode(info.tex_format, main.width, main.height, payload)
    if decoded is None:
        return "fail", f"{info.container}: undecodable {info.tex_format_name}"

    mode, raw = decoded
    per_pixel = {"L": 1, "LA": 2, "RGB": 3, "RGBA": 4}.get(mode, 0)
    expected = main.width * main.height * per_pixel
    if per_pixel and len(raw) < expected:
        return "fail", f"{info.container}: short {mode} payload"
    note = "" if info.consumed >= len(blob) else f" (+{len(blob) - info.consumed}B tail)"
    return "ok", f"{info.tex_format_name} -> {mode}{note}"


def info_container(blob: bytes) -> str:
    return blob[46:55].decode("ascii", "replace").rstrip("\x00") if len(blob) > 55 else "?"


def check_pkg(path: str):
    """Return a list of (status, detail) for every texture in one package."""
    try:
        reader = PkgReader(open(path, "rb").read())
    except (PkgError, OSError) as exc:
        return [("fail", f"{type(exc).__name__}: {exc}")]

    results = []
    for entry in reader.by_extension(".tex"):
        try:
            status, detail = check_tex(reader.payload(entry))
        except Exception as exc:                      # never let one tex abort the run
            status, detail = "fail", f"{type(exc).__name__}: {exc}"
        results.append((status, f"{entry.basename}: {detail}"))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="folders containing scene.pkg files")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--show", type=int, default=15, help="how many failures to print")
    args = parser.parse_args()

    packages = []
    for base, _dirs, files in os.walk(args.root):
        for name in files:
            if name.lower() == "scene.pkg":
                packages.append(os.path.join(base, name))
    if not packages:
        print(f"no scene.pkg found under {args.root}")
        return 1

    print(f"scanning {len(packages)} packages ...")
    stats = Counter()
    failures = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for path, results in zip(packages, pool.map(check_pkg, packages)):
            for status, detail in results:
                stats[status] += 1
                if status == "fail":
                    failures.append((path, detail))

    print(f"results: {dict(stats)}")
    print(f"packages: {len(packages)}")
    if failures:
        print(f"\n{len(failures)} FAILURES:")
        for path, detail in failures[:args.show]:
            print(f"  {os.path.basename(os.path.dirname(path))}: {detail}")
        return 1

    print("\nall textures parsed and decoded successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
