# -*- coding: utf-8 -*-
"""Kirikiri: harvest .txt/.csv/.tsv sitting next to scenario dialogue .ks."""
from __future__ import annotations

from pathlib import Path
from typing import List

from app.core.kirikiri_patch import (
    ENGINE_KS_DIRS,
    MACRO_KS_DIRS,
    is_dialogue_ks_relpath,
)
from app.pipelines.generic_text import WorkItem, collect_items


_SIDECAR_EXT = {".txt", ".csv", ".tsv"}


def collect_scenario_sidecars(
    scripts_root: Path, source_lang: str = "ja"
) -> List[WorkItem]:
    """Text tables under scenario / k_scenario only (never script/system)."""
    root = Path(scripts_root)
    if not root.is_dir():
        return []
    # Prefer collecting per dialogue folder so engine trees are skipped
    dialogue_dirs = set()
    for ks in root.rglob("*.ks"):
        try:
            rel = ks.relative_to(root)
        except ValueError:
            continue
        parts_l = {p.lower() for p in rel.parts}
        if parts_l & (set(ENGINE_KS_DIRS) | set(MACRO_KS_DIRS)):
            continue
        if is_dialogue_ks_relpath(rel):
            dialogue_dirs.add(ks.parent)

    items: List[WorkItem] = []
    seen_files = set()
    for d in sorted(dialogue_dirs):
        for p in d.iterdir():
            if not p.is_file() or p.suffix.lower() not in _SIDECAR_EXT:
                continue
            if p.resolve() in seen_files:
                continue
            seen_files.add(p.resolve())
            # Reuse generic line/csv collector on a one-file root via parent scan filter
            chunk = [
                it
                for it in collect_items(d, source_lang=source_lang)
                if it.path.resolve() == p.resolve()
            ]
            items.extend(chunk)
    return items
