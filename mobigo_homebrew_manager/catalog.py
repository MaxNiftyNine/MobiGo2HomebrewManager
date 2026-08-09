"""Binary INDEX.HB catalog shared with HomebrewLauncher.MBA."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import struct


MAGIC = b"HB02"
LEGACY_MAGIC = b"HB01"
PATH_BYTES = 42
TITLE_BYTES = 18
DESCRIPTION_BYTES = 22
AUTHOR_BYTES = 10
ENTRY_SIZE = 96
LEGACY_ENTRY_SIZE = 64
MAX_ENTRIES = 16
ICON_NAMES = ("default", "game", "puzzle", "media", "tool", "system")
ICON_IDS = {name: index for index, name in enumerate(ICON_NAMES)}


@dataclass(frozen=True)
class CatalogEntry:
    path: str
    title: str
    description: str = ""
    author: str = ""
    icon: int = 0
    flags: int = 0

    @property
    def label(self) -> str:
        return self.title


def icon_id(value: str | int) -> int:
    if isinstance(value, int):
        result = value
    else:
        try:
            result = ICON_IDS[value.strip().lower()]
        except KeyError as error:
            raise ValueError(f"icon must be one of: {', '.join(ICON_NAMES)}") from error
    if result < 0 or result >= len(ICON_NAMES):
        raise ValueError(f"icon id must be in range 0..{len(ICON_NAMES) - 1}")
    return result


def _field(value: str, size: int, name: str, *, required: bool) -> bytes:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError(f"{name} must be ASCII") from error
    if (required and not encoded) or len(encoded) >= size:
        raise ValueError(f"{name} must be at most {size - 1} ASCII bytes")
    return encoded.ljust(size, b"\0")


def encode(entries: list[CatalogEntry]) -> bytes:
    if len(entries) > MAX_ENTRIES:
        raise ValueError(f"the launcher supports at most {MAX_ENTRIES} apps")
    output = bytearray(MAGIC + struct.pack("<HH", len(entries), ENTRY_SIZE))
    for item in entries:
        output += _field(item.path, PATH_BYTES, "path", required=True)
        output += _field(item.title, TITLE_BYTES, "title", required=True)
        output += _field(
            item.description, DESCRIPTION_BYTES, "description", required=False
        )
        output += _field(item.author, AUTHOR_BYTES, "author", required=False)
        output += struct.pack("<HH", icon_id(item.icon), item.flags & 0xFFFF)
    return bytes(output)


def _text(value: bytes) -> str:
    return value.split(b"\0", 1)[0].decode("ascii")


def decode(data: bytes) -> list[CatalogEntry]:
    if len(data) < 8 or data[:4] not in (MAGIC, LEGACY_MAGIC):
        raise ValueError("not an INDEX.HB catalog")
    count, stride = struct.unpack_from("<HH", data, 4)
    legacy = data[:4] == LEGACY_MAGIC
    expected_stride = LEGACY_ENTRY_SIZE if legacy else ENTRY_SIZE
    if count > MAX_ENTRIES or stride != expected_stride:
        raise ValueError("unsupported INDEX.HB layout")
    if len(data) < 8 + count * stride:
        raise ValueError("truncated INDEX.HB catalog")
    result: list[CatalogEntry] = []
    for number in range(count):
        offset = 8 + number * stride
        path = _text(data[offset : offset + PATH_BYTES])
        if legacy:
            title = _text(data[offset + PATH_BYTES : offset + 62])
            flags = struct.unpack_from("<H", data, offset + 62)[0]
            result.append(CatalogEntry(path, title, flags=flags))
            continue
        title_at = offset + PATH_BYTES
        description_at = title_at + TITLE_BYTES
        author_at = description_at + DESCRIPTION_BYTES
        icon, flags = struct.unpack_from("<HH", data, author_at + AUTHOR_BYTES)
        result.append(
            CatalogEntry(
                path,
                _text(data[title_at : title_at + TITLE_BYTES]),
                _text(data[description_at : description_at + DESCRIPTION_BYTES]),
                _text(data[author_at : author_at + AUTHOR_BYTES]),
                icon_id(icon),
                flags,
            )
        )
    return result


def load_hbi(path: Path, *, fallback_title: str) -> CatalogEntry:
    """Load the Starter-generated host companion used during an MBA upload."""
    if not path.is_file():
        return CatalogEntry("unused", fallback_title)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read {path.name}: {error}") from error
    if not isinstance(raw, dict) or raw.get("schema") != 1:
        raise ValueError(f"{path.name} is not a supported HBI metadata file")
    return CatalogEntry(
        "unused",
        str(raw.get("title", fallback_title)),
        str(raw.get("description", "")),
        str(raw.get("author", "")),
        icon_id(str(raw.get("icon", "default"))),
    )
