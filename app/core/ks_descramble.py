# -*- coding: utf-8 -*-
"""Kirikiri script descramble (FE FE 00/01/02 FF FE).

Port of arcusmaximus/KirikiriTools KirikiriDescrambler — plaintext result can go
straight back into the game (no need to rescramble).
"""
from __future__ import annotations

import struct
import zlib
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple


def is_scrambled(data: bytes) -> bool:
    return (
        len(data) >= 5
        and data[0] == 0xFE
        and data[1] == 0xFE
        and data[3] == 0xFF
        and data[4] == 0xFE
        and data[2] in (0, 1, 2)
    )


def descramble_bytes(data: bytes) -> Optional[bytes]:
    """Return UTF-16-LE payload (no BOM) or None if not a scrambled script."""
    if not is_scrambled(data):
        return None
    mode = data[2]
    body = data[5:]
    if mode == 0:
        out = bytearray(body)
        for i in range(0, len(out) - 1, 2):
            if out[i + 1] == 0 and out[i] < 0x20:
                continue
            out[i + 1] ^= out[i] & 0xFE
            out[i] ^= 1
        return bytes(out)
    if mode == 1:
        out = bytearray(body)
        for i in range(0, len(out) - 1, 2):
            c = out[i] | (out[i + 1] << 8)
            c = ((c & 0xAAAA) >> 1) | ((c & 0x5555) << 1)
            out[i] = c & 0xFF
            out[i + 1] = (c >> 8) & 0xFF
        return bytes(out)
    if mode == 2:
        if len(body) < 18:
            return None
        # compressedLength, uncompressedLength, zlib header (2) then deflate
        _comp_len, uncomp_len = struct.unpack_from("<qq", body, 0)
        # C# DeflateStream skips zlib header via ReadInt16 then DeflateStream
        zlib_payload = body[16:]  # includes 2-byte zlib hdr + deflate
        try:
            # raw deflate after zlib header
            if len(zlib_payload) >= 2:
                raw = zlib.decompress(zlib_payload)  # zlib wrapper
            else:
                return None
        except zlib.error:
            try:
                raw = zlib.decompress(zlib_payload[2:], -zlib.MAX_WBITS)
            except zlib.error:
                return None
        if uncomp_len and len(raw) != uncomp_len:
            # still accept if close / truncated OK from stream.Read
            pass
        return raw
    return None


def descramble_file(path: Path) -> Tuple[bool, str]:
    """In-place descramble .ks/.tjs. Returns (changed, note)."""
    data = path.read_bytes()
    if not is_scrambled(data):
        return False, "plain"
    plain = descramble_bytes(data)
    if plain is None:
        return False, "failed"
    # write UTF-16-LE with BOM (normal Kirikiri text)
    path.write_bytes(b"\xff\xfe" + plain)
    mode = data[2]
    return True, f"mode{mode}"


def descramble_tree(root: Path) -> int:
    n = 0
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in (".ks", ".tjs", ".txt"):
            continue
        try:
            changed, _ = descramble_file(p)
        except Exception:
            continue
        if changed:
            n += 1
    return n
