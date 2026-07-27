# -*- coding: utf-8 -*-
"""Bundled UI assets (icons, etc.)."""
from __future__ import annotations

import sys
from pathlib import Path


def _bundle_root() -> Path:
    # PyInstaller onefile extracts to sys._MEIPASS
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent.parent


def app_icon_path() -> Path | None:
    """Prefer .ico on Windows title bar; fall back to PNG."""
    root = _bundle_root()
    for rel in (
        Path("app") / "assets" / "galautotl.ico",
        Path("app") / "assets" / "galautotl.png",
        Path("assets") / "galautotl.ico",
        Path("assets") / "galautotl.png",
    ):
        p = root / rel
        if p.is_file():
            return p
    # Source tree: app/assets next to this package
    here = Path(__file__).resolve().parent
    for name in ("galautotl.ico", "galautotl.png"):
        p = here / name
        if p.is_file():
            return p
    return None
