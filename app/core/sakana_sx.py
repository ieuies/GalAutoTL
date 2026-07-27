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


def pack_zstd(raw: bytes, *, level: int = 3) -> bytes:
    if zstd is None:
        raise RuntimeError("需要 zstandard：pip install zstandard")
    packed = zstd.ZstdCompressor(level=level).compress(raw)
    return struct.pack(">I", len(raw)) + packed


def pack_zstd_fit(raw: bytes, max_size: int) -> bytes:
    """Compress with escalating zstd level until payload fits the archive slot."""
    last: bytes | None = None
    for level in (3, 4, 5, 6, 8, 10, 12, 15, 19, 22):
        last = pack_zstd(raw, level=level)
        if len(last) <= max_size:
            return last
    assert last is not None
    raise ValueError(f"zstd 仍过大: {len(last)} > {max_size}")


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
    _storage_cache: Dict[int, bytes] = field(default_factory=dict, repr=False)

    def storage_bytes(self, arc_index: int) -> bytes:
        if arc_index not in self._storage_cache:
            storage = self.storages.get(arc_index)
            if not storage or not storage.is_file():
                raise FileNotFoundError(f"无 storage arc={arc_index}")
            self._storage_cache[arc_index] = storage.read_bytes()
        return self._storage_cache[arc_index]


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
    return entries, {
        "sx_path": sx_path,
        "arc_count": arc_count,
        "arc_sizes": arc_sizes,
        "key": key,
    }


def _read_sx_index_blob(sx_path: Path) -> Tuple[bytearray, int, bytes]:
    """Return (index_data, header_key, header16)."""
    raw = sx_path.read_bytes()
    if len(raw) <= 0x10 or raw[:8] != b"SSXXDEFL":
        raise ValueError(f"不是 SSXXDEFL 索引: {sx_path.name}")
    key = struct.unpack_from(">i", raw, 8)[0]
    packed = bytearray(raw[0x10:])
    key_lo, key_hi = _index_keys(key, len(packed))
    packed = decrypt_data(packed, key_lo, key_hi)
    index_data = bytearray(unpack_zstd(bytes(packed)))
    return index_data, key, raw[:0x10]


def _sx_arc_md5_offsets(index_data: bytes) -> List[int]:
    """Byte offsets of each archive MD5 (16 bytes) inside decrypted index."""
    pos = 8
    count = _be32(index_data, pos)
    pos += 4
    for _ in range(count):
        nlen = index_data[pos]
        pos += 1 + nlen
    count = _be32(index_data, pos)
    pos += 4
    pos += count * 12
    arc_count = _be16(index_data, pos)
    pos += 2
    offs: List[int] = []
    for _ in range(arc_count):
        pos += 12
        pos += 4  # size
        pos += 8  # unk
        offs.append(pos)
        pos += 16
    return offs


def refresh_json_storage_md5_only(pkg_dir: Path | str) -> Dict[str, str]:
    """Update only JSON storages[].md5 to match files. Never rewrite .sx.

    Safer for boot: regenerating SSXXDEFL has bricked some titles.
    Note: if .sx arc MD5s stay stale, TitleScene.clearCacheCheck still fires.
    Prefer refresh_sx_arc_md5_exact_size after patching storages.
    """
    import hashlib

    pkg_dir = Path(pkg_dir)
    sx = find_sx_index(pkg_dir)
    if not sx:
        raise FileNotFoundError(f"未找到 *.sx: {pkg_dir}")
    base = sx.stem[:-4] if sx.stem.endswith("(00)") else sx.stem
    updated: Dict[str, str] = {}
    for jpath in list(pkg_dir.glob("*.json")):
        try:
            data = json.loads(jpath.read_text(encoding="utf-8"))
        except Exception:
            continue
        storages = data.get("storages")
        if not isinstance(storages, list):
            continue
        changed = False
        for row in storages:
            if not row or len(row) < 3:
                continue
            tag = str(row[0])
            cand = pkg_dir / f"{base}-{tag}.sxstorage"
            if not cand.is_file():
                continue
            digest = hashlib.md5(cand.read_bytes()).hexdigest().upper()
            if str(row[2]).upper() != digest:
                row[2] = digest
                changed = True
                updated[f"{jpath.name}:{tag}"] = digest
        if changed:
            jpath.write_text(
                json.dumps(data, ensure_ascii=False, indent="\t") + "\n",
                encoding="utf-8",
            )
    return updated


