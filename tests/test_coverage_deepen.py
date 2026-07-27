# -*- coding: utf-8 -*-
"""Phase-1 text coverage deepen: deploy UI, Softpal orphans, LCSE system, remain report."""
from __future__ import annotations

from pathlib import Path

from app.core.kirikiri_patch import (
    is_deployable_ks_relpath,
    is_scenario_safe_ui_ks,
)
from app.core.lcse_snx import (
    ChoiceString,
    DialogString,
    ParsedScript,
    SystemString,
    _is_system_ui,
    collect_translatable,
)
from app.core.pipeline_harden import write_remainder_report
from app.core.softpal_script import SoftPalScriptBundle


def test_scenario_safe_ui_deploy_matches_macro_harvest():
    assert is_scenario_safe_ui_ks(Path("scenario/dialog.ks"))
    assert is_scenario_safe_ui_ks(Path("scenario/cgmsk.ks"))
    assert is_scenario_safe_ui_ks(Path("scenario/macro01.ks"))
    assert is_scenario_safe_ui_ks(Path("scenario/first.ks"))
    assert is_scenario_safe_ui_ks(Path("k_scenario/macro.ks"))
    assert is_deployable_ks_relpath(Path("scenario/dialog.ks"))
    assert not is_scenario_safe_ui_ks(Path("k_others/dialog.ks"))
    assert not is_scenario_safe_ui_ks(Path("script/macro.ks"))
    assert not is_deployable_ks_relpath(Path("k_others/first.ks"))


def test_softpal_collects_orphan_text_dat_rows():
    # Minimal TEXT.DAT: 16-byte header + two NUL-terminated entries (4-byte index + payload)
    header = b"\x00" + b"\x00" * 15

    def entry(idx: bytes, text: str) -> bytes:
        return idx + text.encode("cp932") + b"\x00"

    # offset 16: referenced dialog; offset after that: orphan UI
    e0 = entry(b"\x01\x00\x00\x00", "こんにちは")
    e1 = entry(b"\x02\x00\x00\x00", "セーブ")
    text_dat = header + e0 + e1
    # SCRIPT with one show ref pointing at offset 16 only
    # PalScriptTextShow: 24 bytes before marker + 8 marker = 32
    block = bytearray(32)
    # text_offset at +4 = 16, name_offset at +12 = 0x0FFFFFFF
    import struct

    struct.pack_into("<I", block, 4, 16)
    struct.pack_into("<I", block, 12, 0x0FFFFFFF)
    # marker at end: 17 00 01 00 | after_lo 02 00 | after_hi 02 00
    block[24:32] = b"\x17\x00\x01\x00\x02\x00\x02\x00"
    script = bytes(block)
    bundle = SoftPalScriptBundle(script, text_dat)
    assert bundle.refs, "expected at least one show ref"
    units = bundle.collect_units()
    assert "こんにちは" in units
    assert "セーブ" in units  # orphan UI via looks_untranslated


def test_lcse_system_ui_filter_and_collect():
    assert _is_system_ui("セーブ")
    assert _is_system_ui("タイトルに戻る")
    assert not _is_system_ui("foo_bar")
    assert not _is_system_ui(r"data\image.png")
    assert not _is_system_ui("ABCDEF")

    parsed = ParsedScript(
        strings=[
            DialogString(0, 0, "対話です", False),
            ChoiceString(1, "選択肢"),
            SystemString(2, "セーブ"),
            SystemString(3, "config_flag"),
            SystemString(4, r"bg\title.png"),
        ],
        speakers=[],
    )
    # speakers empty — add via dialog path not needed
    items = collect_translatable(parsed)
    kinds = {(k, t) for k, t, _o in items}
    assert ("dialog", "対話です") in kinds
    assert ("choice", "選択肢") in kinds
    assert ("system", "セーブ") in kinds
    assert ("system", "config_flag") not in kinds
    assert not any(t.endswith(".png") for _k, t, _o in items)


def test_write_remainder_report(tmp_path: Path):
    n = write_remainder_report(
        tmp_path,
        "test",
        ["こんにちは", "你好"],
        {"こんにちは": "こんにちは", "你好": "你好"},
    )
    assert n >= 1
    path = tmp_path / "GalAutoTL_remain.txt"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "こんにちは" in text
    assert "pipeline=test" in text
