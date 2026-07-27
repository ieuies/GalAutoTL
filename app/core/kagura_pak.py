# -*- coding: utf-8 -*-
"""Debonosu / Kagura Softpal-style PAK (magic PAK\\0) + Lua 5.1 .scb text.

Does not touch other engines. game.pak stores raw-deflate entries; scripts are
compiled Lua 5.1 bytecode (.scb) with CP932/UTF-8 string constants.
"""
from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

PAK_MAGIC = b"PAK\x00"
ATTR_DIR = 0x10
ATTR_FILE = 0x20

# Skip engine / code identifiers — not dialogue
_SKIP_STR = re.compile(
    r"^(_|[A-Z]{2,}|BTL_|RACE_|STAGE_|TAG_|EFF|GetVar|SetVar|HasVar|Load|Init|Exit|"
    r"Quiet|patch|test|top|enter|boss|loop|rape|memorial|process|save|reload|"
    r"brand|title|system|include)",
)
_HAS_JP = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
_TECH = re.compile(r"^[\x00-\x7f_*./\\:+\-0-9A-Za-z]+$")


@dataclass
class PakEntry:
    name: str
    offset: int
    size: int  # compressed size on disk
    unpack_size: int
    attr: int
    meta_off: int  # offset of 52-byte meta in uncompressed index
    name_off: int


@dataclass
class PakArchive:
    path: Path
    raw: bytes
    version: int
    flags: int
    data_off: int
    index: bytearray
    entries: List[PakEntry]

    @property
    def files(self) -> List[PakEntry]:
        return [e for e in self.entries if e.attr == ATTR_FILE]


def _inflate_raw(data: bytes) -> bytes:
    return zlib.decompress(data, -15)


def _deflate_raw(data: bytes, level: int = 9) -> bytes:
    c = zlib.compress(data, level)
    return c[2:-4]


def open_pak(path: Path | str) -> PakArchive:
    path = Path(path)
    raw = path.read_bytes()
    if raw[:4] != PAK_MAGIC:
        raise ValueError(f"not a Kagura/Softpal PAK: {path.name}")
    version, flags = struct.unpack_from("<HH", raw, 4)
    _root_count, uncomp, comp = struct.unpack_from("<III", raw, 0x18)
    idx = bytearray(_inflate_raw(raw[0x28 : 0x28 + comp]))
    if len(idx) != uncomp:
        raise ValueError("PAK index size mismatch")
    data_off = 0x28 + comp
    entries = _parse_index_tree(idx, data_len=len(raw) - data_off)
    return PakArchive(
        path=path,
        raw=raw,
        version=version,
        flags=flags,
        data_off=data_off,
        index=idx,
        entries=entries,
    )


def _parse_index_tree(idx: bytes | bytearray, data_len: int) -> List[PakEntry]:
    """Sequential Softpal entries: 52-byte meta + name\\0, packed tightly."""
    out: List[PakEntry] = []
    pos = 0
    n = len(idx)
    while pos + 52 < n:
        offset, size_or_count, packed_or_zero, attr = struct.unpack_from("<QQQI", idx, pos)
        name_off = pos + 52
        end = idx.find(b"\x00", name_off)
        if end < 0 or end == name_off:
            break
        name = bytes(idx[name_off:end]).decode("ascii", errors="replace")
        if not name or not all(32 < ord(ch) < 127 for ch in name):
            break
        next_pos = end + 1
        if attr == ATTR_FILE:
            # offset, unpack_size, packed_size
            unpack_size = int(size_or_count)
            packed_size = int(packed_or_zero)
            if packed_size <= 0 or offset + packed_size > data_len:
                pos = next_pos
                continue
            out.append(
                PakEntry(
                    name=name,
                    offset=int(offset),
                    size=packed_size,
                    unpack_size=unpack_size,
                    attr=int(attr),
                    meta_off=pos,
                    name_off=name_off,
                )
            )
        elif attr == ATTR_DIR:
            out.append(
                PakEntry(
                    name=name,
                    offset=0,
                    size=0,
                    unpack_size=int(size_or_count),
                    attr=int(attr),
                    meta_off=pos,
                    name_off=name_off,
                )
            )
        else:
            break
        pos = next_pos
    return out


