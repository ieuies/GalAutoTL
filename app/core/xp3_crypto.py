# -*- coding: utf-8 -*-
"""XP3 simple XOR schemes (Neko-style) — try mechanically when archive is flagged encrypted.

Based on awaken1ng/krkr-xp3 encryption_parameters. Does NOT cover cxdec / per-game
vendor keys; those need GARbro scheme tables.
"""
from __future__ import annotations

from typing import Dict, Tuple

# name -> (master_key, secondary_key, xor_first_byte)
XOR_SCHEMES: Dict[str, Tuple[int, int, bool]] = {
    "neko_vol1": (0x1548E29C, 0xD7, False),
    "neko_vol1_steam": (0x44528B87, 0x23, False),
    "neko_vol0": (0x1548E29C, 0xD7, True),
    "neko_vol0_steam": (0x44528B87, 0x23, True),
}


def xor_decrypt(data: bytes, adler32: int, scheme: str) -> bytes:
    master_key, secondary_key, xor_first = XOR_SCHEMES[scheme]
    adler_key = adler32 ^ master_key
    xor_key = (adler_key >> 24 ^ adler_key >> 16 ^ adler_key >> 8 ^ adler_key) & 0xFF
    if not xor_key:
        xor_key = secondary_key
    buf = bytearray(data)
    if xor_first and buf:
        first_key = adler_key & 0xFF
        if not first_key:
            first_key = master_key & 0xFF
        buf[0] ^= first_key
    for i in range(len(buf)):
        buf[i] ^= xor_key
    return bytes(buf)


# minimum kag_text_quality a trial must reach to be trusted as the filter key
_XP3DEC_OK_QUALITY = 40


def filter_xor_adler_lowbyte(data: bytes, adler32: int) -> bytes:
    """xp3dec.tpm-style filter: XOR every byte with (FileHash & 0xFF).

    Used by some Wamsoft / commercial Kirikiri titles (e.g. 洗脳航路).
    FileHash is the XP3 adlr field of the *plaintext* (pre-filter) stream.
    Falls back to scanning other XOR keys when the adlr low byte does not
    yield readable KAG (needed for a minority of files in some packs).

    Scoring uses :func:`kag_text_quality` (UTF-16 BOM aware), so UTF-16LE
    KAG scripts decrypt via the primary key instead of being rejected by a
    cp932-only heuristic.
    """
    primary = adler32 & 0xFF
    trial = bytes(b ^ primary for b in data) if primary else data
    if primary and kag_text_quality(trial) >= _XP3DEC_OK_QUALITY:
        return trial

    best_b = trial
    best_s = kag_text_quality(best_b)
    for k in range(256):
        if k == primary:
            continue
        trial = bytes(b ^ k for b in data)
        sc = kag_text_quality(trial)
        if sc > best_s:
            best_s = sc
            best_b = trial
            if sc >= _XP3DEC_OK_QUALITY:
                break
    return best_b


def kag_text_quality(data: bytes) -> int:
    """Score how much ``data`` resembles real KAG script text (higher = better).

    Used by the xp3dec adlr filter to prefer a filtered trial over raw bytes
    when BOTH pass the lenient ``looks_like_kag_after_decode`` heuristic.
    Filter garbage sometimes decodes (as cp932/UTF-16 mojibake) into text that
    still contains kana / ``[`` tags and thereby passes the heuristic, so the
    quality score breaks the tie: real KAG scripts decode cleanly (few U+FFFD),
    are line-oriented, and usually open with a ``;``-comment / ``[``-tag line.
    """
    if not data or not looks_like_text(data):
        return -1
    if data[:2] == b"\xff\xfe":
        text = data[2:8000].decode("utf-16-le", errors="replace")
    elif data[:2] == b"\xfe\xff":
        text = data[2:8000].decode("utf-16-be", errors="replace")
    else:
        chunk = data[:8000]
        try:
            text = chunk.decode("cp932")
        except UnicodeDecodeError:
            try:
                text = chunk.decode("utf-8")
            except UnicodeDecodeError:
                text = chunk.decode("cp932", errors="replace")
    fffd = text.count("\ufffd")
    nl = text.count("\n")
    kana = sum(1 for c in text if "\u3040" <= c <= "\u30ff")
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    header = 12 if data[:4] == b"\xff\xfe;\x00" else (6 if data[:1] in (b";", b"[") else 0)
    return header + nl * 3 + kana * 2 + cjk - fffd * 5


def looks_like_text(data: bytes) -> bool:
    """Cheap heuristic: UTF-16LE BOM / FE FE scramble / UTF-8 BOM / printable ASCII KAG."""
    if not data:
        return False
    # cxdec / xp3dec ciphertext magic (before filter)
    if data.startswith(b"\xda`G") or data.startswith(b"\xda\x60\x47"):
        return False
    if data[:2] in (b"\xff\xfe", b"\xfe\xff", b"\xef\xbb"):
        return True
    if len(data) >= 5 and data[0] == 0xFE and data[1] == 0xFE and data[3:5] == b"\xff\xfe":
        return True
    # UTF-16LE without BOM: many NULs in odd positions
    sample = data[: min(64, len(data))]
    if sample and sample[1::2].count(0) >= max(1, len(sample) // 4):
        return True
    head = data[:64]
    if b"[" in head or b";" in head or b"@" in head or b"*" in head:
        return True
    return False


def looks_like_kag_after_decode(data: bytes) -> bool:
    """Stricter: decrypted bytes must decode to KAG-ish Japanese/UTF text."""
    if not data or not looks_like_text(data):
        return False
    text = ""
    if data[:2] == b"\xff\xfe":
        text = data[2:8000].decode("utf-16-le", errors="replace")
    elif data[:2] == b"\xfe\xff":
        text = data[2:8000].decode("utf-16-be", errors="replace")
    else:
        # Avoid cutting CP932 mid-character (strict decode of data[:4000] false-negatives)
        chunk = data[:8000]
        while chunk and chunk[-1] >= 0x80:
            # drop trailing lead byte of incomplete MBCS sequence
            try:
                chunk.decode("cp932")
                break
            except UnicodeDecodeError:
                chunk = chunk[:-1]
        for enc in ("cp932", "utf-8"):
            try:
                text = chunk.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = data[:8000].decode("cp932", errors="replace")
    if "无法识别" in text or "疑似乱码" in text:
        return False
    sample = text[:3000]
    has_kana = any("\u3040" <= c <= "\u30ff" for c in sample)
    has_tag = any(c in sample for c in ";*[@")
    # ※ / 背景 etc. alone are enough with tags
    has_kag_mark = "※" in sample or "[tp]" in sample or "[name" in sample.lower()
    return bool(has_kana or has_kag_mark or (has_tag and len(sample.strip()) > 20))
