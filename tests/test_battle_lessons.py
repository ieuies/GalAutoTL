# -*- coding: utf-8 -*-
"""Regression locks distilled from real localization battles.

Sources of lessons (do not regress):
  - Kagura/reimeiki: UTF-8-in-CP932 mojibake, btText miss, EXE UI, ・ smash,
    translate_batch(cache) positional bug, soft_fit UTF-8 flip
  - Kirikiri/FREAKSTRIKE: never CN-override k_others/macros wholesale;
    UTF-16-LE; poison; chara=/askYesNo/hint UI strings; patch2 not patch
  - LCSE: never API cp932 before GBK slots
  - Softpal: zh→GBK / jp→CP932 codec split
  - Unity/Artemis/Sakana: UNICODE codec only
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.cp932_safe import to_cp932_safe
from app.core.kagura_bt_text import apply_bt_texts, collect_bt_texts, parse_bt_text
from app.core.kagura_exe_ui import EXE_UI, patch_exe_bytes
from app.core.kagura_glossary import UI_GLOSSARY
from app.core.kagura_pak import encode_script_text, _fit_encoded
from app.core.kirikiri_patch import (
    is_deployable_ks_relpath,
    is_dialogue_ks_relpath,
    is_macro_ks_file,
    is_poison_translation,
    is_scenario_safe_ui_ks,
)
from app.core.ks_script import apply_ks_units, collect_ks_units, write_ks
from app.core.pipeline_harden import (
    CODEC_CP932,
    CODEC_GBK,
    CODEC_UNICODE,
    sanitize_dst,
    second_pass_sources,
)
from app.core.translate import TranslateCache, translate_batch


ROOT = Path(__file__).resolve().parents[1]


def _write_ks_u16(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_ks(path, text, "utf-16-le")


# ---------------------------------------------------------------------------
# Kagura / CP932 battle
# ---------------------------------------------------------------------------


def test_prefer_cp932_never_emits_utf8_payload():
    """reimeiki: writing UTF-8 into CP932 Lua slots → 简单 displayed as mojibake."""
    raw = encode_script_text("简单", prefer_cp932=True)
    assert raw is not None
    assert raw != "简单".encode("utf-8")
    assert raw.decode("cp932") == to_cp932_safe("简单")


def test_soft_fit_must_not_flip_to_utf8_in_source():
    src = (ROOT / "app/core/kagura_pak.py").read_text(encoding="utf-8")
    assert "prefer_cp932=not prefer_cp932" not in src
    blob = _fit_encoded("超长中文会被截断到预算", 10, prefer_cp932=True, soft_fit=True)
    assert blob is not None and len(blob) == 10
    blob.decode("cp932")


def test_cp932_safe_keeps_common_sc_words_usable():
    """Aggressive ・ remap of 满/传/蘑菇… was a real display bug."""
    out = to_cp932_safe("满腹传送蘑菇")
    assert out == "満腹伝送茸菇"
    assert "・" not in out
    out.encode("cp932")


def test_bttext_roundtrip_preserves_tail_and_translates():
    """Menus/difficulty lived in btText.dat — must parse/apply without dropping tail."""
    jp = "セーブ".encode("cp932")
    header = b"\x00\x01\x31"
    # id=1 + text + NUL terminator + opaque tail
    blob = header + bytes([1]) + b"1" + bytes([len(jp)]) + jp + b"\x00" + b"OPAQUE_TAIL"
    texts = collect_bt_texts(blob)
    assert any("セーブ" in t for t in texts)
    new_blob, n = apply_bt_texts(blob, {"セーブ": "保存"})
    assert n >= 1
    _h, entries, tail = parse_bt_text(new_blob)
    assert b"OPAQUE_TAIL" in tail
    assert any(e.text == "保存" for e in entries)


def test_bttext_rejects_dot_mangled_translation():
    jp = "テスト項目".encode("cp932")
    blob = b"\x00\x01\x31" + bytes([1]) + b"2" + bytes([len(jp)]) + jp + b"\x00"
    new_blob, n = apply_bt_texts(blob, {"テスト項目": "・・・・・・"})
    assert n == 0
    assert parse_bt_text(new_blob)[1][0].text == "テスト項目"


def test_exe_ui_glossary_fits_cp932_slots():
    """EXE C-string patch: CN must encode and be <= JP byte length."""
    for src, dst in EXE_UI.items():
        sb = src.encode("cp932")
        db = dst.encode("cp932")
        assert len(db) <= len(sb), (src, dst, len(db), len(sb))


def test_exe_patch_nul_terminated_and_pads():
    src = "はい"
    dst = "是"
    sb = src.encode("cp932")
    db = dst.encode("cp932")
    # embed as C string
    data = bytearray(b"XX" + sb + b"\x00" + b"YY")
    n, _ = patch_exe_bytes(data, {src: dst})
    assert n == 1
    assert data[2 : 2 + len(sb)] == db + b"\x00" * (len(sb) - len(db))
    assert data[2 + len(sb)] == 0


def test_kagura_ui_glossary_uses_cp932_safe_load():
    """読取 not 读取 — SC 读 may be fine in UTF-8 but Kagura needs CP932 forms."""
    assert UI_GLOSSARY["ロード"] == "読取"
    UI_GLOSSARY["ロード"].encode("cp932")
    UI_GLOSSARY["セーブ"].encode("cp932")


# ---------------------------------------------------------------------------
# translate_batch API footgun
# ---------------------------------------------------------------------------


def test_translate_batch_rejects_garbage_cp932_arg(tmp_path: Path):
    class _NoClient:
        pass

    cache = TranslateCache(tmp_path / "g.sqlite")
    try:
        # Keyword garbage — hits runtime isinstance guard (not just Python arity)
        with pytest.raises(TypeError):
            translate_batch(
                [" "],
                _NoClient(),  # type: ignore[arg-type]
                "zh_cn",
                cp932="yes",  # type: ignore[arg-type]
                cache=cache,
            )
    finally:
        cache.close()


def test_translate_batch_forbids_positional_cache(tmp_path: Path):
    """Keyword-only after lang prevents old bug: cache mistaken for cp932=True."""

    class _NoClient:
        pass

    cache = TranslateCache(tmp_path / "t.sqlite")
    try:
        with pytest.raises(TypeError):
            translate_batch(
                ["  "],
                _NoClient(),  # type: ignore[arg-type]
                "zh_cn",
                cache,  # type: ignore[misc]
            )
        out = translate_batch(
            ["  ", ""],
            _NoClient(),  # type: ignore[arg-type]
            "zh_cn",
            cp932=False,
            cache=cache,
        )
        assert out == ["  ", ""]
    finally:
        cache.close()


# ---------------------------------------------------------------------------
# Kirikiri / FREAKSTRIKE battle
# ---------------------------------------------------------------------------


def test_kirikiri_skips_engine_and_k_others_dialogue():
    assert not is_dialogue_ks_relpath(Path("script/first.ks"))
    assert not is_dialogue_ks_relpath(Path("system/Config.tjs"))
    assert not is_dialogue_ks_relpath(Path("k_others/first.ks"))
    assert not is_dialogue_ks_relpath(Path("k_bonus/gallery.ks"))
    assert is_dialogue_ks_relpath(Path("scenario/a.ks"))
    assert is_dialogue_ks_relpath(Path("k_scenario/a.ks"))


def test_kirikiri_macro_file_detection():
    assert is_macro_ks_file(Path("macro.ks"))
    assert is_macro_ks_file(Path("first.ks"))
    assert is_macro_ks_file(Path("macro01.ks"))
    assert not is_macro_ks_file(Path("story01.ks"))


def test_kirikiri_safe_ui_macro_deploy_rules():
    assert is_scenario_safe_ui_ks(Path("scenario/macro.ks"))
    assert is_scenario_safe_ui_ks(Path("scenario/dialog.ks"))
    assert is_scenario_safe_ui_ks(Path("scenario/cgmsk.ks"))
    assert is_scenario_safe_ui_ks(Path("scenario/macro01.ks"))
    assert is_deployable_ks_relpath(Path("scenario/first.ks"))
    assert is_deployable_ks_relpath(Path("scenario/dialog.ks"))
    assert not is_scenario_safe_ui_ks(Path("k_others/macro.ks"))
    assert not is_deployable_ks_relpath(Path("k_others/first.ks"))


def test_kirikiri_poison_translations():
    assert is_poison_translation("无法识别，疑似乱码")
    assert is_poison_translation("按原文输出")
    assert not is_poison_translation("你好，世界")


def test_kirikiri_does_not_collect_iscript_tjs_as_dialogue(tmp_path: Path):
    """Translating raw TJS inside [iscript] breaks boot — only safe UI quotes."""
    root = tmp_path / "_galautotl_kirikiri" / "scripts"
    _write_ks_u16(
        root / "scenario" / "x.ks",
        "\n".join(
            [
                "[iscript]",
                'var foo = "これはコード内の日本語";',
                'hint:"選択不可";',
                "[endscript]",
                "「これは対白」",
                "",
            ]
        ),
    )
    units = collect_ks_units(root, source_lang="ja")
    sources = {u.source for u in units}
    assert "「これは対白」" in sources
    assert "これはコード内の日本語" not in sources
    assert "選択不可" in sources


def test_kirikiri_apply_always_utf16_le(tmp_path: Path):
    root = tmp_path / "_galautotl_kirikiri" / "scripts"
    path = root / "scenario" / "d.ks"
    _write_ks_u16(path, "「こんにちは」\n")
    units = collect_ks_units(root, source_lang="ja")
    assert units
    apply_ks_units(units, ["「你好」"] * len(units))
    assert path.read_bytes()[:2] == b"\xff\xfe"


def test_kirikiri_pipeline_uses_patch2_not_patch_xp3():
    """useArchiveIfExists('patch.xp3') → patch.xp3.xp3 trap — must use patch2."""
    text = (ROOT / "app/pipelines/kirikiri.py").read_text(encoding="utf-8")
    core = (ROOT / "app/core/kirikiri_patch.py").read_text(encoding="utf-8")
    blob = text + core
    assert "patch2" in blob
    # avoid recommending bare patch.xp3 as deploy target in comments is ok;
    # ensure addAutoPath uses patch2
    assert 'patch2.xp3' in core


# ---------------------------------------------------------------------------
# Cross-pipeline codec routing (battle: wrong codec = smash/crash)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel,must_have,forbid_live_cp932_true",
    [
        ("app/pipelines/kirikiri.py", "CODEC_UNICODE", True),
        ("app/pipelines/unity.py", "CODEC_UNICODE", True),
        ("app/pipelines/artemis.py", "CODEC_UNICODE", True),
        ("app/pipelines/sakana.py", "CODEC_UNICODE", True),
        ("app/pipelines/lcse.py", "CODEC_GBK", True),
        ("app/pipelines/kagura.py", "CODEC_CP932", False),
        ("app/pipelines/bgi.py", "CODEC_CP932", False),
        ("app/pipelines/yuris.py", "softpal_codecs_for_lang", True),
    ],
)
def test_pipeline_codec_routing(rel, must_have, forbid_live_cp932_true):
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert must_have in text
    if forbid_live_cp932_true:
        live = [
            ln
            for ln in text.splitlines()
            if "cp932=True" in ln and not ln.lstrip().startswith("#")
        ]
        assert live == [], live


def test_softpal_splits_gbk_vs_cp932_by_lang():
    text = (ROOT / "app/pipelines/softpal.py").read_text(encoding="utf-8")
    assert "softpal_codecs_for_lang" in text
    assert "enc, codec = softpal_codecs_for_lang" in text
    from app.core.pipeline_harden import softpal_codecs_for_lang, CODEC_GBK, CODEC_CP932

    assert softpal_codecs_for_lang("zh_cn")[1] == CODEC_GBK
    assert softpal_codecs_for_lang("ja")[1] == CODEC_CP932


def test_gbk_sanitize_does_not_run_cp932_safe_smash():
    """LCSE lesson: to_cp932_safe before GBK write turns 简体 into odd JP forms."""
    # 读取 is fine in GBK; sanitize_dst GBK must keep 简体
    assert sanitize_dst("读取存档", "ロード", CODEC_GBK) == "读取存档"


def test_second_pass_from_battle_leftover_logic():
    remain = second_pass_sources(
        ["セーブ", "こんにちは", "已是中文"],
        {"セーブ": "セーブ", "こんにちは": "こんにちは", "已是中文": "已是中文"},
    )
    # glossary key セーブ skipped; 已是中文 not JP; こんにちは remains
    assert "こんにちは" in remain
    assert "已是中文" not in remain
    assert "セーブ" not in remain


def test_kagura_pipeline_mentions_bttext_and_exe():
    text = (ROOT / "app/pipelines/kagura.py").read_text(encoding="utf-8")
    assert "btText" in text or "bt_text" in text.lower() or "collect_bt_texts" in text
    assert "patch_kagura_exe" in text or "kagura_exe" in text
