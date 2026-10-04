# Contributing

Thanks for taking a look. Bug reports are especially valuable here, because the
formats are only as well understood as the files people have tested.

## Reporting a wallpaper that fails

Please include:

1. The output of `wepkg <path-to-scene.pkg> --list`.
2. The exact error text, or the file that came out wrong.
3. Ideally the output of
   `python tools/validate_library.py "<your workshop folder>"`, which lists
   failing texture names and containers.

Please **do not** attach the wallpaper itself unless you have the right to
redistribute it. A hex dump of the first 128 bytes of a failing `.tex` is
usually enough, and `--list` output plus the container version often pinpoints
the problem on its own.

## Development setup

```bash
git clone https://github.com/yuyuyu0721/wallpaper-engine-extractor
cd wallpaper-engine-extractor

python -m pip install Pillow lz4 pytest
python -m pytest tests -q          # synthetic samples, no game files needed
```

Two helper scripts are included for environments without pytest or without a
usable system temp directory:

```bash
python tools/run_tests.py                    # dependency-free test runner
python tools/fetch_dep.py pytest --dest tools/lib   # vendor wheels locally
```

## Guidelines

- Keep the core dependency-light. `Pillow` (PNG output) and `lz4` (compressed
  textures) are the only runtime requirements; new hard dependencies need a
  good reason.
- Prefer sniffing the payload over trusting a header field. Real wallpapers
  declare the wrong format often enough that magic-byte checks have repeatedly
  been the correct call.
- Add a test for every bug you fix. `tests/samples.py` can build any container
  version and pixel format, so regression tests do not need real data.
- Run `python tools/validate_library.py <workshop folder>` before opening a PR
  if you have wallpapers available - it catches layout regressions fast.

## Code style

Plain Python with type hints and docstrings on public functions. No formatter
is enforced, but please match the surrounding style: 4-space indents, about 90
columns, and comments that explain *why* rather than restating the code.
