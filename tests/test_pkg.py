"""Tests for the PKGV package reader."""

import io

import pytest

from samples import build_pkg
from wepkg.pkg import PkgError, PkgReader, read_pkg


def test_reads_version_entries_and_payloads():
    entries = [
        ("scene.json", b'{"camera": 1}'),
        ("materials/a.tex", b"\x00\x01\x02\x03"),
        ("shaders/x.vert", b"void main(){}"),
    ]
    reader = PkgReader(build_pkg(entries))

    assert reader.version == "PKGV0021"
    assert [e.name for e in reader.entries] == [n for n, _ in entries]
    assert reader.is_plausible()
    for entry, (_name, payload) in zip(reader.entries, entries):
        assert reader.payload(entry) == payload


def test_entry_helpers():
    reader = PkgReader(build_pkg([("dir/sub/file.TEX", b"x")]))
    entry = reader.entries[0]

    assert entry.basename == "file.TEX"
    assert entry.extension == ".tex"          # normalised to lower case
    assert reader.by_extension(".tex") == [entry]
    assert reader.by_extension(".png") == []
    assert reader.find(lambda n: n.endswith(".TEX")) == [entry]
    assert reader.find(lambda n: n.endswith(".tex")) == []      # names keep their case


def test_version_string_length_prefix_is_honoured():
    """Regression: dropping the length prefix shifts every later field by 4."""
    data = build_pkg([("a.json", b"{}"), ("b.bin", b"xyz")])
    reader = PkgReader(data)

    assert reader.version == "PKGV0021"
    assert reader.data_start == 4 + 8 + 4 + (4 + 6 + 8) + (4 + 5 + 8)
    assert data[reader.data_start:reader.data_start + 2] == b"{}"


def test_offsets_are_relative_to_the_data_area():
    reader = PkgReader(build_pkg([("a", b"AAAA"), ("b", b"BBBBBB")]))
    first, second = reader.entries

    assert (first.offset, first.length) == (0, 4)
    assert (second.offset, second.length) == (4, 6)
    assert reader.payload(second) == b"BBBBBB"


def test_out_of_range_entry_is_rejected():
    data = bytearray(build_pkg([("a", b"1234")]))
    # corrupt the length so the payload no longer fits
    data[-8:-4] = (9999).to_bytes(4, "little", signed=True)
    reader = PkgReader(bytes(data))

    with pytest.raises(PkgError):
        reader.payload(reader.entries[0])


@pytest.mark.parametrize("blob", [b"", b"\x00\x01", b"NOTAPKG" + b"\x00" * 32])
def test_rejects_non_packages(blob):
    with pytest.raises(PkgError):
        PkgReader(blob)


def test_rejects_wrong_magic():
    data = build_pkg([("a", b"b")], version="PKGV0021").replace(b"PKGV", b"XXXX", 1)
    with pytest.raises(PkgError, match="not a PKGV package"):
        PkgReader(data)


def test_read_from_path_and_file_object(tmp_path):
    blob = build_pkg([("x.json", b"{}")])
    path = tmp_path / "scene.pkg"
    path.write_bytes(blob)

    from_path = read_pkg(str(path))
    from_file = read_pkg(io.BytesIO(blob))

    assert from_path.payload(from_path.entries[0]) == b"{}"
    assert from_file.payload(from_file.entries[0]) == b"{}"


def test_empty_package_is_plausible():
    reader = PkgReader(build_pkg([]))
    assert reader.entries == []
    assert reader.is_plausible()