def read_entry(arc: PakArchive, entry: PakEntry) -> bytes:
    blob = arc.raw[arc.data_off + entry.offset : arc.data_off + entry.offset + entry.size]
    try:
        plain = _inflate_raw(blob)
    except zlib.error:
        plain = bytes(blob)
    return plain


def is_lua_scb(data: bytes) -> bool:
    return data.startswith(b"\x1bLua")


def iter_lua_string_spans(data: bytes) -> Iterable[Tuple[int, int, bytes]]:
    """Yield (size_field_off, payload_off, payload_bytes_without_nul)."""
    i = 0
    n = len(data)
    while i + 5 < n:
        ln = struct.unpack_from("<I", data, i)[0]
        if 2 <= ln <= 800 and i + 4 + ln <= n and data[i + 4 + ln - 1] == 0:
            payload = data[i + 4 : i + 4 + ln - 1]
            if payload and _looks_like_text_bytes(payload):
                yield i, i + 4, payload
                i += 4 + ln
                continue
        i += 1


def _looks_like_text_bytes(raw: bytes) -> bool:
    bad = sum(1 for b in raw if b < 9 or (13 < b < 32))
    return bad < max(1, len(raw) // 6)


def decode_script_text(raw: bytes) -> Optional[str]:
    for enc in ("utf-8", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def encode_script_text(text: str, prefer_cp932: bool = False) -> Optional[bytes]:
    """Encode script text. When prefer_cp932, never fall back to UTF-8
    (engine reads CP932; UTF-8 payloads display as mojibake)."""
    if prefer_cp932:
        try:
            from app.core.cp932_safe import to_cp932_safe

            text = to_cp932_safe(text)
        except Exception:
            pass
        try:
            return text.encode("cp932")
        except UnicodeEncodeError:
            try:
                return text.encode("cp932", errors="ignore")
            except Exception:
                return None
    for enc in ("utf-8", "cp932"):
        try:
            return text.encode(enc)
        except UnicodeEncodeError:
            continue
    return None


def is_translatable_line(s: str) -> bool:
    if not s or len(s) > 500:
        return False
    if not _HAS_JP.search(s):
        return False
    if _TECH.match(s):
        return False
    if _SKIP_STR.match(s):
        return False
    # pure code-ish
    if s.startswith("_") and " " not in s and "「" not in s:
        return False
    if re.fullmatch(r"[A-Z0-9_]+", s):
        return False
    return True


def collect_scb_units(data: bytes, source_name: str = "") -> List[Tuple[int, str]]:
    """Return list of (payload_offset, text) for translatable Lua strings."""
    units: List[Tuple[int, str]] = []
    seen_off = set()
    for _sz_off, payload_off, payload in iter_lua_string_spans(data):
        if payload_off in seen_off:
            continue
        text = decode_script_text(payload)
        if not text or not is_translatable_line(text):
            continue
        seen_off.add(payload_off)
        units.append((payload_off, text))
    return units


def _resolve_mapped(src: str, mapping: Dict[str, str]) -> Optional[str]:
    """Exact key, or stripped key while keeping leading/trailing whitespace."""
    if src in mapping:
        return mapping[src]
    core = src.strip()
    if core and core in mapping:
        lead = src[: len(src) - len(src.lstrip())]
        trail = src[len(src.rstrip()) :]
        return f"{lead}{mapping[core]}{trail}"
    return None


def _fit_encoded(
    text: str,
    budget: int,
    *,
    prefer_cp932: bool,
    soft_fit: bool,
) -> Optional[bytes]:
    new_b = encode_script_text(text, prefer_cp932=prefer_cp932)
    if new_b is None:
        return None
    if len(new_b) < budget:
        return new_b + b" " * (budget - len(new_b))
    if len(new_b) == budget:
        return new_b
    if not soft_fit:
        return None
    # Never fall back to UTF-8 when prefer_cp932 — engine displays CP932 only.
    t = text
    while t:
        enc = encode_script_text(t, prefer_cp932=prefer_cp932)
        if enc is not None and len(enc) <= budget:
            return enc + b" " * (budget - len(enc))
        t = t[:-1]
    return None


def apply_scb_units(
    data: bytes,
    mapping: Dict[str, str],
    *,
    soft_fit: bool = True,
    prefer_cp932: bool = False,
) -> Tuple[bytes, int]:
    """Replace Lua string payloads. Length must fit original byte budget.

    If translation is shorter, pad with ASCII spaces.
    If longer and soft_fit, truncate on character boundary (last resort).
    Also matches mapping keys after strip() so leading-space JP UI labels work.
    """
    buf = bytearray(data)
    n_changed = 0
    for _sz_off, payload_off, payload in list(iter_lua_string_spans(bytes(buf))):
        src = decode_script_text(payload)
        if not src:
            continue
        dst = _resolve_mapped(src, mapping)
        if not dst or dst == src:
            continue
        if prefer_cp932:
            try:
                from app.core.cp932_safe import to_cp932_safe

                dst = to_cp932_safe(dst)
            except Exception:
                pass
            core = dst.replace(" ", "").replace("　", "")
            if core and core.count("・") >= max(3, len(core) // 2):
                continue
        budget = len(payload)
        new_b = _fit_encoded(dst, budget, prefer_cp932=prefer_cp932, soft_fit=soft_fit)
        if new_b is None or len(new_b) != budget:
            continue
        buf[payload_off : payload_off + budget] = new_b
        n_changed += 1
    return bytes(buf), n_changed


def extract_game_scripts(pak_path: Path, out_dir: Path) -> List[Path]:
    arc = open_pak(pak_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    for e in arc.files:
        if not e.name.endswith(".scb"):
            continue
        data = read_entry(arc, e)
        dest = out_dir / e.name
        dest.write_bytes(data)
        written.append(dest)
    return written


def rebuild_game_pak(
    arc: PakArchive,
    file_blobs: Dict[str, bytes],
    dest: Path,
) -> None:
    """Rewrite PAK with updated file contents; preserve index names/attrs."""
    all_files = sorted(arc.files, key=lambda e: e.offset)
    new_index = bytearray(arc.index)
    chunks: List[bytes] = []
    cursor = 0
    for e in all_files:
        if e.name in file_blobs:
            plain = file_blobs[e.name]
            packed = _deflate_raw(plain)
            unpack_size = len(plain)
            packed_size = len(packed)
            struct.pack_into(
                "<QQQ",
                new_index,
                e.meta_off,
                cursor,
                unpack_size,
                packed_size,
            )
        else:
            packed = bytes(
                arc.raw[arc.data_off + e.offset : arc.data_off + e.offset + e.size]
            )
            struct.pack_into(
                "<QQQ",
                new_index,
                e.meta_off,
                cursor,
                e.unpack_size,
                len(packed),
            )
        chunks.append(packed)
        cursor += len(packed)

    idx_comp = _deflate_raw(bytes(new_index))
    hdr = bytearray(arc.raw[:0x28])
    root_count = struct.unpack_from("<I", arc.raw, 0x18)[0]
    struct.pack_into("<III", hdr, 0x18, root_count, len(new_index), len(idx_comp))
    struct.pack_into("<I", hdr, 0x24, 0)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(bytes(hdr) + idx_comp + b"".join(chunks))


def collect_units_from_scb_dir(scb_dir: Path) -> Tuple[List[str], List[Tuple[Path, int, str]]]:
    """texts (unique ordered), and detail units (path, payload_off, text)."""
    details: List[Tuple[Path, int, str]] = []
    ordered: List[str] = []
    seen = set()
    for p in sorted(scb_dir.glob("*.scb")):
        data = p.read_bytes()
        if not is_lua_scb(data):
            continue
        for off, text in collect_scb_units(data, p.name):
            details.append((p, off, text))
            if text not in seen:
                seen.add(text)
                ordered.append(text)
    return ordered, details


def find_game_pak(game_dir: Path) -> Optional[Path]:
    p = game_dir / "game.pak"
    if p.is_file() and p.stat().st_size > 64:
        return p
    for q in game_dir.glob("*.pak"):
        if q.name.lower() in ("game.pak", "script.pak"):
            return q
    return None
