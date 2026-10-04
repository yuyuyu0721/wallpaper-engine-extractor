#!/usr/bin/env python
"""End-to-end smoke test: build a synthetic package, extract it via the CLI.

Unlike the unit tests this exercises the real command-line entry point and the
installed package, so it catches packaging and wiring problems that unit tests
bypass.  It needs no game files.

    python tools/smoke_test.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))          # synthetic sample builders

from samples import MINIMAL_MP4, PNG_1X1, build_pkg, build_tex, dxt5_solid, solid_bgra  # noqa: E402


def level(width: int, height: int, payload: bytes) -> dict:
    return {"width": width, "height": height, "payload": payload}


def build_wallpaper() -> bytes:
    """A package shaped like a real scene: json, a still, a video, a shader."""
    # solid_bgra takes (blue, green, red), so this is a pure red image; after
    # the BGRA -> RGBA swap the decoded pixel must read back as (255, 0, 0).
    still = build_tex(
        "TEXB0003",
        [[level(4, 4, solid_bgra(4, 4, (0, 0, 255))), level(2, 2, b"\x00" * 16)]],
    )
    dxt = build_tex("TEXB0003", [[level(4, 4, dxt5_solid(4, 4, (255, 0, 0)))]],
                    tex_format=4)
    video = build_tex("TEXB0003", [[level(1920, 1080, MINIMAL_MP4)]], flags=0x20)
    embedded = build_tex("TEXB0003", [[level(1, 1, PNG_1X1)]], free_image_format=13)
    return build_pkg([
        ("scene.json", b'{"camera": {}, "objects": []}'),
        ("materials/still.tex", still),
        ("materials/dxt.tex", dxt),
        ("materials/movie.tex", video),
        ("materials/embedded.tex", embedded),
        ("shaders/effect.frag", b"void main() {}"),
    ])


def check(condition: bool, message: str) -> bool:
    print(f"  {'ok  ' if condition else 'FAIL'} {message}")
    return condition


def main() -> int:
    # Work inside the checkout rather than the system temp directory, which
    # some sandboxes and CI images restrict.  run_tests.py does the same.
    workdir = ROOT / "_smoke_tmp"
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        pkg = workdir / "3028473503"
        pkg.mkdir()
        (pkg / "scene.pkg").write_bytes(build_wallpaper())
        outdir = workdir / "out"

        print("wepkg smoke test")

        # --list must not write anything
        from wepkg.cli import main
        print("--list:")
        if main([str(pkg / "scene.pkg"), "--list"]) != 0:
            print("  FAIL --list returned non-zero")
            return 1
        if list(workdir.rglob("*.mp4")) or list(workdir.rglob("*.png")):
            print("  FAIL --list wrote files")
            return 1
        print("  ok   no files written")

        # extract
        print("extract:")
        code = main([str(pkg / "scene.pkg"), "-o", str(outdir), "--quiet"])
        if code != 0:
            print(f"  FAIL extract returned {code}")
            return 1

        results = [
            check((outdir / "scene.json").is_file(), "scene.json extracted"),
            check((outdir / "shaders" / "effect.frag").is_file(), "shader extracted"),
            check((outdir / "materials" / "still.png").is_file(), "raw texture -> png"),
            check((outdir / "materials" / "dxt.png").is_file(), "DXT5 texture -> png"),
            check((outdir / "materials" / "movie.mp4").is_file(), "video -> mp4"),
            check((outdir / "materials" / "embedded.png").is_file(), "embedded png kept"),
            check((outdir / "materials" / "movie.mp4").read_bytes().endswith(b"free"),
                  "video payload intact"),
            check((outdir / "materials" / "embedded.png").read_bytes() == PNG_1X1,
                  "embedded png byte-exact"),
        ]

        # decoded pixels must be the colour we put in (R/B swapped correctly)
        try:
            from PIL import Image
            with Image.open(outdir / "materials" / "still.png") as image:
                pixel = image.convert("RGB").getpixel((0, 0))
            results.append(check(pixel == (255, 0, 0), f"decoded pixel {pixel} == (255, 0, 0)"))
        except ImportError:
            print("  skip Pillow not available")

        print()
        if all(results):
            print(f"smoke test passed ({sum(results)} checks)")
            return 0
        print(f"smoke test FAILED ({results.count(False)} of {len(results)} failed)")
        return 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
