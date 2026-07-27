# -*- coding: utf-8 -*-
"""Tests for remain closed-loop, scenario sidecar, image UI scan."""
from __future__ import annotations

from pathlib import Path

from app.core.image_ui_scan import scan_image_ui_refs, write_image_ui_report
from app.core.pipeline_harden import (
    classify_remain_line,
    parse_remainder_report,
    remain_filter_set,
    write_remainder_report,
)
from app.core.scenario_sidecar import collect_scenario_sidecars


def test_classify_and_parse_remain(tmp_path: Path):
    assert classify_remain_line("セーブ") == "ui"
    assert classify_remain_line("こんにちは、今日はいい天気ですね") == "dialogue"
    assert classify_remain_line(r"image\btn.png") == "path"

    n = write_remainder_report(
        tmp_path,
        "test",
        ["こんにちは", "セーブ"],
        {"こんにちは": "こんにちは", "セーブ": "セーブ"},
    )
    assert n >= 1
    text = (tmp_path / "GalAutoTL_remain.txt").read_text(encoding="utf-8")
    assert "[dialogue]" in text or "[ui]" in text
    jps = parse_remainder_report(tmp_path / "GalAutoTL_remain.txt")
    assert "こんにちは" in jps


def test_second_pass_respects_allow_filter():
    from app.core.pipeline_harden import second_pass_sources

    remain = second_pass_sources(
        ["こんにちは", "さようなら"],
        {"こんにちは": "こんにちは", "さようなら": "さようなら"},
        allow={"こんにちは"},
    )
    assert remain == ["こんにちは"]


def test_common_ui_respects_remain_filter():
    from app.core.pipeline_harden import apply_common_ui

    full = apply_common_ui({})
    assert "セーブ" in full
    limited = apply_common_ui({}, remain_filter={"ロード"})
    assert "ロード" in limited
    assert "セーブ" not in limited


def test_seed_prior_cn_from_review(tmp_path: Path):
    from app.core.pipeline_harden import _seed_prior_cn, CODEC_UNICODE

    review = tmp_path / "GalAutoTL_review.txt"
    review.write_text(
        "### 1\nJP: こんにちは\nCN: 你好\n\n### 2\nJP: さようなら\nCN: 再见\n",
        encoding="utf-8",
    )
    mapping: dict = {}
    n = _seed_prior_cn(
        ["こんにちは", "セーブ", "あ"],
        {"あ"},
        mapping,
        codec=CODEC_UNICODE,
        cache=None,
        lang="zh_cn",
        model="m",
        source_lang="ja",
        game_dir=tmp_path,
    )
    assert n >= 1
    assert mapping.get("こんにちは") == "你好"
    assert "あ" not in mapping  # in remain_filter — not seeded


def test_remain_filter_set():
    class C:
        extra = {"remain_filter": ["あ", "い"]}

    assert remain_filter_set(C()) == {"あ", "い"}
    assert remain_filter_set(type("X", (), {"extra": {}})()) is None


def test_scenario_sidecar_collect(tmp_path: Path):
    root = tmp_path / "scripts"
    scen = root / "scenario"
    scen.mkdir(parents=True)
    (scen / "story.ks").write_bytes(b"\xff\xfe" + "「テスト」\n".encode("utf-16-le"))
    (scen / "names.txt").write_text("モニカ\n太郎\n", encoding="utf-8")
    items = collect_scenario_sidecars(root, source_lang="ja")
    bodies = {it.source for it in items}
    assert "モニカ" in bodies or "太郎" in bodies


def test_image_ui_scan(tmp_path: Path):
    scen = tmp_path / "scenario"
    scen.mkdir()
    (scen / "a.ks").write_text(
        '@button graphic="button_menu_start.png"\n'
        '[image storage="title_logo.tlg"]\n',
        encoding="utf-8",
    )
    hits = scan_image_ui_refs(tmp_path)
    assets = {h.asset for h in hits}
    assert "button_menu_start.png" in assets
    assert "title_logo.tlg" in assets
    path = write_image_ui_report(tmp_path, hits)
    assert path.is_file()
    assert "button_menu_start.png" in path.read_text(encoding="utf-8")
