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
