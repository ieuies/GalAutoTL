# -*- coding: utf-8 -*-
"""SakanaGL .sx / .sxstorage unpack & size-preserving patch.

Port of GARbro Experimental/SakanaGL/ArcSX.cs (MIT, morkt).
"""
from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_KEY = 0x2E76034B

try:
    import zstandard as zstd
except ImportError:  # pragma: no cover
    zstd = None  # type: ignore


def _i64(x: int) -> int:
    x &= 0xFFFFFFFFFFFFFFFF
    if x >= 0x8000000000000000:
        x -= 0x10000000000000000
    return x


def _u32(x: int) -> int:
    return x & 0xFFFFFFFF


def _be32(data: bytes, off: int = 0) -> int:
    return struct.unpack_from(">I", data, off)[0]


def _be16(data: bytes, off: int = 0) -> int:
    return struct.unpack_from(">H", data, off)[0]


def decrypt_data(data: bytearray | bytes, key_lo: int, key_hi: int) -> bytearray:
    """SakanaGL XOR stream (self-inverse with same keys)."""
    buf = bytearray(data)
    if len(buf) < 4:
        return buf
    key_lo = _u32(key_lo ^ 0x159A55E5)
    key_hi = _u32(key_hi ^ 0x075BCD15)
    v1 = _u32(
        key_hi
        ^ _u32(key_hi << 11)
        ^ _u32((key_hi ^ _u32(key_hi << 11)) >> 8)
        ^ 0x549139A
    )
    v2 = _u32(
        v1
        ^ key_lo
        ^ _u32(key_lo << 11)
        ^ _u32((key_lo ^ _u32(key_lo << 11) ^ _u32(v1 >> 11)) >> 8)
    )
    v3 = _u32(v2 ^ _u32(v2 >> 19) ^ 0x8E415C26)
    v4 = _u32(v3 ^ _u32(v3 >> 19) ^ 0x4D9D5BB8)
    count = len(buf) // 4
    for i in range(count):
        t1 = _u32(
            v4
            ^ v1
            ^ _u32(v1 << 11)
            ^ _u32((v1 ^ _u32(v1 << 11) ^ _u32(v4 >> 11)) >> 8)
        )
        t2 = _u32(v2 ^ _u32(v2 << 11))
        v2 = v4
        v4 = _u32(t1 ^ t2 ^ _u32((t2 ^ _u32(t1 >> 11)) >> 8))
        old = struct.unpack_from("<I", buf, i * 4)[0]
        new = _u32(old ^ (_u32(t1 >> 4) ^ _u32(v4 << 12)))
        struct.pack_into("<I", buf, i * 4, new)
        v1 = v3
        v3 = t1
    return buf


def unpack_zstd(data: bytes) -> bytes:
    if zstd is None:
        raise RuntimeError("需要 zstandard：pip install zstandard")
    if len(data) < 4:
        raise ValueError("zstd blob too small")
    unpacked_size = _be32(data, 0)
    out = zstd.ZstdDecompressor().decompress(
        data[4:], max_output_size=max(unpacked_size * 2, unpacked_size + 1024, 1)
    )
    if unpacked_size and len(out) >= unpacked_size:
        return out[:unpacked_size]
    return out


def pack_zstd(raw: bytes) -> bytes:
    if zstd is None:
        raise RuntimeError("需要 zstandard：pip install zstandard")
    packed = zstd.ZstdCompressor(level=3).compress(raw)
    return struct.pack(">I", len(raw)) + packed


@dataclass
class SxEntry:
    name: str
    flags: int
    arc_index: int
    offset: int
    size: int
    is_packed: bool
    is_encrypted: bool


@dataclass
class SxArchive:
    sx_path: Path
    storages: Dict[int, Path]
    entries: List[SxEntry]
    meta: dict = field(default_factory=dict)


def find_sx_index(pkg_dir: Path) -> Optional[Path]:
    for p in sorted(pkg_dir.glob("*(00).sx")):
        if p.is_file() and p.read_bytes()[:8] == b"SSXXDEFL":
            return p
    for p in sorted(pkg_dir.glob("*.sx")):
        if p.is_file() and p.stat().st_size > 16 and p.read_bytes()[:8] == b"SSXXDEFL":
            return p
    return None


def find_sakana_pkg(game_dir: Path | str) -> Optional[Path]:
    root = Path(game_dir)
    for cand in (root / "pkg", root):
        if cand.is_dir() and find_sx_index(cand):
            return cand
    return None