def refresh_sx_arc_md5_exact_size(pkg_dir: Path | str) -> Dict[str, str]:
    """Patch archive MD5s inside .sx and keep the exact original .sx byte length.

    Only the 16-byte MD5 fields in the decrypted index change. The encrypted
    payload is re-zstd'd and zero-padded back to the original size; if no
    compression level fits, raises instead of rewriting a different-sized .sx
    (that previously bricked boot on some titles).
    """
    import hashlib

    pkg_dir = Path(pkg_dir)
    sx = find_sx_index(pkg_dir)
    if not sx:
        raise FileNotFoundError(f"未找到 *.sx: {pkg_dir}")
    arc = open_sakana_pkg(pkg_dir)
    updated: Dict[str, str] = {}

    digests: Dict[int, bytes] = {}
    for i, path in arc.storages.items():
        digests[i] = hashlib.md5(path.read_bytes()).digest()
        updated[path.name] = digests[i].hex().upper()

    raw = sx.read_bytes()
    enc_len = len(raw) - 0x10
    orig_size = len(raw)
    index_data, key, header16 = _read_sx_index_blob(sx)
    changed = 0
    for i, off in enumerate(_sx_arc_md5_offsets(index_data)):
        if i not in digests:
            continue
        if bytes(index_data[off : off + 16]) != digests[i]:
            index_data[off : off + 16] = digests[i]
            changed += 1
    if not changed:
        # still refresh JSON in case it drifted
        updated.update(refresh_json_storage_md5_only(pkg_dir))
        return updated

    packed: Optional[bytes] = None
    used_level: Optional[int] = None
    for level in (1, 2, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 3, 4, 5):
        cand = pack_zstd(bytes(index_data), level=level)
        if len(cand) > enc_len:
            continue
        trial = cand + b"\x00" * (enc_len - len(cand))
        try:
            if unpack_zstd(trial) == bytes(index_data):
                packed = trial
                used_level = level
                break
        except Exception:
            continue
    if packed is None:
        raise RuntimeError(
            f"无法在原 .sx 体积内重压索引 MD5（enc_len={enc_len}）；已中止以免改文件大小"
        )

    key_lo, key_hi = _index_keys(key, len(packed))
    enc = decrypt_data(bytearray(packed), key_lo, key_hi)
    header = bytearray(header16)
    struct.pack_into(">i", header, 8, key)
    out = bytes(header) + bytes(enc)
    if len(out) != orig_size:
        raise RuntimeError(f".sx 写出长度变化 {orig_size} -> {len(out)}，已中止")
    sx.write_bytes(out)

    # verify round-trip
    index2, _, _ = _read_sx_index_blob(sx)
    for i, off in enumerate(_sx_arc_md5_offsets(index2)):
        if i in digests and bytes(index2[off : off + 16]) != digests[i]:
            raise RuntimeError(f".sx arc{i} MD5 回读校验失败")

    sx_digest = hashlib.md5(sx.read_bytes()).hexdigest().upper()
    updated[sx.name] = sx_digest
    updated["zstd_level"] = str(used_level)
    updated.update(refresh_json_storage_md5_only(pkg_dir))

    base = sx.stem[:-4] if sx.stem.endswith("(00)") else sx.stem
    for jpath in list(pkg_dir.glob("*.json")):
        try:
            data = json.loads(jpath.read_text(encoding="utf-8"))
        except Exception:
            continue
        changed_j = False
        if "md5" in data and str(data.get("md5", "")).upper() != sx_digest:
            data["md5"] = sx_digest
            changed_j = True
            updated[f"{jpath.name}:sx"] = sx_digest
        if "size" in data and int(data.get("size") or 0) != orig_size:
            data["size"] = orig_size
            changed_j = True
        if changed_j:
            jpath.write_text(
                json.dumps(data, ensure_ascii=False, indent="\t") + "\n",
                encoding="utf-8",
            )
    return updated


