# -*- coding: utf-8 -*-
"""Unit tests for the offline cxdec decryption engine (app/core/cxdec.py).

Covers: control-block discovery, LCG golden vectors, bytecode-VM round-trips
(cxdec is its own inverse), scheme JSON loading, and end-to-end extraction of a
synthetic encrypted XP3 archive.
"""
from __future__ import annotations

import json
import os
import struct
import zlib
from pathlib import Path

import pytest

from app.core import cxdec
from app.core.cxdec import (
    CTL_BLOCK_SIGNATURE,
    CxDecryptor,
    CxProgram,
    CxScheme,
    find_control_block,
    find_cxdec_scheme,
    scheme_from_dict,
)

CB_LEN = 0x400


def make_control_block(seed: int = 7) -> bytes:
    """Synthetic on-disk control block: 0x1000 bytes whose first 25 bytes are
    the ASCII marker (that is how it is embedded in real tpm/exe files)."""
    body = bytes(((i * seed + 3) & 0xFF) for i in range(len(CTL_BLOCK_SIGNATURE), 0x1000))
    return CTL_BLOCK_SIGNATURE + body


def inverted_block(raw: bytes) -> list:
    return [((~int.from_bytes(raw[i * 4 : i * 4 + 4], "little")) & 0xFFFFFFFF) for i in range(CB_LEN)]


def default_scheme(cb_raw: bytes | None = None) -> CxScheme:
    return CxScheme(
        mask=0x1FF,
        offset=0x1000,
        prolog_order=[1, 2, 0],
        odd_branch_order=[0, 4, 2, 3, 5, 1],
        even_branch_order=[5, 0, 3, 1, 2, 6, 4, 7],
        control_block=inverted_block(cb_raw or make_control_block()),
    )


# ---------------------------------------------------------------------------
# LCG / bytecode VM
# ---------------------------------------------------------------------------
class TestCxProgram:
    def test_get_random_lcg_golden(self):
        p = CxProgram(0, [0] * CB_LEN)
        assert p.get_random() == 0x3039            # 12345
        assert p.get_random() == 0xE3E5167E
        p1 = CxProgram(1, [0] * CB_LEN)
        assert p1.get_random() == 0x41C77EA6

    def test_get_random_uint32_wrap(self):
        p = CxProgram(0xFFFFFFFF, [0] * CB_LEN)
        for _ in range(16):
            v = p.get_random()
            assert 0 <= v <= 0xFFFFFFFF

    def test_length_limit_respected(self):
        p = CxProgram(0, [0] * CB_LEN)
        assert p.emit_u32(1) is True
        assert p.emit_nop(CxProgram.LENGTH_LIMIT) is False  # 4 + 0x80 > 0x80
        assert p.emit(cxdec.NOP, CxProgram.LENGTH_LIMIT) is False


class TestExecuteXCode:
    def test_golden_vectors(self):
        cb = list(range(CB_LEN))
        dec = CxDecryptor(
            CxScheme(
                mask=0x1FF,
                offset=0x1000,
                prolog_order=[1, 2, 0],
                odd_branch_order=[0, 4, 2, 3, 5, 1],
                even_branch_order=[5, 0, 3, 1, 2, 6, 4, 7],
                control_block=cb,
            )
        )
        vectors = {
            0x00000000: (0x0959C48F, 0x04ACE247),
            0x12345678: (0xEEBABC1A, 0x0EB62F65),
            0xFFFFFFFF: (0x55620400, 0x00000000),
            0x89ABCDEF: (0x018B454A, 0x00C37BF5),
        }
        for h, (r1, r2) in vectors.items():
            assert dec.execute_xcode(h) == (r1, r2)

    def test_deterministic_across_instances(self):
        a = CxDecryptor(default_scheme())
        b = CxDecryptor(default_scheme())
        for h in (0, 1, 0x1234, 0xDEADBEEF):
            assert a.execute_xcode(h) == b.execute_xcode(h)


