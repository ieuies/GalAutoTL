# -*- coding: utf-8 -*-
"""Regression tests for the auto GARbro.Console build helpers (garbro_cli).

GARbro.Console.exe is a thin host (few KB) that needs the official-release
DLLs beside it; the GUI exe must never be treated as a drivable console.
"""
from __future__ import annotations

from pathlib import Path

from app.core.garbro_cli import (
    _is_console_name,
    _patch_net20_csproj,
    _valid_garbro_console,
)


def test_is_console_name():
    assert _is_console_name(Path("GARbro.Console.exe"))
    assert _is_console_name(Path("garbro-cli.exe"))
    assert _is_console_name(Path("GameRes.Console.exe"))
    assert not _is_console_name(Path("GARbro.GUI.exe"))
    assert not _is_console_name(Path("garbro.exe"))


def test_valid_garbro_console_needs_sibling_dlls(tmp_path: Path):
    exe = tmp_path / "GARbro.Console.exe"
    exe.write_bytes(b"\x00" * 9000)
    assert not _valid_garbro_console(exe)  # no DLLs yet
    (tmp_path / "ArcFormats.dll").write_bytes(b"x")
    (tmp_path / "GameRes.dll").write_bytes(b"x")
    assert _valid_garbro_console(exe)
    # GUI is never a usable console, even with the same DLLs beside it
    gui = tmp_path / "GARbro.GUI.exe"
    gui.write_bytes(b"\x00" * 300000)
    assert not _valid_garbro_console(gui)


def test_patch_net20_csproj_idempotent(tmp_path: Path):
    net20 = tmp_path / "Net20"
    net20.mkdir()
    p = net20 / "Net20.csproj"
    p.write_text(
        '<?xml version="1.0"?>\n<Project>\n  <PropertyGroup>\n'
        "    <TargetFrameworkVersion>v2.0</TargetFrameworkVersion>\n"
        "    <TargetFrameworkProfile />\n  </PropertyGroup>\n"
        '  <Import Project="$(MSBuildToolsPath)\\Microsoft.CSharp.targets" />\n</Project>\n',
        encoding="utf-8",
    )
    assert _patch_net20_csproj(tmp_path) is True
    text = p.read_text(encoding="utf-8")
    assert "ReferenceAssemblies.net20" in text
    assert "<FrameworkPathOverride>" in text
    # idempotent: second call must not corrupt / duplicate
    assert _patch_net20_csproj(tmp_path) is True
    assert p.read_text(encoding="utf-8").count("ReferenceAssemblies.net20") == 1
