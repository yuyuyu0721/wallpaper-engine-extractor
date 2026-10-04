"""Command-line interface: ``wepkg`` / ``python -m wepkg``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import __version__
from .convert import describe_pkg, extract_pkg, extract_tex, safe_name
from .pkg import PkgError, read_pkg
from .tex import TexError

__all__ = ["main"]

PROG = "wepkg"

EPILOG = """\
examples:
  wepkg scene.pkg                       extract into ./<wallpaper-id>/
  wepkg scene.pkg --name miku           extract into ./miku/
  wepkg scene.pkg --list                show contents, write nothing
  wepkg scene.pkg --poster              also write a preview frame for videos
  wepkg scene.pkg --textures-only       only convert textures
  wepkg scene.pkg -o D:\\out --json       machine-readable summary
  wepkg C:\\...\\431960 --list            inspect every package in a folder
  wepkg material.tex -o .               convert a bare .tex file

Where are the packages?
  <steam>/steamapps/workshop/content/431960/<wallpaper-id>/scene.pkg
"""


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Extract images, video and other assets from "
                    "Wallpaper Engine .pkg / .tex files.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", help="a scene.pkg, a .tex file, or a folder")
    parser.add_argument("-o", "--outdir", help="output directory (default: ./<name>)")
    parser.add_argument("--name", help="output name instead of the wallpaper id")
    parser.add_argument("--list", "--scan", dest="list_only", action="store_true",
                        help="show what is inside without writing any file")
    parser.add_argument("--textures-only", action="store_true",
                        help="only convert textures, skip shaders/models/etc.")
    parser.add_argument("--poster", action="store_true",
                        help="also write a preview PNG for video wallpapers")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="print a JSON report instead of prose")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="only print errors and the final summary")
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    return parser


def _default_name(pkg_path: str) -> str:
    """Name a package: the wallpaper folder id, falling back to the file name."""
    parent = os.path.basename(os.path.dirname(os.path.abspath(pkg_path)))
    if parent and parent.isdigit():
        return parent
    if parent and parent not in ("", os.sep):
        return parent
    return os.path.splitext(os.path.basename(pkg_path))[0]


def _print_listing(path: str) -> dict:
    info = describe_pkg(path)
    print(f"{path}")
    print(f"  {info['version']}, {info['entry_count']} entries")
    if not info["plausible"]:
        print("  warning: index does not line up with the data")
    for texture in info["textures"]:
        if "error" in texture:
            print(f"  {texture['name']}: unreadable ({texture['error']})")
            continue
        width, height = texture["size"] or ("?", "?")
        print(
            f"  {texture['name']}: {width}x{height} "
            f"container={texture['container']} format={texture['format']} "
            f"fif={texture['free_image_format']} flags=[{texture['flags']}] "
            f"payload={texture['payload']} mipmaps={texture['mipmaps']}"
        )
    return info


def _collect(path: str) -> List[str]:
    """Expand a folder into the list of packages inside it."""
    targets: List[str] = []
    for root, _dirs, files in os.walk(path):
        for name in sorted(files):
            if name.lower().endswith((".pkg", ".tex")):
                targets.append(os.path.join(root, name))
    return targets


def _extract_one(path: str, args: argparse.Namespace) -> int:
    """Extract a single ``.tex`` file."""
    if args.list_only:
        with open(path, "rb") as handle:
            blob = handle.read()
        try:
            from .tex import parse_tex
            info = parse_tex(blob)
        except TexError as exc:
            print(f"{path}: {exc}")
            return 1
        main = info.main_mipmap()
        print(f"{path}")
        print(f"  {info.container} format={info.tex_format_name} "
              f"fif={info.free_image_format_name} flags=[{info.flags_name}]")
        if main:
            from .sniff import sniff
            print(f"  main mipmap {main.width}x{main.height} "
                  f"payload={sniff(main.data())[0] or 'pixels'}")
        return 0

    outdir = args.outdir or os.path.dirname(os.path.abspath(path))
    name = args.name or os.path.splitext(os.path.basename(path))[0]
    os.makedirs(outdir, exist_ok=True)
    with open(path, "rb") as handle:
        blob = handle.read()
    try:
        summary = extract_tex(blob, name, outdir, poster=args.poster)
    except (TexError, OSError, ValueError) as exc:
        print(f"failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(summary if args.quiet else f"    {summary}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point.  Returns the process exit code."""
    args = build_parser().parse_args(argv)
    target = args.path

    if not os.path.exists(target):
        print(f"{PROG}: no such path: {target}", file=sys.stderr)
        return 2

    # folder mode ---------------------------------------------------------
    if os.path.isdir(target):
        packages = _collect(target)
        if not packages:
            print(f"{PROG}: no .pkg or .tex files under {target}", file=sys.stderr)
            return 1
        exit_code = 0
        for package in packages:
            if args.list_only:
                if package.lower().endswith(".pkg"):
                    _print_listing(package)
                else:
                    _extract_one(package, args)
                print()
                continue
            outdir = os.path.join(args.outdir or ".", _default_name(package))
            exit_code |= _run_extract(package, outdir, args)
        return exit_code

    # bare .tex -----------------------------------------------------------
    if target.lower().endswith(".tex"):
        return _extract_one(target, args)

    # .pkg ----------------------------------------------------------------
    if args.list_only:
        try:
            info = _print_listing(target)
        except (PkgError, OSError) as exc:
            print(f"{PROG}: {exc}", file=sys.stderr)
            return 1
        if args.as_json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    outdir = args.outdir or os.path.join(".", safe_name(args.name or _default_name(target)))
    return _run_extract(target, outdir, args)


def _run_extract(path: str, outdir: str, args: argparse.Namespace) -> int:
    """Run one package extraction and report the outcome."""
    announce = (lambda message: None) if args.quiet else (lambda message: print(f"    {message}"))
    if not args.quiet and not args.as_json:
        print(f"Extracting {os.path.basename(path)} -> {outdir}{os.sep}")

    try:
        result = extract_pkg(
            path,
            outdir,
            textures_only=args.textures_only,
            poster=args.poster,
            progress=announce,
        )
    except (PkgError, ValueError, OSError) as exc:
        print(f"{PROG}: {path}: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps({
            "package": path,
            "outdir": outdir,
            "written": result.written,
            "failed": result.failed,
            "skipped": len(result.skipped),
        }, ensure_ascii=False, indent=2))
    else:
        if result.failed and not args.quiet:
            for message in result.failed:
                print(f"    FAILED {message}", file=sys.stderr)
        print(
            f"  {len(result.written)} item(s) written, "
            f"{len(result.failed)} failed"
            + (f", {len(result.skipped)} skipped" if result.skipped else "")
        )
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
