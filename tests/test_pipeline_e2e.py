# -*- coding: utf-8 -*-
"""Mini end-to-end: drive a real run_* pipeline with a fake AI client.

Before this file, no test exercised a full pipeline (解包→翻译→写回);
they only tested helper functions. These tests prove a pipeline can be
driven end-to-end with no external tools and no real API.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from app.config import AppConfig


class FakeAI:
    """Returns fixed numbered translations based on which source line appears
    in the user prompt. Handles batch prompts that may contain multiple lines."""

    model = "fake-model"
    _TABLE = {
        "こんにちは": "你好",
        "セーブ": "保存",
    }

    def __init__(self, *a, **k) -> None:
        pass

    def chat(self, system, user, retries=3) -> str:
        hits = [src for src in self._TABLE if src in user]
        if not hits:
            return "1|原文"
        # Return one numbered line per matched source (batch-safe).
        return "\n".join(f"{i + 1}|{self._TABLE[s]}" for i, s in enumerate(hits))


def _make_softpal_game(tmp_path: Path) -> Path:
    """Minimal classic SoftPal dir: data/SCRIPT.SRC + data/TEXT.DAT."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    header = b"\x00" + b"\x00" * 15

    def entry(idx: bytes, text: str) -> bytes:
        return idx + text.encode("cp932") + b"\x00"

    text_dat = (
        header
        + entry(b"\x01\x00\x00\x00", "こんにちは")
        + entry(b"\x02\x00\x00\x00", "セーブ")
    )
    # One PalScriptTextShow ref → offset 16 (the dialog line).
    block = bytearray(32)
    struct.pack_into("<I", block, 4, 16)
    struct.pack_into("<I", block, 12, 0x0FFFFFFF)
    block[24:32] = b"\x17\x00\x01\x00\x02\x00\x02\x00"
    (data_dir / "SCRIPT.SRC").write_bytes(bytes(block))
    (data_dir / "TEXT.DAT").write_bytes(text_dat)
    return tmp_path


def test_softpal_pipeline_end_to_end(tmp_path: Path, monkeypatch):
    """run_softpal: parse → translate (fake AI) → GBK writeback of both
    referenced dialog AND orphan UI rows."""
    import app.pipelines.softpal as sp

    game = _make_softpal_game(tmp_path)
    monkeypatch.setattr(sp, "OpenAICompatClient", FakeAI)

    cfg = AppConfig(
        game_dir=str(game),
        text_dir=str(game),
        api_key="x",
        api_model="fake",
    )
    cfg.do_backup = False
    cfg.mt_polish = False
    cfg.source_lang = "ja"
    cfg.lang = "zh_cn"

    logs: list[str] = []
    sp.run_softpal(cfg, log=logs.append)

    out_text = game / "data" / "TEXT.DAT"
    assert out_text.is_file(), "写回 TEXT.DAT 应存在"
    raw = out_text.read_bytes()
    assert "你好".encode("gbk") in raw, "被引用对白应写回中文"
    assert "保存".encode("gbk") in raw, "孤立 UI 条目也应写回中文"

    out_script = game / "data" / "SCRIPT.SRC"
    assert out_script.is_file(), "写回 SCRIPT.SRC 应存在"


def test_softpal_pipeline_rejects_empty_text(tmp_path: Path, monkeypatch):
    """No translatable text → clean RuntimeError (not a silent success)."""
    import app.pipelines.softpal as sp

    game = tmp_path / "game"
    (game / "data").mkdir(parents=True)
    # TEXT.DAT with only a header (no entries), SCRIPT with no show refs
    (game / "data" / "TEXT.DAT").write_bytes(b"\x00" + b"\x00" * 15)
    (game / "data" / "SCRIPT.SRC").write_bytes(b"\x00" * 32)
    monkeypatch.setattr(sp, "OpenAICompatClient", FakeAI)

    cfg = AppConfig(game_dir=str(game), text_dir=str(game), api_key="x", api_model="fake")
    cfg.do_backup = False
    cfg.mt_polish = False
    cfg.source_lang = "ja"
    cfg.lang = "zh_cn"

    with pytest.raises(RuntimeError):
        sp.run_softpal(cfg, log=lambda m: None)


