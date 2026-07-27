# -*- coding: utf-8 -*-
"""LC-ScriptEngine package (lcsebody + .lst) unpack / patch.

Format adapted from cqjjjzr/LCSELocalizationTools (Kotlin) and
Inori/FuckGalEngine lcse-unpack (XOR keys).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ENTRY_SIZE = 76
FILENAME_SIZE = 0x40

TYPE_EXT = {
    1: ".snx",
    2: ".bmp",
    3: ".png",
    4: ".wav",
    5: ".ogg",
}
EXT_TYPE = {v: k for k, v in TYPE_EXT.items()}


def _key32(byte: int) -> int:
    b = byte & 0xFF
    return b | (b << 8) | (b << 16) | (b << 24)


def _xor_bytes(data: bytes, key_byte: int) -> bytes:
    k = key_byte & 0xFF
    return bytes(b ^ k for b in data)


@dataclass
class LcseEntry:
    offset: int
    length: int
    filename: str  # without extension
    type_id: int

    @property
    def full_name(self) -> str:
        return self.filename + TYPE_EXT.get(self.type_id, ".dat")


def detect_lst_key(lst_data: bytes) -> Optional[int]:
    """Return XOR key byte for .lst when count matches file size."""
    if len(lst_data) < 4 + ENTRY_SIZE:
        return None
    count_enc = struct.unpack_from("<I", lst_data, 0)[0]
    candidates: List[int] = []
    # C++ style: replicate high byte of encrypted first offset
    off_enc = struct.unpack_from("<I", lst_data, 4)[0]
    candidates.append((off_enc >> 24) & 0xFF)
    candidates.extend(range(256))
    seen = set()
    for kb in candidates:
        if kb in seen:
            continue
        seen.add(kb)
        count = count_enc ^ _key32(kb)
        if count <= 0 or count > 200_000:
            continue
        if 4 + count * ENTRY_SIZE == len(lst_data):
            return kb
    return None


def read_index(lst_data: bytes, key_byte: int) -> List[LcseEntry]:
    k32 = _key32(key_byte)
    count = struct.unpack_from("<I", lst_data, 0)[0] ^ k32
    entries: List[LcseEntry] = []
    pos = 4
    for _ in range(count):
        chunk = lst_data[pos : pos + ENTRY_SIZE]
        pos += ENTRY_SIZE
        offset, length = struct.unpack_from("<II", chunk, 0)
        offset ^= k32
        length ^= k32
        name_raw = chunk[8 : 8 + FILENAME_SIZE]
        name_bytes = bytearray()
        for b in name_raw:
            if b == 0:
                break
            name_bytes.append(b ^ (key_byte & 0xFF))
        filename = bytes(name_bytes).decode("cp932", errors="replace")
        type_id = struct.unpack_from("<i", chunk, 8 + FILENAME_SIZE)[0]
        entries.append(LcseEntry(offset, length, filename, type_id))
    return entries


def write_index(entries: List[LcseEntry], key_byte: int) -> bytes:
    k32 = _key32(key_byte)
    out = bytearray()
    out += struct.pack("<I", len(entries) ^ k32)
    for e in entries:
        out += struct.pack("<I", e.offset ^ k32)
        out += struct.pack("<I", e.length ^ k32)
        name = e.filename.encode("cp932", errors="replace")
        name = bytes(b ^ (key_byte & 0xFF) for b in name)
        name = name[: FILENAME_SIZE - 1].ljust(FILENAME_SIZE, b"\x00")
        out += name
        out += struct.pack("<i", e.type_id)
    return bytes(out)


def find_package_pair(game_dir: Path) -> Tuple[Path, Path]:
    """Return (package, list) for primary lcsebody* script pack."""
    game_dir = Path(game_dir)
    bodies = sorted(
        p
        for p in game_dir.glob("lcsebody*")
        if p.is_file() and not p.name.lower().endswith(".lst")
    )
    if not bodies:
        raise FileNotFoundError(f"未找到 lcsebody* 封包: {game_dir}")
    # prefer lcsebody1
    pkg = next((p for p in bodies if p.name.lower() == "lcsebody1"), bodies[0])
    lst = Path(str(pkg) + ".lst")
    if not lst.is_file():
        raise FileNotFoundError(f"未找到清单: {lst}")
    return pkg, lst


def unpack_scripts(
    package_path: Path,
    list_path: Path,
    out_dir: Path,
    *,
    key_byte: Optional[int] = None,
    snx_key: Optional[int] = None,
    only_snx: bool = True,
) -> Tuple[int, int, int]:
    """Unpack resources. Returns (lst_key, snx_key, count)."""
    lst_data = list_path.read_bytes()
    if key_byte is None:
        key_byte = detect_lst_key(lst_data)
    if key_byte is None:
        raise ValueError("无法自动探测 .lst XOR 密钥，请手动指定")
    if snx_key is None:
        snx_key = (key_byte + 1) & 0xFF

    entries = read_index(lst_data, key_byte)
    out_dir.mkdir(parents=True, exist_ok=True)
    pkg = package_path.read_bytes()
    n = 0
    for e in entries:
        if only_snx and e.type_id != 1:
            continue
        raw = pkg[e.offset : e.offset + e.length]
        if len(raw) != e.length:
            raise ValueError(f"读取越界: {e.full_name}")
        if e.type_id == 1:
            raw = _xor_bytes(raw, snx_key)
        (out_dir / e.full_name).write_bytes(raw)
        n += 1
    return key_byte, snx_key, n


def patch_package(
    package_path: Path,
    list_path: Path,
    patch_dir: Path,
    out_package: Path,
    out_list: Path,
    *,
    key_byte: int,
    snx_key: int,
    snx_only: bool = True,
) -> int:
    """Rebuild package replacing files found in patch_dir (by basename)."""
    lst_data = list_path.read_bytes()
    entries = read_index(lst_data, key_byte)
    patches: Dict[str, Path] = {}
    for p in Path(patch_dir).iterdir():
        if p.is_file():
            patches[p.name.lower()] = p

    pkg = package_path.read_bytes()
    new_entries: List[LcseEntry] = []
    chunks: List[bytes] = []
    offset = 0
    replaced = 0

    for e in entries:
        patch = patches.get(e.full_name.lower())
        use_patch = patch is not None and (not snx_only or e.type_id == 1)
        if use_patch:
            data = patch.read_bytes()
            if e.type_id == 1:
                data = _xor_bytes(data, snx_key)
            replaced += 1
        else:
            data = pkg[e.offset : e.offset + e.length]
        chunks.append(data)
        new_entries.append(LcseEntry(offset, len(data), e.filename, e.type_id))
        offset += len(data)

    out_package.parent.mkdir(parents=True, exist_ok=True)
    out_package.write_bytes(b"".join(chunks))
    out_list.write_bytes(write_index(new_entries, key_byte))
    return replaced


def resolve_keys(
    list_path: Path, package_path: Path
) -> Tuple[int, int]:
    """Detect lst key; verify snx key by trying parse of first SNX header sizes."""
    from app.core.lcse_snx import try_parse_snx

    lst_data = list_path.read_bytes()
    key_byte = detect_lst_key(lst_data)
    if key_byte is None:
        raise ValueError("无法探测 .lst 密钥")
    entries = read_index(lst_data, key_byte)
    snx_entries = [e for e in entries if e.type_id == 1]
    if not snx_entries:
        return key_byte, (key_byte + 1) & 0xFF

    pkg = package_path.read_bytes()
    sample = snx_entries[0]
    enc = pkg[sample.offset : sample.offset + sample.length]
    for sk in ((key_byte + 1) & 0xFF, key_byte, 0x03, 0x02, 0x01, 0x00):
        dec = _xor_bytes(enc, sk)
        if try_parse_snx(dec):
            return key_byte, sk
    return key_byte, (key_byte + 1) & 0xFF
