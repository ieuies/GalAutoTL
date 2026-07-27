# -*- coding: utf-8 -*-
"""Post-process / normalize JP strings using online XUA tips (splitter-friendly)."""
from __future__ import annotations

import re
from typing import List, Optional, Set, Tuple

from app.core.unity_raw_text import HAS_KANA, _want_jp

# Ending banners often glued in stringliteral.json
_END_HEAD = re.compile(
    r"^(GAME\s*OVER|BAD\s*END|GAME\s*CLEAR|TRUE\s*END|NORMAL\s*END)[\s\n\r]+(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_CODE_TAIL = re.compile(
    r"(BEVEL_ON|BILLBOARD|Background/|Character/|Audio/|SE/|BGM/|m_|get_|set_).*$",
    re.DOTALL,
)

# Chinese numeral phrases LLM often substitutes for Arabic digits
_CN_NUM_ATOM = re.compile(
    r"(?:[一二三四五六七八九两]?千[零〇一二三四五六七八九两百十]*|"
    r"[一二三四五六七八九两]?百[零〇一二三四五六七八九两十]*|"
    r"[二三四五六七八九]?十[一二三四五六七八九]?|"
    r"[一二三四五六七八九两零〇]|十)"
)
_FW_DIGIT = str.maketrans("０１２３４５６７８９", "0123456789")
_CN_NUM_CHARS = re.compile(r"[一二三四五六七八九十两零〇百千]")
_SUF = (
    "回合",
    "人",
    "年",
    "枚",
    "倍",
    "回",
    "次",
    "日",
    "月",
    "个",
    "個",
    "层",
    "層",
    "级",
    "級",
    "点",
    "點",
    "张",
    "張",
    "%",
    "％",
)


def digits_eaten(src: str, dst: str) -> bool:
    """True if src had Arabic digits that dst lost into Chinese numerals."""
    if not src or not dst:
        return False
    src_nums = re.findall(r"[0-9]+", src.translate(_FW_DIGIT))
    if not src_nums:
        return False
    dst_n = dst.translate(_FW_DIGIT)
    if not _CN_NUM_CHARS.search(dst):
        return False
    return any(dst_n.count(n) < src_nums.count(n) for n in set(src_nums))


def _missing_digit_queue(src_nums: list, dst: str) -> list:
    from collections import Counter

    dst_n = dst.translate(_FW_DIGIT)
    need = Counter(src_nums)
    have = Counter(re.findall(r"[0-9]+", dst_n))
    left = Counter({n: max(0, need[n] - have.get(n, 0)) for n in need})
    ordered: list = []
    for n in src_nums:
        if left[n] > 0:
            ordered.append(n)
            left[n] -= 1
    return ordered


def preserve_arabic_digits(src: str, dst: str) -> str:
    """Force Arabic digits from src into dst (never leave 一二三 for game numbers).

    Chinese numeral *idioms* (一计、乱七八糟…) are shielded so they are not
    eaten when the source line also contains unrelated dates/IDs.
    """
    if not src or not dst:
        return dst
    try:
        from app.core.mt_polish import shield_digit_idioms, unshield_digit_idioms

        dst_work, parks = shield_digit_idioms(dst)
    except Exception:
        dst_work, parks = dst, {}

    src_nums = re.findall(r"[0-9]+", src.translate(_FW_DIGIT))
    if not src_nums:
        return unshield_digit_idioms(dst_work, parks) if parks else dst

    out = dst_work
    if not digits_eaten(src, out) and re.findall(r"[0-9]+", out.translate(_FW_DIGIT)) == src_nums:
        return unshield_digit_idioms(out, parks) if parks else out

    # Pass 1: 「两个人」「一回合」「两次」→ 「2个人」「1回合」「2次」
    for suf in _SUF:
        q = _missing_digit_queue(src_nums, out)
        if not q:
            break
        pit = iter(q)
        pat = re.compile(_CN_NUM_ATOM.pattern + re.escape(suf))

        def _repl_suf(m: re.Match, s: str = suf) -> str:
            try:
                return next(pit) + s
            except StopIteration:
                return m.group(0)

        out = pat.sub(_repl_suf, out)

    # Pass 2: remaining bare Chinese numerals
    q = _missing_digit_queue(src_nums, out)
    if q:
        pit = iter(q)

        def _repl_bare(m: re.Match) -> str:
            try:
                return next(pit)
            except StopIteration:
                return m.group(0)

        out = _CN_NUM_ATOM.sub(_repl_bare, out)

    # Pass 3: single CN digit chars still eating slots
    q = _missing_digit_queue(src_nums, out)
    if q:
        pit = iter(q)

        def _repl_char(m: re.Match) -> str:
            try:
                return next(pit)
            except StopIteration:
                return m.group(0)

        out = re.sub(r"[一二三四五六七八九两零〇]", _repl_char, out)

    return unshield_digit_idioms(out, parks) if parks else out


