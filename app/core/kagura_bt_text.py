# -*- coding: utf-8 -*-
"""Debonosu/Kagura btText.dat (battle/UI string table) parse + rebuild."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class BtTextEntry:
    kind: str  # "id" | "bare"
    text: str
    id: str = ""
    # original encoded text bytes length (for soft-fit stats)
    orig_text_len: int = 0


def _is_digits(b: bytes) -> bool:
    return bool(b) and all(48 <= x <= 57 for x in b)


def parse_bt_text(data: bytes) -> Tuple[bytes, List[BtTextEntry], bytes]:
    """Return (header, entries, tail_blob).

    Layout:
      header (3 bytes) +
      id-records: (u8 id_len)(ascii id)(u8 text_len)(cp932 text)  [empty slots omit text] +
      bare-records: (u8 text_len)(cp932 text) until a 0 length +
      opaque tail (binary / padding) — must be preserved verbatim
    """
    if len(data) < 4:
        return data, [], b""
    header = data[:3]
    i = 3
    entries: List[BtTextEntry] = []

    # Phase 1: id records
    while i < len(data):
        id_len = data[i]
        if not (1 <= id_len <= 16):
            break
        if i + 1 + id_len > len(data):
            break
        id_b = data[i + 1 : i + 1 + id_len]
        if not _is_digits(id_b):
            break
        pos = i + 1 + id_len
        if pos >= len(data):
            break
        text_len = data[pos]
        if text_len == 0:
            entries.append(BtTextEntry("id", "", id_b.decode("ascii"), 0))
            i = pos + 1
            continue
        if pos + 1 + text_len > len(data):
            break
        text_b = data[pos + 1 : pos + 1 + text_len]
        if _is_digits(text_b) and 1 <= text_len <= 16:
            entries.append(BtTextEntry("id", "", id_b.decode("ascii"), 0))
            i = pos
            continue
        try:
            text = text_b.decode("cp932")
        except UnicodeDecodeError:
            break
        entries.append(BtTextEntry("id", text, id_b.decode("ascii"), text_len))
        i = pos + 1 + text_len

    # Phase 2: bare length-prefixed strings
    while i < len(data):
        text_len = data[i]
        if text_len == 0:
            break
        if i + 1 + text_len > len(data):
            break
        text_b = data[i + 1 : i + 1 + text_len]
        try:
            text = text_b.decode("cp932")
        except UnicodeDecodeError:
            break
        entries.append(BtTextEntry("bare", text, "", text_len))
        i += 1 + text_len

    tail = data[i:]  # includes the terminating 0 and binary blob
    return header, entries, tail


def build_bt_text(header: bytes, entries: List[BtTextEntry], tail: bytes = b"") -> bytes:
    out = bytearray(header[:3] if len(header) >= 3 else (header + b"\x00\x01\x31")[:3])
    for e in entries:
        text_b = e.text.encode("cp932", errors="strict") if e.text else b""
        if e.kind == "id":
            id_b = e.id.encode("ascii")
            if not (1 <= len(id_b) <= 16) or not _is_digits(id_b):
                raise ValueError(f"bad id: {e.id!r}")
            out.append(len(id_b))
            out.extend(id_b)
            if not text_b:
                continue
            if len(text_b) > 255:
                text_b = text_b[:255]
            out.append(len(text_b))
            out.extend(text_b)
        else:
            if not text_b:
                continue
            if len(text_b) > 255:
                text_b = text_b[:255]
            out.append(len(text_b))
            out.extend(text_b)
    out.extend(tail if tail is not None else b"\x00")
    return bytes(out)


def collect_bt_texts(data: bytes) -> List[str]:
    _h, entries, _t = parse_bt_text(data)
    # unique preserve order
    seen = set()
    out: List[str] = []
    for e in entries:
        s = e.text.strip()
        if not s or s in seen:
            continue
        if not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", s):
            continue
        seen.add(s)
        out.append(e.text)  # keep original spacing
    return out


def apply_bt_texts(data: bytes, mapping: dict[str, str]) -> Tuple[bytes, int]:
    header, entries, tail = parse_bt_text(data)
    n = 0
    for e in entries:
        if not e.text:
            continue
        # exact / strip match
        dst = mapping.get(e.text)
        if dst is None:
            core = e.text.strip()
            if core in mapping:
                lead = e.text[: len(e.text) - len(e.text.lstrip())]
                trail = e.text[len(e.text.rstrip()) :]
                dst = f"{lead}{mapping[core]}{trail}"
        if not dst or dst == e.text:
            continue
        try:
            from app.core.cp932_safe import to_cp932_safe

            dst = to_cp932_safe(dst)
        except Exception:
            pass
        try:
            dst.encode("cp932")
        except UnicodeEncodeError:
            dst2 = "".join(
                ch if ch.encode("cp932", errors="ignore") else "" for ch in dst
            )
            try:
                dst2.encode("cp932")
                dst = dst2
            except UnicodeEncodeError:
                continue
        # Reject near-total・ mangling (bad SC→JP remap)
        if dst and dst.count("・") >= max(3, len(dst.replace(" ", "")) // 2):
            continue
        if dst != e.text:
            e.text = dst
            n += 1
    return build_bt_text(header, entries, tail), n
