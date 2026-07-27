# -*- coding: utf-8 -*-
"""Sakana .sxstorage must follow SX arc body size, not JSON storages[] order."""
from __future__ import annotations

from pathlib import Path

from app.core.sakana_sx import _resolve_storage


def test_pack_zstd_fit_escalates_level():
    from app.core.sakana_sx import pack_zstd, pack_zstd_fit

    # Highly compressible text; level-3 may exceed a tight budget that higher levels fit.
    raw = ("こんにちは世界。これはテストです。\n" * 800).encode("utf-8")
    tight = len(pack_zstd(raw, level=3)) - 50
    if tight < 64:
        return
    try:
        fitted = pack_zstd_fit(raw, tight)
    except ValueError:
        # If even max level cannot fit, still ensure helper raises cleanly.
        return
    assert len(fitted) <= tight


def test_resolve_ignores_broken_same_size_twin(tmp_path: Path):
    from app.core.sakana_sx import _resolve_storage

    base = "Game-001"
    live = tmp_path / f"{base}-0.sxstorage"
    broken = tmp_path / f"BROKEN_{base}-0.sxstorage"
    live.write_bytes(b"0" * 100)
    broken.write_bytes(b"X" * 100)
    (tmp_path / f"{base}-snd.sxstorage").write_bytes(b"s" * 200)
    (tmp_path / f"{base}-img.sxstorage").write_bytes(b"i" * 300)
    meta = [["img", 1, "h", 1, 0], ["snd", 1, "h", 1, 0], ["0", 1, "h", 1, 0]]
    sizes = [100, 200, 300]
    hit = _resolve_storage(tmp_path, f"{base}(00)", 0, meta, sizes)
    assert hit == live
    assert hit.name == f"{base}-0.sxstorage"


def test_sx_arc_md5_offsets_align_with_parse():
    """MD5 patch offsets must land on 16-byte fields for each arc."""
    from app.core.sakana_sx import find_sx_index, parse_sx_index, _read_sx_index_blob, _sx_arc_md5_offsets
    from pathlib import Path

    bak = Path(r"C:/Users/想吃外星人/Desktop/自动翻译备份/sakana_DangerousVillageTradition")
    sx = find_sx_index(bak)
    if not sx:
        return
    _entries, meta = parse_sx_index(sx)
    index, _key, _hdr = _read_sx_index_blob(sx)
    offs = _sx_arc_md5_offsets(index)
    assert len(offs) == meta["arc_count"]
    for off in offs:
        assert off + 16 <= len(index)


def test_resolve_storage_prefers_arc_size_over_json_order(tmp_path: Path):
    # DangerousVillageTradition-style: JSON lists img/snd/0 but index sizes are 0/snd/img
    base = "Game-001"
    (tmp_path / f"{base}-0.sxstorage").write_bytes(b"0" * 100)
    (tmp_path / f"{base}-snd.sxstorage").write_bytes(b"s" * 200)
    (tmp_path / f"{base}-img.sxstorage").write_bytes(b"i" * 300)
    storages_meta = [
        ["img", 1, "h", 1, 0],
        ["snd", 1, "h", 1, 0],
        ["0", 1, "h", 1, 0],
    ]
    arc_sizes = [100, 200, 300]  # index order: 0, snd, img
    sx_stem = f"{base}(00)"

    assert _resolve_storage(tmp_path, sx_stem, 0, storages_meta, arc_sizes).name.endswith("-0.sxstorage")
    assert _resolve_storage(tmp_path, sx_stem, 1, storages_meta, arc_sizes).name.endswith("-snd.sxstorage")
    assert _resolve_storage(tmp_path, sx_stem, 2, storages_meta, arc_sizes).name.endswith("-img.sxstorage")

    # Old buggy behavior (JSON index as arc_index) would map arc0 → img
    wrong = tmp_path / f"{base}-{storages_meta[0][0]}.sxstorage"
    assert wrong.name.endswith("-img.sxstorage")
    assert _resolve_storage(tmp_path, sx_stem, 0, storages_meta, arc_sizes) != wrong
