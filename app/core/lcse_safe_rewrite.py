# -*- coding: utf-8 -*-
"""Safe SNX writeback: keep original instruction bytes & string slots (exact lengths).

Longer Chinese (GBK) than the JP original can overflow LCSE message buffers and
softlock mid-game. We clamp/pad every string into its original slot size and leave
STRING_REF offsets untouched.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Callable, Optional

from app.core.lcse_snx import RawScript, RawString, read_raw_snx, write_raw_snx

LogFn = Optional[Callable[[str], None]]


def _trunc_mb(body: bytes, room: int) -> bytes:
    if room <= 0:
        return b""
    if len(body) <= room:
        return body
    cut = body[:room]
    i = 0
    while i < len(cut):
        if cut[i] < 0x80:
            i += 1
        else:
            if i + 1 >= len(cut):
                cut = cut[:i]
                break
            i += 2
    return cut


def _fit_slot(content: bytes, slot: bytes) -> bytes:
    """Fit translated content into the exact original slot bytes length.

    Keep LCSE dialog trailer ``\\x02\\x03`` immediately before NUL — padding spaces
    must never be inserted between trailer and NUL (that breaks click-to-advance).
    """
    n = len(slot)
    if n == 0:
        return b""
    orig_has_nul = slot.endswith(b"\x00")
    body = content[:-1] if content.endswith(b"\x00") else bytes(content)

    trailer = b""
    if body.endswith(b"\x02\x03"):
        trailer = b"\x02\x03"
        body = body[:-2]

    nul_len = 1 if orig_has_nul else 0
    room = n - nul_len - len(trailer)
    if room < 0:
        # slot too small for trailer — fall back to original slot
        return slot
    body = _trunc_mb(body, room)
    if len(body) < room:
        body = body + (b" " * (room - len(body)))

    out = body + trailer + (b"\x00" if orig_has_nul else b"")
    if len(out) != n:
        return slot
    return out


def rewrite_snx_fixed_slots(orig_snx: bytes, translated_snx: bytes) -> bytes:
    """Replace string payloads only; instructions & slot sizes stay as original."""
    o = read_raw_snx(orig_snx)
    try:
        t = read_raw_snx(translated_snx)
    except Exception:
        return orig_snx
    if len(o.strings) != len(t.strings):
        return orig_snx

    t_by_ord = {s.ordinal: s for s in t.strings}
    new_strings: list[RawString] = []
    for s in sorted(o.strings, key=lambda x: x.offset):
        src = t_by_ord.get(s.ordinal)
        if src is None:
            content = s.content
        else:
            content = _fit_slot(src.content, s.content)
        new_strings.append(RawString(s.ordinal, s.offset, content))

    # keep original instructions verbatim (offsets still valid because slot sizes match)
    return write_raw_snx(RawScript(list(o.instructions), new_strings))


def harden_game_package(
    game_dir: Path,
    bak_pkg: Path,
    bak_lst: Path,
    *,
    key_byte: int,
    snx_key: int,
    log: LogFn = None,
) -> int:
    from app.core.lcse_pack import patch_package, unpack_scripts

    game_dir = Path(game_dir)
    pkg = game_dir / "lcsebody1"
    lst = game_dir / "lcsebody1.lst"
    td = Path(tempfile.mkdtemp(prefix="lcse_harden_"))
    bak_dir = td / "bak"
    cur_dir = td / "cur"
    out_dir = td / "out"
    out_dir.mkdir()
    unpack_scripts(bak_pkg, bak_lst, bak_dir, key_byte=key_byte, snx_key=snx_key, only_snx=True)
    unpack_scripts(pkg, lst, cur_dir, key_byte=key_byte, snx_key=snx_key, only_snx=True)

    changed = 0
    for bf in sorted(bak_dir.glob("*.snx")):
        cf = cur_dir / bf.name
        if not cf.exists():
            shutil.copy2(bf, out_dir / bf.name)
            continue
        try:
            safe = rewrite_snx_fixed_slots(bf.read_bytes(), cf.read_bytes())
        except Exception as e:
            if log:
                log(f"硬化失败，回退原文 {bf.name}: {e}")
            shutil.copy2(bf, out_dir / bf.name)
            continue
        if safe != bf.read_bytes():
            changed += 1
        (out_dir / bf.name).write_bytes(safe)

    # Prefer patching onto BACKUP package so asset offsets are pristine
    tmp = td / "patched"
    tmp.mkdir()
    patch_package(
        bak_pkg,
        bak_lst,
        out_dir,
        tmp / "lcsebody1",
        tmp / "lcsebody1.lst",
        key_byte=key_byte,
        snx_key=snx_key,
        snx_only=True,
    )
    shutil.copy2(tmp / "lcsebody1", pkg)
    shutil.copy2(tmp / "lcsebody1.lst", lst)
    if log:
        log(f"安全写回完成：{changed} 个 SNX 已按原槽位夹紧长度（指令表保持原版）")
    return changed
