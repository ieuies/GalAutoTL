# -*- coding: utf-8 -*-
"""Helpers for GalAutoTL work directories (_galautotl_*).

Lesson (Artemis / Desktop\\222): never skip files solely because the *absolute*
path contains ``_galautotl_`` — when the collect root *is* the work tree, that
wipes every unit and looks like \"engine unsupported\".
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence


def is_nested_galautotl_part(parts: Sequence[str]) -> bool:
    """True if any path part is a nested work dir (relative to collect root)."""
    return any(str(p).lower().startswith("_galautotl_") for p in parts)


def rel_parts_under(root: Path, path: Path) -> tuple[str, ...] | None:
    try:
        return path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        return None
