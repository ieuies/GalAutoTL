# -*- coding: utf-8 -*-
"""Minimal XP3 reader/writer (unencrypted archives).

Based on the public XP3 layout used by Kirikiri / awaken1ng/krkr-xp3.
Encrypted packages (cxdec / vendor XOR) are detected and rejected with a clear error —
unpack those first with GARbro, then run the Kirikiri pipeline on the loose tree.
"""
from __future__ import annotations

import os
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable, List, Optional, Tuple

XP3_SIG = b"XP3\x0d\x0a\x20\x0a\x1a\x8b\x67\x01"
FLAG_CONTINUE = 0x80
FLAG_ZLIB = 0x01
FLAG_RAW = 0x00
ENC_BIT = 1 << 31

# Windows-illegal in a single path segment (also reject control chars)
_WIN_BAD_CHARS = set('<>:"|?*')
_PROTECTED_MARKERS = (
    "this is a protected archive",
    "$$$ this is a protected",
    "著作者はこのアーカイブ",
    "extracting this archive may infringe",
)


def is_extractable_xp3_path(rel: str) -> bool:
    """False for anti-extract stub names / paths Windows cannot create."""
    if not rel or not rel.strip():
        return False
    low = rel.lower().replace("\\", "/")
    if any(m in low for m in _PROTECTED_MARKERS):
        return False
    if "$$$" in rel:
        return False
    # keep relative; reject absolute / drive / UNC / parent traversal
    p = Path(rel.replace("\\", "/"))
    if p.is_absolute() or getattr(p, "drive", ""):
        return False
    parts = p.parts
    if any(part in (".", "..") for part in parts):
        return False
    if len(rel) > 180 or any(len(part) > 120 for part in parts):
        return False
    for part in parts:
        if not part or part.endswith(" ") or part.endswith("."):
            return False
        if any(c in _WIN_BAD_CHARS or ord(c) < 32 for c in part):
            return False
    return True


class XP3Error(RuntimeError):
    pass


@dataclass
class XP3Entry:
    path: str
    offset: int
    uncompressed_size: int
    compressed_size: int
    is_compressed: bool
    adler32: int
    encrypted: bool


def _read_exact(f: BinaryIO, n: int) -> bytes:
    data = f.read(n)
    if len(data) != n:
        raise XP3Error("Unexpected end of XP3 file")
    return data


def _read_index_blob(f: BinaryIO) -> bytes:
    f.seek(len(XP3_SIG))
    (offset,) = struct.unpack("<Q", _read_exact(f, 8))
    if not offset:
        raise XP3Error("Missing XP3 file index offset")
    f.seek(offset)
    (flag,) = struct.unpack("<B", _read_exact(f, 1))
    if flag == FLAG_CONTINUE:
        _read_exact(f, 8)  # unused size field in continue header
        (offset,) = struct.unpack("<Q", _read_exact(f, 8))
        f.seek(offset)
        (flag,) = struct.unpack("<B", _read_exact(f, 1))
    if flag == FLAG_ZLIB:
        comp_sz, uncomp_sz = struct.unpack("<QQ", _read_exact(f, 16))
        blob = zlib.decompress(_read_exact(f, comp_sz))
        if len(blob) != uncomp_sz:
            raise XP3Error("Index size mismatch after zlib")
        return blob
    if flag == FLAG_RAW:
        (uncomp_sz,) = struct.unpack("<Q", _read_exact(f, 8))
        return _read_exact(f, uncomp_sz)
    raise XP3Error(f"Unexpected XP3 index flag: 0x{flag:02x}")


