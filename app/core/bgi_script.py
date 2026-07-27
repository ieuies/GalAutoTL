# -*- coding: utf-8 -*-
"""BGI / Ethornell scenario script string extract & patch.

Pointer math from mchubby Bgi_script_tools:
  text_addr = dword - code_size  (relative into trailing text pool).
  String ops are STR_TYPE (0x3) immediately before the address dword.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

import re as _re

HAS_KANA = _re.compile(r"[\u3040-\u30ff]")
HAS_CJK = _re.compile(r"[\u4e00-\u9fff]")
HAS_HALF_KATA = _re.compile(r"[\uff61-\uff9f]")

MAGIC = b"BurikoCompiledScriptVer1.00\x00"
STR_TYPE = 0x3
TEXT_FCN = 0x140
BKLG_FCN = 0x143
RUBY_FCN = 0x14B
# ver100 offsets of TEXT_FCN relative to string addr dword
NAME_POS = 0x0C
TEXT_POS = 0x04
RUBYK_POS = 0x04
RUBYF_POS = 0x0C
BKLG_POS = 0x0C


@dataclass
class BgiUnit:
    path: Path
    code_pos: int  # absolute file offset of the string-address dword
    source: str
    marker: str  # N | T | Z


def _hdr_size(data: bytes) -> int:
    if data.startswith(MAGIC):
        return 0x1C + struct.unpack_from("<I", data, 0x1C)[0]
    return 0


def _section_boundary(data: bytes) -> int:
    pos = -1
    cur = 0
    while True:
        res = data.find(b"\x1b\x00\x00\x00", cur)
        if res < 0:
            break
        pos = res
        cur = res + 1
    if pos < 0:
        return len(data)
    return pos + 4


def _decode_text(blob: bytes) -> str:
    """Round-trip decode. When CP932/GBK both fit but disagree, prefer real JP/CN text."""
    if not blob:
        return ""
    opts: Dict[str, str] = {}
    for enc in ("cp932", "gbk"):
        try:
            s = blob.decode(enc)
            if s.encode(enc) == blob:
                opts[enc] = s
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
    if not opts:
        return blob.decode("cp932", errors="replace")
    if len(opts) == 1:
        return next(iter(opts.values()))
    a = opts["cp932"]
    b = opts["gbk"]
    if a == b:
        return a
    # Fullwidth kana → Japanese CP932
    if HAS_KANA.search(a) and not HAS_KANA.search(b):
        return a
    if HAS_KANA.search(b) and not HAS_KANA.search(a):
        return b
    # Halfwidth kata mojibake from misreading GBK as CP932
    if HAS_HALF_KATA.search(a) and not HAS_HALF_KATA.search(b):
        return b
    if HAS_HALF_KATA.search(b) and not HAS_HALF_KATA.search(a):
        return a
    # Divergent CJK (e.g. 你好): prefer GBK
    return b


def _encode_text(text: str) -> bytes:
    try:
        return text.encode("cp932")
    except UnicodeEncodeError:
        return text.encode("gbk", errors="replace")

def _want(s: str) -> bool:
    s = s.strip()
    if len(s) < 1:
        return False
    low = s.lower()
    if low.startswith(("data", "sys", "voice", "bgm", "se\\", "se/", "graph", "effect")):
        return False
    if "\\" in s or "/" in s:
        # path-like
        if "." in s.split("/")[-1].split("\\")[-1]:
            return False
    return bool(HAS_KANA.search(s) or HAS_CJK.search(s))


def _check_fcn(code: bytes, pos: int, fcn: Optional[int], rel: int) -> bool:
    if fcn is None:
        return False
    off = pos + rel
    if off + 4 > len(code):
        return False
    return struct.unpack_from("<I", code, off)[0] == fcn


def is_bgi_script(path: Path) -> bool:
    try:
        raw = path.read_bytes()[:0x20]
    except OSError:
        return False
    return raw.startswith(MAGIC)


def collect_bgi_units(path: Path) -> List[BgiUnit]:
    data = path.read_bytes()
    hdr = _hdr_size(data)
    bound = _section_boundary(data)
    if bound <= hdr or bound >= len(data):
        return []
    code = data[hdr:bound]
    text = data[bound:]
    pool: Dict[int, str] = {}
    pos = 0
    for p in text.split(b"\x00"):
        pool[pos] = _decode_text(p)
        pos += len(p) + 1

    units: List[BgiUnit] = []
    seen: Set[int] = set()
    code_size = len(code)
    i = 4
    while i + 4 <= code_size:
        typ = struct.unpack_from("<I", code, i - 4)[0]
        dword = struct.unpack_from("<I", code, i)[0]
        text_addr = dword - code_size
        if text_addr in pool and typ == STR_TYPE:
            s = pool[text_addr]
            if _want(s) and i not in seen:
                if _check_fcn(code, i, TEXT_FCN, NAME_POS):
                    marker = "N"
                elif _check_fcn(code, i, TEXT_FCN, TEXT_POS):
                    marker = "T"
                elif _check_fcn(code, i, RUBY_FCN, RUBYK_POS) or _check_fcn(
                    code, i, RUBY_FCN, RUBYF_POS
                ):
                    marker = "T"
                elif _check_fcn(code, i, BKLG_FCN, BKLG_POS):
                    marker = "T"
                else:
                    marker = "Z"
                    # skip short path-like Z noise already filtered by _want
                units.append(
                    BgiUnit(path=path, code_pos=hdr + i, source=s, marker=marker)
                )
                seen.add(i)
        i += 4
    return units


def apply_bgi_units(path: Path, units: List[BgiUnit], translations: List[str]) -> bytes:
    from app.core.pipeline_harden import looks_already_chinese

    data = path.read_bytes()
    hdr = _hdr_size(data)
    bound = _section_boundary(data)
    code = bytearray(data[hdr:bound])
    code_size = len(code)
    # rebuild text pool: keep unused originals, append/reuse translations
    text_pool = bytearray(data[bound:])
    cache: Dict[str, int] = {}
    for u, t in zip(units, translations):
        if looks_already_chinese(u.source) or not t:
            t = u.source
        rel_off = u.code_pos - hdr
        if t not in cache:
            cache[t] = len(text_pool)
            text_pool += _encode_text(t) + b"\x00"
        struct.pack_into("<I", code, rel_off, code_size + cache[t])
    return data[:hdr] + bytes(code) + bytes(text_pool)


def find_bgi_scripts(root: Path) -> List[Path]:
    from app.core.workdirs import is_nested_galautotl_part, rel_parts_under

    out: List[Path] = []
    root_res = root.resolve()
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = rel_parts_under(root_res, p)
        if rel is None:
            continue
        if is_nested_galautotl_part(rel):
            continue
        name = p.name.lower()
        if name.endswith("._bp") or name.endswith(".bp"):
            continue
        if p.suffix.lower() in (
            ".arc",
            ".exe",
            ".dll",
            ".ogg",
            ".wav",
            ".png",
            ".jpg",
            ".bmp",
            ".avi",
            ".wmv",
        ):
            continue
        try:
            head = p.read_bytes()[:0x20]
        except OSError:
            continue
        if head.startswith(MAGIC):
            out.append(p)
            continue
        # headerless scenario (ver000)
        if p.suffix == "" and 256 < p.stat().st_size < 20_000_000:
            raw = p.read_bytes()
            if b"\x1b\x00\x00\x00" in raw[: min(len(raw), 5_000_000)]:
                out.append(p)
    return sorted(out)
