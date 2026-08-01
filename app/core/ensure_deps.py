# -*- coding: utf-8 -*-
"""Ensure optional runtime deps (UnityPy, …) without asking the user to pip manually."""
from __future__ import annotations

import importlib
import subprocess
import sys
from typing import Callable, List, Optional, Sequence, Tuple

LogFn = Optional[Callable[[str], None]]

# (import_name, pip_name)
# Pinned ranges: verified against these majors; a big upstream bump could break
# internal API paths (UnityPy.helpers.TypeTreeGenerator etc.). Adjust on purpose.
_REQUIRED: Sequence[Tuple[str, str]] = (
    ("UnityPy", "UnityPy>=1.10.0"),
    ("TypeTreeGeneratorAPI", "TypeTreeGeneratorAPI>=0.0.10"),
    ("zstandard", "zstandard>=0.22.0"),
)


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def ensure_package(import_name: str, pip_name: str, log: LogFn = None) -> bool:
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        pass
    if _frozen():
        if log:
            log(
                f"缺少 {import_name}：请用「运行.bat」或重新执行 build_exe.bat（把 UnityPy 打进包）"
            )
        return False
    if log:
        log(f"正在自动安装依赖 {pip_name} …")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", pip_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        importlib.invalidate_caches()
        importlib.import_module(import_name)
        if log:
            log(f"已安装 {pip_name}")
        return True
    except Exception as e:
        if log:
            log(f"自动安装 {pip_name} 失败: {e}")
        return False


def ensure_runtime_deps(log: LogFn = None) -> List[str]:
    """Install/import optional deps. Returns list of missing package names."""
    missing: List[str] = []
    for imp, pip in _REQUIRED:
        if not ensure_package(imp, pip, log):
            missing.append(pip)
    return missing
