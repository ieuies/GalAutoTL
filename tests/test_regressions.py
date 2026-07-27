# -*- coding: utf-8 -*-
"""pytest: regression locks for GalAutoTL correctness lessons."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.kirikiri_patch import is_deployable_ks_relpath, is_dialogue_ks_relpath
from app.core.ks_script import apply_ks_units, collect_ks_units, write_ks
from app.core.pipeline_harden import (
    CODEC_CP932,
    CODEC_GBK,
    CODEC_UNICODE,
    looks_untranslated,
    sanitize_dst,
    second_pass_sources,
)


def _write_ks_u16(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_ks(path, text, "utf-16-le")


def test_sanitize_rejects_poison_and_dots():
    assert sanitize_dst("无法识别，疑似乱码", "あ", CODEC_UNICODE) is None
    assert sanitize_dst("保存", "セーブ", CODEC_UNICODE) == "保存"
    assert sanitize_dst("读取存档", "ロード", CODEC_GBK) == "读取存档"
    assert sanitize_dst("・・・・・・・", "テストテスト", CODEC_CP932) is None


def test_second_pass_skips_pure_chinese():
    remain = second_pass_sources(
        ["こんにちは", "你好"],
        {"こんにちは": "こんにちは", "你好": "你好"},
    )
    assert remain == ["こんにちは"]
    assert second_pass_sources(["こんにちは"], {"こんにちは": "你好啊"}) == []


def test_looks_untranslated():
    assert looks_untranslated("こんにちは")
    assert not looks_untranslated("你好世界")


def test_ks_collects_chara_askyesno_hint(tmp_path: Path):
    root = tmp_path / "_galautotl_kirikiri" / "scripts"
    scen = root / "scenario"
    _write_ks_u16(
        scen / "story.ks",
        "\n".join(
            [
                '@name chara="俺"',
                '@name chara="モニカ"',
                "「テスト台詞」",
                "@eval exp=\"tf.result = askYesNo('戻りますか？')\"",
                "",
            ]
        ),
    )
    _write_ks_u16(
        scen / "macro.ks",
        "\n".join(
            [
                "@iscript",
                'hint:"本編で見ていないため、選択することが出来ません"',
                'kag.historyLayer.store("【選択肢】");',
                "@endscript",
                "",
            ]
        ),
    )
    units = collect_ks_units(root, source_lang="ja")
    sources = {u.source for u in units}
    assert "俺" in sources
    assert "モニカ" in sources
    assert "戻りますか？" in sources
    assert "本編で見ていないため、選択することが出来ません" in sources
    assert "【選択肢】" in sources


def test_ks_apply_chara_roundtrip(tmp_path: Path):
    root = tmp_path / "_galautotl_kirikiri" / "scripts"
    path = root / "scenario" / "n.ks"
    _write_ks_u16(path, '@name chara="モニカ"\n')
    units = collect_ks_units(root, source_lang="ja")
    assert any(u.source == "モニカ" for u in units)
    apply_ks_units(units, ["莫妮卡" if u.source == "モニカ" else u.source for u in units])
    raw = path.read_bytes()
    text = raw.decode("utf-16-le")
    if text.startswith("\ufeff"):
        text = text[1:]
    assert 'chara="莫妮卡"' in text
    assert "モニカ" not in text


def test_deploy_allows_scenario_macro_not_k_others():
    assert is_dialogue_ks_relpath(Path("scenario/story.ks"))
    assert not is_dialogue_ks_relpath(Path("scenario/macro.ks"))
    assert is_deployable_ks_relpath(Path("scenario/macro.ks"))
    assert is_deployable_ks_relpath(Path("scenario/dialog.ks"))
    assert not is_deployable_ks_relpath(Path("k_others/first.ks"))


def test_kagura_soft_fit_stays_cp932():
    from app.core.kagura_pak import _fit_encoded

    src = Path(__file__).resolve().parents[1] / "app" / "core" / "kagura_pak.py"
    assert "prefer_cp932=not prefer_cp932" not in src.read_text(encoding="utf-8")
    blob = _fit_encoded("超长测试句子会被截断处理", 8, prefer_cp932=True, soft_fit=True)
    assert blob is not None and len(blob) == 8
    blob.decode("cp932")  # must not be UTF-8 garbage


@pytest.mark.parametrize(
    "rel,forbid_cp932_true",
    [
        ("app/pipelines/kirikiri.py", True),
        ("app/pipelines/unity.py", True),
        ("app/pipelines/lcse.py", True),  # comment may mention it; check no call
    ],
)
def test_unicode_pipelines_never_force_cp932_api(rel: str, forbid_cp932_true: bool):
    root = Path(__file__).resolve().parents[1]
    text = (root / rel).read_text(encoding="utf-8")
    assert "CODEC_UNICODE" in text or "CODEC_GBK" in text
    # No live API force; allow comments containing the words
    live = [
        line
        for line in text.splitlines()
        if "cp932=True" in line and not line.strip().startswith("#")
    ]
    assert live == [], f"{rel} still forces cp932=True: {live}"


def test_kagura_pipeline_forces_cp932_codec():
    text = (
        Path(__file__).resolve().parents[1] / "app" / "pipelines" / "kagura.py"
    ).read_text(encoding="utf-8")
    assert "CODEC_CP932" in text
