# -*- coding: utf-8 -*-
"""Quality fixes: kanji-only JP collect, review merge, polish skip review."""
from __future__ import annotations

from pathlib import Path

from app.core.pipeline_harden import looks_untranslated
from app.core.review_table import export_review_table, load_review_overrides


def test_kanji_only_jp_collected(tmp_path: Path):
    from app.core.ks_script import collect_ks_units, write_ks

    root = tmp_path / "scripts"
    scen = root / "scenario"
    scen.mkdir(parents=True)
    write_ks(
        scen / "ui.ks",
        "[iscript]\n[endscript]\n"
        "確認\n"
        '@button title="設定"\n'
        '@sel caption="選択肢"\n'
        "こんにちは\n",
        "utf-16-le",
    )
    units = collect_ks_units(root, source_lang="ja")
    sources = {u.source for u in units}
    assert "確認" in sources
    assert "設定" in sources
    assert "選択肢" in sources
    assert "こんにちは" in sources


def test_finished_cn_still_skipped_in_collect(tmp_path: Path):
    from app.core.ks_script import collect_ks_units, write_ks

    root = tmp_path / "scripts"
    scen = root / "scenario"
    scen.mkdir(parents=True)
    write_ks(
        scen / "cn.ks",
        "[iscript]\n[endscript]\n"
        "你好，请确定要继续吗？\n"
        "这是已经汉化过的对白内容。\n",
        "utf-16-le",
    )
    units = collect_ks_units(root, source_lang="ja")
    sources = {u.source for u in units}
    assert "你好，请确定要继续吗？" not in sources
    assert "这是已经汉化过的对白内容。" not in sources


def test_looks_untranslated_kanji_ui():
    assert looks_untranslated("確認")
    assert looks_untranslated("設定")
    assert looks_untranslated("選択肢")
    assert not looks_untranslated("你好，请确定继续。")
    assert not looks_untranslated("确定")


def test_review_export_merges_second_pass(tmp_path: Path):
    export_review_table(
        tmp_path,
        ["こんにちは", "さようなら", "確認"],
        ["你好", "再见", "确认"],
    )
    # Simulate leak-pass export of only one line
    export_review_table(tmp_path, ["確認"], ["确认啦"])
    ov = load_review_overrides(tmp_path)
    assert ov.get("こんにちは") == "你好"
    assert ov.get("さようなら") == "再见"
    assert ov.get("確認") == "确认啦"


def test_review_export_keeps_hand_edit_on_hole(tmp_path: Path):
    export_review_table(tmp_path, ["あ"], ["手改译文"])
    # Batch reports untranslated hole for same JP
    export_review_table(tmp_path, ["あ"], ["あ"])
    ov = load_review_overrides(tmp_path)
    assert ov.get("あ") == "手改译文"


def test_polish_skips_review_file(tmp_path: Path):
    from app.core.mt_polish import discover_polish_targets, polish_file

    review = tmp_path / "GalAutoTL_review.txt"
    review.write_text(
        "### 1\nJP: 会社の清掃\nCN: 公司的清洁\n",
        encoding="utf-8",
    )
    (tmp_path / "cn_scenario").mkdir()
    assert review not in discover_polish_targets(tmp_path)
    assert polish_file(review) == 0
    assert "公司的清洁" in review.read_text(encoding="utf-8")