class TestRoundTrip:
    """cxdec Decode is an involution, so decrypt∘decrypt == identity."""

    @pytest.mark.parametrize("hash_", [0, 1, 0x12345678, 0xFFFFFFFF])
    @pytest.mark.parametrize("offset", [0, 0x55, 0x1A2B])
    @pytest.mark.parametrize("size", [1, 64, 4096, 70000])
    def test_roundtrip(self, hash_, offset, size):
        dec = CxDecryptor(default_scheme())
        data = os.urandom(size)
        enc = dec.decrypt(hash_, data, offset=offset)
        assert enc != data
        assert dec.decrypt(hash_, enc, offset=offset) == data

    def test_nana_roundtrip(self):
        scheme = CxScheme(
            mask=0xFF,
            offset=0x2000,
            nana=True,
            random_seed=0xDEADBEEF,
            control_block=inverted_block(make_control_block(11)),
        )
        dec = CxDecryptor(scheme)
        data = os.urandom(10000)
        enc = dec.decrypt(0x55667788, data)
        assert dec.decrypt(0x55667788, enc) == data


# ---------------------------------------------------------------------------
# Control block discovery
# ---------------------------------------------------------------------------
class TestControlBlock:
    def test_find_on_dword_boundary(self):
        raw = make_control_block()
        tpm = bytearray(os.urandom(0x3000))
        tpm[0x1234 : 0x1234 + 0x1000] = raw
        cb = find_control_block(bytes(tpm))
        assert cb is not None
        assert cb == inverted_block(raw)

    def test_find_with_leading_padding(self):
        raw = make_control_block()
        tpm = b"\x00\x00\x00\x00" + raw + os.urandom(0x100)
        cb = find_control_block(tpm)
        assert cb is not None
        assert cb == inverted_block(raw)

    def test_skips_misaligned_marker(self):
        # marker at offset 2 is not dword-aligned; the scan advances by 4
        raw = make_control_block()
        tpm = bytearray(b"\x00\x00" + raw + os.urandom(0x200))
        assert find_control_block(bytes(tpm)) is None

    def test_too_short(self):
        assert find_control_block(os.urandom(0x800)) is None


