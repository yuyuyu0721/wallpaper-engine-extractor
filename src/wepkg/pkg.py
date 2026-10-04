"""Reader for the ``PKGV`` package format used by Wallpaper Engine.

Layout (all integers little-endian)::

    int32   length of the version string
    char[]  version string, e.g. "PKGV0021"
    int32   entry count
    entry[count]:
        int32   length of the entry name
        char[]  entry name (UTF-8, usually a path such as "materials/x.tex")
        int32   offset of the payload, relative to the start of the data area
        int32   payload length
    <data area>: concatenated entry payloads

The version string is preceded by its own length prefix.  Forgetting that
prefix shifts every following field by four bytes, which produces plausible
but wrong values (this is the classic way to mis-parse this format).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import List, Tuple

__all__ = ["PkgEntry", "PkgReader", "PkgError", "read_pkg"]

MAX_ENTRY_COUNT = 100_000
MAX_NAME_LENGTH = 4096


class PkgError(Exception):
    """Raised when a byte stream is not a readable PKGV package."""


@dataclass(frozen=True)
class PkgEntry:
    """One named entry inside a package."""

    name: str
    offset: int
    length: int

    @property
    def basename(self) -> str:
        """Name with any directory part removed."""
        return self.name.replace("\\", "/").rsplit("/", 1)[-1]

    @property
    def extension(self) -> str:
        """Lower-case extension including the dot, or an empty string."""
        base = self.basename
        return ("." + base.rsplit(".", 1)[1].lower()) if "." in base else ""


class PkgReader:
    """Parses a PKGV index and hands out entry payloads."""

    def __init__(self, data: bytes):
        self._data = data
        self.version, self.entries, self.data_start = self._read_index(data)

    # -- parsing ---------------------------------------------------------
    @staticmethod
    def _read_index(data: bytes) -> Tuple[str, List[PkgEntry], int]:
        if len(data) < 16:
            raise PkgError("file is too small to be a package")

        pos = 0
        (name_len,) = struct.unpack_from("<i", data, pos)
        pos += 4
        if not 0 < name_len <= 32:
            raise PkgError(f"implausible version-string length {name_len}")

        version = data[pos:pos + name_len].decode("ascii", "replace")
        pos += name_len
        if not version.startswith("PKGV"):
            raise PkgError(f"not a PKGV package (magic {version!r})")

        (count,) = struct.unpack_from("<i", data, pos)
        pos += 4
        if not 0 <= count <= MAX_ENTRY_COUNT:
            raise PkgError(f"implausible entry count {count}")

        entries: List[PkgEntry] = []
        for index in range(count):
            if pos + 4 > len(data):
                raise PkgError(f"truncated index at entry {index}")
            (entry_name_len,) = struct.unpack_from("<i", data, pos)
            pos += 4
            if not 0 <= entry_name_len <= MAX_NAME_LENGTH:
                raise PkgError(f"bad name length {entry_name_len} at entry {index}")
            if pos + entry_name_len + 8 > len(data):
                raise PkgError(f"truncated entry name at entry {index}")

            name = data[pos:pos + entry_name_len].decode("utf-8", "replace")
            pos += entry_name_len
            offset, length = struct.unpack_from("<ii", data, pos)
            pos += 8

            if offset < 0 or length < 0:
                raise PkgError(f"negative offset/length for entry {name!r}")
            entries.append(PkgEntry(name, offset, length))

        return version, entries, pos

    # -- access ----------------------------------------------------------
    def payload(self, entry: PkgEntry) -> bytes:
        """Return the raw bytes of ``entry``."""
        start = self.data_start + entry.offset
        end = start + entry.length
        if start < self.data_start or end > len(self._data):
            raise PkgError(f"entry {entry.name!r} points outside the file")
        return self._data[start:end]

    def find(self, predicate) -> List[PkgEntry]:
        """Return entries for which ``predicate(name)`` is true."""
        return [e for e in self.entries if predicate(e.name)]

    def by_extension(self, extension: str) -> List[PkgEntry]:
        """Return entries with the given extension, e.g. ``".tex"``."""
        want = extension.lower()
        return [e for e in self.entries if e.extension == want]

    def is_plausible(self) -> bool:
        """Cheap sanity check that the index really lines up with the data.

        The first entry normally starts at offset 0 and is a JSON file, which
        must therefore begin with ``{`` (or ``[``).
        """
        if not self.entries:
            return True
        first = self.entries[0]
        if first.offset != 0 or self.data_start >= len(self._data):
            return False
        head = self._data[self.data_start:self.data_start + 1]
        if first.extension == ".json":
            return head in (b"{", b"[")
        return True


def read_pkg(source) -> PkgReader:
    """Read a package from a path or a file-like object.

    Parameters
    ----------
    source:
        A filesystem path (``str`` / ``os.PathLike``) or an open binary
        file object.
    """
    if hasattr(source, "read"):
        data = source.read()
    else:
        with open(source, "rb") as handle:
            data = handle.read()
    if isinstance(data, str):
        raise PkgError("package data must be opened in binary mode")
    return PkgReader(data)
