# -*- coding: utf-8 -*-
"""Artemis Engine .pfs (pf6/pf8) unpacker.

Based on YuriSizuku/GalgameReverse artemis_pf8 + GARbro ArcPFS:
  pf8: SHA-1(index[0x07:0x07+index_size]) → 20-byte XOR key for each file's data.
  pf6/pf2: no encryption.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


class PFSError(RuntimeError):
    pass


@dataclass
class PFSEntry:
    name: str
    offset: int
    size: int


def _parse_entries(data: bytes) -> Tuple[bytes, int, List[PFSEntry], Optional[bytes]]:
    if len(data) < 11 or data[0:2] != b"pf":
        raise PFSError("Not a PFS archive")
    ver_ch = data[2]
    if ver_ch not in (ord("2"), ord("4"), ord("5"), ord("6"), ord("8"), ord("9")):
        # still try if digit
        if not (0x30 <= ver_ch <= 0x39):
            raise PFSError(f"Unknown PFS magic: {data[:3]!r}")
    index_size = struct.unpack_from("<I", data, 3)[0]
    if 7 + index_size > len(data):
        raise PFSError("Truncated PFS index")
    index = data[7 : 7 + index_size]
    count = struct.unpack_from("<I", index, 0)[0]
    if count <= 0 or count > 500000:
        raise PFSError(f"Bad file count: {count}")
    cur = 4
    entries: List[PFSEntry] = []
    for _ in range(count):
        if cur + 4 > len(index):
            raise PFSError("Truncated entry")
        name_len = struct.unpack_from("<I", index, cur)[0]
        cur += 4
        if name_len < 0 or cur + name_len + 12 > len(index):
            raise PFSError("Bad name length")
        name_raw = index[cur : cur + name_len]
        cur += name_len
        cur += 4  # separator zeros
        offset, size = struct.unpack_from("<II", index, cur)
        cur += 8
        name = name_raw.split(b"\x00")[0].decode("utf-8", errors="replace").replace("\\", "/")
        entries.append(PFSEntry(name=name, offset=offset, size=size))

    magic = data[:3]
    # encrypt for pf4/5/8/9
    key = None
    if magic in (b"pf4", b"pf5", b"pf8", b"pf9"):
        key = hashlib.sha1(index).digest()
    return magic, count, entries, key


def _xor(data: bytes, key: bytes) -> bytes:
    kb = key
    n = len(kb)
    return bytes(b ^ kb[i % n] for i, b in enumerate(data))


def extract_pfs(
    archive: Path | str,
    out_dir: Path | str,
    *,
    only_suffixes: Optional[set[str]] = None,
) -> int:
    archive = Path(archive)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = archive.read_bytes()
    _magic, _n, entries, key = _parse_entries(data)
    written = 0
    for e in entries:
        if only_suffixes is not None:
            suf = Path(e.name).suffix.lower()
            base = Path(e.name).name.lower()
            if suf not in only_suffixes and base != "system.ini":
                continue
        if e.offset + e.size > len(data):
            continue
        blob = data[e.offset : e.offset + e.size]
        if key is not None:
            low = e.name.lower()
            if not (low.endswith(".mp4") or low.endswith(".flv") or low.endswith(".avi")):
                blob = _xor(blob, key)
        dest = out_dir / e.name.replace("\\", "/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
        written += 1
    return written


def extract_pfs_scripts(archive: Path | str, out_dir: Path | str) -> int:
    script_suf = {".ast", ".txt", ".asb", ".iet", ".lua", ".ini", ".csv", ".tsv", ".json"}
    return extract_pfs(archive, out_dir, only_suffixes=script_suf)


def find_pfs_archives(game_dir: Path) -> List[Path]:
    """Locate Artemis PFS packs. Prefer ``root.pfs`` over split ``root.pfs.000`` volumes."""
    root = Path(game_dir)
    found: List[Path] = []
    for pat in ("*.pfs", "*.PFS"):
        found.extend(root.glob(pat))
    # also root.pfs without catching volumes twice
    for p in root.glob("root.pfs"):
        found.append(p)
    for p in root.glob("arc.pfs"):
        found.append(p)

    def _has_pf_magic(p: Path) -> bool:
        try:
            with p.open("rb") as f:
                return f.read(2) == b"pf"
        except OSError:
            return False

    seen = set()
    out: List[Path] = []
    for p in found:
        if not p.is_file():
            continue
        # skip split volumes like root.pfs.000 (suffix .000) — not standalone pf headers usually
        if p.suffix.lstrip(".").isdigit():
            continue
        k = str(p.resolve()).lower()
        if k in seen:
            continue
        if not _has_pf_magic(p):
            continue
        seen.add(k)
        out.append(p)
    # If nothing, fall back to numbered volumes that still have pf magic
    if not out:
        for p in root.iterdir():
            if not p.is_file():
                continue
            name = p.name.lower()
            if not (name.startswith("root.pfs.") or name.startswith("arc.pfs.")):
                continue
            if not _has_pf_magic(p):
                continue
            k = str(p.resolve()).lower()
            if k not in seen:
                seen.add(k)
                out.append(p)
    out.sort(key=lambda p: (0 if p.name.lower() in {"root.pfs", "arc.pfs"} else 1, p.name.lower()))
    return out
