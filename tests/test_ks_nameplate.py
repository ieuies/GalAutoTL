# -*- coding: utf-8 -*-
"""Regression: KAG speaker nameplates must never be sent to the MT model.

Real bug: `[name text="晶穗"]` values (speaker names) were collected as
translatable units; the AI hallucinated full dialogue sentences for them
(e.g. 晶穗 -> "啊！喂，你该不会是……哥哥吧？"), which broke the in-game name
box in 28/31 scenario files of the user's game.
"""
from __future__ import annotations

from pathlib import Path

from app.core.ks_script import _is_nameplate_value, collect_ks_units


def _write_ks_u16(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))


class TestNameplateValues:
    def test_is_nameplate_value(self):
        assert _is_nameplate_value("晶穗")
        assert _is_nameplate_value("俊郎")
        assert _is_nameplate_value("俊郎の母")
        assert _is_nameplate_value("ゆかり")
        assert not _is_nameplate_value("啊！喂，你该不会是……哥哥吧？")
        assert not _is_nameplate_value("我知道了，嗯～，人都到齐了吗？")
        assert not _is_nameplate_value("これは長いセリフですよ")


class TestCollectNameplatesSkipped:
    def test_name_tag_not_collected(self, tmp_path: Path):
        root = tmp_path / "_galautotl_kirikiri" / "scripts"
        _write_ks_u16(
            root,
            "scenario/story.ks",
            '[name text="晶穗"]\n'
            '[voice id="akh" file="vf10_000akh0000"]\n'
            "「それでね、兄ヤンってば…」\n"
            "[tp]\n",
        )
        units = collect_ks_units(root, source_lang="ja")
        sources = [u.source for u in units]
        assert "晶穗" not in sources
        assert "「それでね、兄ヤンってば…」" in sources

    def test_chara_nameplate_not_collected(self, tmp_path: Path):
        root = tmp_path / "_galautotl_kirikiri" / "scripts"
        _write_ks_u16(root, "scenario/story.ks", '@name chara="モニカ"\n「テスト」\n')
        units = collect_ks_units(root, source_lang="ja")
        assert "モニカ" not in [u.source for u in units]

    def test_long_attr_still_collected(self, tmp_path: Path):
        # title=/hint=/msg= values that are real UI text still translate
        root = tmp_path / "_galautotl_kirikiri" / "scripts"
        _write_ks_u16(
            root,
            "scenario/story.ks",
            '[title text="選択肢はまだありません"]\n[hint text="本編を見ていない"]\n',
        )
        units = collect_ks_units(root, source_lang="ja")
        sources = {u.source for u in units}
        assert "選択肢はまだありません" in sources
        assert "本編を見ていない" in sources