def is_sakana_game(game_dir: Path | str) -> bool:
    root = Path(game_dir)
    if (root / "sakanagl.dll").is_file():
        return True
    return find_sakana_pkg(root) is not None


def _index_keys(key: int, length: int) -> Tuple[int, int]:
    lkey = _i64(key + length)
    lkey = _i64(key ^ _i64(961 * lkey - 124789) ^ DEFAULT_KEY)
    lu = lkey & 0xFFFFFFFFFFFFFFFF
    key_lo = lu & 0xFFFFFFFF
    key_hi = ((lu >> 32) & 0xFFFFFFFF) ^ 0x2E6
    return key_lo, key_hi


def _entry_keys(offset: int, size: int) -> Tuple[int, int]:
    key_lo = _u32((offset >> 4) ^ (size << 16) ^ DEFAULT_KEY)
    key_hi = _u32((size >> 16) ^ 0x2E6)
    return key_lo, key_hi


def parse_sx_index(sx_path: Path) -> Tuple[List[SxEntry], dict]:
    raw = sx_path.read_bytes()
    if len(raw) <= 0x10 or raw[:8] != b"SSXXDEFL":
        raise ValueError(f"不是 SSXXDEFL 索引: {sx_path.name}")
    key = struct.unpack_from(">i", raw, 8)[0]
    packed = bytearray(raw[0x10:])
    key_lo, key_hi = _index_keys(key, len(packed))
    packed = decrypt_data(packed, key_lo, key_hi)
    index_data = unpack_zstd(bytes(packed))

    pos = 8
    count = _be32(index_data, pos)
    pos += 4
    names: List[str] = []
    for _ in range(count):
        nlen = index_data[pos]
        pos += 1
        names.append(index_data[pos : pos + nlen].decode("utf-8", errors="replace"))
        pos += nlen

    count = _be32(index_data, pos)
    pos += 4
    entries: List[SxEntry] = []
    for _ in range(count):
        arc = _be16(index_data, pos)
        flags = _be16(index_data, pos + 2)
        offset = _be32(index_data, pos + 4)
        size = _be32(index_data, pos + 8)
        pos += 12
        entries.append(
            SxEntry(
                name="",
                flags=flags,
                arc_index=arc,
                offset=offset << 4,
                size=size,
                is_packed=bool(flags & 0x03),
                is_encrypted=(flags & 0x10) == 0,
            )
        )

    arc_count = _be16(index_data, pos)
    pos += 2
    arc_sizes: List[int] = []
    for _ in range(arc_count):
        pos += 12
        arc_sizes.append(_be32(index_data, pos) << 4)
        pos += 4 + 8 + 16

    extra = _be16(index_data, pos)
    pos += 2
    if extra > 0:
        pos += extra * 24

    def deserialize_tree(path: str = "") -> None:
        nonlocal pos
        count = _be16(index_data, pos)
        pos += 2
        name_index = struct.unpack_from(">i", index_data, pos)[0]
        pos += 4
        file_index = struct.unpack_from(">i", index_data, pos)[0]
        pos += 4
        name = str(Path(path) / names[name_index]) if path else names[name_index]
        name = name.replace("\\", "/")
        if file_index == -1:
            for _ in range(count):
                deserialize_tree(name)
        else:
            if 0 <= file_index < len(entries):
                entries[file_index].name = name

    deserialize_tree()
    return entries, {"sx_path": sx_path, "arc_count": arc_count, "arc_sizes": arc_sizes, "key": key}


def _resolve_storage(pkg_dir: Path, sx_stem: str, arc_index: int, storages_meta: list, arc_sizes: list) -> Optional[Path]:
    if 0 <= arc_index < len(storages_meta):
        tag = str(storages_meta[arc_index][0])
        base = sx_stem[:-4] if sx_stem.endswith("(00)") else sx_stem
        cand = pkg_dir / f"{base}-{tag}.sxstorage"
        if cand.is_file():
            return cand
    if 0 <= arc_index < len(arc_sizes):
        size = arc_sizes[arc_index]
        for sp in pkg_dir.glob("*.sxstorage"):
            if sp.stat().st_size == size:
                return sp
    # name ends with -0 / -img / -snd
    for sp in pkg_dir.glob("*.sxstorage"):
        stem = sp.stem
        if stem.endswith(f"-{arc_index}") or stem.endswith(f"_{arc_index}"):
            return sp
    return None


