# -*- coding: utf-8 -*-
from app.core.unity_hazy_text import (
    expand_hazy_mapping,
    has_advscript_ruby,
    has_advscript_ui_markup,
    sanitize_advscript_markup_for_ui,
    sanitize_advscript_ruby_for_ui,
)


def test_sanitize_keeps_base_and_tmp_font():
    raw = "[sruby-50][vruby-10][fきん]锦[fし]丝[fぼり]堀[fこう]公[fえん]园"
    assert sanitize_advscript_markup_for_ui(raw) == "锦丝堀公园"
    telop = (
        '[sruby-50]<cspace=-4px><color=#ddd><font="TELOP">'
        "[vruby-10][fきん]錦[fし]糸[fぼり]堀[fこう]公[fえん]園"
    )
    out = sanitize_advscript_markup_for_ui(telop)
    assert "[sruby" not in out.lower()
    assert "[f" not in out or "[font" in out
    assert "錦糸堀公園" in out or "锦" in out
    assert '<font="TELOP">' in out
    assert sanitize_advscript_markup_for_ui('[font="TELOP"]公园') == '[font="TELOP"]公园'


def test_sanitize_ff_strips_color_and_scale():
    s = "[x2][sruby50][ffふくなが]福永 [ffようこ]葉子[c0]"
    assert sanitize_advscript_markup_for_ui(s) == "福永 葉子"
    assert not has_advscript_ruby(sanitize_advscript_markup_for_ui(s))
    assert not has_advscript_ui_markup(sanitize_advscript_markup_for_ui(s))


def test_sanitize_menu_gameplay_row():
    raw = "[c4][x1.3]游戏玩法[c0]"
    assert sanitize_advscript_markup_for_ui(raw) == "游戏玩法"
    assert sanitize_advscript_ruby_for_ui(raw) == "游戏玩法"


def test_sanitize_rlp_and_size():
    assert sanitize_advscript_markup_for_ui("左[r]右[l]页[p]尾") == "左右页尾"
    assert sanitize_advscript_markup_for_ui("[size=24]标题[c0]") == "标题"


def test_expand_stripped_key_does_not_keep_ruby_cn():
    mapping = {
        "[sruby-50][vruby-10][fきん]錦[fし]糸[fぼり]堀[fこう]公[fえん]園": (
            "[sruby-50][vruby-10][fきん]锦[fし]丝[fぼり]堀[fこう]公[fえん]园"
        ),
        "錦糸堀公園": "锦丝堀公园",
    }
    expanded = expand_hazy_mapping(mapping)
    assert expanded["錦糸堀公園"] == "锦丝堀公园"
    assert not has_advscript_ruby(expanded["錦糸堀公園"])
    assert not has_advscript_ui_markup(expanded["錦糸堀公園"])