def refresh_sakana_checksums(pkg_dir: Path | str) -> Dict[str, str]:
    """Update JSON + SX index MD5 after patching .sxstorage (required for Start/newgame).

    TitleScene runs packagecheck against storage MD5s and the .sx file's own
    size/md5. Prefer rewriting the index at the **original .sx size** (zero-pad
    compressed payload) so engines that seal both fields keep working.
    """
    import hashlib

    pkg_dir = Path(pkg_dir)
    sx = find_sx_index(pkg_dir)
    if not sx:
        raise FileNotFoundError(f"未找到 *.sx: {pkg_dir}")
    arc = open_sakana_pkg(pkg_dir)
    updated: Dict[str, str] = {}

    digests: Dict[int, bytes] = {}
    for i, path in arc.storages.items():
        digests[i] = hashlib.md5(path.read_bytes()).digest()
        updated[path.name] = digests[i].hex().upper()

    raw = sx.read_bytes()
    enc_len = len(raw) - 0x10
    index_data, key, header16 = _read_sx_index_blob(sx)
    for i, off in enumerate(_sx_arc_md5_offsets(index_data)):
        if i in digests:
            index_data[off : off + 16] = digests[i]

    packed: Optional[bytes] = None
    for level in (3, 4, 5, 6, 8, 10, 12, 15, 19, 22, 1, 2):
        cand = pack_zstd(bytes(index_data), level=level)
        if len(cand) <= enc_len:
            trial = cand + b"\x00" * (enc_len - len(cand))
            if unpack_zstd(trial) == bytes(index_data):
                packed = trial
                break
    if packed is None:
        packed = pack_zstd(bytes(index_data), level=8)

    key_lo, key_hi = _index_keys(key, len(packed))
    enc = decrypt_data(bytearray(packed), key_lo, key_hi)
    header = bytearray(header16)
    struct.pack_into(">i", header, 8, key)
    sx.write_bytes(bytes(header) + bytes(enc))

    sx_digest = hashlib.md5(sx.read_bytes()).hexdigest().upper()
    sx_size = sx.stat().st_size
    updated[sx.name] = sx_digest

    base = sx.stem[:-4] if sx.stem.endswith("(00)") else sx.stem
    for jpath in list(pkg_dir.glob("*.json")):
        try:
            data = json.loads(jpath.read_text(encoding="utf-8"))
        except Exception:
            continue
        changed = False
        if "md5" in data and str(data.get("md5", "")).upper() != sx_digest:
            data["md5"] = sx_digest
            changed = True
            updated[f"{jpath.name}:sx"] = sx_digest
        if "size" in data and int(data.get("size") or 0) != sx_size:
            data["size"] = sx_size
            changed = True
            updated[f"{jpath.name}:size"] = str(sx_size)
        storages = data.get("storages")
        if isinstance(storages, list):
            for row in storages:
                if not row or len(row) < 3:
                    continue
                tag = str(row[0])
                cand = pkg_dir / f"{base}-{tag}.sxstorage"
                if not cand.is_file():
                    continue
                digest = hashlib.md5(cand.read_bytes()).hexdigest().upper()
                if str(row[2]).upper() != digest:
                    row[2] = digest
                    changed = True
                    updated[f"{jpath.name}:{tag}"] = digest
        if changed:
            jpath.write_text(
                json.dumps(data, ensure_ascii=False, indent="\t") + "\n",
                encoding="utf-8",
            )
    return updated


def _is_live_sxstorage(path: Path, base: str) -> bool:
    """Ignore backup/broken twins that share the same size as the real pack."""
    name = path.name
    stem = path.stem
    low = name.lower()
    if low.startswith("broken_") or low.startswith("bak_") or low.startswith("backup_"):
        return False
    # Prefer canonical Game-001-0.sxstorage over random same-size copies
    if stem.startswith(base + "-") or stem.startswith(base + "_"):
        return True
    # allow tag-only names like data-0.sxstorage when base differs slightly
    return not any(stem.upper().startswith(p) for p in ("BROKEN_", "BAK_", "BACKUP_"))


