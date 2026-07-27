# -*- coding: utf-8 -*-
"""Second full「开始汉化」must not re-API / overwrite finished Chinese."""
from __future__ import annotations

from pathlib import Path

from app.core.pipeline_harden import (
    CODEC_UNICODE,
    looks_already_chinese,
    translate_to_mapping,
)


def test_looks_already_chinese_dialogue():
    assert looks_already_chinese("你好，今天天气真不错。")
    assert looks_already_chinese("确定")
    assert looks_already_chinese("请选择存档")
    # Short kanji-only JP UI must stay eligible (do NOT match bare 了 inside 終了)
    assert not looks_already_chinese("選択肢")
    assert not looks_already_chinese("終了")
    assert not looks_already_chinese("了解")
    assert not looks_already_chinese("取消")  # JP/CN shared UI kanji
    assert not looks_already_chinese("こんにちは")
    assert not looks_already_chinese("セーブ")
    assert not looks_already_chinese("確認")
    assert not looks_already_chinese("設定")
    # CN 「取消」alone is ambiguous; long CN / other markers still protected
    assert looks_already_chinese("确定要取消吗")


def test_translate_to_mapping_skips_already_cn():
    calls = {"n": 0}

    class _Client:
        model = "test-model"

        def chat(self, *a, **k):
            calls["n"] += 1
            raise RuntimeError("API must not be called for already-CN corpus")

    mapping = translate_to_mapping(
        [
            "你好，请确定要保存吗？",
            "这是已经汉化过的对白内容啊。",
        ],
        _Client(),
        "zh_cn",
        codec=CODEC_UNICODE,
        cache=None,
        do_polish=False,
        source_lang="ja",
        label="主译",
    )
    assert calls["n"] == 0
    assert mapping.get("你好，请确定要保存吗？") == "你好，请确定要保存吗？"
    assert mapping.get("这是已经汉化过的对白内容啊。") == "这是已经汉化过的对白内容啊。"


def test_ks_tree_looks_cn(tmp_path: Path):
    from app.core.kirikiri_patch import ks_tree_looks_already_chinese
    from app.core.ks_script import write_ks

    scen = tmp_path / "scenario"
    scen.mkdir()
    # Realistic KAG lines so looks_like_ks_script accepts the file
    write_ks(
        scen / "a.ks",
        "[iscript]\n[endscript]\n"
        "你好，今天的天气真的很不错呢。\n"
        "请确定要继续吗？\n"
        "这是第三句中文对白内容。\n"
        "@wait time=200\n",
        "utf-16-le",
    )
    write_ks(
        scen / "b.ks",
        "; comment\n"
        "返回标题吧朋友们啊啊。\n"
        "读取存档请确定。\n"
        "开始游戏吧，这是中文。\n"
        "请选择你的选项内容。\n",
        "utf-16-le",
    )
    assert ks_tree_looks_already_chinese(tmp_path)


def test_apply_ks_units_wont_overwrite_cn(tmp_path: Path):
    from app.core.ks_script import KsUnit, apply_ks_units, write_ks, read_ks

    p = tmp_path / "a.ks"
    write_ks(p, "你好，请确定继续。\n", "utf-16-le")
    units = [
        KsUnit(
            path=p,
            line_index=0,
            kind="line",
            source="你好，请确定继续。",
            encoding="utf-16-le",
            attr_key="",
        ),
    ]
    n = apply_ks_units(units, ["被翻坏的句子"])
    assert n == 0
    text, _ = read_ks(p)
    assert "你好，请确定继续。" in text
    assert "被翻坏" not in text


def test_find_plaintext_no_cn_fallback_with_archive(tmp_path: Path, monkeypatch):
    """CN unencrypted + XP3 present → do not use CN tree as JP source."""
    from app.core import kirikiri_patch as kp
    from app.core.ks_script import write_ks
    import app.core.xp3_io as xp3_io

    game = tmp_path / "game"
    unenc = game / "unencrypted" / "scenario"
    unenc.mkdir(parents=True)
    body = "[iscript]\n[endscript]\n" + "\n".join(
        [
            "你好，今天的天气真的很不错呢。",
            "请确定要继续吗？",
            "这是第三句中文对白内容。",
            "我们一起回去吧，好吗？",
            "这里什么都没有了啊。",
            "请您关闭这个窗口。",
            "@wait time=200",
        ]
    ) + "\n"
    write_ks(unenc / "a.ks", body, "utf-16-le")
    assert kp.ks_tree_looks_already_chinese(game / "unencrypted")
    monkeypatch.setattr(xp3_io, "find_xp3_archives", lambda _g: [game / "data.xp3"])
    assert kp.find_plaintext_source(game, None) is None


def test_find_plaintext_allows_cn_unencrypted_without_archive(tmp_path: Path, monkeypatch):
    from app.core import kirikiri_patch as kp
    from app.core.ks_script import write_ks
    import app.core.xp3_io as xp3_io

    game = tmp_path / "game"
    unenc = game / "unencrypted" / "scenario"
    unenc.mkdir(parents=True)
    body = "[iscript]\n[endscript]\n" + "\n".join(
        [
            "你好，今天的天气真的很不错呢。",
            "请确定要继续吗？",
            "这是第三句中文对白内容。",
            "我们一起回去吧，好吗？",
            "这里什么都没有了啊。",
            "请您关闭这个窗口。",
            "@wait time=200",
        ]
    ) + "\n"
    write_ks(unenc / "a.ks", body, "utf-16-le")
    monkeypatch.setattr(xp3_io, "find_xp3_archives", lambda _g: [])
    src = kp.find_plaintext_source(game, None)
    assert src == game / "unencrypted"

