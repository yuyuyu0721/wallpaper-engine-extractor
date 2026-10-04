#!/usr/bin/env python3
"""Download pure/binary wheels from PyPI into a local lib/ dir - no pip needed.

Used to vendor the few optional deps of we_extract.py (lz4, pillow) so the tool
stays self-contained.

    python fetch_dep.py lz4 pillow --dest ./lib
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import sysconfig
import urllib.request
import zipfile


def tags():
    """Candidate platform tag substrings, most specific first."""
    impl = sys.implementation.name
    ver = f"{sys.version_info[0]}{sys.version_info[1]}"
    plat = sysconfig.get_platform().replace("-", "_").replace(".", "_")
    if os.name == "nt":
        plats = ["win_amd64" if "amd64" in plat else "win32", "win_amd64", "win32", "any"]
    elif sys.platform == "darwin":
        plats = [plat, "macosx_11_0_arm64", "macosx_10_9_x86_64", "any"]
    else:
        plats = ["manylinux2014_x86_64", "manylinux1_x86_64", "linux_x86_64", "any"]
    return f"cp{ver}", plats


def pick(files, impl_tag, plats):
    """Choose the best wheel: prefer a cpXY binary for our platform."""
    def score(fn):
        if not fn.endswith(".whl"):
            return None
        parts = fn[:-4].split("-")
        py, abi, platform = parts[-3], parts[-2], parts[-1]
        if py not in (impl_tag, "py3") and not py.startswith("cp"):
            return None
        s = 0
        if py == impl_tag:
            s += 100
        elif py == "py3":
            s += 10
        else:
            return None
        for i, p in enumerate(plats):
            if p in platform:
                s += 50 - i * 5
                break
        else:
            if platform != "any":
                return None
        return s

    scored = [(score(f["filename"]), f) for f in files]
    scored = [x for x in scored if x[0] is not None]
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    return scored[0][1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("packages", nargs="+")
    ap.add_argument("--dest", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
    ap.add_argument("--list", action="store_true", help="only list candidates")
    args = ap.parse_args()

    impl_tag, plats = tags()
    print(f"target: {impl_tag}  platforms={plats}")
    os.makedirs(args.dest, exist_ok=True)

    for pkg in args.packages:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=60) as r:
            meta = json.load(r)
        ver = meta["info"]["version"]
        files = meta["releases"][ver]
        if args.list:
            print(f"\n{pkg} {ver}:")
            for f in files:
                print("  " + f["filename"])
            continue

        wheel = pick(files, impl_tag, plats)
        if wheel is None:
            print(f"{pkg} {ver}: no compatible wheel (sdist would need a compiler)")
            continue

        whl_path = os.path.join(args.dest, ".wheels", wheel["filename"])
        os.makedirs(os.path.dirname(whl_path), exist_ok=True)
        if not os.path.exists(whl_path):
            print(f"{pkg} {ver}: downloading {wheel['filename']} "
                  f"({wheel['size'] // 1024} KiB)")
            with urllib.request.urlopen(wheel["url"], timeout=300) as r, \
                    open(whl_path, "wb") as fh:
                while chunk := r.read(1 << 20):
                    fh.write(chunk)
        else:
            print(f"{pkg} {ver}: cached {wheel['filename']}")

        with zipfile.ZipFile(whl_path) as z:
            names = z.namelist()
            z.extractall(args.dest)
        top = sorted({n.split("/")[0] for n in names
                      if not n.startswith(".") and "/" in n and not n.endswith("dist-info")})
        print(f"  installed -> {args.dest}  ({', '.join(top[:6])})")

    print("\ndone.")


if __name__ == "__main__":
    main()
