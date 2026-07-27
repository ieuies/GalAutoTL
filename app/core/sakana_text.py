# -*- coding: utf-8 -*-
"""Collect / apply translatable strings from SakanaGL extracted files."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

HAS_KANA = re.compile(r"[\u3040-\u30ff]")
HAS_CJK = re.compile(r"[\u4e00-\u9fff]")
HAS_LATIN = re.compile(r"[A-Za-z]{3,}")

POISON = ("无法识别", "疑似乱码", "按原文输出", "无法翻译")


@dataclass
class SakanaUnit:
    rel: str
    encoding: str  # utf-8 | utf-16-le | cp932
    kind: str  # line | json
    meta: object
    source: str


def _want(s: str, source_lang: str = "ja") -> bool:
    s = (s or "").strip()
    if len(s) < 1 or len(s) > 500:
        return False
    if re.fullmatch(r"[\d\s\.\-_:/\\]+", s):
        return False
    if source_lang == "en":
        return bool(HAS_LATIN.search(s))
    return bool(HAS_KANA.search(s) or HAS_CJK.search(s))


def _decode(data: bytes) -> Tuple[str, str]:
    if data.startswith(b"\xff\xfe"):
        return data.decode("utf-16-le"), "utf-16-le"
    if data.startswith(b"\xfe\xff"):
        return data.decode("utf-16-be"), "utf-16-be"
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    # UTF-16 LE without BOM heuristic
    if len(data) >= 4 and data[1] == 0 and data[3] == 0:
        try:
            return data.decode("utf-16-le"), "utf-16-le"
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8", "cp932"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8"


def _encode(text: str, encoding: str) -> bytes:
    if encoding == "utf-16-le":
        return b"\xff\xfe" + text.encode("utf-16-le")
    if encoding == "utf-16-be":
        return b"\xfe\xff" + text.encode("utf-16-be")
    if encoding == "utf-8-sig":
        return text.encode("utf-8-sig")
    try:
        return text.encode(encoding)
    except UnicodeEncodeError:
        return text.encode("utf-8")


def _walk_json(obj, path: str = "") -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(_walk_json(v, f"{path}.{k}" if path else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_walk_json(v, f"{path}[{i}]"))
    elif isinstance(obj, str):
        out.append((path, obj))
    return out


def _set_json_path(obj, path: str, value: str) -> None:
    # simple path: a.b[0].c
    parts = re.findall(r"[^.\[\]]+|\[\d+\]", path)
    cur = obj
    for i, part in enumerate(parts[:-1]):
        if part.startswith("["):
            cur = cur[int(part[1:-1])]
        else:
            cur = cur[part]
    last = parts[-1]
    if last.startswith("["):
        cur[int(last[1:-1])] = value
    else:
        cur[last] = value


def collect_sakana_units(root: Path, source_lang: str = "ja") -> List[SakanaUnit]:
    units: List[SakanaUnit] = []
    root = Path(root)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith("_"):
            continue
        rel = path.relative_to(root).as_posix()
        try:
            data = path.read_bytes()
        except Exception:
            continue
        if len(data) > 8_000_000:
            continue
        text, enc = _decode(data)
        suf = path.suffix.lower()
        if suf == ".json":
            try:
                obj = json.loads(text)
            except Exception:
                obj = None
            if obj is not None:
                for jpath, val in _walk_json(obj):
                    if _want(val, source_lang):
                        units.append(SakanaUnit(rel, enc, "json", jpath, val))
                continue
        # line-based for other text
        if suf in {".png", ".jpg", ".jpeg", ".ogg", ".wav", ".mp3", ".webp", ".dds"}:
            continue
        lines = text.splitlines(keepends=True)
        # if binary-ish skip
        if "\x00" in text and enc.startswith("utf-8"):
            continue
        for i, line in enumerate(lines):
            body = line.rstrip("\r\n")
            if _want(body, source_lang):
                units.append(SakanaUnit(rel, enc, "line", i, body))
    return units


def apply_sakana_units(root: Path, units: List[SakanaUnit], translated: List[str]) -> int:
    from collections import defaultdict

    from app.core.pipeline_harden import looks_already_chinese

    by_file: dict[str, List[Tuple[SakanaUnit, str]]] = defaultdict(list)
    for u, t in zip(units, translated):
        if looks_already_chinese(u.source):
            continue
        if not t or any(p in t for p in POISON):
            continue
        if t == u.source:
            continue
        by_file[u.rel].append((u, t))

    touched = 0
    for rel, group in by_file.items():
        path = root / rel
        if not path.is_file():
            continue
        data = path.read_bytes()
        text, enc = _decode(data)
        # prefer unit encoding
        enc = group[0][0].encoding or enc

        json_items = [(u, t) for u, t in group if u.kind == "json"]
        line_items = [(u, t) for u, t in group if u.kind == "line"]

        if json_items:
            try:
                obj = json.loads(text)
            except Exception:
                obj = None
            if obj is not None:
                for u, t in json_items:
                    try:
                        _set_json_path(obj, str(u.meta), t)
                    except Exception:
                        pass
                text = json.dumps(obj, ensure_ascii=False, indent=2)
                if data.endswith(b"\n"):
                    text += "\n"

        if line_items:
            lines = text.splitlines(keepends=True)
            for u, t in line_items:
                i = int(u.meta)
                if i >= len(lines):
                    continue
                ending = ""
                raw = lines[i]
                if raw.endswith("\r\n"):
                    ending = "\r\n"
                elif raw.endswith("\n"):
                    ending = "\n"
                lines[i] = t + ending
            text = "".join(lines)

        path.write_bytes(_encode(text, enc))
        touched += 1
    return touched
