# -*- coding: utf-8 -*-
"""Regression: glossary placeholders must not collapse to bare「0」or crash scripts."""
from __future__ import annotations

from pathlib import Path

from app.core.glossary import (
    Glossary,
    has_glossary_leak,
    mask_glossary_terms,
    placeholder_token,
    scrub_glossary_artifacts,
    unmask_glossary_terms,
)
from app.core.pipeline_harden import CODEC_UNICODE, sanitize_dst
from app.core.review_table import export_review_table, is_corrupt_review_cn, resolve_review_override


def test_placeholder_uses_letters_not_digits():
    assert placeholder_token(0) == "⟦GALTL_A⟧"
    assert "0" not in placeholder_token(0)
    assert placeholder_token(26) == "⟦GALTL_AA⟧"


def test_mask_unmask_roundtrip():
    g = Glossary(pairs=(("千夏", "千夏小姐"), ("二人", "两个人")))
    masked, keys = mask_glossary_terms("今日出会った二人と千夏", g)
    assert "⟦GALTL_A⟧" in masked and "⟦GALTL_B⟧" in masked
    assert "0" not in masked
    out = unmask_glossary_terms(masked, g, keys)
    assert out == "今日出会った两个人と千夏小姐"


def test_legacy_and_broken_placeholders_restore():
    g = Glossary(pairs=(("千夏", "千夏小姐"), ("二人", "两个人")))
    keys = ["二人", "千夏"]
    assert "两个人" in unmask_glossary_terms("遇到的{{GALTL0}}", g, keys)
    # truncated closing brace — previously left {{GALTL0} and crashed Sakana
    assert "千夏小姐" in unmask_glossary_terms("别去{{GALTL0}那里", g, ["千夏"])
    assert "千夏小姐" in unmask_glossary_terms("别去⟦GALTL_A那里", g, ["千夏"])


def test_zero_collapse_detected_and_scrubbed():
    g = Glossary(pairs=(("二人", "两个人"), ("千夏", "千夏")))
    assert has_glossary_leak("今天遇到的0个人")
    assert has_glossary_leak("别去0夏小姐那里")
    assert has_glossary_leak("{{GALTL0}}")
    assert not has_glossary_leak("今天遇到的两个人")

    s = scrub_glossary_artifacts(
        "别去0夏小姐那里", src="千夏さんのところ", glossary=g, keys=["千夏"]
    )
    assert "0夏" not in s
    assert "千夏小姐小姐" not in s
    assert "千夏" in s

    s2 = scrub_glossary_artifacts(
        "今天遇到的0个人", src="今日出会った二人", glossary=g, keys=["二人"]
    )
    assert s2 == "今天遇到的两个人"


def test_sanitize_dst_rejects_unrepairable_leak():
    # no JP hint → cannot repair 0夏 → drop
    assert sanitize_dst("0夏还在", "何か", CODEC_UNICODE) is None
    # JP hint → scrub then accept
    fixed = sanitize_dst("今天遇到的0个人", "今日出会った二人", CODEC_UNICODE)
    assert fixed == "今天遇到的两个人"


def test_persona_note_parse_and_prompt():
    from app.core.glossary import parse_glossary_text, merge_glossaries, Glossary

    g = parse_glossary_text(
        "あや=绫 ;; 女，学生，自称「我」\n"
        "ひなた|日向|女，幼驯染\n"
        "普通=普通词\n"
    )
    assert g.pairs  # has pairs
    assert dict(g.pairs)["あや"] == "绫"
    assert dict(g.pairs)["ひなた"] == "日向"
    assert g.note_for("あや").startswith("女")
    assert "自称" in g.note_for("あや")
    assert "幼驯染" in g.note_for("ひなた")
    assert g.note_for("普通") == ""
    block = g.as_prompt_block()
    assert "あや → 绫（女" in block
    assert "角色备注" in block
    # mask still ignores notes
    masked, keys = mask_glossary_terms("あやが来た", g)
    assert "⟦GALTL_" in masked
    assert "女" not in masked

    merged = merge_glossaries(
        g,
        Glossary(pairs=(("あや", "绫酱"),), notes=(("あや", "女，语气软"),)),
    )
    assert dict(merged.pairs)["あや"] == "绫酱"
    assert "语气软" in merged.note_for("あや")


def test_default_persona_rules_and_user_override(tmp_path: Path):
    from app.core.glossary import (
        DEFAULT_PERSONA_RULES,
        PERSONA_RULES_NAME,
        build_glossary_prompt_block,
        load_persona_rules,
        parse_glossary_text,
    )

    # no game dir → built-in
    assert "僕/俺" in load_persona_rules(None) or "自称" in load_persona_rules(None)
    assert "他/她" in DEFAULT_PERSONA_RULES

    # first load creates editable file with defaults
    rules = load_persona_rules(tmp_path)
    assert (tmp_path / PERSONA_RULES_NAME).is_file()
    assert "对称" in rules or "あなた" in rules

    # user override
    (tmp_path / PERSONA_RULES_NAME).write_text(
        "# comment\n【测试覆盖规则】只用咱作自称\n", encoding="utf-8"
    )
    assert "只用咱作自称" in load_persona_rules(tmp_path)
    assert "僕/俺" not in load_persona_rules(tmp_path)

    g = parse_glossary_text("あや=绫 ;; 女性，自称我")
    block = build_glossary_prompt_block(g, tmp_path)
    assert "只用咱作自称" in block
    assert "あや → 绫" in block


def test_review_rejects_corrupt_override(tmp_path: Path):
    assert is_corrupt_review_cn("0夏")
    assert is_corrupt_review_cn("{{GALTL0}}")
    assert not is_corrupt_review_cn("正常译文")

    # export must not persist corrupt CN when a good prior exists
    path = export_review_table(
        tmp_path,
        ["今日出会った二人"],
        ["今天遇到的两个人"],
    )
    assert path.is_file()
    export_review_table(
        tmp_path,
        ["今日出会った二人"],
        ["今天遇到的0个人"],  # corrupt — must not overwrite
    )
    text = path.read_text(encoding="utf-8")
    assert "0个人" not in text
    assert "两个人" in text

    by_idx = {0: ("今日出会った二人", "今天遇到的0个人")}
    by_src = {"今日出会った二人": "今天遇到的0个人"}
    assert resolve_review_override(0, "今日出会った二人", by_idx, by_src) is None
