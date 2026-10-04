"""End-to-end tests for extraction, including the CLI."""

import json
import os
import struct

import pytest

from samples import (
    MINIMAL_MP4,
    PNG_1X1,
    build_pkg,
    build_tex,
    dxt5_solid,
    solid_bgra,
)
from wepkg.cli import main
from wepkg.convert import describe_pkg, extract_pkg, extract_tex, safe_name


def _level(width, height, payload, **extra):
    level = {"width": width, "height": height, "payload": payload}
    level.update(extra)
    return level


def _wallpaper_pkg():
    """A package shaped like a real scene: json + a still texture + a video."""
    scene = json.dumps({"camera": {}, "objects": []}).encode()
    still = build_tex(
        "TEXB0003",
        [[_level(4, 4, solid_bgra(4, 4, (0, 0, 255))), _level(2, 2, b"\x00" * 16)]],
        tex_format=0,
        free_image_format=-1,
    )
    video = build_tex(
        "TEXB0003",
        [[_level(1920, 1080, MINIMAL_MP4)]],
        tex_format=0,
        flags=0x20,
    )
    return build_pkg([
        ("scene.json", scene),
        ("materials/still.tex", still),
        ("materials/movie.tex", video),
        ("shaders/effect.frag", b"void main(){}"),
    ])


# -- safe_name ---------------------------------------------------------------

def test_safe_name_keeps_cjk_and_strips_illegal_characters():
    assert safe_name("miku 横") == "miku 横"
    assert safe_name("a/b") == "a_b"
    assert safe_name("q?*") == "q__"
    assert safe_name("") == "output"
    assert safe_name("...") == "output"
    assert safe_name("trailing. ") == "trailing"


# -- extract_tex -------------------------------------------------------------

def test_extract_embedded_png(tmp_path):
    blob = build_tex("TEXB0003", [[_level(1, 1, PNG_1X1)]], free_image_format=13)
    summary = extract_tex(blob, "pic", str(tmp_path))

    assert (tmp_path / "pic.png").exists()
    assert (tmp_path / "pic.png").read_bytes() == PNG_1X1
    assert "embedded PNG" in summary


def test_extract_video(tmp_path):
    blob = build_tex("TEXB0003", [[_level(1920, 1080, MINIMAL_MP4)]], flags=0x20)
    summary = extract_tex(blob, "movie", str(tmp_path))

    assert (tmp_path / "movie.mp4").read_bytes() == MINIMAL_MP4
    assert "video (MP4)" in summary


def test_extract_raw_pixels_writes_png(tmp_path):
    """ARGB8888 must come out with R and B swapped, not as raw BGRA."""
    blob = build_tex("TEXB0003", [[_level(2, 2, solid_bgra(2, 2, (10, 20, 30)))]])

    extract_tex(blob, "raw", str(tmp_path))

    from PIL import Image
    with Image.open(tmp_path / "raw.png") as image:
        assert image.size == (2, 2)
        assert image.convert("RGB").getpixel((0, 0)) == (30, 20, 10)


def test_extract_dxt5_writes_png(tmp_path):
    payload = dxt5_solid(4, 4, (255, 0, 0), alpha=255)
    blob = build_tex("TEXB0003", [[_level(4, 4, payload)]], tex_format=4)

    summary = extract_tex(blob, "dxt", str(tmp_path))

    assert (tmp_path / "dxt.png").exists()
    assert "DXT5" in summary
    from PIL import Image
    with Image.open(tmp_path / "dxt.png") as image:
        assert image.convert("RGB").getpixel((0, 0)) == (255, 0, 0)


def test_extract_undecodable_format_keeps_raw_bytes(tmp_path):
    blob = build_tex("TEXB0003", [[_level(4, 4, b"\xAB" * 64)]], tex_format=12)  # BC7
    summary = extract_tex(blob, "bc7", str(tmp_path))

    dumped = tmp_path / "bc7.bc7.bin"
    assert dumped.exists()
    assert dumped.read_bytes() == b"\xAB" * 64
    assert "not decodable" in summary


def test_extract_sanitises_texture_name(tmp_path):
    blob = build_tex("TEXB0003", [[_level(1, 1, solid_bgra(1, 1, (1, 1, 1)))]])

    extract_tex(blob, "bad/name?", str(tmp_path))

    assert (tmp_path / "bad_name_.png").exists()


# -- extract_pkg -------------------------------------------------------------

def test_extract_pkg_writes_all_entries(tmp_path):
    pkg = tmp_path / "scene.pkg"
    pkg.write_bytes(_wallpaper_pkg())
    outdir = tmp_path / "out"

    result = extract_pkg(str(pkg), str(outdir))

    assert result.ok
    assert (outdir / "scene.json").exists()
    assert (outdir / "materials" / "still.png").exists()
    assert (outdir / "materials" / "movie.mp4").exists()
    assert (outdir / "shaders" / "effect.frag").read_bytes() == b"void main(){}"
    assert json.loads((outdir / "scene.json").read_text())["objects"] == []


def test_extract_pkg_textures_only(tmp_path):
    pkg = tmp_path / "scene.pkg"
    pkg.write_bytes(_wallpaper_pkg())
    outdir = tmp_path / "out"

    result = extract_pkg(str(pkg), str(outdir), textures_only=True)

    assert (outdir / "materials" / "movie.mp4").exists()
    assert not (outdir / "scene.json").exists()
    assert not (outdir / "shaders" / "effect.frag").exists()
    assert any("scene.json" in item for item in result.skipped)