# ---------------------------------------------------------------------------
# Scheme JSON loading
# ---------------------------------------------------------------------------
class TestSchemeLoading:
    def test_scheme_from_dict_with_hex_block(self):
        raw = make_control_block()
        d = {
            "mask": "0x1FF",
            "offset": "0x1000",
            "prolog_order": [1, 2, 0],
            "odd_branch_order": [0, 4, 2, 3, 5, 1],
            "even_branch_order": [5, 0, 3, 1, 2, 6, 4, 7],
            "control_block": raw.hex(),
        }
        s = scheme_from_dict(d)
        assert s.mask == 0x1FF
        assert s.offset == 0x1000
        assert s.control_block == inverted_block(raw)

    def test_find_cxdec_scheme_scans_tpm(self, tmp_path: Path):
        raw = make_control_block()
        (tmp_path / "GalAutoTL_cxdec.json").write_text(
            json.dumps(
                {
                    "mask": "0x1FF",
                    "offset": "0x1000",
                    "prolog_order": [1, 2, 0],
                    "odd_branch_order": [0, 4, 2, 3, 5, 1],
                    "even_branch_order": [5, 0, 3, 1, 2, 6, 4, 7],
                    "tpm": "game.tpm",
                }
            ),
            encoding="utf-8",
        )
        tpm = bytearray(os.urandom(0x2000))
        tpm[0x800 : 0x800 + 0x1000] = raw
        (tmp_path / "game.tpm").write_bytes(bytes(tpm))

        s = find_cxdec_scheme(tmp_path)
        assert s is not None
        assert s.control_block == inverted_block(raw)

    def test_find_cxdec_scheme_multi_game_map(self, tmp_path: Path, monkeypatch):
        raw = make_control_block(3)
        cfg = tmp_path / "appdata" / "GalAutoTL"
        cfg.mkdir(parents=True)
        (cfg / "cxdec_schemes.json").write_text(
            json.dumps(
                {
                    "someother": {"mask": "0x0", "offset": "0x0"},
                    "mygame": {
                        "mask": "0x1FF",
                        "offset": "0x1000",
                        "prolog_order": [1, 2, 0],
                        "odd_branch_order": [0, 4, 2, 3, 5, 1],
                        "even_branch_order": [5, 0, 3, 1, 2, 6, 4, 7],
                        "tpm": "g.tpm",
                    },
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
        game = tmp_path / "mygame"
        game.mkdir()
        tpm = bytearray(os.urandom(0x1800))
        tpm[0x400 : 0x400 + 0x1000] = raw
        (game / "g.tpm").write_bytes(bytes(tpm))

        s = find_cxdec_scheme(game)
        assert s is not None
        assert s.control_block == inverted_block(raw)


# ---------------------------------------------------------------------------
# End-to-end: synthetic encrypted XP3 -> cxdec extract
# ---------------------------------------------------------------------------
XP3_SIG = b"XP3\r\n \n\x1a\x8bg\x01"


def _build_cxdec_xp3(path: Path, scheme: CxScheme, entries):
    """entries: list of (relpath, plaintext).  Bodies are cxdec-encrypted with
    a fixed adlr hash; index marks them encrypted via an 'eliF' pre-chunk."""
    decryptor = CxDecryptor(scheme)
    HASH = 0x12345678
    with path.open("wb") as f:
        f.write(XP3_SIG)
        f.write(struct.pack("<Q", 0))  # index offset placeholder
        blobs = []
        for rel, plain in entries:
            enc = decryptor.decrypt(HASH, plain)
            offset = f.tell()
            f.write(enc)
            # info
            name_u16 = rel.encode("utf-16le") + b"\x00\x00"
            info_size = 4 + 8 + 8 + 2 + len(name_u16)
            info = b"info" + struct.pack("<Q", info_size) + struct.pack(
                "<IQQH", 0, len(plain), len(enc), len(rel)
            ) + name_u16
            adlr = b"adlr" + struct.pack("<QI", 4, HASH)
            segm = b"segm" + struct.pack("<Q", 28) + struct.pack(
                "<IQQQ", 0, offset, len(plain), len(enc)
            )
            body = info + adlr + segm
            blob = b"eliF" + struct.pack("<Q", 0) + b"File" + struct.pack("<Q", len(body)) + body
            blobs.append(blob)
        index_raw = b"".join(blobs)
        index_off = f.tell()
        f.write(struct.pack("<BQ", 0, len(index_raw)))
        f.write(index_raw)
        f.seek(len(XP3_SIG))
        f.write(struct.pack("<Q", index_off))
    return HASH


class TestExtractXp3Cxdec:
    def test_extract_recovers_plaintext(self, tmp_path: Path):
        scheme = default_scheme()
        jp = "[tp]こんにちは、世界。\n[name]主人公\n「今日はいい天気だね」\n"
        entries = [
            ("scenario/start.ks", (jp * 40).encode("cp932")),
            ("scenario/map.ks", (jp * 25).encode("cp932")),
            ("scenario/bgm.tjs", b"// tjs\n"),
        ]
        arc = tmp_path / "data.xp3"
        _build_cxdec_xp3(arc, scheme, entries)
        out = tmp_path / "out"
        n = cxdec.extract_xp3_cxdec(arc, out, scheme, log=None)
        assert n == 3
        got = (out / "scenario" / "start.ks").read_bytes()
        assert got == entries[0][1]
        assert (out / "scenario" / "map.ks").read_bytes() == entries[1][1]
        assert (out / "scenario" / "bgm.tjs").read_bytes() == entries[2][1]
        # decrypted .ks must pass the pipeline's strict deployable gate
        from app.core.kirikiri_patch import count_deployable_ks

        good, total = count_deployable_ks(out)
        assert (good, total) == (2, 2)

    def test_wrong_scheme_raises(self, tmp_path: Path):
        scheme = default_scheme()
        entries = [
            ("scenario/a.ks", b"[tp]hello\n" * 40),
            ("scenario/b.ks", b"[tp]world\n" * 40),
            ("scenario/c.ks", b"[tp]third\n" * 40),
        ]
        arc = tmp_path / "data.xp3"
        _build_cxdec_xp3(arc, scheme, entries)
        out = tmp_path / "out"
        wrong = CxScheme(
            mask=0x7F,
            offset=0x200,
            prolog_order=[0, 1, 2],
            odd_branch_order=[1, 2, 3, 4, 5, 0],
            even_branch_order=[2, 3, 4, 5, 6, 7, 0, 1],
            control_block=inverted_block(make_control_block(99)),
        )
        with pytest.raises(RuntimeError, match="cxdec"):
            cxdec.extract_xp3_cxdec(arc, out, wrong, log=None)
