# -*- coding: utf-8 -*-
"""P0: BGI/YU-RIS/Sakana full-run prefers backup JP; polish skips deploy dirs."""
from __future__ import annotations

from pathlib import Path

from app.core.mt_polish import discover_polish_targets


def test_polish_skips_deployed_scenario_and_script(tmp_path: Path):
    (tmp_path / "cn_scenario").mkdir()
    (tmp_path / "cn_scenario" / "a.ks").write_text("你好请确定\n", encoding="utf-8")
    (tmp_path / "script").mkdir()
    (tmp_path / "script" / "a.ast").write_text('text={"你好请确定"}\n', encoding="utf-8")
    work = tmp_path / "_galautotl_kirikiri" / "scripts"
    work.mkdir(parents=True)
    (work / "b.ks").write_text("工作区\n", encoding="utf-8")
    hits = discover_polish_targets(tmp_path)
    names = {p.name for p in hits}
    assert "a.ks" not in names or all("cn_scenario" not in str(p) for p in hits)
    assert all("script" not in p.parts or "_galautotl_" in str(p) for p in hits)


def test_bgi_prepare_full_prefers_backup_arcs(tmp_path: Path, monkeypatch):
    from app.pipelines import bgi as bgi_mod

    game = tmp_path / "game"
    game.mkdir()
    # Fake: no arcs in game, existing CN loose would be wrong for full — with arcs in bak
    bak = tmp_path / "bak"
    bak.mkdir()
    (bak / "data01.arc").write_bytes(b"ARC")

    monkeypatch.setattr(bgi_mod, "_bak_dir", lambda _g: bak)
    monkeypatch.setattr(bgi_mod, "_find_arcs", lambda root: list(root.glob("*.arc")))
    called = {"n": 0}

    def _fake_extract(arcs, scripts, game_dir, tools_dir, log):
        called["n"] += 1
        called["arcs"] = [a.name for a in arcs]
        (scripts / "scene").mkdir(parents=True)
        # minimal fake script file so find_bgi_scripts can be stubbed
        (scripts / "scene" / "x").write_bytes(b"BurikoCompiledScript\x00")

    monkeypatch.setattr(bgi_mod, "_extract_arcs", _fake_extract)
    monkeypatch.setattr(
        bgi_mod,
        "find_bgi_scripts",
        lambda root: list(root.rglob("x")) if (root / "scene" / "x").exists() else [],
    )

    work = game / "_galautotl_bgi"
    work.mkdir()
    out = bgi_mod._prepare(game, work, None, "", remain_only=False)
    assert called["n"] == 1
    assert "data01.arc" in called["arcs"]
    assert out == work / "scripts"


def test_yuris_full_skips_loose_when_ypf(tmp_path: Path, monkeypatch):
    from app.pipelines import yuris as y_mod

    game = tmp_path / "game"
    ys = game / "ysbin"
    ys.mkdir(parents=True)
    (ys / "yst00001.ybn").write_bytes(b"YSTB")
    (game / "data.ypf").write_bytes(b"YPF")

    monkeypatch.setattr(y_mod, "_find_ypf", lambda root: list(root.glob("*.ypf")))
    monkeypatch.setattr(y_mod, "_bak_dir", lambda _g: tmp_path / "nobak")
    extracted = {"ok": False}

    def _fake_garbro(arc, sub, garbro, log):
        extracted["ok"] = True
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "yst00001.ybn").write_bytes(b"YSTBjp")
        return True

    monkeypatch.setattr(y_mod, "find_garbro", lambda _extra: Path("garbro"))
    monkeypatch.setattr(y_mod, "extract_with_garbro", _fake_garbro)

    work = game / "_w"
    work.mkdir()
    out = y_mod._ensure_ybn(game, work, None, "", remain_only=False)
    assert extracted["ok"]
    data = next(out.rglob("*.ybn")).read_bytes()
    assert data == b"YSTBjp"
