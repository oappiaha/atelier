"""Shared test helpers: tiny valid PNGs (no PIL) and hashing.

make_png is ported from T1's runtime-verified seed script (seed_barrel_bag.py).
"""

import hashlib
import os
import struct
import zlib

# V1 single-tenant workspace (seeded by migration 0001, TDD §2.1)
WS = "00000000-0000-0000-0000-000000000001"


def make_png(w: int = 8, h: int = 8, rgb: tuple[int, int, int] | None = None) -> bytes:
    """A small but fully valid PNG. Random color by default so each call
    produces unique bytes (unique sha256) unless rgb is pinned."""
    if rgb is None:
        rgb = tuple(os.urandom(3))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
