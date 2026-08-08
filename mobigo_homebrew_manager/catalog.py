"""Binary INDEX.HB catalog shared with HomebrewLauncher.MBA."""

from __future__ import annotations

from dataclasses import dataclass
import struct


MAGIC = b"HB01"
PATH_BYTES = 42
LABEL_BYTES = 20
ENTRY_SIZE = 64
MAX_ENTRIES = 16


@dataclass(frozen=True)
class CatalogEntry:
    path: str
    label: str
    flags: int = 0


def _field(value: str, size: int, name: str) -> bytes:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError(f"{name} must be ASCII") from error
    if not encoded or len(encoded) >= size:
        raise ValueError(f"{name} must be 1 to {size - 1} ASCII bytes")
    return encoded.ljust(size, b"\0")


def encode(entries: list[CatalogEntry]) -> bytes:
    if len(entries) > MAX_ENTRIES:
        raise ValueError(f"the launcher supports at most {MAX_ENTRIES} apps")
    output = bytearray(MAGIC + struct.pack("<HH", len(entries), ENTRY_SIZE))
    for item in entries:
        output += _field(item.path, PATH_BYTES, "path")
        output += _field(item.label, LABEL_BYTES, "label")
        output += struct.pack("<H", item.flags & 0xFFFF)
    return bytes(output)


def decode(data: bytes) -> list[CatalogEntry]:
    if len(data) < 8 or data[:4] != MAGIC:
        raise ValueError("not an INDEX.HB catalog")
    count, stride = struct.unpack_from("<HH", data, 4)
    if count > MAX_ENTRIES or stride != ENTRY_SIZE:
        raise ValueError("unsupported INDEX.HB layout")
    if len(data) < 8 + count * ENTRY_SIZE:
        raise ValueError("truncated INDEX.HB catalog")
    result: list[CatalogEntry] = []
    for number in range(count):
        offset = 8 + number * ENTRY_SIZE
        path = data[offset : offset + PATH_BYTES].split(b"\0", 1)[0]
        label = data[
            offset + PATH_BYTES : offset + PATH_BYTES + LABEL_BYTES
        ].split(b"\0", 1)[0]
        flags = struct.unpack_from("<H", data, offset + 62)[0]
        result.append(CatalogEntry(path.decode("ascii"), label.decode("ascii"), flags))
    return result