def _parse_entries(index: bytes) -> List[XP3Entry]:
    entries: List[XP3Entry] = []
    i = 0
    n = len(index)
    while i < n:
        # optional encryption chunk first (eliF / etc.)
        had_enc_chunk = False
        enc_path = ""
        if i + 4 > n:
            break
        tag = index[i : i + 4]
        if tag != b"File":
            if i + 12 > n:
                raise XP3Error("Truncated encryption chunk")
            (esz,) = struct.unpack_from("<Q", index, i + 4)
            # try recover real path from encryption chunk: size + adler(I) + namelen(H) + utf16
            enc_payload = index[i + 12 : i + 12 + esz]
            had_enc_chunk = True
            i += 12 + esz
            if i + 4 > n or index[i : i + 4] != b"File":
                raise XP3Error("Expected File chunk after encryption header")
            try:
                if len(enc_payload) >= 6:
                    _ad, nlen = struct.unpack_from("<IH", enc_payload, 0)
                    enc_path = enc_payload[6 : 6 + nlen * 2].decode("utf-16le")
            except Exception:
                enc_path = ""

        (fsize,) = struct.unpack_from("<Q", index, i + 4)
        start = i + 12
        end = start + fsize
        if end > n:
            raise XP3Error("Truncated File chunk")
        i = end

        path = enc_path if had_enc_chunk else ""
        encrypted = had_enc_chunk
        adler = 0
        segs: List[Tuple[bool, int, int, int]] = []
        pos = start
        while pos < end:
            ctag = index[pos : pos + 4]
            (csize,) = struct.unpack_from("<Q", index, pos + 4)
            cstart = pos + 12
            cend = cstart + csize
            if cend > end:
                raise XP3Error(f"Truncated subchunk {ctag!r}")
            payload = index[cstart:cend]
            if ctag == b"info":
                flags, _u_sz, _c_sz, name_len = struct.unpack_from("<IQQH", payload, 0)
                encrypted = encrypted or bool(flags & ENC_BIT)
                if not path:
                    name_bytes = payload[4 + 8 + 8 + 2 : 4 + 8 + 8 + 2 + name_len * 2]
                    path = name_bytes.decode("utf-16le")
            elif ctag == b"adlr":
                (adler,) = struct.unpack_from("<I", payload, 0)
            elif ctag == b"segm":
                off = 0
                while off + 28 <= len(payload):
                    flags, seg_off, u_sz, c_sz = struct.unpack_from("<IQQQ", payload, off)
                    segs.append((bool(flags & 1), seg_off, u_sz, c_sz))
                    off += 28
            pos = cend

        if not path or not segs:
            raise XP3Error("File entry missing info/segm")
        offset = segs[0][1]
        entry = XP3Entry(
            path=path.replace("\\", "/"),
            offset=offset,
            uncompressed_size=sum(s[2] for s in segs),
            compressed_size=sum(s[3] for s in segs),
            is_compressed=any(s[0] for s in segs),
            adler32=adler,
            encrypted=encrypted,
        )
        entry._segs = segs  # type: ignore[attr-defined]
        entries.append(entry)
    return entries


def list_xp3(path: Path | str) -> List[XP3Entry]:
    path = Path(path)
    with path.open("rb") as f:
        if _read_exact(f, len(XP3_SIG)) != XP3_SIG:
            raise XP3Error(f"Not an XP3 archive: {path.name}")
        index = _read_index_blob(f)
    return _parse_entries(index)


def _extract_entry(f: BinaryIO, entry: XP3Entry) -> bytes:
    segs = getattr(entry, "_segs", None)
    if not segs:
        segs = [
            (
                entry.is_compressed,
                entry.offset,
                entry.uncompressed_size,
                entry.compressed_size,
            )
        ]
    parts: List[bytes] = []
    for is_comp, offset, u_sz, c_sz in segs:
        f.seek(offset)
        data = _read_exact(f, c_sz)
        if is_comp:
            data = zlib.decompress(data)
        if len(data) != u_sz:
            raise XP3Error(f"Segment size mismatch: {entry.path}")
        parts.append(data)
    return b"".join(parts)


def read_xp3_entry(archive: Path | str, entry: XP3Entry) -> bytes:
    """Read + decompress a single entry's body from ``archive``."""
    with Path(archive).open("rb") as f:
        return _extract_entry(f, entry)


def extract_xp3(
    archive: Path | str,
    out_dir: Path | str,
    *,
    only_suffixes: Optional[Iterable[str]] = None,
    skip_encrypted: bool = False,
    xor_scheme: Optional[str] = None,
    ignore_encryption_flag: bool = False,
) -> Tuple[int, int]:
    """Extract archive. Returns (written, skipped_encrypted).

    xor_scheme: one of xp3_crypto.XOR_SCHEMES keys; applied to encrypted entries.
    ignore_encryption_flag: treat ENC bit as advisory — write raw bytes (some packs
    set the bit while bodies are already plaintext).
    """
    from app.core.xp3_crypto import xor_decrypt

    archive = Path(archive)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffixes = {s.lower() for s in only_suffixes} if only_suffixes else None
    entries = list_xp3(archive)
    written = 0
    skipped = 0
    with archive.open("rb") as f:
        for e in entries:
            if not is_extractable_xp3_path(e.path):
                skipped += 1
                continue
            if suffixes is not None:
                if Path(e.path).suffix.lower() not in suffixes:
                    continue
            if e.encrypted and not xor_scheme and not ignore_encryption_flag:
                if skip_encrypted:
                    skipped += 1
                    continue
                raise XP3Error(
                    f"{archive.name} 内含加密文件（如 {e.path}）。"
                    "将自动尝试内置 XOR / GARbro；仍失败时再手工解包。"
                )
            data = _extract_entry(f, e)
            if e.encrypted and xor_scheme:
                data = xor_decrypt(data, e.adler32, xor_scheme)
            dest = out_dir / Path(e.path.replace("/", os.sep))
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
            except OSError:
                # leftover illegal names on some hosts — never abort whole extract
                skipped += 1
                continue
            written += 1
    return written, skipped


