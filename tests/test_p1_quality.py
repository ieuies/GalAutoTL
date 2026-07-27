# -*- coding: utf-8 -*-
"""P1: RealLive remain merge, Artemis mini e2e, remain kanji coverage."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.core.pipeline_harden import looks_untranslated, write_remainder_report
from app.core.ks_script import collect_ks_units, write_ks
from app.pipelines.reallive import (
    LINE_RE,
    _collect_utf_lines,
    _utf_body,
    translate_utf_tree,
)


def test_reallive_collects_kanji_ui(tmp_path: Path):
    p = tmp_path / "a.utf"
    p.write_text("<001> 確認\n<002> こんにちは\n", encoding="utf-8")
    _lines, pending = _collect_utf_lines(p)
    bodies = {b for _, _, b in pending}
    assert "確認" in bodies
    assert "こんにちは" in bodies


def test_reallive_remain_merges_existing_cn(tmp_path: Path):
    jp = tmp_path / "jp"
    cn = tmp_path / "cn"
    jp.mkdir()
    cn.mkdir()
    (jp / "s.utf").write_text(
        "<001> あいう\n<002> 確認\n<003> さようなら\n",
        encoding="utf-8",
    )
    (cn / "s.utf").write_text(
        "<001> 一二三\n<002> 確認\n<003> 再见\n",
        encoding="utf-8",
    )

    calls: list[list[str]] = []

    class _Client:
        model = "m"

        def chat(self, *a, **k):
            raise RuntimeError("should use batch mock")

    def _fake_batch(bodies, *a, **k):
        calls.append(list(bodies))
        return ["确认了" if b == "確認" else f"T:{b}" for b in bodies]

    import app.pipelines.reallive as rl

    cfg = SimpleNamespace(
        api_base="",
        api_key="x",
        api_model="m",
        temperature=0,
        lang="zh_cn",
        batch_size=8,
        cp932_safe=False,
        source_lang="ja",
        game_dir=str(tmp_path),
        text_dir="",
        mt_polish=False,
        do_backup=False,
        extra={"remain_filter": {"確認"}},
    )
    orig = rl.translate_batch
    rl.translate_batch = _fake_batch
    try:
        n = translate_utf_tree(jp, cn, cfg)  # type: ignore[arg-type]
    finally:
        rl.translate_batch = orig

    assert n >= 1
    assert calls and calls[0] == ["確認"]
    text = (cn / "s.utf").read_text(encoding="utf-8")
    assert "确认了" in text
    assert "一二三" in text  # unrelated CN preserved
    assert "再见" in text


def test_reallive_remainder_uses_bare_body(tmp_path: Path):
    cn = tmp_path / "cn"
    cn.mkdir()
    (cn / "s.utf").write_text("<012> こんにちは\n", encoding="utf-8")
    left = []
    for line in (cn / "s.utf").read_text(encoding="utf-8").splitlines():
        b = _utf_body(line)
        assert b == "こんにちは"
        assert not b.startswith("<")
        left.append(b)
    n = write_remainder_report(tmp_path, "reallive", left, {s: s for s in left})
    assert n >= 1
    raw = (tmp_path / "GalAutoTL_remain.txt").read_text(encoding="utf-8")
    assert "JP: こんにちは" in raw
    assert "JP: <012>" not in raw


def test_artemis_mini_e2e_collect_apply(tmp_path: Path):
    from app.core.artemis_text import apply_artemis_units, collect_artemis_units

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "01.ast").write_text(
        'text={"こんにちは、今日はいい天気ですね。"}\n'
        'name={"name", name="太郎"}\n',
        encoding="utf-8",
    )
    units = collect_artemis_units(scripts)
    assert units
    sources = [u.source for u in units]
    mapping = {
        "こんにちは、今日はいい天気ですね。": "你好，今天天气真不错。",
        "太郎": "太郎",
    }
    translated = [mapping.get(u.source, u.source) for u in units]
    n = apply_artemis_units(units, translated)
    assert n >= 1
    text = (scripts / "01.ast").read_text(encoding="utf-8")
    assert "你好，今天天气真不错。" in text


def test_remain_includes_kanji_caption_collect(tmp_path: Path):
    root = tmp_path / "scripts"
    scen = root / "scenario"
    scen.mkdir(parents=True)
    write_ks(
        scen / "ui.ks",
        "[iscript]\n[endscript]\n"
        '@button title="設定"\n'
        '@sel caption="選択肢"\n'
        "確認\n",
        "utf-16-le",
    )
    units = collect_ks_units(root, source_lang="ja")
    sources = {u.source for u in units}
    assert "設定" in sources
    assert "選択肢" in sources
    assert "確認" in sources
    for s in ("設定", "選択肢", "確認"):
        assert looks_untranslated(s)