def _resolve_storage(pkg_dir: Path, sx_stem: str, arc_index: int, storages_meta: list, arc_sizes: list) -> Optional[Path]:
    """Map SX arc_index → .sxstorage file.

    Prefer body size from the SX index (same idea as GARbro). Do **not** trust
    JSON ``storages[]`` list order as arc_index — games like DangerousVillageTradition
    list ``img/snd/0`` while index order is ``0/snd/img``, which would read scripts
    from the image archive and make decrypt/zstd look "broken".
    """
    expected_size = arc_sizes[arc_index] if 0 <= arc_index < len(arc_sizes) else None
    base = sx_stem[:-4] if sx_stem.endswith("(00)") else sx_stem

    def _size_ok(p: Path) -> bool:
        return expected_size is None or (p.is_file() and p.stat().st_size == expected_size)

    def _candidates_by_size() -> List[Path]:
        if expected_size is None:
            return []
        all_hit = [
            sp
            for sp in pkg_dir.glob("*.sxstorage")
            if sp.is_file() and sp.stat().st_size == expected_size
        ]
        live = [sp for sp in all_hit if _is_live_sxstorage(sp, base)]
        return live or all_hit

    # Prefer exact canonical name: {base}-{tag}.sxstorage with matching size
    if expected_size is not None and storages_meta:
        for row in storages_meta:
            if not row:
                continue
            cand = pkg_dir / f"{base}-{row[0]}.sxstorage"
            if cand.is_file() and cand.stat().st_size == expected_size and _is_live_sxstorage(cand, base):
                return cand

    by_size = _candidates_by_size()
    if len(by_size) == 1:
        return by_size[0]
    if len(by_size) > 1:
        tags = {str(row[0]) for row in storages_meta if row}
        # Prefer base-tag.sxstorage among same-size live files
        preferred = [
            sp
            for sp in by_size
            if sp.stem.startswith(base + "-")
            and (not tags or any(sp.stem == f"{base}-{t}" for t in tags))
        ]
        if len(preferred) == 1:
            return preferred[0]
        tagged = [
            sp
            for sp in by_size
            if any(sp.stem.endswith(f"-{t}") for t in tags)
        ]
        if len(tagged) == 1:
            return tagged[0]
        by_idx = [sp for sp in by_size if sp.stem.endswith(f"-{arc_index}")]
        if len(by_idx) == 1:
            return by_idx[0]
        # Deterministic: canonical base-* first, then name sort
        by_size = sorted(by_size, key=lambda p: (0 if p.stem.startswith(base + "-") else 1, p.name))
        return by_size[0]

    # JSON tag hint — only accept when size matches (list order ≠ arc_index)
    if expected_size is not None and storages_meta:
        for row in storages_meta:
            if not row:
                continue
            cand = pkg_dir / f"{base}-{row[0]}.sxstorage"
            if cand.is_file() and cand.stat().st_size == expected_size:
                return cand
    elif 0 <= arc_index < len(storages_meta):
        tag = str(storages_meta[arc_index][0])
        cand = pkg_dir / f"{base}-{tag}.sxstorage"
        if cand.is_file():
            return cand

    # name ends with -0 / -img / -snd / -{arc_index}
    for sp in pkg_dir.glob("*.sxstorage"):
        if not _is_live_sxstorage(sp, base):
            continue
        stem = sp.stem
        if stem.endswith(f"-{arc_index}") or stem.endswith(f"_{arc_index}"):
            if _size_ok(sp):
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

    arc_count = int(meta.get("arc_count") or 0)
    arc_sizes = meta.get("arc_sizes") or []
    storage_map: Dict[int, Path] = {}
    for i in range(arc_count):
        p = _resolve_storage(pkg_dir, sx.stem, i, storages_meta, arc_sizes)
        if p:
            storage_map[i] = p

    missing = [i for i in range(arc_count) if i not in storage_map]
    if missing:
        raise FileNotFoundError(
            f"无法按 SX 索引匹配 .sxstorage（缺 arc {missing}）。"
            f"期望体积={arc_sizes!r}；请检查 pkg 内封包是否齐全。"
        )
    for i, p in storage_map.items():
        if i < len(arc_sizes) and p.stat().st_size != arc_sizes[i]:
            raise ValueError(
                f"封包映射不一致: arc {i} 期望 {arc_sizes[i]} 字节，"
                f"实际 {p.name}={p.stat().st_size}（勿按 JSON storages 顺序硬套）"
            )

    named = [e for e in entries if e.name]
    return SxArchive(sx_path=sx, storages=storage_map, entries=named, meta=meta)