def test_generic_text_pipeline_end_to_end(tmp_path: Path, monkeypatch):
    """run_generic on a plain .txt dir: collect → translate (fake AI) → writeback."""
    import app.pipelines.generic_text as gt

    game = tmp_path / "game"
    (game / "script").mkdir(parents=True)
    script = game / "script" / "story.txt"
    script.write_text("こんにちは、世界。\nまた明日。\n", encoding="utf-8")

    # Avoid detect_engine routing this dir to an engine pipeline: a bare txt
    # folder with no .xp3/.ks/.pac etc. should classify as generic text.
    monkeypatch.setattr(gt, "OpenAICompatClient", FakeAI)

    cfg = AppConfig(game_dir=str(game), text_dir=str(game), api_key="x", api_model="fake")
    cfg.do_backup = False
    cfg.mt_polish = False
    cfg.source_lang = "ja"
    cfg.lang = "zh_cn"

    logs: list[str] = []
    gt.run_generic(cfg, log=logs.append)

    out = script.read_text(encoding="utf-8")
    assert "你好" in out, "txt 行应写回中文"
    assert "こんにちは" not in out, "日文原文应被替换"


def test_generic_text_pipeline_detects_engine_and_routes(tmp_path: Path, monkeypatch):
    """A .xp3 file must route generic → kirikiri instead of mis-translating a pack."""
    import app.pipelines.generic_text as gt
    import app.pipelines.kirikiri as kr

    game = tmp_path / "game"
    game.mkdir()
    (game / "data.xp3").write_bytes(b"XP3\x00")
    called = {"n": 0}

    def _fake_kirikiri(cfg, log=None, progress=None, cancel=None):
        called["n"] += 1

    # run_generic does `from app.pipelines.kirikiri import run_kirikiri` inside the
    # function — patch the real function in its defining module.
    monkeypatch.setattr(kr, "run_kirikiri", _fake_kirikiri)
    monkeypatch.setattr(gt, "OpenAICompatClient", FakeAI)

    cfg = AppConfig(game_dir=str(game), text_dir=str(game), api_key="x", api_model="fake")
    cfg.do_backup = False
    cfg.mt_polish = False

    gt.run_generic(cfg, log=lambda m: None)
    assert called["n"] == 1, "带 .xp3 的目录应路由到 kirikiri 管线"


def test_detect_drills_into_nested_game_folder(tmp_path: Path):
    """Game one folder deep (root/GameName/data.xp3) must still detect engine."""
    from app.core.detect import detect_engine

    root = tmp_path / "SteamLibrary"
    game = root / "MyGame"
    game.mkdir(parents=True)
    (game / "data.xp3").write_bytes(b"XP3\x00")

    det = detect_engine(root)
    assert det.pipeline == "kirikiri", f"嵌套游戏应识别为 kirikiri，实际 {det.pipeline}"
    assert det.hints and "子文件夹" in det.hints[0], "应提示检测到子文件夹"


def test_detect_nested_does_not_misjudge_plain_text(tmp_path: Path):
    """A folder of plain text must stay generic even with subfolders present."""
    from app.core.detect import detect_engine

    root = tmp_path / "texts"
    root.mkdir()
    (root / "readme.txt").write_text("plain text\n", encoding="utf-8")

    det = detect_engine(root)
    assert det.pipeline == "generic", f"纯文本目录不应误判，实际 {det.pipeline}"


def test_detect_nested_depth_capped(tmp_path: Path):
    """Deep nesting must not trigger runaway recursion."""
    from app.core.detect import detect_engine

    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "x.txt").write_text("text\n", encoding="utf-8")

    det = detect_engine(tmp_path / "a")  # should terminate quickly
    assert det.pipeline in ("generic", "packed")
