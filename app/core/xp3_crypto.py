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


def filter_xor_adler_lowbyte(data: bytes, adler32: int) -> bytes:
    """xp3dec.tpm-style filter: XOR every byte with (FileHash & 0xFF).

    Used by some Wamsoft / commercial Kirikiri titles (e.g. 洗脳航路).
    FileHash is the XP3 adlr field of the *plaintext* (pre-filter) stream.
    Falls back to scanning other XOR keys when the adlr low byte does not
    yield readable KAG (needed for a minority of files in some packs).
    """
    def score(trial: bytes) -> int:
        try:
            text = trial.decode("cp932")
        except UnicodeDecodeError:
            return -1
        nl = text.count("\n")
        if nl < 5 and len(trial) > 200:
            return -1
        kana = sum(1 for c in text[:3000] if "\u3040" <= c <= "\u30ff")
        tags = text.count("[tp]") + text.count("[haikei") + text.count("※")
        return nl + kana * 2 + tags * 15

    primary = adler32 & 0xFF
    if primary:
        trial = bytes(b ^ primary for b in data)
        if score(trial) >= 80:
            return trial

    best_b = trial if primary else data
    best_s = score(best_b) if primary else -1
    for k in range(256):
        if k == primary:
            continue
        trial = bytes(b ^ k for b in data)
        sc = score(trial)
        if sc > best_s:
            best_s = sc
            best_b = trial
            if sc >= 120:
                break
    return best_b


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