def read_entry(arc: SxArchive, entry: SxEntry) -> bytes:
    data = arc.storage_bytes(entry.arc_index)
    blob = bytearray(data[entry.offset : entry.offset + entry.size])
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
    """Size-preserving write into .sxstorage.

    - ``.ks``: keep original decrypted tail after the new zstd frame (proven OK).
    - ``.sk`` / ``.conf`` / ``.scp``: zero-fill the remainder (keep-tail junk can
      brick title/boot when the engine loads these at startup).
    - unpacked slots: pad with spaces.
    """
    storage = arc.storages.get(entry.arc_index)
    if not storage or not storage.is_file():
        raise FileNotFoundError(f"无 storage arc={entry.arc_index}")
    slot = bytearray(arc.storage_bytes(entry.arc_index)[entry.offset : entry.offset + entry.size])
    if len(slot) != entry.size:
        raise ValueError(f"短读槽位 {entry.name}: {len(slot)}/{entry.size}")

    lo = hi = 0
    plain = bytearray(slot)
    if entry.is_encrypted:
        lo, hi = _entry_keys(entry.offset, entry.size)
        plain = decrypt_data(plain, lo, hi)

    if entry.is_packed:
        payload = pack_zstd_fit(raw, entry.size)
        rel = entry.name.replace("\\", "/").lower()
        zero_pad = rel.endswith((".sk", ".conf", ".scp", ".skfx"))
        if zero_pad:
            plain = bytearray(payload + b"\x00" * (entry.size - len(payload)))
            if unpack_zstd(bytes(plain)) != raw:
                raise ValueError(f"零填充回读失败: {entry.name}")
        else:
            # Keep original decrypted bytes after the new zstd frame.
            plain = bytearray(plain)
            plain[: len(payload)] = payload
    else:
        if len(raw) > entry.size:
            raise ValueError(
                f"译文过大无法塞回槽位 {entry.name}: {len(raw)} > {entry.size}"
            )
        # Unpacked slot IS the file: pad with spaces (never NUL — C-string readers).
        pad = b" " * (entry.size - len(raw))
        plain = bytearray(raw) + pad

    out: bytearray = plain
    if entry.is_encrypted:
        out = decrypt_data(bytearray(plain), lo, hi)
    with storage.open("r+b") as f:
        f.seek(entry.offset)
        f.write(out)
    arc._storage_cache.pop(entry.arc_index, None)


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
    text_fail = 0
    text_total = 0
    last_err: Optional[BaseException] = None
    for e in arc.entries:
        suf = Path(e.name).suffix.lower()
        want_text = suf in _TEXT_SUF
        if want_text:
            text_total += 1
        try:
            data = read_entry(arc, e)
        except Exception as ex:
            last_err = ex
            if want_text:
                text_fail += 1
            continue
        if only_text and not want_text and not _looks_textual(data):
            continue
        dest = out_dir.joinpath(*Path(e.name).parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        n += 1
    if text_total >= 3 and text_fail * 2 >= text_total:
        raise RuntimeError(
            f"Sakana 文本条目解密/解压失败过多（{text_fail}/{text_total}）。"
            f"常见原因：.sxstorage 与 arc_index 映射错误。"
            + (f"\n最后错误: {last_err}" if last_err else "")
        )
    return n, arc
