# -*- coding: utf-8 -*-
"""Auto-locate and invoke GARbro / garbro-cli for encrypted archives."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, List, Optional

LogFn = Optional[Callable[[str], None]]

CANDIDATE_NAMES = (
    "garbro-cli.exe",
    "GARbro.Console.exe",
    "GameRes.Console.exe",
    "garbro.exe",
    "GARbro.exe",
)


def find_garbro(extra_dirs: Optional[List[Path]] = None) -> Optional[Path]:
    paths: List[Path] = []
    if extra_dirs:
        paths.extend(extra_dirs)
    # tools next to this app / common locations
    env = os.environ.get("GALAUTOTL_GARBRO") or os.environ.get("GARBRO")
    if env:
        paths.append(Path(env))
    desk = Path.home() / "Desktop"
    for base in (
        desk / "工具",
        desk / "tools",
        desk / "GARbro",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "GARbro",
        Path(os.environ.get("LOCALAPPDATA", "")) / "GARbro",
    ):
        if base:
            paths.append(base)
    # PATH
    for name in CANDIDATE_NAMES:
        hit = shutil.which(name)
        if hit:
            return Path(hit)
    for base in paths:
        p = Path(base)
        if p.is_file() and p.suffix.lower() == ".exe":
            return p
        if p.is_dir():
            for name in CANDIDATE_NAMES:
                cand = p / name
                if cand.is_file():
                    return cand
            # shallow search
            for name in CANDIDATE_NAMES:
                for hit in p.rglob(name):
                    return hit
    return None


def extract_with_garbro(
    archive: Path,
    out_dir: Path,
    garbro: Path,
    log: LogFn = None,
) -> bool:
    """Best-effort extract. Returns True if out_dir gained files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    before = sum(1 for _ in out_dir.rglob("*") if _.is_file())
    name = garbro.name.lower()
    attempts: List[List[str]] = []
    if "cli" in name:
        attempts.append([str(garbro), "extract", "--input", str(archive), "--output", str(out_dir)])
        attempts.append([str(garbro), "x", "-o", str(out_dir), "-y", str(archive)])
        attempts.append([str(garbro), "x", "-o", str(out_dir), str(archive)])
    elif "console" in name:
        attempts.append([str(garbro), "x", str(archive), str(out_dir)])
        attempts.append([str(garbro), str(archive), str(out_dir)])
    else:
        # GUI — cannot automate reliably
        if log:
            log(f"找到 GARbro GUI（{garbro.name}），无法静默解包；请改用 garbro-cli 或手动解")
        return False

    for cmd in attempts:
        try:
            if log:
                log("调用: " + " ".join(cmd))
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
                cwd=str(garbro.parent),
            )
            after = sum(1 for _ in out_dir.rglob("*") if _.is_file())
            if after > before:
                if log:
                    log(f"GARbro 解出 {after - before} 个文件")
                return True
            if r.returncode == 0 and after > before:
                return True
        except Exception as e:
            if log:
                log(f"GARbro 调用失败: {e}")
            continue
    return False
