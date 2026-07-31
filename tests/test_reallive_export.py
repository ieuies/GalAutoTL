# -*- coding: utf-8 -*-
from pathlib import Path

from app.core.reallive_export import find_seen_txt, _occupied_slots


def test_find_seen_txt(tmp_path: Path):
    assert find_seen_txt(tmp_path) is None
    p = tmp_path / "SEEN.TXT"
    p.write_bytes(b"\x00" * 100)
    assert find_seen_txt(tmp_path) == p


def test_occupied_slots_empty_index(tmp_path: Path):
    # 10000 * 8 empty index + no payloads
    seen = tmp_path / "SEEN.TXT"
    seen.write_bytes(b"\x00" * 80000)
    assert _occupied_slots(seen) == []
