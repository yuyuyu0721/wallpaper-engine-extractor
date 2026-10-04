# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `tools/smoke_test.py`, an end-to-end check that drives the real CLI over a
  synthetic package and asserts on the files it produces.

### Fixed

- CI installed only the test dependencies, so the CLI smoke step failed with
  `No module named wepkg` on every runner. The workflow now installs the
  package itself, which also brings the console script and packaging metadata
  under test.
- `pytest` now keeps its temporary directories inside the checkout
  (`basetemp`), so the suite runs where the system temp directory is restricted.

## [0.1.0]

### Added

- PKGV package reader (`.pkg` index parsing and payload extraction).
- TEX reader supporting `TEXB0001`, `TEXB0002`, `TEXB0003` and `TEXB0004`,
  with automatic detection of the mipmap record layout.
- Payload sniffing: embedded MP4, PNG, JPEG, WebP, GIF, DDS, BMP, TIFF and PSD
  are exported as-is instead of being run through a pixel decoder.
- Pixel decoding for ARGB8888, RGB888, RGB565, RGBa1010102, RG88 and R8.
- Self-contained S3TC decoder for DXT1, DXT3 and DXT5.
- LZ4 mipmap decompression, including a fallback for blocks that carry a size
  header or that are flagged compressed but stored verbatim.
- `wepkg` command-line interface with listing, selective extraction and JSON
  reporting, plus optional ffmpeg-based video preview frames.
- Test suite built on synthetic `.pkg`/`.tex` samples, so no game files are
  required, and a dependency-free fallback runner.
- `tools/validate_library.py` for validating the reader against a real
  workshop library.
- Simplified Chinese README (`README.zh-CN.md`), cross-linked with the English
  one.

[Unreleased]: https://github.com/yuyuyu0721/wallpaper-engine-extractor/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/yuyuyu0721/wallpaper-engine-extractor/releases/tag/v0.1.0