def _apply_xp3dec_adler_filter(
    out_dir: Path, entries: List[XP3Entry]
) -> int:
    """Post-extract: XOR ciphertext with (adler&0xFF) when needed. Returns files fixed.

    Some packs (e.g. xp3dec.tpm titles) filter every script but leave the XP3
    ENC bit clear, so the filter must also run on files whose raw bytes happen
    to pass ``looks_like_kag_after_decode`` (filter garbage often decodes to
    mojibake that still contains kana/``[``).  We compare text quality of the
    raw bytes vs the filtered trial and keep whichever is clearly better.
    """
    from app.core.xp3_crypto import (
        filter_xor_adler_lowbyte,
        kag_text_quality,
        looks_like_kag_after_decode,
    )

    by_path = {e.path.replace("\\", "/"): e for e in entries}
    fixed = 0
    for p in out_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(out_dir).as_posix()
        e = by_path.get(rel)
        if e is None:
            continue
        raw = p.read_bytes()
        if looks_like_kag_after_decode(raw):
            # fast path: already-good BOM'd UTF-16 or `;`/`[`-leading KAG script.
            # Filter garbage from these titles never starts with a BOM or a
            # KAG comment/tag line, so skipping here keeps plain games fast
            # while still catching the mis-decoded (filtered) files below.
            if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or raw[:1] in (b";", b"["):
                continue
        trial = filter_xor_adler_lowbyte(raw, e.adler32)
        trial_ok = looks_like_kag_after_decode(trial)
        if trial_ok and trial != raw:
            raw_ok = looks_like_kag_after_decode(raw)
            if not raw_ok or kag_text_quality(trial) > kag_text_quality(raw):
                p.write_bytes(trial)
                fixed += 1
    return fixed


def extract_xp3_try_schemes(
    archive: Path | str,
    out_dir: Path | str,
    *,
    only_suffixes: Optional[Iterable[str]] = None,
) -> Tuple[int, str]:
    """Try plain / xp3dec / Neko XOR. Returns (written, mode).

    Prefer plaintext bodies even when the XP3 ENC bit is set (common on some packs).
    """
    import shutil

    from app.core.xp3_crypto import (
        XOR_SCHEMES,
        filter_xor_adler_lowbyte,
        looks_like_kag_after_decode,
        xor_decrypt,
    )

    archive = Path(archive)
    out_dir = Path(out_dir)
    entries = list_xp3(archive)
    has_enc = any(e.encrypted for e in entries)

    def _reset_out() -> None:
        if out_dir.exists():
            shutil.rmtree(out_dir)

    if not has_enc:
        _reset_out()
        n, _ = extract_xp3(archive, out_dir, only_suffixes=only_suffixes)
        fixed = _apply_xp3dec_adler_filter(out_dir, entries)
        if fixed:
            return n, f"xp3dec_adler({fixed})"
        return n, "plain"

    # Probe: prefer a .ks entry
    probe_entry = None
    sufs = {s.lower() for s in only_suffixes} if only_suffixes else None
    for e in entries:
        if not e.encrypted or not is_extractable_xp3_path(e.path):
            continue
        if sufs and Path(e.path).suffix.lower() not in sufs:
            continue
        probe_entry = e
        break
    if probe_entry is None:
        for e in entries:
            if e.encrypted and is_extractable_xp3_path(e.path):
                probe_entry = e
                break

    if probe_entry is not None:
        with archive.open("rb") as f:
            raw = _extract_entry(f, probe_entry)

        # 1) Body already readable KAG → ignore ENC flag
        if looks_like_kag_after_decode(raw):
            _reset_out()
            n, _ = extract_xp3(
                archive,
                out_dir,
                only_suffixes=only_suffixes,
                ignore_encryption_flag=True,
            )
            return n, "plain_ignore_enc"

        # 2) xp3dec-style adler low-byte XOR
        trial = filter_xor_adler_lowbyte(raw, probe_entry.adler32)
        if looks_like_kag_after_decode(trial):
            _reset_out()
            n, _ = extract_xp3(
                archive,
                out_dir,
                only_suffixes=only_suffixes,
                ignore_encryption_flag=True,
            )
            fixed = _apply_xp3dec_adler_filter(out_dir, entries)
            return n, f"xp3dec_adler({fixed})"

        # 3) Neko-family XOR — require KAG sniff, not mere "looks like text"
        for name in XOR_SCHEMES:
            trial = xor_decrypt(raw, probe_entry.adler32, name)
            if looks_like_kag_after_decode(trial):
                _reset_out()
                n, _ = extract_xp3(
                    archive, out_dir, only_suffixes=only_suffixes, xor_scheme=name
                )
                return n, name

    _reset_out()
    n, skipped = extract_xp3(
        archive, out_dir, only_suffixes=only_suffixes, skip_encrypted=True
    )
    if n:
        fixed = _apply_xp3dec_adler_filter(out_dir, entries)
        if fixed:
            return n, f"partial_xp3dec(skipped_enc={skipped},fixed={fixed})"
        return n, f"partial_plain(skipped_enc={skipped})"
    return 0, "encrypted_unknown"