def is_poison_dict_key(src: str) -> bool:
    """Reject keys that over-match (single kana, digit-only). Keep real short UI/CJK."""
    s = (src or "").strip("\x00").strip()
    if not s:
        return True
    if re.fullmatch(r"[0-9０-９\s.．、:：\-_/]+", s):
        return True
    # TMP icon-only / punct-only
    if not re.search(r"[\u3040-\u30ff\u4e00-\u9fffA-Za-z0-9]", s):
        return True
    letters = re.findall(r"[\u3040-\u30ff\u4e00-\u9fffA-Za-z]", s)
    if not letters:
        return True
    # Scrolling partials: single kana like 「ネ」poisons 「ネオヨコハマ」
    # Single CJK UI like 「下」「車」must stay.
    if len(letters) == 1:
        ch = letters[0]
        if "\u3040" <= ch <= "\u30ff" or ("A" <= ch <= "Z") or ("a" <= ch <= "z"):
            return True
    # crumbs that are only kana + pause: 「あ、」「も、」
    kana_only = re.findall(r"[\u3040-\u30ff]", s)
    if (
        len(s) <= 3
        and s[-1:] in "、。…"
        and len(kana_only) == len(letters)
        and len(letters) <= 2
    ):
        return True
    if re.match(r"^\{\{=\}\}", s):
        return True
    return False


def is_poison_dict_value(dst: str, src: str = "") -> bool:
    d = (dst or "").strip()
    if not d:
        return True
    # Batch leakage: value has '23. xxx' but source does not start with same index style
    m = re.match(r"^(\d{1,3})[\.、\)]\s+\S", d)
    if m:
        sm = re.match(r"^(\d{1,3})[\.、\)]\s+", (src or "").strip())
        if not sm or sm.group(1) != m.group(1):
            return True
    return False


def split_glued_game_strings(text: str) -> List[str]:
    """Split welded metadata / literal blobs into dialogue-sized pieces."""
    t = (text or "").strip("\x00").strip()
    if not t:
        return []
    out: List[str] = []
    seen: Set[str] = set()

    def add(s: str) -> None:
        s = s.strip()
        if not s or s in seen:
            return
        if is_poison_dict_key(s):
            return
        if not _want_jp(s, loose=True) and not HAS_KANA.search(s):
            if not re.search(r"[。！？「」ぁ-ゟァ-ヿ]", s):
                return
        seen.add(s)
        out.append(s)

    m = _END_HEAD.match(t)
    if m:
        body = m.group(2).strip()
        body = _CODE_TAIL.sub("", body).strip()
        add(t)  # full form still useful
        add(body)
        for part in re.split(r"(?<=[。！？\n])", body):
            add(part)
        return out

    # Strip code/path tails then sentence-split (do not emit tiny crumbs)
    cleaned = _CODE_TAIL.sub("", t).strip()
    if cleaned and cleaned != t:
        add(cleaned)
    if len(t) > 60 or "\n" in t or t.count("。") >= 1:
        for part in re.split(r"(?<=[。！？\n])", cleaned or t):
            add(part)
    else:
        add(t)
    return out


def scrub_translation_pair(src: str, dst: str) -> Optional[Tuple[str, str]]:
    """Return cleaned pair or None if it should be dropped."""
    src = (src or "").strip("\x00")
    dst = (dst or "").strip("\x00")
    if not src or not dst or src == dst:
        return None
    if is_poison_dict_key(src) or is_poison_dict_value(dst, src):
        return None
    dst = preserve_arabic_digits(src, dst)
    # Strip leaked batch index only when source itself is not numbered that way
    if is_poison_dict_value(dst, src):
        dst = re.sub(r"^\d{1,3}[\.、\)]\s+", "", dst).strip()
        dst = preserve_arabic_digits(src, dst)
    if not dst or src == dst:
        return None
    if is_poison_dict_value(dst, src):
        return None
    # Prefer drop over shipping 2→两
    if digits_eaten(src, dst):
        return None
    return src, dst


# No digit-prefix splitter: XUA looks up capture groups and re-introduces 1→一
XUA_SPLITTER_RULES = """\
# GalAutoTL splitters (XUnity sr: syntax)
sr:"^([\\ue000-\\uf8ff]+\\s*)([\\S\\s]+)$"=$1$2
sr:"^(GAME OVER|BAD END|GAME CLEAR)(\\n[\\S\\s]+)$"=$1$2
sr:"^(\\[.+?\\]\\s*)([\\S\\s]+)$"=$1$2
"""
