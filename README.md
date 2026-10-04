# wallpaper-engine-extractor

Extract images, video, and other assets from **Wallpaper Engine** `.pkg` files
and `.tex` textures.

Wallpaper Engine keeps each downloaded wallpaper in a Steam workshop folder:

```
<steam>/steamapps/workshop/content/431960/<wallpaper-id>/
├── scene.pkg        <- everything the wallpaper needs
├── project.json     <- metadata
└── preview.gif      <- thumbnail
```

This tool unpacks `scene.pkg` and converts the textures inside it into ordinary
files you can open, edit, or re-use.

## Highlights

- **Knows the real payload type.** Many wallpapers are not images at all: the
  "texture" is a complete MP4, PNG, or JPEG. The tool sniffs the payload and
  exports it correctly instead of trusting the header.
- **Decodes S3TC/DXT.** DXT1, DXT3 and DXT5 textures are converted to PNG by a
  self-contained decoder - no DirectX, no external converter.
- **Handles every container version.** `TEXB0001` through `TEXB0004`, raw
  pixel formats (ARGB8888, RGB888, RGB565, RGBa1010102, RG88, R8), and
  LZ4-compressed mipmaps.
- **Verified on real data.** Every texture in a 254-wallpaper library
  (**2321 textures**) parses and decodes successfully. See
  [Validation](#validation).
- **Zero native dependencies in the core.** Pure Python; `Pillow` writes PNGs
  and `lz4` handles compressed textures.

## Install

```bash
pip install wepkg
```

Or run straight from a checkout, with no installation:

```bash
python -m pip install Pillow lz4   # only these two are required
python -m wepkg scene.pkg --list   # with src/ on PYTHONPATH
```

## Usage

```bash
# See what is inside without writing anything (start here)
wepkg scene.pkg --list

# Extract into ./<wallpaper-id>/
wepkg scene.pkg

# Pick your own output folder
wepkg scene.pkg --name miku
wepkg scene.pkg -o ./out

# Video wallpapers: also write a preview frame (needs ffmpeg, see below)
wepkg scene.pkg --poster

# Only textures, skip shaders/models/effects
wepkg scene.pkg --textures-only

# Inspect a whole workshop folder
wepkg "C:/Program Files (x86)/Steam/steamapps/workshop/content/431960" --list

# Convert a bare .tex
wepkg materials/miku.tex -o ./out
```

| Option | Description |
| --- | --- |
| `-o, --outdir DIR` | Output directory (default `./<name>`) |
| `--name NAME` | Output name instead of the wallpaper id |
| `--list`, `--scan` | Show contents and detected formats; write nothing |
| `--textures-only` | Skip shaders, models and effects |
| `--poster` | Extra preview PNG for video wallpapers |
| `--json` | Machine-readable report |
| `-q, --quiet` | Only errors and the final summary |
| `--version` | Print the version |

## What you get

The container header is not always honest about its contents, so the payload is
inspected first and the declared format is only used as a fallback.

| Detected payload | Output |
| --- | --- |
| Embedded MP4 (video wallpaper) | `.mp4` |
| Embedded PNG / JPEG / WebP / GIF / DDS | same format, byte-for-byte |
| ARGB8888, RGB888, RGB565, RGBa1010102 | `.png` |
| RG88, R8 | `.png` |
| DXT1, DXT3, DXT5 | `.png` |
| LZ4-compressed mipmaps | decompressed first |
| Unsupported (e.g. BC7) | `.bin` with the raw payload - nothing is lost |

Scene metadata (`scene.json`, `project.json`, material and model JSON) is
exported alongside the textures.

> **Video wallpapers are common.** If a package only produces an `.mp4`, that is
> the wallpaper - it is a video, not a still image. Play it with VLC, mpv, or
> PotPlayer, or import it into Wallpaper Engine directly.

## `--poster` and ffmpeg

Turning a video into a preview frame needs ffmpeg. The tool looks at `$FFMPEG`,
then `PATH`, and if neither has it, downloads a static build once into the
system temp directory. Without ffmpeg everything else still works; only
`--poster` is skipped.

## Validation

`tools/validate_library.py` parses and decodes a whole workshop folder **in
memory** and reports anything that fails - fast, and it writes nothing:

```bash
python tools/validate_library.py "<steam>/steamapps/workshop/content/431960"
```

Current result on a real library:

```
scanning 254 packages ...
results: {'ok': 2321}
packages: 254

all textures parsed and decoded successfully.
```

Unit tests build synthetic `.pkg`/`.tex` streams, so they need no game files:

```bash
python -m pytest tests          # if pytest is available
python tools/run_tests.py       # dependency-free fallback runner
```

## Format notes

These were established by inspection and are the parts most likely to break if
the format ever changes.

**PKG**

```
int32   version-string length
char[]  version, e.g. "PKGV0021"
int32   entry count
  int32  name length / char[] name / int32 offset / int32 length
<data area>: concatenated payloads, offsets relative to its start
```

The version string has its own length prefix. Omitting it shifts every later
field by four bytes and yields plausible-looking nonsense.

**TEX**

```
"TEXV0005\0"  "TEXI0001\0"
uint32 format, flags, texture W/H, image W/H, unknown
"TEXB000x\0"
uint32 imageCount
uint32 fif        (TEXB0003/0004 only)
uint32 unknown    (TEXB0004 only)
per image: uint32 mipCount, then mipCount * (record + payload)
```

Per-container mipmap records - `lead` is any container-level field before the
records, `size_index` locates the payload length:

| Container | record | lead | size_index | record contents |
| --- | --- | --- | --- | --- |
| `TEXB0001` | 12 | 0 | 2 | `w h size` |
| `TEXB0002` | 20 | 0 | 4 | `w h comp dec size` |
| `TEXB0003` | 20 | 0 | 4 | `w h comp dec size` |
| `TEXB0004` | 20 | 4 | 4 | `w h comp dec size` |

Gotchas worth remembering:

1. `TEXB0001`/`TEXB0002` have **no** free-image-format field; reading one
   consumes the next field and desynchronises the stream.
2. The level count is not stored consistently, so the parser tries each
   plausible count and keeps the interpretation that consumes the file exactly.
3. `ARGB8888` is stored **BGRA**; R and B must be swapped on output or colours
   come out wrong.
4. `RG88` means `G = luminance`, `R = alpha`.
5. DXT colours round-trip through RGB565, so a decoded channel can differ from
   the artist's original by one quantisation step.

## Project layout

```
src/wepkg/
├── pkg.py        PKGV package index reader
├── tex.py        TEX texture reader (all container versions)
├── pixels.py     raw + DXT pixel decoding
├── sniff.py      payload type detection
├── convert.py    extraction to files
├── ffmpeg.py     optional ffmpeg discovery
└── cli.py        command-line interface
tests/            synthetic-sample tests (no game files needed)
tools/            validation and helper scripts
```

## Contributing

Issues and pull requests are welcome. If a wallpaper fails to extract, the most
useful report includes the `--list` output and the failing file name; a
`validate_library.py` failure line is ideal.

## License

[MIT](LICENSE)

## Acknowledgements

The PKG/TEX formats were originally reverse-engineered by the
[RePKG](https://github.com/notscuffed/repkg) and
[linux-wallpaperengine](https://github.com/Almamu/linux-wallpaperengine)
projects. This is an independent Python implementation with its own decoder and
a focus on payload sniffing.
