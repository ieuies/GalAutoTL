# -*- coding: utf-8 -*-
"""Sakana writeback must not touch UI/shaders that brick the game."""
from __future__ import annotations

from pathlib import Path

from app.core.sakana_text import collect_sakana_units, is_sakana_safe_rel


def test_sakana_safe_rel_only_scenario_ks():
    assert is_sakana_safe_rel("scenario/ep000.ks")
    assert not is_sakana_safe_rel("scenario\\main.ks")
    assert not is_sakana_safe_rel("scenario/define.ks")
    assert not is_sakana_safe_rel("scenario/start.ks")
    assert not is_sakana_safe_rel("ui/720p/title/title.scp")
    assert not is_sakana_safe_rel("system/shader/sprite.skfx")
    assert not is_sakana_safe_rel("sndconf.json")
    assert not is_sakana_safe_rel("img/720p/bg/rain.scp")


def test_collect_skips_ui_scripts(tmp_path: Path):
    scen = tmp_path / "scenario"
    scen.mkdir()
    (scen / "ep000.ks").write_text("こんにちは世界\n", encoding="utf-8")
    ui = tmp_path / "ui" / "title"
    ui.mkdir(parents=True)
    (ui / "title.scp").write_text("こんにちは\nスタート\n", encoding="utf-8")
    units = collect_sakana_units(tmp_path)
    assert units
    assert all(u.rel.startswith("scenario/") for u in units)
    assert all(u.rel.endswith(".ks") for u in units)


def test_collect_skips_define_and_comments(tmp_path: Path):
    scen = tmp_path / "scenario"
    scen.mkdir()
    (scen / "define.ks").write_text(";//トランジションを行う\nこんにちは\n", encoding="utf-8")
    (scen / "ep000.ks").write_text(
        ";//時間　：昼\n[FADEOUTBGM]\n隣に妻がいる。\n",
        encoding="utf-8",
    )
    units = collect_sakana_units(tmp_path)
    assert all(u.rel == "scenario/ep000.ks" for u in units)
    assert all(not u.source.strip().startswith(";") for u in units)
    assert any("隣" in u.source for u in units)


def test_collect_case_choice_lines(tmp_path: Path):
    scen = tmp_path / "scenario"
    scen.mkdir()
    (scen / "ep001.ks").write_text(
        ';//選択肢\n[case "話しかけてみる"]\n[case "話しかけないでおく"]\n本文です。\n',
        encoding="utf-8",
    )
    units = collect_sakana_units(tmp_path)
    cases = [u for u in units if u.kind == "case"]
    assert {u.source for u in cases} == {"話しかけてみる", "話しかけないでおく"}
