# -*- coding: utf-8 -*-
"""Classic SoftPal data.pac unpack (SoftPal-Tool / community layout).

40-byte directory entries starting at 0x804 (2052); first-file offset at 0x828.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional


@dataclass
class PacEntry:
    name: str
    size: int
    offset: int


def _rol8(byte: int, shift: int) -> int:
    shift %= 8
    b = byte & 0xFF
    return ((b << shift) | (b >> (8 - shift))) & 0xFF


def softpal_decrypt(data: bytes | bytearray) -> bytes:
    """SoftPal file decrypt (SoftPal-Tool pal_file_decrypt, no numpy)."""
    buf = bytearray(data)
    shift = 4
    for i in range(16, max(16, len(buf) - 4), 4):
        buf[i] = _rol8(buf[i], shift)
        dword = struct.unpack_from("<I", buf, i)[0]
        dword ^= 0x084DF873
        dword ^= 0xFF987DEE
        struct.pack_into("<I", buf, i, dword & 0xFFFFFFFF)
        shift += 1
    return bytes(buf)


def is_softpal_pac(path: Path | str) -> bool:
    path = Path(path)
    if not path.is_file() or path.stat().st_size < 2200:
        return False
    try:
        with path.open("rb") as f:
            f.seek(2088)
            first_off = struct.unpack("<I", f.read(4))[0]
            if first_off < 2052 or first_off > path.stat().st_size:
                return False
            f.seek(2052)
            chunk = f.read(40)
            if len(chunk) < 40:
                return False
            name = chunk[:32].split(b"\x00", 1)[0]
            return bool(name) and all(32 < b < 127 for b in name)
    except OSError:
        return False


def list_pac(path: Path | str) -> List[PacEntry]:
    path = Path(path)
    raw = path.read_bytes()
    if len(raw) < 2092:
        raise ValueError(f"PAC too small: {path}")
    first_off = struct.unpack_from("<I", raw, 2088)[0]
    entries: List[PacEntry] = []
    for i in range(2052, first_off, 40):
        block = raw[i : i + 40]
        if len(block) < 40:
            break
        name_b = block[:32].split(b"\x00", 1)[0]
        if not name_b:
            continue
        try:
            name = name_b.decode("ascii")
        except UnicodeDecodeError:
            continue
        size, offset = struct.unpack_from("<II", block, 32)
        if offset + size > len(raw) or size == 0:
            continue
        entries.append(PacEntry(name=name, size=size, offset=offset))
    return entries


def extract_named(
    path: Path | str,
    names: Iterable[str],
    out_dir: Path,
    *,
    decrypt: bool = True,
) -> Dict[str, Path]:
    """Extract files by name (case-insensitive). Optionally SoftPal-decrypt."""
    path = Path(path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    want = {n.upper(): n for n in names}
    found: Dict[str, Path] = {}
    raw = path.read_bytes()
    for e in list_pac(path):
        key = e.name.upper()
        if key not in want:
            continue
        data = raw[e.offset : e.offset + e.size]
        if decrypt:
            try:
                data = softpal_decrypt(data)
            except Exception:
                pass
        dest = out_dir / e.name
        # normalize common casing
        if key == "SCRIPT.SRC":
            dest = out_dir / "SCRIPT.SRC"
        elif key == "TEXT.DAT":
            dest = out_dir / "TEXT.DAT"
        elif key == "POINT.DAT":
            dest = out_dir / "POINT.DAT"
        dest.write_bytes(data)
        found[dest.name.upper()] = dest
    return found


def find_data_pac(game_dir: Path) -> Optional[Path]:
    for name in ("data.pac", "DATA.PAC", "Data.pac"):
        p = game_dir / name
        if p.is_file() and is_softpal_pac(p):
            return p
    for p in game_dir.glob("*.pac"):
        if is_softpal_pac(p):
            # prefer ones that contain SCRIPT.SRC
            try:
                names = {e.name.upper() for e in list_pac(p)}
            except Exception:
                continue
            if "SCRIPT.SRC" in names and "TEXT.DAT" in names:
                return p
    return None


def pac_has_script_pair(path: Path) -> bool:
    try:
        names = {e.name.upper() for e in list_pac(path)}
    except Exception:
        return False
    return "SCRIPT.SRC" in names and "TEXT.DAT" in names
