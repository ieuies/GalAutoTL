# -*- coding: utf-8 -*-
"""LCSE / Liquid Chinese display patches (GBK CharSet + DBCS + GetACP).

Lessons from LCSE localization:
- Text is GetGlyphOutlineA; lfCharSet must be GB2312 (0x86), not Shift-JIS (0x80).
- Hook CreateFontIndirectA so every font create forces CharSet.
- Expand SJIS lead/trail checks to GBK.
- Force GetACP/GetOEMCP stubs to return 936 on builds that use ImageBase 0x400000.
- Do NOT use Locale Emulator「日语运行」after patching — start on Chinese Windows directly.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Callable, Optional

LogFn = Optional[Callable[[str], None]]


def _patch_bytes(data: bytearray, old: bytes, new: bytes) -> int:
    assert len(old) == len(new)
    n = 0
    i = 0
    while True:
        j = data.find(old, i)
        if j < 0:
            break
        data[j : j + len(old)] = new
        n += 1
        i = j + len(new)
    return n


def patch_exe_for_gbk(exe: Path, log: LogFn = None) -> int:
    """Apply proven GBK display patches to a Liquid LCSE exe. Returns patch count."""
    data = bytearray(Path(exe).read_bytes())
    n = 0

    n += _patch_bytes(
        data, bytes([0xC6, 0x43, 0x17, 0x80]), bytes([0xC6, 0x43, 0x17, 0x86])
    )
    n += _patch_bytes(
        data, bytes([0xC6, 0x45, 0x0F, 0x80]), bytes([0xC6, 0x45, 0x0F, 0x86])
    )
    n += _patch_bytes(
        data,
        bytes([0xB8, 0x80, 0x00, 0x00, 0x00, 0xE8]),
        bytes([0xB8, 0x86, 0x00, 0x00, 0x00, 0xE8]),
    )
    n += _patch_bytes(
        data,
        bytes([0x3C, 0x9F, 0x76, 0x08, 0x3C, 0xE0]),
        bytes([0x3C, 0xFE, 0x76, 0x08, 0x3C, 0xE0]),
    )
    n += _patch_bytes(
        data,
        bytes.fromhex("80f940720580f97e760a80f980720880f9fc7703b001"),
        bytes.fromhex("80f940720580f97e760a80f980720880f9fe7703b001"),
    )
    n += _patch_bytes(
        data,
        bytes.fromhex("80fa40720580fa7e761580fa80720580fafc760b"),
        bytes.fromhex("80fa40720580fa7e761580fa80720580fafe760b"),
    )
    n += _patch_bytes(
        data, bytes([0x3C, 0xFC, 0x77, 0x17]), bytes([0x3C, 0xFE, 0x77, 0x17])
    )
    n += _patch_bytes(
        data, bytes([0x3C, 0xFC, 0x77, 0x14]), bytes([0x3C, 0xFE, 0x77, 0x14])
    )
    n += _patch_bytes(
        data,
        bytes([0xFF, 0x25, 0xD8, 0x05, 0x4B, 0x00]),
        bytes([0xB8, 0xA8, 0x03, 0x00, 0x00, 0xC3]),
    )
    n += _patch_bytes(
        data,
        bytes([0xFF, 0x25, 0xD4, 0x05, 0x4B, 0x00]),
        bytes([0xB8, 0xA8, 0x03, 0x00, 0x00, 0xC3]),
    )

    cave = None
    for cand in range(0x84800, 0x84F00, 16):
        if cand + 32 <= len(data) and all(b == 0 for b in data[cand : cand + 32]):
            cave = cand
            break
    iat_ff25 = bytes([0xFF, 0x25, 0x68, 0x05, 0x4B, 0x00])
    iat_ff15 = bytes([0xFF, 0x15, 0x68, 0x05, 0x4B, 0x00])
    if cave is not None:
        sites: list[tuple[int, int]] = []
        for pat, op in ((iat_ff15, 0xE8), (iat_ff25, 0xE9)):
            i = 0
            while True:
                j = data.find(pat, i)
                if j < 0:
                    break
                sites.append((j, op))
                i = j + 1
        code = bytes([0x8B, 0x44, 0x24, 0x04, 0xC6, 0x40, 0x17, 0x86]) + iat_ff25
        data[cave : cave + len(code)] = code
        n += 1
        for j, op in sites:
            rel = cave - (j + 5)
            data[j : j + 6] = bytes([op]) + struct.pack("<i", rel) + bytes([0x90])
            n += 1

    if n:
        Path(exe).write_bytes(data)
        if log:
            log(f"引擎 GBK 显示补丁: {n} 处（CharSet+DBCS+GetACP+CreateFont钩子）← {exe.name}")
    elif log:
        log(f"引擎编码补丁未匹配（{exe.name}），若乱码请反馈该游戏 exe")
    return n


def write_cn_launcher(game_dir: Path, exe_name: str, log: LogFn = None) -> Path:
    """Write bat + short readme: Chinese Windows, no Japanese LE, prefer new game."""
    game_dir = Path(game_dir)
    bat = game_dir / "点我启动_中文汉化版.bat"
    bat.write_text(
        "\r\n".join(
            [
                "@echo off",
                "chcp 936 >nul",
                'cd /d "%~dp0"',
                "echo [GalAutoTL] Do NOT use Locale Emulator Japanese.",
                "echo [GalAutoTL] Prefer NEW GAME after localize.",
                f'start "" "{exe_name}"',
                "",
            ]
        ),
        encoding="gbk",
        errors="replace",
    )
    tip = game_dir / "汉化启动说明.txt"
    tip.write_text(
        "GalAutoTL / LCSE 汉化启动说明\n"
        "================================\n"
        "1. 双击「点我启动_中文汉化版.bat」或直接运行主程序\n"
        "2. 不要用 Locale Emulator / NTLEA 的「日语运行」\n"
        "3. 汉化后请尽量「新游戏」；旧存档卡在半截场景可能点不动\n"
        "4. 显示补丁会改 exe；原版在桌面「自动翻译备份」\n",
        encoding="utf-8",
    )
    marker = game_dir / "2djgame.txt"
    if not marker.exists():
        marker.write_text("", encoding="utf-8")
    if log:
        log(f"已写入启动脚本: {bat.name}")
    return bat