def pack_xp3(
    folder: Path | str,
    archive: Path | str,
    *,
    zero_adler: bool = False,
) -> int:
    """Pack all files under folder into an unencrypted XP3. Returns file count.

    zero_adler: set adler32=0 on every entry (KirikiriTools version.dll bypass marker).
    """
    folder = Path(folder)
    archive = Path(archive)
    archive.parent.mkdir(parents=True, exist_ok=True)

    files: List[Tuple[str, Path]] = []
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(folder).as_posix()
        files.append((rel, p))
    if not files:
        raise XP3Error(f"Nothing to pack under {folder}")

    with archive.open("wb") as f:
        f.write(XP3_SIG)
        f.write(struct.pack("<Q", 0))  # index offset placeholder

        entry_blobs: List[bytes] = []
        for internal, src in files:
            raw = src.read_bytes()
            adler = 0 if zero_adler else (zlib.adler32(raw) & 0xFFFFFFFF)
            compressed = zlib.compress(raw, 9)
            if len(compressed) < len(raw):
                payload = compressed
                is_comp = True
                c_sz = len(compressed)
            else:
                payload = raw
                is_comp = False
                c_sz = len(raw)
            offset = f.tell()
            f.write(payload)

            time_chunk = b"time" + struct.pack("<QQ", 8, 0)
            adlr_chunk = b"adlr" + struct.pack("<QI", 4, adler)
            seg_flags = 1 if is_comp else 0
            segs_payload = struct.pack("<IQQQ", seg_flags, offset, len(raw), c_sz)
            segm_chunk = b"segm" + struct.pack("<Q", len(segs_payload)) + segs_payload
            name_u16 = internal.encode("utf-16le") + b"\x00\x00"
            info_size = 4 + 8 + 8 + 2 + len(name_u16)
            info_chunk = (
                b"info"
                + struct.pack("<QIQQH", info_size, 0, len(raw), c_sz, len(internal))
                + name_u16
            )
            body = time_chunk + adlr_chunk + segm_chunk + info_chunk
            entry_blobs.append(b"File" + struct.pack("<Q", len(body)) + body)

        index_raw = b"".join(entry_blobs)
        index_zlib = zlib.compress(index_raw, 9)
        index_off = f.tell()
        if len(index_zlib) + 1 + 16 < len(index_raw) + 1 + 8:
            f.write(struct.pack("<BQQ", FLAG_ZLIB, len(index_zlib), len(index_raw)))
            f.write(index_zlib)
        else:
            f.write(struct.pack("<BQ", FLAG_RAW, len(index_raw)))
            f.write(index_raw)
        f.seek(len(XP3_SIG))
        f.write(struct.pack("<Q", index_off))
    return len(files)


def find_xp3_archives(game_dir: Path) -> List[Path]:
    root = Path(game_dir)
    pats = list(root.glob("*.xp3")) + list(root.glob("*.XP3"))
    # de-dup case on Windows
    seen = set()
    out: List[Path] = []
    for p in pats:
        key = str(p.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    # prefer scenario / data / patch-like names last for overwrite safety
    def score(p: Path) -> Tuple[int, str]:
        n = p.name.lower()
        if "patch" in n or n.startswith("cn"):
            return (2, n)
        if "scenario" in n or "scn" in n or "script" in n:
            return (0, n)
        if "data" in n:
            return (1, n)
        return (1, n)

    out.sort(key=score)
    return out
