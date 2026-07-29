# -*- coding: utf-8 -*-
"""Unity / Hazy writeback smoke tests (no real game packs required)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.core.unity_hazy_text import (
    _replace_ssei_by_txtid,
    _replace_window_by_txtid,
    finalize_hazy_after_translate,
    sanitize_adv_text_payload,
)


def test_window_txtid_replace_keeps_wait_tags_and_single_txtid():
    script = (
        '10.ui.oat(WindowMessage:これはテストです。[l][p]|txtid=DOC01)'
        '11.ui.oat(WindowMessage:別の行です。[l]|txtid=DOC02)'
    )
    # CN missing waits + leaked |txtid= (classic MT contamination)
    by_id = {
        "DOC01": "这是测试。|txtid=DOC01)",
        "DOC02": "另一行。",
    }
    out, n = _replace_window_by_txtid(script, by_id, force=True)
    assert n == 2
    assert out.count("|txtid=DOC01") == 1
    assert out.count("|txtid=DOC02") == 1
    assert "这是测试。[l][p]|txtid=DOC01" in out
    assert "另一行。[l]|txtid=DOC02" in out
    # body must not re-embed txtid
    assert "|txtid=DOC01)|txtid=" not in out
    assert sanitize_adv_text_payload("坏|txtid=X)") == "坏"


def test_window_txtid_no_force_skips_identical_payload():
    script = "WindowMessage:已经是中文。[l]|txtid=A1"
    out, n = _replace_window_by_txtid(
        script, {"A1": "已经是中文。[l]"}, force=False
    )
    assert n == 0
    assert out == script


def test_ssei_txtid_replace_plain_and_url_encoded():
    # plain
    plain = 'adv.ssei(text="見回す",foo=1,txtid=CH01)'
    out, n = _replace_ssei_by_txtid(plain, {"CH01": "环顾四周"}, force=True)
    assert n == 1
    assert 'text="环顾四周"' in out
    assert "txtid=CH01" in out

    # percent-encoded JP slot stays encoded for CN
    enc = 'adv.ssei(text="%E8%A6%8B%E5%9B%9E%E3%81%99",bar=0,txtid=CH02)'
    out2, n2 = _replace_ssei_by_txtid(enc, {"CH02": "环顾四周"}, force=True)
    assert n2 == 1
    assert "txtid=CH02" in out2
    assert "%E7%8E%AF" in out2 or "环顾四周" in out2  # encoded or plain OK
    # must not drop txtid suffix structure
    assert out2.startswith("adv.ssei(text=")


def test_finalize_call_order_on_fake_game_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """finalize_hazy_after_translate must run harden steps in order without crashing."""
    calls: List[str] = []

    def track(name: str):
        def _fn(*_a: Any, **_k: Any):
            calls.append(name)
            if name == "apply":
                return 3
            if name == "fill":
                return {"hits": 1}
            if name == "ssei":
                return {"fillable": 2, "still_kana": 0}
            if name == "lineno":
                return {"a036_rows": 1, "a024_inline": 0}
            if name == "glossary":
                return 4
            if name == "resync":
                return 5
            return None

        return _fn

    import app.core.unity_hazy_text as hz

    monkeypatch.setattr(hz, "apply_hazy_mapping", track("apply"))
    monkeypatch.setattr(hz, "fill_existing_translations", track("fill"))
    monkeypatch.setattr(hz, "fill_ssei_choices_from_existing", track("ssei"))
    monkeypatch.setattr(hz, "scrub_lineno_leaks_in_hazy_packs", track("lineno"))
    monkeypatch.setattr(hz, "patch_hazy_localization_glossary", track("glossary"))
    monkeypatch.setattr(hz, "scrub_hazy_script_jp_fragments", track("scrub"))
    monkeypatch.setattr(hz, "resync_a024_from_a036_txtid", track("resync"))

    game = tmp_path / "fake_game"
    game.mkdir()
    # empty mapping + no dict: still runs post steps after apply skip path
    stats = finalize_hazy_after_translate(
        game,
        {"こんにちは": "你好"},
        dict_path=None,
        log=None,
    )
    assert calls == [
        "apply",
        "ssei",
        "lineno",
        "glossary",
        "scrub",
        "resync",
    ]
    assert stats.get("apply_hits") == 3
    assert stats.get("ssei_fillable") == 2
    assert stats.get("lineno_scrub") == 1
    assert stats.get("glossary") == 4
    assert stats.get("a024_resync") == 5


def test_finalize_with_dict_path_calls_fill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: List[str] = []

    def track(name: str, ret: Any = 0):
        def _fn(*_a: Any, **_k: Any):
            calls.append(name)
            return ret

        return _fn

    import app.core.unity_hazy_text as hz

    monkeypatch.setattr(hz, "apply_hazy_mapping", track("apply", 1))
    monkeypatch.setattr(
        hz, "fill_existing_translations", track("fill", {"rows": 2})
    )
    monkeypatch.setattr(
        hz, "fill_ssei_choices_from_existing", track("ssei", {})
    )
    monkeypatch.setattr(
        hz, "scrub_lineno_leaks_in_hazy_packs", track("lineno", {})
    )
    monkeypatch.setattr(hz, "patch_hazy_localization_glossary", track("glossary", 0))
    monkeypatch.setattr(hz, "scrub_hazy_script_jp_fragments", track("scrub"))
    monkeypatch.setattr(hz, "resync_a024_from_a036_txtid", track("resync", 0))

    d = tmp_path / "GalAutoTL.txt"
    d.write_text("a=b\n", encoding="utf-8")
    finalize_hazy_after_translate(tmp_path, {}, dict_path=d, log=None)
    assert calls[0] == "apply"
    assert "fill" in calls
    assert calls.index("fill") < calls.index("ssei")


def test_finalize_empty_game_dir_no_crash(tmp_path: Path):
    """No Hazy packs: finalize must not raise (apply returns 0 / steps no-op)."""
    game = tmp_path / "empty_unity"
    game.mkdir()
    stats = finalize_hazy_after_translate(game, {"あ": "啊"}, log=lambda _m: None)
    assert isinstance(stats, dict)
    assert "apply_hits" in stats
