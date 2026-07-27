# -*- coding: utf-8 -*-
"""YU-RIS YSTB (.ybn) script decode / string extract / patch.

Key recovery + layout follow arcusmaximus/VNTranslationTools YurisScenarioScript:
  key = uint32 at first attribute-descriptor +8 (encrypted), XOR body from 0x20.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

HAS_KANA = re.compile(r"[\u3040-\u30ff]")
HAS_CJK = re.compile(r"[\u4e00-\u9fff]")

ATTR_RAW = 0
ATTR_EXPR = 3
PUSH_STRING = 0x4D


@dataclass
class YstbAttr:
    desc_off: int
    attr_id: int
    attr_type: int
    value_len: int
    value_off: int  # absolute


@dataclass
class YstbUnit:
    path: Path
    attr_index: int
    source: str
    attr_type: int


class YstbError(RuntimeError):
    pass


def _xor_body(data: bytearray, key: int) -> None:
    if not key:
        return
    kb = struct.pack("<I", key)
    for i in range(0x20, len(data)):
        data[i] ^= kb[i & 3]


def _parse_header(data: bytes) -> Tuple[int, int, int, int, int, int]:
    if len(data) < 0x20 or data[:4] != b"YSTB":
        raise YstbError("Not YSTB")
    version, n_instr, instr_sz, desc_sz, vals_sz, lines_sz = struct.unpack_from(
        "<IIIIII", data, 4
    )
    if instr_sz != n_instr * 4:
        raise YstbError("Instruction size mismatch")
    return version, n_instr, instr_sz, desc_sz, vals_sz, lines_sz


def _descriptor_types_ok(data: bytes, desc_off: int, desc_sz: int) -> bool:
    if desc_sz < 12:
        return True
    ok = 0
    bad = 0
    for pos in range(desc_off, desc_off + desc_sz - 11, 12):
        atype = struct.unpack_from("<h", data, pos + 2)[0]
        if atype in (0, 1, 2, 3):
            ok += 1
        else:
            bad += 1
    return ok > 0 and bad == 0


def load_ystb(path: Path | str) -> Tuple[bytearray, int, dict]:
    """Return (decrypted mutable data, xor_key, offsets dict)."""
    path = Path(path)
    raw = bytearray(path.read_bytes())
    version, n_instr, instr_sz, desc_sz, vals_sz, lines_sz = _parse_header(raw)
    instr_off = 0x20
    desc_off = instr_off + instr_sz
    vals_off = desc_off + desc_sz
    lines_off = vals_off + vals_sz
    if lines_off > len(raw):
        raise YstbError("Truncated YSTB")
    # tolerate padded archives
    if lines_off + lines_sz > len(raw):
        lines_sz = max(0, len(raw) - lines_off)

    key = 0
    if desc_sz >= 12:
        candidate = struct.unpack_from("<I", raw, desc_off + 8)[0]
        trial = bytearray(raw)
        if candidate:
            _xor_body(trial, candidate)
        if _descriptor_types_ok(trial, desc_off, desc_sz):
            raw = trial
            key = candidate
        else:
            # already plain?
            if _descriptor_types_ok(raw, desc_off, desc_sz):
                key = 0
            else:
                # last resort: still apply candidate (VNTextPatch behavior)
                if candidate:
                    _xor_body(raw, candidate)
                    key = candidate
    meta = {
        "version": version,
        "n_instr": n_instr,
        "instr_sz": instr_sz,
        "desc_sz": desc_sz,
        "vals_sz": vals_sz,
        "lines_sz": lines_sz,
        "instr_off": instr_off,
        "desc_off": desc_off,
        "vals_off": vals_off,
        "lines_off": lines_off,
    }
    return raw, key, meta


def _iter_attrs(data: bytes, meta: dict) -> List[YstbAttr]:
    out: List[YstbAttr] = []
    pos = meta["desc_off"]
    end = meta["vals_off"]
    vals_off = meta["vals_off"]
    while pos + 12 <= end:
        aid, atype, vlen, vrel = struct.unpack_from("<hhII", data, pos)
        out.append(
            YstbAttr(
                desc_off=pos,
                attr_id=aid,
                attr_type=atype,
                value_len=vlen,
                value_off=vals_off + vrel,
            )
        )
        pos += 12
    return out


def _unquote(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def _quote(s: str, q: str = '"') -> str:
    esc = s.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")
    if q == '"':
        esc = esc.replace('"', '\\"')
    else:
        esc = esc.replace("'", "\\'")
    return f"{q}{esc}{q}"


def _decode_game_text(blob: bytes) -> str:
    """CP932 for JP originals; GBK for our CN patches (CP932 often 'succeeds' on GBK as mojibake)."""
    sj = gk = None
    try:
        sj = blob.decode("cp932")
    except UnicodeDecodeError:
        pass
    try:
        gk = blob.decode("gbk")
    except UnicodeDecodeError:
        pass
    if sj is None and gk is None:
        return blob.decode("cp932", errors="replace")
    if sj is None:
        return gk  # type: ignore[return-value]
    if gk is None or sj == gk:
        return sj
    kana_sj = len(HAS_KANA.findall(sj))
    kana_gk = len(HAS_KANA.findall(gk))
    if kana_sj > kana_gk:
        return sj
    if kana_sj == 0:
        return gk
    return sj


def _encode_game_text(text: str) -> bytes:
    """JP originals are CP932; CN text often needs GBK (你 etc. missing in CP932)."""
    try:
        return text.encode("cp932")
    except UnicodeEncodeError:
        return text.encode("gbk", errors="replace")


def attr_to_text(data: bytes, attr: YstbAttr) -> Optional[str]:
    if attr.value_len <= 0:
        return None
    blob = bytes(data[attr.value_off : attr.value_off + attr.value_len])
    if attr.attr_type == ATTR_RAW:
        return _decode_game_text(blob)
    if attr.attr_type == ATTR_EXPR:
        if len(blob) < 3 or blob[0] != PUSH_STRING:
            return None
        arg_len = struct.unpack_from("<H", blob, 1)[0]
        if 3 + arg_len != len(blob):
            return None
        return _unquote(_decode_game_text(blob[3:]))
    return None


def _looks_dialogue(s: str) -> bool:
    s = s.strip()
    if not s or len(s) < 2:
        return False
    if s.startswith(("ysbin", "cg", "bg", "se", "bgm", "voice", "system")):
        return False
    if re.fullmatch(r"[\d\s\.\-_:/\\]+", s):
        return False
    return bool(HAS_KANA.search(s) or HAS_CJK.search(s))


def collect_units_from_ystb(path: Path, data: bytes, meta: dict) -> List[YstbUnit]:
    units: List[YstbUnit] = []
    attrs = _iter_attrs(data, meta)
    for i, a in enumerate(attrs):
        if a.attr_type not in (ATTR_RAW, ATTR_EXPR):
            continue
        text = attr_to_text(data, a)
        if text is None or not _looks_dialogue(text):
            continue
        # skip pure paths
        if "/" in text or "\\" in text:
            if not HAS_KANA.search(text) and len(text) < 40:
                continue
        units.append(YstbUnit(path=path, attr_index=i, source=text, attr_type=a.attr_type))
    return units


def _encode_attr(attr_type: int, text: str, old_blob: bytes) -> bytes:
    if attr_type == ATTR_RAW:
        return _encode_game_text(text)
    q = '"'
    if old_blob and len(old_blob) > 3 and old_blob[3:4] in (b"'", b'"'):
        q = chr(old_blob[3])
    body = _encode_game_text(_quote(text, q))
    return bytes([PUSH_STRING]) + struct.pack("<H", len(body)) + body


def apply_units_to_ystb(
    data: bytearray, meta: dict, key: int, units: List[YstbUnit], translations: List[str]
) -> bytes:
    from app.core.pipeline_harden import looks_already_chinese

    attrs = _iter_attrs(data, meta)
    updates = {}
    for u, t in zip(units, translations):
        if not t or t == u.source or looks_already_chinese(u.source):
            continue
        updates[u.attr_index] = t
    vals_off = meta["vals_off"]
    lines_off = meta["lines_off"]
    lines = bytes(data[lines_off : lines_off + meta["lines_sz"]])

    new_vals = bytearray()
    desc_patch = bytearray(data[meta["desc_off"] : meta["vals_off"]])
    for i, a in enumerate(attrs):
        old = bytes(data[a.value_off : a.value_off + a.value_len])
        if i in updates:
            blob = _encode_attr(a.attr_type, updates[i], old)
        else:
            blob = old
        new_rel = len(new_vals)
        new_vals += blob
        local = a.desc_off - meta["desc_off"]
        struct.pack_into("<I", desc_patch, local + 4, len(blob))
        struct.pack_into("<I", desc_patch, local + 8, new_rel)

    # Prefer first descriptor offset == 0 so XOR key rediscovery stays stable
    if len(desc_patch) >= 12 and key:
        # already new_rel starts at 0 for first attr; assert
        pass

    out = bytearray()
    out += data[: meta["desc_off"]]
    out += desc_patch
    out += new_vals
    out += lines
    struct.pack_into("<I", out, 0x14, len(new_vals))
    if key:
        _xor_body(out, key)
        # key rediscovery: plaintext offset0 encrypts to key at desc+8
        emb = struct.unpack_from("<I", out, meta["desc_off"] + 8)[0]
        if emb != key:
            # force embed by rewriting offset0 then re-xor that dword only
            # (should not happen if first value offset is 0)
            pass
    return bytes(out)


def process_ybn_collect(path: Path) -> Tuple[List[YstbUnit], bytearray, int, dict]:
    data, key, meta = load_ystb(path)
    units = collect_units_from_ystb(path, data, meta)
    return units, data, key, meta


def is_ystb_file(path: Path) -> bool:
    try:
        raw = path.read_bytes()[:4]
        return raw == b"YSTB"
    except OSError:
        return False
