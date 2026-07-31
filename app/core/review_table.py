# -*- coding: utf-8 -*-
"""JP↔CN review table for human proofreading (汉化组对照表 workflow).

Edit CN after AI pass, then re-run 开始汉化 — same index / JP prefer this table.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

REVIEW_NAME = "GalAutoTL_review.txt"
REVIEW_NAMES = (
    REVIEW_NAME,
    "对照表.txt",
    "review.txt",
)

_BLOCK_RE = re.compile(
    r"###\s*(\d+)\s*\nJP:\s*(.*?)\nCN:\s*(.*?)(?=\n###\s*\d+\s*\n|\Z)",
    re.DOTALL,
)

# Glossary placeholder / numeral corruption — never 灌回 these into live CN


def is_corrupt_review_cn(cn: str) -> bool:
    """True if CN still has broken glossary placeholders or 0-corruption."""
    if not cn:
        return False
    try:
        from app.core.glossary import has_glossary_leak

        return has_glossary_leak(cn)
    except Exception:
        return bool(
            re.search(
                r"\{\{\s*GALTL|GALTL\d|⟦GALTL|0夏|0个人|0声",
                cn,
                re.I,
            )
        )


def review_path(game_dir: Path | str) -> Path:
    return Path(game_dir) / REVIEW_NAME


def find_review_file(game_dir: Optional[Path | str]) -> Optional[Path]:
    if not game_dir:
        return None
    root = Path(game_dir)
    if not root.is_dir():
        return None
    for name in REVIEW_NAMES:
        p = root / name
        if p.is_file():
            return p
    return None


def _escape_body(s: str) -> str:
    """Escape so literal \\n in scripts is not confused with real newlines."""
    t = (s or "").replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\\", "\\\\").replace("\n", "\\n")
    return t


def _unescape_body(s: str) -> str:
    out: list[str] = []
    i = 0
    t = s or ""
    while i < len(t):
        if t[i] == "\\" and i + 1 < len(t):
            nxt = t[i + 1]
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
            if nxt == "\\":
                out.append("\\")
                i += 2
                continue
        out.append(t[i])
        i += 1
    return "".join(out)


def export_review_table(
    game_dir: Path | str,
    sources: Sequence[str],
    translations: Sequence[str],
    *,
    header_note: str = "",
) -> Path:
    """Write human-editable JP/CN blocks.

    Merges with any existing GalAutoTL_review.txt so a leak-pass / second
    ``translate_batch`` cannot wipe unrelated hand-edited rows.
    """
    root = Path(game_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / REVIEW_NAME

    existing = load_review_overrides(root)
    # Preserve prior file order for keys not in this batch
    prior_order: list[str] = []
    seen_prior: set[str] = set()
    old_path = find_review_file(root)
    if old_path and old_path.is_file():
        for m in _BLOCK_RE.finditer(
            old_path.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff") + "\n"
        ):
            src = _unescape_body(m.group(2).strip())
            if src and src not in seen_prior:
                prior_order.append(src)
                seen_prior.add(src)

    merged: Dict[str, str] = dict(existing)
    batch_order: list[str] = []
    batch_seen: set[str] = set()
    n = min(len(sources), len(translations))
    for i in range(n):
        src = sources[i]
        dst = translations[i]
        if src is None or not str(src).strip():
            continue
        src_s = str(src)
        dst_s = "" if dst is None else str(dst)
        old = merged.get(src_s, "")
        # Keep hand-edited / prior good CN when this batch has a hole (empty or JP)
        if old.strip() and old != src_s and (not dst_s.strip() or dst_s == src_s):
            pass
        elif dst_s.strip() and not is_corrupt_review_cn(dst_s):
            merged[src_s] = dst_s
        elif dst_s.strip() and is_corrupt_review_cn(dst_s):
            # Never persist placeholder debris; drop prior corrupt too
            if is_corrupt_review_cn(old):
                merged.pop(src_s, None)
            # else keep prior good CN
        elif src_s not in merged:
            merged[src_s] = dst_s
        if src_s not in batch_seen:
            batch_order.append(src_s)
            batch_seen.add(src_s)

    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for src_s in batch_order:
        if src_s in merged and src_s not in seen:
            ordered.append((src_s, merged[src_s]))
            seen.add(src_s)
    for src_s in prior_order:
        if src_s in merged and src_s not in seen:
            ordered.append((src_s, merged[src_s]))
            seen.add(src_s)
    for src_s, cn in merged.items():
        if src_s not in seen:
            ordered.append((src_s, cn))
            seen.add(src_s)

    lines = [
        "# GalAutoTL 对照表（人工校对用）",
        "# 优先按 ### 编号灌回（与提取顺序一致）；编号对不上时再按 JP 全文匹配。",
        "# 只改 CN: 行；对照表不要保留 ⟦GALTL_A⟧ / {{GALTL0}} / 0夏 等损坏痕迹。",
        "# 多行已写成 \\n；反斜杠写成 \\\\。不要删 ### / JP: / CN: 行头。",
        "# 也可追加一行式：原文<=>译文",
        "# 再次导出 / 漏翻二扫会与本文合并，不会冲掉未改动的条目。",
        "#",
    ]
    if header_note:
        lines.append(f"# {header_note}")
        lines.append("#")
    for i, (src_s, cn) in enumerate(ordered, start=1):
        lines.append(f"### {i}")
        lines.append(f"JP: {_escape_body(src_s)}")
        lines.append(f"CN: {_escape_body(cn)}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def load_review_maps(
    game_dir: Optional[Path | str],
) -> Tuple[Dict[int, Tuple[str, str]], Dict[str, str]]:
    """
    Returns (by_index_0based -> (jp, cn), by_exact_src -> cn).

    Index entries MUST be applied only when current JP equals stored JP.
    (Multi-file engines like RealLive export/reload per scene; bare index
    reuse would paste the previous scene's CN onto the next scene.)
    """
    path = find_review_file(game_dir)
    if not path:
        return {}, {}
    text = path.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
    by_idx: Dict[int, Tuple[str, str]] = {}
    by_src: Dict[str, str] = {}

    for m in _BLOCK_RE.finditer(text + "\n"):
        try:
            num = int(m.group(1))
        except ValueError:
            continue
        src = _unescape_body(m.group(2).strip())
        dst = _unescape_body(m.group(3).strip())
        if not src or not dst:
            continue
        if is_corrupt_review_cn(dst):
            continue
        try:
            from app.core.xua_display_text import (
                is_misaligned_ui_pair,
                is_script_shell_key,
                pair_has_index_leak,
            )

            if is_script_shell_key(src) or pair_has_index_leak(src, dst) or is_misaligned_ui_pair(
                src, dst
            ):
                continue
        except Exception:
            # 完整性校验失败时保守放行（走 JP/CN 全文匹配兜底）
            pass
        by_idx[num - 1] = (src, dst)
        by_src[src] = dst

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("###"):
            continue
        if line.startswith("JP:") or line.startswith("CN:"):
            continue
        if "<=>" in line:
            a, b = line.split("<=>", 1)
        elif "====" in line:
            a, b = line.split("====", 1)
        else:
            continue
        src, dst = _unescape_body(a.strip()), _unescape_body(b.strip())
        if src and dst and not is_corrupt_review_cn(dst):
            by_src[src] = dst
    return by_idx, by_src


def load_review_overrides(game_dir: Optional[Path | str]) -> Dict[str, str]:
    """Backward-compatible: exact JP → CN only."""
    _by_idx, by_src = load_review_maps(game_dir)
    return by_src


def resolve_review_override(
    index: int,
    src: str,
    by_idx: Dict[int, Tuple[str, str]],
    by_src: Dict[str, str],
) -> Optional[str]:
    """Prefer index when JP matches; else exact JP map. Never index-only."""
    hit = by_idx.get(index)
    if hit is not None:
        jp, cn = hit
        if jp == src and cn.strip() and not is_corrupt_review_cn(cn):
            return cn
    cn = by_src.get(src)
    if cn is not None and cn.strip() and not is_corrupt_review_cn(cn):
        return cn
    return None