def test_extract_pkg_records_failures_without_aborting(tmp_path):
    good = build_tex("TEXB0003", [[_level(1, 1, solid_bgra(1, 1, (1, 1, 1)))]])
    pkg = tmp_path / "scene.pkg"
    pkg.write_bytes(build_pkg([
        ("broken.tex", b"NOTATEX" + b"\x00" * 64),
        ("good.tex", good),
    ]))
    outdir = tmp_path / "out"

    result = extract_pkg(str(pkg), str(outdir))

    assert not result.ok
    assert any("broken.tex" in message for message in result.failed)
    assert (outdir / "good.png").exists()          # extraction continued


def test_extract_pkg_rejects_mismatched_index(tmp_path):
    data = bytearray(_wallpaper_pkg())
    data[data.find(b"{")] = ord("X")               # corrupt the first payload
    pkg = tmp_path / "scene.pkg"
    pkg.write_bytes(bytes(data))

    with pytest.raises(ValueError, match="does not line up"):
        extract_pkg(str(pkg), str(tmp_path / "out"))


def test_describe_pkg_reports_textures(tmp_path):
    pkg = tmp_path / "scene.pkg"
    pkg.write_bytes(_wallpaper_pkg())

    info = describe_pkg(str(pkg))

    assert info["plausible"]
    assert info["entry_count"] == 4
    by_name = {t["name"]: t for t in info["textures"]}
    assert by_name["materials/still.tex"]["payload"] == "pixels"
    assert by_name["materials/movie.tex"]["payload"] == "mp4"
    assert by_name["materials/still.tex"]["mipmaps"] == 2


# -- CLI ---------------------------------------------------------------------

def test_cli_extracts_and_reports(tmp_path, capsys):
    pkg = tmp_path / "scene.pkg"
    pkg.write_bytes(_wallpaper_pkg())
    outdir = tmp_path / "out"

    code = main([str(pkg), "-o", str(outdir)])

    assert code == 0
    assert (outdir / "materials" / "movie.mp4").exists()
    assert "item(s) written" in capsys.readouterr().out


def test_cli_list_writes_nothing(tmp_path, capsys):
    pkg = tmp_path / "scene.pkg"
    pkg.write_bytes(_wallpaper_pkg())

    code = main([str(pkg), "--list"])
    output = capsys.readouterr().out

    assert code == 0
    assert "PKGV0021" in output
    assert "payload=mp4" in output
    assert not list(tmp_path.glob("**/*.mp4"))


def test_cli_json_report(tmp_path, capsys):
    pkg = tmp_path / "scene.pkg"
    pkg.write_bytes(_wallpaper_pkg())

    code = main([str(pkg), "-o", str(tmp_path / "out"), "--json", "--quiet"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["failed"] == []
    assert any("movie.mp4" in item for item in payload["written"])


def test_cli_derives_name_from_wallpaper_folder(tmp_path, monkeypatch):
    folder = tmp_path / "3028473503"
    folder.mkdir()
    (folder / "scene.pkg").write_bytes(_wallpaper_pkg())
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    code = main([str(folder / "scene.pkg")])

    assert code == 0
    # the default output name is the wallpaper folder id
    assert (workdir / "3028473503" / "materials" / "movie.mp4").exists()


def test_cli_missing_path_returns_two(tmp_path, capsys):
    code = main([str(tmp_path / "nope.pkg")])
    assert code == 2
    assert "no such path" in capsys.readouterr().err


def test_cli_extracts_bare_tex(tmp_path, capsys):
    tex = tmp_path / "material.tex"
    tex.write_bytes(build_tex("TEXB0003", [[_level(1, 1, PNG_1X1)]], free_image_format=13))

    code = main([str(tex), "-o", str(tmp_path / "out")])

    assert code == 0
    assert (tmp_path / "out" / "material.png").exists()


def test_cli_folder_mode_writes_each_package(tmp_path):
    root = tmp_path / "workshop"
    for wallpaper in ("1111", "2222"):
        folder = root / wallpaper
        folder.mkdir(parents=True)
        (folder / "scene.pkg").write_bytes(_wallpaper_pkg())
    outdir = tmp_path / "out"

    code = main([str(root), "-o", str(outdir), "--quiet"])

    assert code == 0
    assert (outdir / "1111" / "materials" / "movie.mp4").exists()
    assert (outdir / "2222" / "materials" / "movie.mp4").exists()


def test_cli_poster_uses_injected_ffmpeg(tmp_path, monkeypatch):
    """--poster must not require a real ffmpeg during tests."""
    calls = {}

    def fake_extract_poster(ffmpeg, video, destination, **kwargs):
        calls["video"] = video
        with open(destination, "wb") as handle:
            handle.write(PNG_1X1)
        return True

    monkeypatch.setattr("wepkg.ffmpeg.extract_poster", fake_extract_poster)
    monkeypatch.setattr("wepkg.ffmpeg.ensure", lambda *a, **k: "ffmpeg")

    pkg = tmp_path / "scene.pkg"
    pkg.write_bytes(_wallpaper_pkg())
    outdir = tmp_path / "out"

    code = main([str(pkg), "-o", str(outdir), "--poster", "--quiet"])

    assert code == 0
    assert (outdir / "materials" / "movie-preview.png").exists()
    assert calls["video"].endswith("movie.mp4")
