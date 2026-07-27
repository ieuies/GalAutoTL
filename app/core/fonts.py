# -*- coding: utf-8 -*-
"""Pick a Chinese-capable UI/game font and optionally copy into game folder."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

# Prefer fonts that cover simplified + traditional CJK well
CANDIDATE_NAMES = (
    "msyh.ttc",  # 微软雅黑
    "msyh.ttf",
    "msyhbd.ttc",
    "msyhl.ttc",
    "simhei.ttf",  # 黑体
    "simsun.ttc",  # 宋体
    "SIMSUN.TTC",
    "SourceHanSansCN-Regular.otf",
    "SourceHanSansSC-Regular.otf",
    "NotoSansCJKsc-Regular.otf",
    "NotoSansSC-Regular.otf",
    "Deng.ttf",  # 等线
    "Dengb.ttf",
)


def windows_fonts_dir() -> Path:
    windir = os.environ.get("WINDIR", r"C:\Windows")
    return Path(windir) / "Fonts"


def find_cjk_font_files() -> List[Path]:
    fonts = windows_fonts_dir()
    found: list[Path] = []
    for name in CANDIDATE_NAMES:
        p = fonts / name
        if p.exists():
            found.append(p)
    return found


def preferred_cjk_font_file() -> Optional[Path]:
    files = find_cjk_font_files()
    return files[0] if files else None


def preferred_ui_font_family() -> str:
    """Qt font family name for GUI."""
    # Order for QFont
    for family, file_hint in (
        ("Microsoft YaHei UI", "msyh"),
        ("Microsoft YaHei", "msyh"),
        ("微软雅黑", "msyh"),
        ("SimHei", "simhei"),
        ("Noto Sans CJK SC", "Noto"),
        ("Source Han Sans SC", "SourceHan"),
        ("DengXian", "Deng"),
        ("SimSun", "simsun"),
    ):
        for p in find_cjk_font_files():
            if file_hint.lower() in p.name.lower():
                return family
    return "Microsoft YaHei UI"


def copy_cjk_font_to_game(
    game_dir: str | Path,
    dest_name: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Copy a system CJK font into the game folder (and common subfolders).
    Many engines (Kirikiri, some Unity/ren'py) will pick up *.ttf/*.ttc here
    or you can point the engine at the file.
    """
    src = preferred_cjk_font_file()
    if not src:
        return False, "未在 Windows\\Fonts 找到微软雅黑/黑体等中文字体"
    root = Path(game_dir)
    if not root.is_dir():
        return False, f"游戏目录无效: {root}"

    out_name = dest_name or src.name
    targets = [
        root / out_name,
        root / "font" / out_name,
        root / "fonts" / out_name,
        root / "Font" / out_name,
        root / "Fonts" / out_name,
    ]
    copied: list[str] = []
    for dest in targets:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and dest.stat().st_size == src.stat().st_size:
                copied.append(str(dest) + " (已存在)")
                continue
            shutil.copy2(src, dest)
            copied.append(str(dest))
        except Exception as e:
            return False, f"复制失败 {dest}: {e}"

    tip = (
        f"已从系统复制字体: {src.name}\n"
        + "\n".join(copied)
        + "\n\n若游戏仍缺字：在游戏设置/配置里把对话字体改成该文件"
        "（Kirikiri 常见为 Config.tjs / 启动项指定 TTF）。"
        "\nRealLive 古早引擎仅靠换字体不够，需 Locale Emulator + CP932 或代理。"
    )
    return True, tip
