# -*- coding: utf-8 -*-
"""Scan scripts for image-based UI references (cannot auto-translate pixels)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Set

LogFn = Optional[Callable[[str], None]]

IMAGE_UI_REPORT = "GalAutoTL_image_ui.txt"

# graphic="btn.png" / storage=title.tlg / @image file=...
_ATTR_IMG = re.compile(
    r"""\b(?:graphic|storage|source|file|image|img|face|bg|button|thumb|icon)\s*=\s*"""
    r"""(?P<q>["']?)(?P<val>[^"'\s\]]+\.(?:png|tlg|jpg|jpeg|bmp|webp|dds|gif))(?P=q)""",
    re.IGNORECASE,
)
_TAG_IMG = re.compile(
    r"""\[(?:image|button|bg|fg|layopt|trans)\b[^\]]*\]""",
    re.IGNORECASE,
)

_TEXT_EXTS = {".ks", ".tjs", ".txt", ".csv", ".tsv", ".json", ".yml", ".yaml", ".rpy"}


@dataclass
class ImageUiHit:
    rel: str
    line_no: int
    asset: str
    snippet: str


def _iter_text_files(root: Path):
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in _TEXT_EXTS:
            continue
        # skip our own reports / work dumps
        name = p.name.lower()
        if name.startswith("galautotl"):
            continue
        if "_galautotl_" in str(p).lower() and "scripts" not in p.parts:
            # allow work scripts tree
            pass
        yield p


def scan_image_ui_refs(root: Path, *, max_hits: int = 5000) -> List[ImageUiHit]:
    """Collect image asset references that often carry painted text."""
    root = Path(root)
    hits: List[ImageUiHit] = []
    seen: Set[str] = set()
    for path in _iter_text_files(root):
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        # skip obvious binary
        if b"\x00" in raw[:200] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            try:
                text = raw.decode("utf-16-le")
            except Exception:
                continue
        else:
            for enc in ("utf-8-sig", "utf-8", "cp932", "gbk"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    text = None
            else:
                continue
        try:
            rel = str(path.relative_to(root)).replace("\\", "/")
        except ValueError:
            rel = str(path)
        for li, line in enumerate(text.splitlines(), 1):
            for m in _ATTR_IMG.finditer(line):
                asset = m.group("val").replace("\\", "/")
                key = f"{rel}|{asset}"
                if key in seen:
                    continue
                seen.add(key)
                hits.append(
                    ImageUiHit(
                        rel=rel,
                        line_no=li,
                        asset=asset,
                        snippet=line.strip()[:160],
                    )
                )
                if len(hits) >= max_hits:
                    return hits
    return hits


def write_image_ui_report(
    out_dir: Path,
    hits: List[ImageUiHit],
    *,
    log: LogFn = None,
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / IMAGE_UI_REPORT
    lines = [
        "# GalAutoTL 图片 UI 清单（机翻改不了像素字）",
        f"# count={len(hits)}",
        "# 做法：GARbro/解包 → 按 asset 改图 → 松散覆盖或 patch",
        "# 本文件仅列脚本里引用到的图，不保证每张都有字",
        "",
    ]
    for i, h in enumerate(hits, 1):
        lines.append(f"--- {i} ---")
        lines.append(f"asset: {h.asset}")
        lines.append(f"from: {h.rel}:{h.line_no}")
        lines.append(f"line: {h.snippet}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    if log:
        log(f"图片 UI 清单: {len(hits)} 条 → {IMAGE_UI_REPORT}")
    return path
