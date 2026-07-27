# -*- coding: utf-8 -*-
"""IL2CPP global-metadata.dat UTF-8 string extract + length-preserving patch.

Used for story/ending literals not present in Unity asset bundles.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Set, Tuple

from app.core.unity_raw_text import fit_utf8_bytes

LogFn = Optional[Callable[[str], None]]

_U8_RUN = re.compile(rb"(?:[\x09\x0a\x0d\x20-\x7e]|[\xc2-\xf4][\x80-\xbf]{1,3}){4,2500}")
_HIRA_U8 = re.compile(rb"\xe3(?:\x81[\x81-\xbf]|\x82[\x80-\x9f])")
_BRACKET_U8 = re.compile(rb"\xe3\x80\x8c")  # 「

HAS_KANA = re.compile(r"[\u3040-\u30ff]")
HAS_CJK = re.compile(r"[\u4e00-\u9fff]")


@dataclass
class MetaUnit:
    meta_path: str
    offset: int
    length: int
    source: str


def find_metadata(game_dir: Path) -> Optional[Path]:
    cands = list(game_dir.glob("*_Data/il2cpp_data/Metadata/global-metadata.dat"))
    cands += list(game_dir.glob("il2cpp_data/Metadata/global-metadata.dat"))
    cands += list(game_dir.rglob("global-metadata.dat"))
    for p in cands:
        if p.is_file() and p.stat().st_size > 1024:
            return p
    return None


def _want(s: str, *, loose: bool = False) -> bool:
    s = s.strip()
    if len(s) < (2 if loose else 4):
        return False
    # JSON / input-system / code dumps
    if "{" in s[:40] and ('"' in s or "'" in s):
        return False
    if '"name"' in s or '"path"' in s or "VirtualMouse" in s:
        return False
    low = s.lower()
    if low.startswith(
        (
            "assets/",
            "character/",
            "background/",
            "audio/",
            "resources/",
            "prefab",
            "get_",
            "set_",
            "m_",
            "system.",
            "unityengine.",
        )
    ):
        return False
    if "/" in s and not (("「" in s) or ("。" in s) or ("、" in s)):
        if re.search(r"\.(png|jpg|wav|ogg|mp3|prefab|unity|controller|anim)$", low):
            return False
        if s.count("/") >= 1 and not HAS_KANA.search(s[:20] if len(s) > 20 else s):
            return False
    if re.fullmatch(r"[A-Za-z0-9_.:/\\<>\[\]\-\"'\s\{\},]+", s):
        return False
    kana = HAS_KANA.findall(s)
    cjk = HAS_CJK.findall(s)
    if not kana and "「" not in s:
        if loose and cjk and len(s) <= 48:
            return True
        return False
    if s.count(".") >= 8 and sum(1 for c in s if c.isdigit()) >= 16:
        return False
    if any(
        x in s
        for x in (
            "FontAsset",
            "Attempted to",
            "Exception",
            "StackTrace",
            "UnityEngine",
            "System.",
        )
    ):
        return False
    if len(s) > 120 and len(set(kana)) > 50 and (s.count("。") + s.count("「")) < 1:
        return False
    ascii_n = sum(1 for c in s if ord(c) < 128)
    if ascii_n > len(s) * 0.7 and len(kana) < (2 if loose else 4):
        return False
    jp_n = len(kana) + len(cjk)
    if jp_n < (2 if loose else 4):
        return False
    if not loose and jp_n / max(len(s), 1) < 0.12 and "「" not in s and "。" not in s:
        return False
    if not (HAS_KANA.search(s) or ("「" in s and HAS_CJK.search(s)) or (loose and cjk)):
        return False
    if len(s) >= 8 or "「" in s or "。" in s or "！" in s or "？" in s:
        return True
    return len(kana) >= (2 if loose else 3)


def collect_meta_units(
    game_dir: Path, log: LogFn = None, *, for_runtime: bool = False
) -> List[MetaUnit]:
    mp = find_metadata(game_dir)
    if not mp:
        if log:
            log("未找到 global-metadata.dat（非 IL2CPP 或路径不同）")
        return []
    data = mp.read_bytes()
    if log:
        log(f"扫描 IL2CPP 元数据: {mp.name} ({len(data) // 1024} KB)")

    units: List[MetaUnit] = []
    seen_off: Set[int] = set()
    seen_text: Set[str] = set()
    for m in _U8_RUN.finditer(data):
        frag = m.group(0)
        if not (_HIRA_U8.search(frag) or _BRACKET_U8.search(frag)):
            continue
        try:
            text = frag.decode("utf-8")
        except UnicodeDecodeError:
            continue
        core = text.strip(" \t\r\n\x00")
        if not _want(core, loose=for_runtime):
            continue
        enc = core.encode("utf-8")
        idx = frag.find(enc)
        if idx < 0:
            continue
        off = m.start() + idx
        if off in seen_off:
            continue
        if data[off : off + len(enc)] != enc:
            continue
        seen_off.add(off)

        chunks: List[Tuple[int, str]] = [(off, core)]
        # Runtime dict: split glued heap strings into sentence/line keys XUA can match
        if for_runtime and (len(core) > 40 or core.count("\n") >= 1 or core.count("。") >= 1):
            chunks = []
            pos = 0
            for part in re.split(r"(?<=[。！？\n、])", core):
                piece = part.strip(" \t\r\n\x00")
                if not piece or not _want(piece, loose=True):
                    pos += len(part)
                    continue
                rel = core.find(piece, max(0, pos - 2))
                if rel < 0:
                    rel = pos
                chunks.append((off + rel, piece))
                pos = rel + len(piece)
            if not chunks:
                chunks = [(off, core)]

        for c_off, piece in chunks:
            if for_runtime:
                if piece in seen_text:
                    continue
                if len(piece) > 2500:
                    continue
                seen_text.add(piece)
            units.append(
                MetaUnit(
                    meta_path=str(mp.resolve()),
                    offset=c_off,
                    length=len(piece.encode("utf-8")),
                    source=piece,
                )
            )

    if log:
        log(f"元数据待译条目: {len(units)}" + ("（运行时拆句）" if for_runtime else ""))
    return units


def apply_meta_units(units: List[MetaUnit], translations: List[str], log: LogFn = None) -> int:
    if not units:
        return 0
    path = Path(units[0].meta_path)
    if not path.is_file():
        return 0
    bak = path.with_suffix(path.suffix + ".galautotl.bak")
    if not bak.exists():
        shutil.copy2(path, bak)
        if log:
            log(f"备份: {bak.name}")
    data = bytearray(path.read_bytes())
    n = 0
    for u, t in sorted(zip(units, translations), key=lambda x: -x[0].offset):
        nb = fit_utf8_bytes(t, u.length, pad=b"\x00")
        if u.offset + len(nb) > len(data):
            continue
        data[u.offset : u.offset + len(nb)] = nb
        n += 1
    path.write_bytes(bytes(data))
    if log:
        log(f"已写回元数据 {n} 处 → {path.name}")
    return 1 if n else 0
