# -*- coding: utf-8 -*-
"""PARANORMASIGHT / Hazy AdvScript harden regressions."""
from __future__ import annotations

from pathlib import Path

from app.core.unity_hazy_text import (
    _scrub_lineno_leak,
    expand_hazy_fill_mapping,
    preserve_click_wait_tags,
    sanitize_adv_text_payload,
    sanitize_advscript_markup_for_ui,
)
from app.core.unity_runtime_inject import spoof_tmp_font_bundle_for_game


def test_preserve_click_wait_trailing_l_p():
    src = "これはテストです。[l][p]"
    cn = "这是测试。"
    assert preserve_click_wait_tags(src, cn) == "这是测试。[l][p]"
    # Already has waits — still prefer src trailing run
    assert preserve_click_wait_tags(src, "这是测试。[l]") == "这是测试。[l][p]"


def test_preserve_click_wait_missing_counts():
    # No trailing-only run: mid-line waits are topped up by count
    src = "点[l]击继续"
    cn = "点击继续"
    assert preserve_click_wait_tags(src, cn) == "点击继续[l]"


def test_loc_sanitize_strips_tags_dialogue_keeps_them():
    raw = "[c4][x1.3]游戏玩法[c0][l]"
    loc = sanitize_advscript_markup_for_ui(raw)
    assert "[c4]" not in loc and "[l]" not in loc
    assert "游戏玩法" in loc
    # Dialogue path must keep wait tags via preserve, not Loc sanitize
    dialogue = preserve_click_wait_tags(raw, "游戏玩法")
    assert dialogue.endswith("[l]") or "[l]" in dialogue


def test_expand_wrapper_windowmessage_to_plain_jp():
    jp_wrap = (
        '12.ui.oat(WindowMessage:メニューボタンを押してください。[l]|txtid=abc)'
    )
    cn_wrap = "请按下菜单按钮。[l]"
    expanded, stats = expand_hazy_fill_mapping({jp_wrap: cn_wrap})
    plain = "メニューボタンを押してください。[l]"
    assert plain in expanded
    assert "菜单" in expanded[plain]
    # wm and/or display expansion must index the payload
    assert stats.get("added_wm", 0) + stats.get("added_display", 0) >= 1


def test_sanitize_payload_strips_txtid_leak_and_font_name():
    leaked = "你好世界|txtid=DOC01)|txtid=DOC01)"
    assert "|txtid=" not in sanitize_adv_text_payload(leaked)
    assert "TELOP" in sanitize_adv_text_payload('<font="泰洛普">标题')
    assert 'MAIN' in sanitize_adv_text_payload('<font="主要">')


def test_scrub_lineno_leak_menu_and_once():
    key = "250.ui.oat(WindowMessage:メニューボタン…)"
    assert "250" not in _scrub_lineno_leak(key, "这里有250个菜单按钮")
    key2 = "483.ui.oat(WindowMessage:…１回…)"
    assert _scrub_lineno_leak(key2, "仅限483次") == "仅限1次"


def test_spoof_tmp_font_is_noop(tmp_path: Path):
    assert spoof_tmp_font_bundle_for_game(tmp_path, "arialuni_sdf_u2019") is None
    assert not list(tmp_path.iterdir())