def open_sakana_pkg(pkg_dir: Path | str) -> SxArchive:
    pkg_dir = Path(pkg_dir)
    sx = find_sx_index(pkg_dir)
    if not sx:
        raise FileNotFoundError(f"未找到 SSXXDEFL 索引 *.sx: {pkg_dir}")
    entries, meta = parse_sx_index(sx)

    storages_meta: list = []
    for jname in (sx.with_suffix(".json"), pkg_dir / f"{sx.stem}.json"):
        if jname.is_file():
            try:
                storages_meta = json.loads(jname.read_text(encoding="utf-8")).get("storages") or []
            except Exception:
                storages_meta = []
            break

    storage_map: Dict[int, Path] = {}
    for i in range(meta.get("arc_count") or 0):
        p = _resolve_storage(pkg_dir, sx.stem, i, storages_meta, meta.get("arc_sizes") or [])
        if p:
            storage_map[i] = p

    named = [e for e in entries if e.name]
    return SxArchive(sx_path=sx, storages=storage_map, entries=named, meta=meta)


def read_entry(arc: SxArchive, entry: SxEntry) -> bytes:
    storage = arc.storages.get(entry.arc_index)
    if not storage or not storage.is_file():
        raise FileNotFoundError(f"无 storage arc={entry.arc_index} for {entry.name}")
    blob = bytearray(storage.read_bytes()[entry.offset : entry.offset + entry.size])
    if len(blob) != entry.size:
        raise ValueError(f"短读 {entry.name}: {len(blob)}/{entry.size}")
    if entry.is_encrypted:
        lo, hi = _entry_keys(entry.offset, entry.size)
        blob = decrypt_data(blob, lo, hi)
    raw = bytes(blob)
    if entry.is_packed:
        raw = unpack_zstd(raw)
    return raw


def write_entry(arc: SxArchive, entry: SxEntry, raw: bytes) -> None:
    """Size-preserving write into .sxstorage (pad if smaller; fail if larger)."""
    storage = arc.storages.get(entry.arc_index)
    if not storage or not storage.is_file():
        raise FileNotFoundError(f"无 storage arc={entry.arc_index}")
    payload = raw
    if entry.is_packed:
        payload = pack_zstd(raw)
    if len(payload) > entry.size:
        raise ValueError(
            f"译文过大无法塞回槽位 {entry.name}: {len(payload)} > {entry.size}"
        )
    if len(payload) < entry.size:
        payload = payload + b"\x00" * (entry.size - len(payload))
    blob = bytearray(payload)
    if entry.is_encrypted:
        lo, hi = _entry_keys(entry.offset, entry.size)
        blob = decrypt_data(blob, lo, hi)
    with storage.open("r+b") as f:
        f.seek(entry.offset)
        f.write(blob)


_TEXT_SUF = {
    ".txt",
    ".csv",
    ".tsv",
    ".json",
    ".xml",
    ".ks",
    ".tjs",
    ".lua",
    ".yml",
    ".yaml",
    ".ini",
    ".ss",
    ".sjs",
    ".scn",
    ".script",
    ".bytes",
}


def _looks_textual(data: bytes) -> bool:
    if not data:
        return False
    sample = data[: min(2000, len(data))]
    # UTF-16 LE BOM or many NULs on odd/even
    if sample.startswith(b"\xff\xfe") or sample.startswith(b"\xfe\xff"):
        return True
    if sample[1:2] == b"\x00" and sum(1 for i in range(1, len(sample), 2) if sample[i : i + 1] == b"\x00") > len(sample) // 4:
        return True
    try:
        t = sample.decode("utf-8")
    except UnicodeDecodeError:
        try:
            t = sample.decode("cp932")
        except UnicodeDecodeError:
            return False
    if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", t):
        return True
    printable = sum(1 for c in t if c.isprintable() or c in "\r\n\t")
    return printable >= len(t) * 0.85


def extract_sakana_pkg(
    pkg_dir: Path | str,
    out_dir: Path | str,
    *,
    only_text: bool = True,
) -> Tuple[int, SxArchive]:
    pkg_dir = Path(pkg_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    arc = open_sakana_pkg(pkg_dir)
    n = 0
    for e in arc.entries:
        try:
            data = read_entry(arc, e)
        except Exception:
            continue
        suf = Path(e.name).suffix.lower()
        if only_text and suf not in _TEXT_SUF and not _looks_textual(data):
            continue
        dest = out_dir.joinpath(*Path(e.name).parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        n += 1
    return n, arc
