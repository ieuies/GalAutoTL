# -*- coding: utf-8 -*-
"""Stronger tests for former 'limitations': behavior > source greps, mock quality gates."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.core.kirikiri_patch import is_deployable_ks_relpath, stage_normalized_tree
from app.core.ks_script import apply_ks_units, collect_ks_units, write_ks
from app.core.pipeline_harden import (
    CODEC_CP932,
    CODEC_GBK,
    CODEC_UNICODE,
    PIPELINE_TRANSLATE_CODEC,
    expected_translate_codec,
    softpal_codecs_for_lang,
    zip_to_mapping,
)

ROOT = Path(__file__).resolve().parents[1]


def _write_ks(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_ks(path, text, "utf-16-le")


# ---------------------------------------------------------------------------
# Fix limitation: source-string greps → policy + AST (runtime constants)
# ---------------------------------------------------------------------------


def test_softpal_codecs_for_lang_behavior():
    assert softpal_codecs_for_lang("zh_cn") == ("gbk", CODEC_GBK)
    assert softpal_codecs_for_lang("zh-tw") == ("gbk", CODEC_GBK)
    assert softpal_codecs_for_lang("ja") == ("cp932", CODEC_CP932)
    assert softpal_codecs_for_lang("") == ("cp932", CODEC_CP932)


def test_expected_translate_codec_table():
    assert expected_translate_codec("kirikiri") == CODEC_UNICODE
    assert expected_translate_codec("unity") == CODEC_UNICODE
    assert expected_translate_codec("lcse") == CODEC_GBK
    assert expected_translate_codec("kagura") == CODEC_CP932
    assert expected_translate_codec("softpal", "zh_cn") == CODEC_GBK
    assert expected_translate_codec("softpal", "ja") == CODEC_CP932
    assert expected_translate_codec("yuris", "zh_cn") == CODEC_GBK
    assert expected_translate_codec("yuris", "ja") == CODEC_CP932


def _translate_to_mapping_codec_names(py_file: Path) -> set[str]:
    """Return Name ids passed as codec= to translate_to_mapping(...)."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = None
        if isinstance(fn, ast.Name):
            name = fn.id
        elif isinstance(fn, ast.Attribute):
            name = fn.attr
        if name != "translate_to_mapping":
            continue
        for kw in node.keywords:
            if kw.arg != "codec":
                continue
            if isinstance(kw.value, ast.Name):
                found.add(kw.value.id)
            elif isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                found.add(kw.value.value)
    return found


@pytest.mark.parametrize(
    "pipeline,const_name",
    [
        ("kirikiri", "CODEC_UNICODE"),
        ("unity", "CODEC_UNICODE"),
        ("artemis", "CODEC_UNICODE"),
        ("sakana", "CODEC_UNICODE"),
        ("lcse", "CODEC_GBK"),
        ("kagura", "CODEC_CP932"),
        ("bgi", "CODEC_CP932"),
    ],
)
def test_pipeline_passes_codec_via_ast(pipeline: str, const_name: str):
    """Prove translate_to_mapping(..., codec=CONST) — not a comment string."""
    path = ROOT / "app" / "pipelines" / f"{pipeline}.py"
    names = _translate_to_mapping_codec_names(path)
    assert const_name in names, f"{pipeline}: codec kwargs={names}"


def test_yuris_uses_lang_split_codec_like_softpal():
    text = (ROOT / "app/pipelines/yuris.py").read_text(encoding="utf-8")
    assert "softpal_codecs_for_lang" in text
    assert "codec=codec" in text
    from app.core.pipeline_harden import expected_translate_codec, CODEC_GBK, CODEC_CP932

    assert expected_translate_codec("yuris", "zh_cn") == CODEC_GBK
    assert expected_translate_codec("yuris", "ja") == CODEC_CP932


def test_softpal_uses_helper_not_inline_only():
    text = (ROOT / "app/pipelines/softpal.py").read_text(encoding="utf-8")
    assert "softpal_codecs_for_lang" in text
    assert "enc, codec = softpal_codecs_for_lang" in text


# ---------------------------------------------------------------------------
# Fix limitation: "API quality" → mock outputs through zip_to_mapping gates
# ---------------------------------------------------------------------------


def test_mock_ai_poison_dropped():
    m = zip_to_mapping(
        ["こんにちは", "セーブ"],
        ["无法识别，疑似乱码", "按原文输出"],
        codec=CODEC_UNICODE,
    )
    assert m == {}


def test_mock_ai_dots_dropped_on_cp932():
    m = zip_to_mapping(
        ["テスト", "セーブ"],
        ["・・・・・・", "保存"],
        codec=CODEC_CP932,
    )
    assert "テスト" not in m
    assert m.get("セーブ") == "保存"


def test_mock_ai_good_cn_kept_unicode():
    m = zip_to_mapping(["セーブ", "はい"], ["保存", "是"], codec=CODEC_UNICODE)
    assert m == {"セーブ": "保存", "はい": "是"}


def test_mock_ai_gbk_keeps_simplified():
    m = zip_to_mapping(["ロード"], ["读取"], codec=CODEC_GBK)
    assert m["ロード"] == "读取"


# ---------------------------------------------------------------------------
# Fix limitation: mini end-to-end (not full game, but real write path)
# ---------------------------------------------------------------------------


def test_mini_e2e_kirikiri_collect_apply_stage(tmp_path: Path):
    """Tiny scenario tree: nameplate + confirm → apply → stage deployable files."""
    scripts = tmp_path / "_galautotl_kirikiri" / "scripts"
    _write_ks(
        scripts / "scenario" / "a.ks",
        "\n".join(
            [
                '@name chara="モニカ"',
                "「こんにちは」",
                "@eval exp=\"tf.r = askYesNo('戻りますか？')\"",
                "",
            ]
        ),
    )
    _write_ks(
        scripts / "scenario" / "macro.ks",
        "\n".join(
            [
                "@iscript",
                'hint:"選択不可です";',
                "@endscript",
                "",
            ]
        ),
    )
    units = collect_ks_units(scripts, source_lang="ja")
    sources = [u.source for u in units]
    assert "モニカ" in sources
    assert "戻りますか？" in sources
    assert "選択不可です" in sources

    mapping = {
        "モニカ": "莫妮卡",
        "「こんにちは」": "「你好」",
        "戻りますか？": "要返回吗？",
        "選択不可です": "无法选择",
    }
    translated = [mapping.get(s, s) for s in sources]
    apply_ks_units(units, translated)

    text = (scripts / "scenario" / "a.ks").read_bytes().decode("utf-16-le")
    if text.startswith("\ufeff"):
        text = text[1:]
    assert 'chara="莫妮卡"' in text
    assert "要返回吗？" in text
    assert "モニカ" not in text

    patch = tmp_path / "patch_tree"
    n = stage_normalized_tree(scripts, patch)
    assert n >= 1
    assert (patch / "scenario" / "a.ks").is_file()
    # safe UI macro must be deployable after string-only patch
    assert is_deployable_ks_relpath(Path("scenario/macro.ks"))
    assert (patch / "scenario" / "macro.ks").is_file()
    macro_txt = (patch / "scenario" / "macro.ks").read_bytes().decode("utf-16-le")
    assert "无法选择" in macro_txt


# ---------------------------------------------------------------------------
# Remaining limitations (cannot fully automate) — documented checklist file
# ---------------------------------------------------------------------------


def test_manual_checklist_exists():
    p = ROOT / "tests" / "验收清单.txt"
    assert p.is_file()
    body = p.read_text(encoding="utf-8")
    assert "图片 UI" in body or "图片UI" in body
    assert "实机" in body
