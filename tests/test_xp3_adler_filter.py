# -*- coding: utf-8 -*-
"""Regression tests for the xp3dec adlr-XOR filter in xp3_io / xp3_crypto.

Some KiriKiri packs (xp3dec.tpm titles) filter every script with
``XOR (FileHash & 0xFF)`` but leave the XP3 ENC bit clear.  The filter garbage
often decodes (as cp932/UTF-16 mojibake) into text that still passes the
lenient ``looks_like_kag_after_decode`` heuristic, so the post-extract filter
used to skip those files and they stayed encrypted (scenes stayed Japanese).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.xp3_crypto import (
    filter_xor_adler_lowbyte,
    kag_text_quality,
    looks_like_kag_after_decode,
)
from app.core.xp3_io import _apply_xp3dec_adler_filter


def _make_plain() -> bytes:
    text = (
        ";10_000\r\n\r\n"
        ";アイキャッチ\r\n"
        '[haikei file="ec01a" st="bg" fade="cross"]\r\n'
        "「こんにちは、世界。テストです」\r\n"
    ) * 30
    return b"\xff\xfe" + text.encode("utf-16-le")


def _filtered_file(tmp_path: Path, name: str, data: bytes, adler: int) -> Path:
    out = tmp_path / "out"
    out.mkdir(parents=True, exist_ok=True)
    p = out / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return out


class TestKagTextQuality:
    def test_plaintext_beats_xor_garbage(self):
        plain = _make_plain()
        garbage = bytes(b ^ 0x5A for b in plain)
        assert kag_text_quality(plain) > kag_text_quality(garbage)

    def test_bom_semicolon_header_bonus(self):
        plain = _make_plain()
        no_header = b"\xff\xfe" + plain[4:]  # drop the ';' comment char
        assert kag_text_quality(plain) > kag_text_quality(no_header)


class TestApplyAdlerFilter:
    def test_fixes_obvious_ciphertext(self, tmp_path: Path):
        """raw fails the heuristic -> filtered trial must win (original behavior)."""
        plain = _make_plain()
        adler = 0x1234_00AF
        key = adler & 0xFF
        raw = bytes(b ^ key for b in plain)
        assert not looks_like_kag_after_decode(raw)
        out = _filtered_file(tmp_path, "scenario/10_000.ks", raw, adler)
        # fake the XP3Entry via a lightweight namespace
        from types import SimpleNamespace

        entries = [SimpleNamespace(path="scenario/10_000.ks", adler32=adler)]
        fixed = _apply_xp3dec_adler_filter(out, entries)
        assert fixed == 1
        assert (out / "scenario" / "10_000.ks").read_bytes() == plain

    def test_fixes_ciphertext_that_passes_heuristic(self, tmp_path: Path):
        """THE regression: raw filter-garbage passes looks_like_kag_after_decode
        but the filtered trial is real text -> must still decrypt."""
        plain = _make_plain()
        # pick a key whose ciphertext (raw) passes the lenient heuristic
        hits = [
            k
            for k in range(1, 256)
            if looks_like_kag_after_decode(bytes(b ^ k for b in plain))
        ]
        assert hits, "no heuristic-passing key found"
        key = hits[0]
        raw = bytes(b ^ key for b in plain)
        assert looks_like_kag_after_decode(raw), "raw should pass heuristic"
        adler = key  # adler low byte == key (any low byte works)
        out = _filtered_file(tmp_path, "scenario/10_040.ks", raw, adler)
        from types import SimpleNamespace

        entries = [SimpleNamespace(path="scenario/10_040.ks", adler32=adler)]
        fixed = _apply_xp3dec_adler_filter(out, entries)
        assert fixed == 1
        assert (out / "scenario" / "10_040.ks").read_bytes() == plain

    def test_keeps_genuine_plaintext(self, tmp_path: Path):
        plain = _make_plain()
        adler = 0x1234_00AF
        out = _filtered_file(tmp_path, "scenario/10_020.ks", plain, adler)
        from types import SimpleNamespace

        entries = [SimpleNamespace(path="scenario/10_020.ks", adler32=adler)]
        fixed = _apply_xp3dec_adler_filter(out, entries)
        assert fixed == 0
        assert (out / "scenario" / "10_020.ks").read_bytes() == plain


class TestApplyAdlerFilterPrefixTrap:
    """Filter ciphertext that *coincidentally* starts with a BOM or `[` must
    still be decrypted (regression: the old fast-path skipped it and the
    game's opening 00_000.ks stayed garbled)."""

    def test_ciphertext_starting_with_bracket_is_fixed(self, tmp_path: Path):
        plain = _make_plain()
        # pick a key whose ciphertext starts with '[' (0x5B) AND passes the
        # lenient heuristic (exactly what happened in the real game)
        hits = []
        for k in range(1, 256):
            enc = bytes(b ^ k for b in plain)
            if enc[:1] == b"[" and looks_like_kag_after_decode(enc):
                hits.append(k)
                break
        assert hits, "no '['-prefixed heuristic-passing key found"
        key = hits[0]
        raw = bytes(b ^ key for b in plain)
        adler = key
        from types import SimpleNamespace

        out = _filtered_file(tmp_path, "scenario/00_000.ks", raw, adler)
        entries = [SimpleNamespace(path="scenario/00_000.ks", adler32=adler)]
        fixed = _apply_xp3dec_adler_filter(out, entries)
        assert fixed == 1
        assert (out / "scenario" / "00_000.ks").read_bytes() == plain

    def test_ciphertext_starting_with_bom_is_fixed(self, tmp_path: Path):
        plain = _make_plain()
        # key=1 makes the ciphertext start with the UTF-16BE BOM (0xFE 0xFF) —
        # a "looks like a BOM'd plaintext" trap that the old fast path skipped
        key = 1
        raw = bytes(b ^ key for b in plain)
        assert raw[:2] == b"\xfe\xff"
        from types import SimpleNamespace

        out = _filtered_file(tmp_path, "scenario/00_010.ks", raw, key)
        entries = [SimpleNamespace(path="scenario/00_010.ks", adler32=key)]
        fixed = _apply_xp3dec_adler_filter(out, entries)
        assert fixed == 1
        assert (out / "scenario" / "00_010.ks").read_bytes() == plain
