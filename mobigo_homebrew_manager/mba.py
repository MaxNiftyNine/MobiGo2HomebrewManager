"""Strict profile checks for the two verified MobiGo 2 MBA roles."""

from __future__ import annotations

from dataclasses import dataclass
import struct


MAGIC = b"bM_gbMQa"


@dataclass(frozen=True)
class MbaProfile:
    role: str
    file_size: int
    field_0c: int
    entry: int
    load_base: int
    compatibility: int


VERIFIED = {
    "SY": MbaProfile("SY", 0x174000, 0x5387A, 0x0DFC1D, 0x0C8800, 0x0F3E60),
    "G1": MbaProfile("G1", 0x214000, 0x3BC0B, 0x0E1A55, 0x0C8800, 0x0F3E5C),
}


def inspect(data: bytes) -> MbaProfile:
    if len(data) < 0x1000 or data[:8] != MAGIC or len(data) & 1:
        raise ValueError("file is not a complete, even-sized MobiGo MBA")
    declared_words, field_0c, compatibility, entry, load_base = struct.unpack_from(
        "<5I", data, 0x08
    )
    if declared_words != len(data) // 2:
        raise ValueError("MBA header word count does not match file size")
    for profile in VERIFIED.values():
        if (len(data), field_0c, entry, load_base, compatibility) == (
            profile.file_size,
            profile.field_0c,
            profile.entry,
            profile.load_base,
            profile.compatibility,
        ):
            return profile
    raise ValueError(
        "MBA launch metadata is not one of the verified SY/G1 profiles: "
        f"entry={entry:#x} load={load_base:#x} compatibility={compatibility:#x}"
    )


def require_role(data: bytes, role: str) -> MbaProfile:
    profile = inspect(data)
    wanted = role.upper()
    if profile.role != wanted:
        raise ValueError(f"{profile.role} MBA cannot be installed in the {wanted} role")
    return profile
