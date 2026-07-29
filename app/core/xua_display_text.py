# -*- coding: utf-8 -*-
"""Extract what the player actually sees from AdvScript / dump lines.

Deep-scan often harvests whole lines like::

    1841.ui.oat(WindowMessage:そうだ。[r]確か 興家…|txtid=…)

XUA never sees that string — TMP only gets the WindowMessage body. Shipping the
shell as a dict key wastes API quota and leaves the story Japanese while UI
fills with misaligned junk from GalAutoTL_review.txt.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

# 1841.ui.oat(...), 46.ui.mo(...), 3224.adv.ssei(...)
_SCRIPT_SHELL = re.compile(
    r"^(?P<num>\d+)\.(?P<cmd>(?:ui|adv|spr|lbl)\.[A-Za-z]+)\((?P<body>.*)\)\s*$",
    re.DOTALL,
)
_WINDOW_MSG = re.compile(
    r"WindowMessage:((?:(?!\|txtid=).)+)",
    re.DOTALL,
)
_TEXT_EQ = re.compile(
    r"(?:^|[,(\s])text\d*=([^,|\)]*)",
    re.IGNORECASE,
)
_NAME_EQ = re.compile(
    r"(?:^|[,(\s])name=([^,|\)]*)",
    re.IGNORECASE,
)
_ADV_TAG = re.compile(r"\[[a-zA-Z_/][^\]\n]*\]")
# line-number digit leaked into CN: 「1841个」「这42条」「资料3874览」
_LEAKED_INDEX = re.compile(
    r"(?<![0-9])(\d{2,5})(个|条|览|页|次|名|人|枚|件)"
)


def is_script_shell_key(s: str) -> bool:
    t = (s or "").strip()
    if not t:
        return False
    if _SCRIPT_SHELL.match(t):
        return True
    if re.match(r"^\d+\.(?:ui|adv|spr|lbl)\.", t):
        return True
    return False


def _maybe_urldecode_adv_slot(s: str) -> str:
    """Decode AdvScript percent-encoding (adv.ssei text=); plain text passes through."""
    if not s or "%" not in s:
        return s
    if not re.search(r"%[0-9A-Fa-f]{2}", s):
        return s
    try:
        from urllib.parse import unquote

        return unquote(s, encoding="utf-8", errors="replace")
    except Exception:
        return s


def extract_display_strings(s: str) -> List[str]:
    """Return human-visible JP/CN fragments suitable as XUA keys/values."""
    t = (s or "").strip("\x00").strip()
    if not t:
        return []
    body = t
    m = _SCRIPT_SHELL.match(t)
    if m:
        body = m.group("body") or ""

    out: List[str] = []
    seen = set()

    def add(x: str) -> None:
        x = (x or "").strip()
        if not x or x in seen:
            return
        # drop pure labels / ids
        if re.fullmatch(r"[A-Za-z0-9_\-./]+", x):
            return
        seen.add(x)
        out.append(x)
        # adv.ssei text="%E3%81%82…" → also index decoded JP/CN
        decoded = _maybe_urldecode_adv_slot(x)
        if decoded != x:
            add(decoded)

    for wm in _WINDOW_MSG.finditer(body):
        add(wm.group(1).strip())
    for te in _TEXT_EQ.finditer(body):
        add(te.group(1).strip().strip('"').strip("'"))
    for ne in _NAME_EQ.finditer(body):
        add(ne.group(1).strip().strip('"').strip("'"))

    if out:
        return out

    # Not a shell — plain UI / Hazy table cell
    if not m and not is_script_shell_key(t):
        # reject obvious bytecode / path dumps
        if t.count("=") >= 3 and "label=" in t.lower():
            return []
        add(t)
    return out


def strip_adv_tags(s: str) -> str:
    return _ADV_TAG.sub("", s or "").strip()


def pair_has_index_leak(src: str, dst: str) -> bool:
    """True when a leading script line-number appears as a fake quantity in CN."""
    sm = re.match(r"^(\d{2,5})\.", (src or "").strip())
    if not sm:
        return False
    num = sm.group(1)
    d = dst or ""
    if re.search(rf"(?<![0-9]){re.escape(num)}(个|条|览|页|次|名|人)", d):
        return True
    # 「启动资料3874览」style glued
    if re.search(rf"(?<![0-9]){re.escape(num)}(?![0-9])", d) and "WindowMessage" in src:
        return True
    return False


def is_misaligned_ui_pair(src: str, dst: str) -> bool:
    """Heuristic: short UI JP mapped to unrelated short CN (shift pollution)."""
    s = (src or "").strip()
    d = (dst or "").strip()
    if not s or not d or len(s) > 24:
        return False
    # known poisoned leftovers from early PARANORMASIGHT runs
    poison = {
        ("公園前", "从途中"),
        ("タイトルへ戻る", "兴家彰吾"),
        ("新着", "返回标题"),
        ("文化/社会", "最新"),
        ("途中から", "选项"),
        ("セーブデータ全消去", "公园前"),
    }
    if (s, d) in poison:
        return True
    # CN still wrapped in script shell
    if "ui.oat(" in d or "WindowMessage:" in d or d.startswith("|"):
        return True
    # Half JP left in "CN"
    if re.search(r"[\u3040-\u30ff]{2,}", d) and re.search(r"[\u4e00-\u9fff]", d):
        # mixed is sometimes OK (names); flag only when mostly still JP kana+kanji UI
        kana = len(re.findall(r"[\u3040-\u30ff]", d))
        if kana >= 4 and len(d) <= 40:
            return True
    return False


def expand_pair_to_display(src: str, dst: str) -> List[Tuple[str, str]]:
    """Map one harvested pair → zero or more display-level XUA pairs."""
    if pair_has_index_leak(src, dst) or is_misaligned_ui_pair(src, dst):
        return []
    srcs = extract_display_strings(src)
    if not srcs:
        return []
    dsts = extract_display_strings(dst)
    # If dst is plain CN (no shell), use it for every extracted src fragment
    if not dsts:
        if is_script_shell_key(dst) or "WindowMessage:" in (dst or ""):
            return []
        dsts = [dst.strip()] if (dst or "").strip() else []
    if not dsts:
        return []

    out: List[Tuple[str, str]] = []
    if len(srcs) == 1 and len(dsts) == 1:
        out.append((srcs[0], dsts[0]))
    elif len(srcs) == len(dsts):
        out.extend(zip(srcs, dsts))
    elif len(dsts) == 1:
        # URL-decode / tag variants of one JP shell → same plain CN
        for a in srcs:
            out.append((a, dsts[0]))
    else:
        # Prefer first WindowMessage-sized pair; avoid wrong zip
        out.append((srcs[0], dsts[0]))

    cleaned: List[Tuple[str, str]] = []
    for a, b in out:
        a, b = a.strip(), b.strip()
        if not a or not b or a == b:
            continue
        if is_script_shell_key(a) or is_misaligned_ui_pair(a, b):
            continue
        if pair_has_index_leak(a, b):
            continue
        cleaned.append((a, b))
        # also tag-stripped form for TMP without AdvScript tokens
        sa, sb = strip_adv_tags(a), strip_adv_tags(b)
        if sa and sb and sa != a and sa != sb:
            cleaned.append((sa, sb))
    return cleaned


def scrub_sources_for_translate(texts: List[str]) -> List[str]:
    """Deduped display strings only — drop script shells that cannot be extracted."""
    seen = set()
    out: List[str] = []
    for t in texts:
        parts = extract_display_strings(t)
        if not parts and not is_script_shell_key(t or ""):
            parts = [(t or "").strip()] if (t or "").strip() else []
        for p in parts:
            if not p or p in seen:
                continue
            if is_script_shell_key(p):
                continue
            seen.add(p)
            out.append(p)
    return out
