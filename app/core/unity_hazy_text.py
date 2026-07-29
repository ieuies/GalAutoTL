# -*- coding: utf-8 -*-
"""PARANORMASIGHT-style Hazy packs in StreamingAssets/a###.

These are UnityFS bundles with a short proprietary header before the UnityFS
magic. Story text lives in TextAssets such as Hazy_Script_JP / Hazy_Localization_JP
(id,text lines). The JP build reads dialogue from a024 scripts; a036 tables alone
are not enough — we harvest display strings for XUA runtime replace.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import quote, unquote

from app.core.unity_raw_text import HAS_KANA, HAS_CJK

LogFn = Optional[Callable[[str], None]]

_TAG_RE = re.compile(r"\[[^\]]+\]")
_CONTROL_TAG_RE = re.compile(r"\[[a-zA-Z_/][^\]\n]*\]")
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200d\ufeff\u2060\u00ad]")
_WEIRD_SPACE_RE = re.compile(r"[\u00a0\u3000]+")
# AdvScript ruby (il2cpp has sruby/vruby string literals). Not TMP [font=…].
_SRUBY_TAG_RE = re.compile(r"\[sruby[^\]]*\]", re.I)
_VRUBY_TAG_RE = re.compile(r"\[vruby[^\]]*\]", re.I)
_FF_RUBY_TAG_RE = re.compile(r"\[ff[^\]]*\]")
_F_RUBY_TAG_RE = re.compile(r"\[f(?!ont\b)[^\]]*\]")
_HAS_ADVSCRIPT_RUBY_RE = re.compile(r"\[(?:s?vruby|sruby|vruby|ff?(?!ont\b))", re.I)
# Localization / menu UI also does not parse color/scale/linebreak AdvScript tags.
_C_COLOR_TAG_RE = re.compile(r"\[c\d*\]", re.I)
_X_SCALE_TAG_RE = re.compile(r"\[x[^\]]*\]", re.I)
_SIZE_TAG_RE = re.compile(r"\[size=[^\]]*\]", re.I)
_RLP_TAG_RE = re.compile(r"\[([rlp])\]", re.I)
_HAS_ADVSCRIPT_UI_MARKUP_RE = re.compile(
    r"\[(?:(?:sruby|vruby|ff?(?!ont\b))[^\]]*|c\d*|x[^\]]*|size=[^\]]*|[rlp])\]",
    re.I,
)

_HAZY_NAME_HINTS = (
    "hazy_script_jp",
    "hazy_localization_jp",
    "hazy_script_en",  # keep for bilingual builds
    "scrolltexttest",
    "credit01_jp",
)

_WINDOW_MSG = re.compile(
    r"WindowMessage:((?:(?!\|txtid=).)+)",
    re.DOTALL,
)
# AdvScript: WindowMessage:…|txtid=first_0165)
_WINDOW_MSG_TXTID = re.compile(
    r"WindowMessage:((?:(?!\|txtid=).)+)\|txtid=([A-Za-z0-9_]+)",
    re.DOTALL,
)
_DIALOG_TEXT = re.compile(r"text\d+=([^,|\)]+)")
# Choice / yes-no buttons: adv.ssei(text="%URL%"|"plain",…,txtid=id)
_SSEI_TEXT = re.compile(r'(adv\.ssei\(text=")([^"]*)(")', re.I)
_SSEI_TXTID = re.compile(
    r'adv\.ssei\(text="([^"]*)"((?:(?!\btxtid=).)*?)\btxtid=([A-Za-z0-9_]+)',
    re.I | re.DOTALL,
)
_LINE_CMD_PREFIX = re.compile(
    r"^(\d+)\.(?:ui\.(?:oat|mo|aat)|adv\.ssei)\(",
    re.I,
)


def _urldecode_adv(s: str) -> str:
    """Decode AdvScript percent-encoding; plain text passes through."""
    if not s:
        return s
    if "%" in s:
        try:
            return unquote(s, encoding="utf-8", errors="strict")
        except Exception:
            try:
                return unquote(s, encoding="utf-8", errors="replace")
            except Exception:
                return s
    return s


def _urlencode_adv(s: str) -> str:
    """Percent-encode for adv.ssei text= (uppercase hex like the JP build)."""
    enc = quote(s or "", safe="")
    return re.sub(r"%[0-9a-fA-F]{2}", lambda m: m.group(0).upper(), enc)


def _scrub_lineno_leak(jp_key: str, cn: str) -> str:
    """Fix MT leak: AdvScript line number NNN substituted into CN.

    Keys like ``483.ui.oat(WindowMessage:…１回…)`` became ``仅限483次``.
    Same pattern poisons ``本所七不思議`` → ``本所482不思议``,
    ``メニューボタン`` → ``250个菜单按钮``, ``改めて`` → ``重新来382次``, etc.
    Also works when ``jp_key`` is plain bak JP (no ``N.ui.oat`` prefix).
    """
    if not jp_key or not cn:
        return cn
    out = cn
    # Strip leaked command shells from CN values
    if "adv.cltxt(" in out:
        out = re.sub(r"adv\.cltxt\([^)]*\)", "", out)
    m = _LINE_CMD_PREFIX.match(jp_key.strip())
    num = m.group(1) if m else None
    if num and num in out:
        n = re.escape(num)
        if re.search(r"[１1]\s*回|１回|1回|一度|改めて", jp_key):
            out = out.replace(f"仅限{num}次", "仅限1次")
            out = out.replace(f"能够使用{num}次", "能够使用1次")
            out = out.replace(f"使用{num}次", "使用1次")
            out = re.sub(rf"重新来{n}次", "重新来过", out)
            out = re.sub(rf"(?<!\d){n}次", "1次", out)
        if "七不思議" in jp_key or "七不思" in jp_key:
            out = out.replace(f"本所{num}不思议", "本所七不思议")
            out = out.replace(f"本所{num}不思議", "本所七不思议")
            out = out.replace(f"《本所{num}不思议》", "《本所七不思议》")
            out = re.sub(rf"(?<!\d){n}不思[议議]?", "七不思议", out)
        if "メニュー" in jp_key:
            out = re.sub(rf"这里有{n}个菜单按钮", "这里有菜单按钮", out)
            out = re.sub(rf"(?<!\d){n}个菜单", "菜单", out)
        if "いずれか１" in jp_key or "１個" in jp_key:
            out = re.sub(rf"任意{n}个", "任意一个", out)
            out = re.sub(rf"(?<!\d){n}个Joy", "一个Joy", out)
        if re.search(r"見回|ぐる|１周", jp_key):
            out = re.sub(rf"环顾{n}周", "环顾四周", out)
            out = re.sub(rf"环视{n}圈", "环视一周", out)
        if "ビックリ" in jp_key:
            out = re.sub(rf"吓我{n}跳", "吓我一跳", out)
        if "両国" in jp_key:
            out = re.sub(rf"(?<!\d){n}国桥", "两国桥", out)
        if re.search(r"壮絶|繰り広", jp_key):
            out = re.sub(rf"展开了{n}场惨烈", "展开了惨烈", out)
        if "深夜" in jp_key and re.search(r"[０0]時", jp_key):
            out = re.sub(rf"深夜{n}点", "深夜十二点", out)
    # Bak-JP path: any leaked digits in 本所NNN不思 / 仅限NNN次
    if "七不思議" in jp_key or "七不思" in jp_key:
        out = re.sub(r"本所\d{1,5}不思[议議]?", "本所七不思议", out)
        out = re.sub(r"(?<![0-9])\d{2,5}不思[议議]?", "七不思议", out)
    if re.search(r"[１1]\s*回|１回|1回|一度|改めて", jp_key):
        out = re.sub(r"仅限\d{2,5}次", "仅限1次", out)
        out = re.sub(r"能够使用\d{2,5}次", "能够使用1次", out)
        out = re.sub(r"重新来\d{2,5}次", "重新来过", out)
    if "ビックリ" in jp_key:
        out = re.sub(r"吓我\d{2,5}跳", "吓我一跳", out)
    if re.search(r"見回|ぐる|１周", jp_key):
        out = re.sub(r"环顾\d{2,5}周", "环顾四周", out)
        out = re.sub(r"环视\d{2,5}圈", "环视一周", out)
    if "両国" in jp_key:
        out = re.sub(r"(?<![0-9])\d{1,3}国桥", "两国桥", out)
    if "先生/小姐" in out and "[%0]" in jp_key:
        out = out.replace("先生/小姐", "[%0]大人")
    return out


def normalize_hazy_key(s: str) -> str:
    """Strip, drop zero-width/BOM, collapse odd whitespace."""
    if not s:
        return ""
    s = s.strip().lstrip("\ufeff")
    s = _ZERO_WIDTH_RE.sub("", s)
    s = _WEIRD_SPACE_RE.sub(" ", s)
    s = re.sub(r" +", " ", s)
    return s.strip()


def has_advscript_ruby(text: str) -> bool:
    """True if text contains AdvScript ruby tags ([sruby]/[vruby]/[f]/[ff])."""
    return bool(text and _HAS_ADVSCRIPT_RUBY_RE.search(text))


def has_advscript_ui_markup(text: str) -> bool:
    """True if text has AdvScript tags Localization UI will show raw."""
    return bool(text and _HAS_ADVSCRIPT_UI_MARKUP_RE.search(text))


# Trailing click/page wait tags that must survive dialogue writeback
_TRAILING_WAIT_TAGS_RE = re.compile(r"((?:\[(?:l|p)\])+)\s*$")


def preserve_click_wait_tags(src: str, cn: str) -> str:
    """Copy AdvScript click-wait tags from ``src`` onto ``cn`` so auto-skip cannot recur.

    Prefer the trailing ``[l]``/``[p]`` run from ``src`` (usual ``…[l][p]``).
    If ``src`` has wait tags but no trailing run, append any missing ``[l]``/``[p]``
    counts at the end. Never strips waits that ``cn`` already has beyond ``src``.
    """
    if not cn:
        return cn
    src = src or ""
    m = _TRAILING_WAIT_TAGS_RE.search(src)
    if m:
        trail = m.group(1)
        body = _TRAILING_WAIT_TAGS_RE.sub("", cn)
        return body + trail
    src_l, src_p = src.count("[l]"), src.count("[p]")
    if src_l == 0 and src_p == 0:
        return cn
    cn_l, cn_p = cn.count("[l]"), cn.count("[p]")
    missing = ""
    if src_l > cn_l:
        missing += "[l]" * (src_l - cn_l)
    if src_p > cn_p:
        missing += "[p]" * (src_p - cn_p)
    return cn + missing if missing else cn


def sanitize_advscript_markup_for_ui(text: str) -> str:
    """Strip AdvScript markup Localization / menu UI cannot parse.

    Removes ruby (``[sruby]/[vruby]/[f]/[ff]``), color (``[c4]``/``[c0]``),
    scale/size (``[x1.3]``/``[size=…]``), and linebreak/page (``[r]``/``[l]``/``[p]``).
    Keeps visible CJK/JP text. Does not touch TMP ``[font=…]``.
    Dialogue TELOP that *does* go through AdvScript should keep tags — call this
    only for UI/loc paths. Never use on Hazy_Script_JP / WindowMessage writeback.
    """
    if not text or not has_advscript_ui_markup(text):
        return text
    s = _SRUBY_TAG_RE.sub("", text)
    s = _VRUBY_TAG_RE.sub("", s)
    s = _FF_RUBY_TAG_RE.sub("", s)
    s = _F_RUBY_TAG_RE.sub("", s)
    s = _C_COLOR_TAG_RE.sub("", s)
    s = _X_SCALE_TAG_RE.sub("", s)
    s = _SIZE_TAG_RE.sub("", s)
    # [r]/[l]/[p]: drop; collapse leftover double spaces from mid-line [r]
    s = _RLP_TAG_RE.sub("", s)
    s = re.sub(r" {2,}", " ", s)
    return s


def sanitize_advscript_ruby_for_ui(text: str) -> str:
    """Backward-compatible alias of :func:`sanitize_advscript_markup_for_ui`."""
    return sanitize_advscript_markup_for_ui(text)


def _cn_prefer_rank(s: str) -> int:
    """Higher = better writeback target. Pure CN wins over hybrid/JP."""
    s = s or ""
    if not s:
        return 0
    if _is_good_cn(s):
        return 4
    if HAS_CJK.search(s) and HAS_KANA.search(s):
        return 2  # hybrid
    if HAS_CJK.search(s):
        return 3  # CJK without kana (symbols/latin ok)
    return 1


def _put_prefer_cn(out: Dict[str, str], k: str, v: str) -> bool:
    """Insert/replace mapping entry; prefer pure CN over hybrid. Returns True if stored."""
    k = (k or "").strip()
    v = (v or "").strip()
    if not k or not v or k == v:
        return False
    cur = out.get(k)
    if cur is None:
        out[k] = v
        return True
    if _cn_prefer_rank(v) > _cn_prefer_rank(cur):
        out[k] = v
        return True
    # Prefer UI-safe CN when a stripped key was first filled from a markup TELOP entry.
    if has_advscript_ui_markup(cur) and not has_advscript_ui_markup(v):
        if _cn_prefer_rank(v) >= _cn_prefer_rank(cur):
            out[k] = v
            return True
    return False


def expand_hazy_mapping(mapping: dict) -> dict:
    """Add tag-stripped / normalized variants for looser Hazy matching."""
    out: Dict[str, str] = {}

    def put(k: str, v: str) -> None:
        _put_prefer_cn(out, k, v)

    for jp, cn in mapping.items():
        if not jp or not cn:
            continue
        put(jp, cn)
        jp_stripped = _TAG_RE.sub("", jp).strip()
        # Stripped JP keys are used for plain Loc/UI rows — never attach raw AdvScript CN.
        cn_for_plain = sanitize_advscript_markup_for_ui(cn)
        cn_stripped = _TAG_RE.sub("", cn_for_plain).strip()
        if jp_stripped and jp_stripped != jp:
            if _TAG_RE.search(cn_for_plain):
                put(jp_stripped, cn_for_plain)
            else:
                put(jp_stripped, cn_stripped or cn_for_plain)
        put(normalize_hazy_key(jp), cn)
        if jp_stripped:
            norm_stripped = normalize_hazy_key(jp_stripped)
            if norm_stripped and norm_stripped != normalize_hazy_key(jp):
                if _TAG_RE.search(cn_for_plain):
                    put(norm_stripped, cn_for_plain)
                else:
                    put(norm_stripped, cn_stripped or cn_for_plain)
    return out


def expand_hazy_fill_mapping(mapping: dict) -> Tuple[dict, Dict[str, int]]:
    """Expand GalAutoTL for pack fill: display/WM payloads + prefer pure CN.

    Recovered dict keys are often ``N.ui.oat(WindowMessage:JP|txtid=…)`` wrappers
    while bak Hazy_Script_JP rows are plain JP — index extracted payloads too.
    """
    from app.core.xua_display_text import (
        expand_pair_to_display,
        extract_display_strings,
        strip_adv_tags,
    )

    stats: Dict[str, int] = {
        "base_keys": len(mapping),
        "added_display": 0,
        "added_wm": 0,
        "added_strip": 0,
        "preferred_pure": 0,
    }
    seed: Dict[str, str] = {}

    def put(k: str, v: str, counter: Optional[str] = None) -> None:
        before = seed.get(k)
        if not _put_prefer_cn(seed, k, v):
            return
        if before is None and counter:
            stats[counter] = stats.get(counter, 0) + 1
        elif before is not None and _is_good_cn(v) and not _is_good_cn(before):
            stats["preferred_pure"] += 1

    for jp, cn in mapping.items():
        if not jp or not cn:
            continue
        put(jp, cn)
        try:
            for a, b in expand_pair_to_display(jp, cn):
                put(a, b, "added_display")
                sa, sb = strip_adv_tags(a), strip_adv_tags(b)
                if sa and sb and sa != a:
                    put(sa, sb, "added_strip")
        except Exception:
            pass
        if "WindowMessage:" in jp:
            m = _WINDOW_MSG.search(jp)
            if m:
                jp_payload = m.group(1).strip()
                cn_payload = cn
                if "WindowMessage:" in (cn or ""):
                    m2 = _WINDOW_MSG.search(cn)
                    if m2:
                        cn_payload = m2.group(1).strip()
                elif _is_good_cn(cn) and "ui.oat(" not in cn:
                    cn_payload = cn
                else:
                    try:
                        ds = extract_display_strings(cn)
                        if ds:
                            cn_payload = ds[0]
                    except Exception:
                        pass
                cn_payload = _scrub_lineno_leak(jp, cn_payload)
                put(jp_payload, cn_payload, "added_wm")
                sa, sb = strip_adv_tags(jp_payload), strip_adv_tags(cn_payload)
                if sa and sb and sa != jp_payload:
                    put(sa, sb, "added_strip")
        try:
            for ds in extract_display_strings(jp):
                cn_use = cn
                if "WindowMessage:" in (cn or "") or "ui.oat(" in (cn or ""):
                    ds_cn = extract_display_strings(cn)
                    cn_use = ds_cn[0] if ds_cn else cn
                cn_use = _scrub_lineno_leak(jp, cn_use)
                put(ds, cn_use, "added_display")
                sa, sb = strip_adv_tags(ds), strip_adv_tags(cn_use)
                if sa and sb and sa != ds:
                    put(sa, sb, "added_strip")
        except Exception:
            pass

    # adv.ssei(text="%URL%"…,txtid=ID) wrappers → plain JP + tid index helpers
    stats["added_ssei"] = 0
    for jp, cn in mapping.items():
        if not jp or not cn or "ssei" not in jp.lower():
            continue
        cn_plain = _ssei_display_cn(cn)
        cn_plain = sanitize_advscript_markup_for_ui(cn_plain)
        cn_plain = _scrub_lineno_leak(jp, cn_plain)
        if not cn_plain or not HAS_CJK.search(cn_plain):
            continue
        m = _SSEI_TXTID.search(jp)
        if m:
            jp_plain = _urldecode_adv(m.group(1)).strip()
            if jp_plain:
                put(jp_plain, cn_plain, "added_ssei")
                sa, sb = _TAG_RE.sub("", jp_plain).strip(), _TAG_RE.sub("", cn_plain).strip()
                if sa and sb and sa != jp_plain:
                    put(sa, sb, "added_strip")
        else:
            m2 = _SSEI_TEXT.search(jp)
            if m2:
                jp_plain = _urldecode_adv(m2.group(2)).strip()
                if jp_plain:
                    put(jp_plain, cn_plain, "added_ssei")

    # Also scrub CN values stored under the full N.ui.oat(…) keys
    for k in list(seed.keys()):
        seed[k] = _scrub_lineno_leak(k, seed[k])

    expanded = expand_hazy_mapping(seed)
    # Prefer pure CN from seed over any hybrid left by soft expand
    for k, v in seed.items():
        _put_prefer_cn(expanded, k, v)
    stats["keys_before_hazy_expand"] = len(seed)
    stats["keys_after"] = len(expanded)
    return expanded, stats


def _ssei_display_cn(cn: str) -> str:
    """Pull visible CN from a dict value that may itself be an adv.ssei shell."""
    cn = (cn or "").strip()
    if not cn:
        return ""

    def from_slot(slot: str) -> str:
        slot = slot or ""
        # Truncated harvests leave broken %XX tails — reject.
        if re.search(r"%(?![0-9A-Fa-f]{2})", slot):
            return ""
        plain = _urldecode_adv(slot).strip()
        if not plain or "adv.ssei" in plain:
            return ""
        return plain

    m = _SSEI_TEXT.search(cn)
    if m:
        return from_slot(m.group(2))
    if "adv.ssei(" in cn:
        return ""
    if "WindowMessage:" in cn:
        m2 = _WINDOW_MSG.search(cn)
        if m2:
            return m2.group(1).strip()
    return cn


def index_adv_ssei_txtid_cn(mapping: dict) -> Dict[str, str]:
    """Build txtid → CN from GalAutoTL keys shaped like N.adv.ssei(...,txtid=ID)."""
    out: Dict[str, str] = {}
    for jp, cn in (mapping or {}).items():
        if not jp or not cn or "ssei" not in jp.lower():
            continue
        cn_plain = sanitize_advscript_markup_for_ui(_ssei_display_cn(cn))
        cn_plain = _scrub_lineno_leak(jp, cn_plain)
        if not cn_plain or not HAS_CJK.search(cn_plain):
            continue
        m = _SSEI_TXTID.search(jp)
        tid = m.group(3) if m else None
        if not tid:
            mt = re.search(r"\btxtid=([A-Za-z0-9_]+)", jp)
            tid = mt.group(1) if mt else None
        if not tid:
            continue
        # Skip identity / still-JP "translations"
        jp_plain = _urldecode_adv(m.group(1)).strip() if m else ""
        if jp_plain and normalize_hazy_key(cn_plain) == normalize_hazy_key(jp_plain):
            continue
        if tid not in out or _cn_prefer_rank(cn_plain) > _cn_prefer_rank(out[tid]):
            out[tid] = cn_plain
    return out


def _ssei_cn_acceptable(jp: str, cn: str, live: str = "") -> bool:
    """Whether CN is good enough to write over a still-JP/hybrid ssei/table row."""
    if not cn or not HAS_CJK.search(cn):
        return False
    if "adv.ssei(" in cn or "WindowMessage:" in cn:
        return False
    cn = sanitize_advscript_markup_for_ui(cn.strip())
    if not cn or not HAS_CJK.search(cn):
        return False
    jp_n = normalize_hazy_key(_TAG_RE.sub("", jp or ""))
    cn_n = normalize_hazy_key(_TAG_RE.sub("", cn))
    if jp_n and cn_n == jp_n:
        return False
    if live and _cn_prefer_rank(cn) < _cn_prefer_rank(live):
        return False
    if live and _is_good_cn(live) and not _is_good_cn(cn):
        return False
    if _is_good_cn(cn):
        return True
    # Hybrid: only if fewer kana than JP and strictly better than live
    jp_k = len(HAS_KANA.findall(jp or ""))
    cn_k = len(HAS_KANA.findall(cn))
    if cn_k >= jp_k:
        return False
    if live and _cn_prefer_rank(cn) <= _cn_prefer_rank(live):
        return False
    return True


def count_a024_ssei_lang_stats(game_dir: Path) -> Dict[str, int]:
    """Count live a024 adv.ssei(text=,txtid=) choices by language mix."""
    from app.core.unity_bundle_crypto import configure_unitypy_fallback, load_unity_env

    stats = {
        "total": 0,
        "mostly_jp": 0,
        "pure_cn": 0,
        "hybrid": 0,
        "other": 0,
        "still_kana": 0,
    }
    fp = find_hazy_pack(game_dir, "a024")
    if fp is None:
        return stats
    try:
        configure_unitypy_fallback(game_dir, log=None)
    except Exception:
        pass
    cache = Path(game_dir) / "_galautotl_unity" / "hazy_ab"
    try:
        env, _ = load_unity_env(fp, cache_dir=cache, log=None, game_dir=Path(game_dir))
    except Exception:
        return stats
    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        try:
            data = obj.read()
        except Exception:
            continue
        text = _read_ta_text(data)
        if not text or "adv.ssei(" not in text:
            continue
        for m in _SSEI_TXTID.finditer(text):
            plain = _urldecode_adv(m.group(1)).strip()
            stats["total"] += 1
            if _is_good_cn(plain):
                stats["pure_cn"] += 1
                continue
            k = len(HAS_KANA.findall(plain))
            c = len(HAS_CJK.findall(plain))
            if k > 0:
                stats["still_kana"] += 1
            if k > 0 and k >= c:
                stats["mostly_jp"] += 1
            elif k > 0 and c > 0:
                stats["hybrid"] += 1
            else:
                stats["other"] += 1
    return stats


def _merge_xua_dict_paths(*paths: Path) -> dict:
    """Load several XUA dicts; prefer pure CN when keys collide."""
    out: Dict[str, str] = {}
    for path in paths:
        p = Path(path)
        if not p.is_file():
            continue
        mapping, _ = load_xua_dict_prefer_good_cn(p)
        for k, v in mapping.items():
            if k not in out:
                out[k] = v
            elif _cn_prefer_rank(v) > _cn_prefer_rank(out[k]):
                out[k] = v
    return out


def fill_ssei_choices_from_existing(
    game_dir: Path,
    dict_path: Optional[Path] = None,
    log: LogFn = None,
) -> Dict[str, int]:
    """Fill still-JP a024 adv.ssei choices from existing GalAutoTL (no API).

    Writes CN into a036 Hazy_Script_JP by txtid, then force-resyncs a024 ssei
    text= slots (URL-encoded when the original slot used percent-encoding).
    """
    game_dir = Path(game_dir)
    dict_dir = game_dir / "BepInEx" / "Translation" / "zh-CN" / "Text"
    primary = Path(dict_path) if dict_path else dict_dir / "GalAutoTL.txt"
    mapping = _merge_xua_dict_paths(
        primary,
        dict_dir / "GalAutoTL.txt.before_scrub.bak",
        dict_dir / "_AutoGeneratedTranslations.txt",
    )
    stats: Dict[str, int] = {
        "dict_keys": len(mapping),
        "ssei_tid_index": 0,
        "a036_writes": 0,
        "a024_sync": 0,
        "fillable": 0,
        "skipped_weak": 0,
    }
    before = count_a024_ssei_lang_stats(game_dir)
    stats["ssei_still_kana_before"] = before.get("still_kana", 0)
    stats["ssei_mostly_jp_before"] = before.get("mostly_jp", 0)
    stats["ssei_pure_cn_before"] = before.get("pure_cn", 0)
    stats["ssei_total"] = before.get("total", 0)
    if log:
        log(
            f"a024 ssei before: total={before.get('total')} "
            f"still_kana={before.get('still_kana')} "
            f"jp={before.get('mostly_jp')} hybrid={before.get('hybrid')} "
            f"pure_cn={before.get('pure_cn')}"
        )

    expanded, expand_stats = expand_hazy_fill_mapping(mapping)
    fuzzy = _build_fuzzy_keys(expanded)
    by_tid = index_adv_ssei_txtid_cn(mapping)
    stats["ssei_tid_index"] = len(by_tid)
    stats["dict_expanded"] = len(expanded)
    stats["added_ssei"] = expand_stats.get("added_ssei", 0)
    if log:
        log(
            f"词典: {len(mapping)} 键 → expand {len(expanded)} "
            f"(ssei+{expand_stats.get('added_ssei', 0)})；txtid索引 {len(by_tid)}"
        )

    bak = _load_bak_script_table(game_dir)
    fp036, env036, hit = _load_a036_text_asset(game_dir, "hazy_script_jp", log)
    if not hit:
        stats["error"] = 1
        return stats
    _obj, data036, name036, live_text, _how = hit
    live = parse_hazy_script_table(live_text)

    from app.core.unity_bundle_crypto import configure_unitypy_fallback, load_unity_env

    fp024 = find_hazy_pack(game_dir, "a024")
    if fp024 is None:
        if log:
            log("未找到 a024")
        return stats
    try:
        configure_unitypy_fallback(game_dir, log=None)
    except Exception:
        pass
    cache = Path(game_dir) / "_galautotl_unity" / "hazy_ab"
    env024, _ = load_unity_env(fp024, cache_dir=cache, log=None, game_dir=game_dir)

    id_to_cn: Dict[str, str] = {}
    seen_tid: Set[str] = set()
    for obj in env024.objects:
        if obj.type.name != "TextAsset":
            continue
        try:
            data = obj.read()
        except Exception:
            continue
        text = _read_ta_text(data)
        if not text or "adv.ssei(" not in text:
            continue
        for m in _SSEI_TXTID.finditer(text):
            plain = _urldecode_adv(m.group(1)).strip()
            tid = m.group(3)
            if tid in seen_tid:
                continue
            seen_tid.add(tid)
            if not HAS_KANA.search(plain):
                continue
            jp = bak.get(tid) or plain
            live_row = live.get(tid, "")
            cn = by_tid.get(tid)
            src = "tid" if cn else None
            if not cn:
                cn = _lookup_hazy_cn(jp, expanded, fuzzy)
                src = "jp" if cn else None
            if not cn:
                cn = _lookup_hazy_cn(plain, expanded, fuzzy)
                src = "plain" if cn else None
            if cn:
                # Dict values may be truncated adv.ssei shells — unwrap or drop.
                unwrapped = _ssei_display_cn(cn)
                if unwrapped:
                    cn = unwrapped
                elif "adv.ssei(" in cn:
                    stats["skipped_weak"] += 1
                    continue
            if not cn:
                stats["skipped_weak"] += 1
                continue
            cn = sanitize_advscript_markup_for_ui(cn)
            cn = _scrub_lineno_leak(jp, cn)
            if not _ssei_cn_acceptable(jp, cn, live_row):
                stats["skipped_weak"] += 1
                continue
            # Don't overwrite equal live
            if live_row.strip() == cn.strip():
                # Still count as fillable for a024 sync if a024 plain differs
                if plain.strip() != cn.strip():
                    id_to_cn[tid] = cn
                    stats["fillable"] += 1
                continue
            id_to_cn[tid] = cn
            stats["fillable"] += 1

    if id_to_cn:
        new_text, n = patch_hazy_csv_by_id(live_text, id_to_cn)
        if n > 0 and new_text != live_text:
            if _write_ta_text(data036, new_text):
                try:
                    _save_hazy_pack_inplace(
                        fp036,
                        env036,
                        game_dir=game_dir,
                        log=log,
                        label=f"{name036} ssei-fill {n} 条",
                    )
                    stats["a036_writes"] = n
                    live_text = new_text
                except Exception as e:
                    if log:
                        log(f"a036 保存失败: {e}")
                    stats["a036_save_error"] = 1
                    return stats

    # a036 may lack choice-only ids (lclz_* etc.) — always push id_to_cn into a024 ssei.
    sync_map = parse_hazy_script_table(live_text)
    sync_map.update(id_to_cn)
    stats["a024_ssei_direct"] = _apply_a024_ssei_by_txtid(
        game_dir, sync_map, log=log, force=True
    )
    # Also resync WindowMessage from a036 for any script rows we did write.
    stats["a024_sync"] = resync_a024_from_a036_txtid(game_dir, log, force=True)

    after = count_a024_ssei_lang_stats(game_dir)
    stats["ssei_still_kana_after"] = after.get("still_kana", 0)
    stats["ssei_mostly_jp_after"] = after.get("mostly_jp", 0)
    stats["ssei_pure_cn_after"] = after.get("pure_cn", 0)
    stats["ssei_hybrid_after"] = after.get("hybrid", 0)
    if log:
        log(
            f"a024 ssei after: total={after.get('total')} "
            f"still_kana={after.get('still_kana')} "
            f"jp={after.get('mostly_jp')} hybrid={after.get('hybrid')} "
            f"pure_cn={after.get('pure_cn')}"
        )
        log(
            f"ssei fill: fillable={stats['fillable']} a036={stats['a036_writes']} "
            f"ssei_direct={stats['a024_ssei_direct']} sync={stats['a024_sync']} "
            f"skipped_weak={stats['skipped_weak']}"
        )
    return stats


def _apply_a024_ssei_by_txtid(
    game_dir: Path,
    by_id: Dict[str, str],
    log: LogFn = None,
    *,
    force: bool = True,
) -> int:
    """Write tid→CN into live a024 adv.ssei text= slots (URL-encode when needed)."""
    from app.core.unity_bundle_crypto import configure_unitypy_fallback, load_unity_env

    if not by_id:
        return 0
    fp = find_hazy_pack(game_dir, "a024")
    if fp is None:
        return 0
    try:
        configure_unitypy_fallback(game_dir, log=None)
    except Exception:
        pass
    cache = Path(game_dir) / "_galautotl_unity" / "hazy_ab"
    env, _ = load_unity_env(fp, cache_dir=cache, log=None, game_dir=Path(game_dir))
    changed = False
    total = 0
    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        try:
            data = obj.read()
        except Exception:
            continue
        text = _read_ta_text(data)
        if not text or "adv.ssei(" not in text:
            continue
        new_text, n = _replace_ssei_by_txtid(text, by_id, force=force)
        if n <= 0 or new_text == text:
            continue
        if not _write_ta_text(data, new_text):
            continue
        changed = True
        total += n
    if not changed:
        return 0
    try:
        _save_hazy_pack_inplace(
            fp, env, game_dir=game_dir, log=log, label=f"ssei txtid direct {total}"
        )
    except Exception as e:
        if log:
            log(f"a024 ssei direct 保存失败: {e}")
        return 0
    return total


def scrub_lineno_leaks_in_hazy_packs(game_dir: Path, log: LogFn = None) -> Dict[str, int]:
    """Scrub 本所NNN不思 / 仅限NNN次 style lineno leaks in live a036 (+ a024 sync)."""
    game_dir = Path(game_dir)
    stats = {"a036_rows": 0, "a024_sync": 0}
    bak = _load_bak_script_table(game_dir)
    fp, env, hit = _load_a036_text_asset(game_dir, "hazy_script_jp", log)
    if not hit:
        return stats
    _obj, data, name, text, _how = hit
    live = parse_hazy_script_table(text)
    id_to_cn: Dict[str, str] = {}
    for tid, cell in live.items():
        jp = bak.get(tid, "")
        if not jp:
            # Still fix obvious 本所\d+不思 even without bak if pattern is clear
            if not re.search(r"本所\d{1,5}不思", cell) and not re.search(
                r"仅限\d{2,5}次", cell
            ):
                continue
        fixed = _scrub_lineno_leak(jp or f"0.ui.oat(本所七不思議)", cell)
        # Extra: if cell has 本所\d+不思 and bak has 七, already handled.
        # Bare digit leaks without bak JP for 七:
        if re.search(r"本所\d{1,5}不思", fixed) and (
            "七不思議" in (jp or "") or "七不思" in (jp or "")
        ):
            fixed = re.sub(r"本所\d{1,5}不思[议議]?", "本所七不思议", fixed)
        if fixed != cell:
            id_to_cn[tid] = fixed
    if id_to_cn:
        new_text, n = patch_hazy_csv_by_id(text, id_to_cn)
        if n > 0 and new_text != text and _write_ta_text(data, new_text):
            try:
                _save_hazy_pack_inplace(
                    fp, env, game_dir=game_dir, log=log, label=f"{name} lineno-scrub {n}"
                )
                stats["a036_rows"] = n
            except Exception as e:
                if log:
                    log(f"a036 lineno scrub 保存失败: {e}")
                return stats
    if stats["a036_rows"]:
        stats["a024_sync"] = resync_a024_from_a036_txtid(game_dir, log, force=True)
    # Also scrub inline a024 leftovers not tied to a036 ids (e.g. lclz)
    stats["a024_inline"] = _scrub_a024_inline_lineno_leaks(game_dir, bak, log)
    if log:
        log(
            f"lineno scrub: a036={stats['a036_rows']} a024_sync={stats['a024_sync']} "
            f"a024_inline={stats['a024_inline']}"
        )
    return stats


def _scrub_a024_inline_lineno_leaks(
    game_dir: Path, bak: Dict[str, str], log: LogFn = None
) -> int:
    """Fix 本所NNN不思 inside a024 WindowMessage / ssei payloads via txtid bak JP."""
    from app.core.unity_bundle_crypto import configure_unitypy_fallback, load_unity_env

    fp = find_hazy_pack(game_dir, "a024")
    if fp is None:
        return 0
    try:
        configure_unitypy_fallback(game_dir, log=None)
    except Exception:
        pass
    cache = Path(game_dir) / "_galautotl_unity" / "hazy_ab"
    env, _ = load_unity_env(fp, cache_dir=cache, log=None, game_dir=Path(game_dir))
    changed = False
    total = 0

    def fix_payload(payload: str, tid: str) -> str:
        jp = bak.get(tid, "")
        if not jp and "本所" not in payload and "仅限" not in payload:
            return payload
        return _scrub_lineno_leak(jp or ("本所七不思議" if "本所" in payload else ""), payload)

    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        try:
            data = obj.read()
        except Exception:
            continue
        text = _read_ta_text(data)
        if text is None:
            continue
        if "本所" not in text and "仅限" not in text:
            continue
        new_text = text
        n = 0

        def wm_repl(m: re.Match) -> str:
            nonlocal n
            old, tid = m.group(1), m.group(2)
            fixed = fix_payload(old, tid)
            if fixed == old:
                return m.group(0)
            n += 1
            return f"WindowMessage:{fixed}|txtid={tid}"

        def ssei_repl(m: re.Match) -> str:
            nonlocal n
            old_enc, mid, tid = m.group(1), m.group(2), m.group(3)
            plain = _urldecode_adv(old_enc)
            fixed = fix_payload(plain, tid)
            if fixed == plain:
                return m.group(0)
            slot = _urlencode_adv(fixed) if _ssei_text_needs_encode(old_enc) else fixed
            if slot == old_enc:
                return m.group(0)
            n += 1
            return f'adv.ssei(text="{slot}"{mid}txtid={tid}'

        if "WindowMessage:" in new_text:
            new_text = _WINDOW_MSG_TXTID.sub(wm_repl, new_text)
        if "adv.ssei(" in new_text:
            new_text = _SSEI_TXTID.sub(ssei_repl, new_text)
        # lclz / scripts may embed 本所NNN without txtid — fix when bak-less pattern
        if re.search(r"本所\d{2,5}不思", new_text):
            # Only replace when nearby context suggests 七不思議 leak (digit run)
            patched, c = re.subn(r"本所\d{2,5}不思[议議]?", "本所七不思议", new_text)
            # Conservative: only if we also see 不思议 domain OR already had 本所 leaks
            if c and ("不思" in text):
                new_text = patched
                n += c
        if n <= 0 or new_text == text:
            continue
        if not _write_ta_text(data, new_text):
            continue
        changed = True
        total += n
    if changed:
        try:
            _save_hazy_pack_inplace(
                fp, env, game_dir=game_dir, log=log, label=f"lineno inline scrub {total}"
            )
        except Exception as e:
            if log:
                log(f"a024 inline scrub 保存失败: {e}")
            return 0
    return total


def load_xua_dict(path: Path) -> dict:
    """Parse XUA JP=CN dictionary file."""
    out: Dict[str, str] = {}
    path = Path(path)
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        tmp = raw.replace("{{=}}", "\0")
        if "=" not in tmp:
            continue
        k, v = tmp.split("=", 1)
        k = k.replace("\0", "=").replace("\\n", "\n")
        v = v.replace("\0", "=").replace("\\n", "\n")
        if k and v and k not in out:
            out[k] = v
    return out


def _is_good_cn(s: str) -> bool:
    """Pure CN: has CJK, no kana."""
    s = s or ""
    return bool(HAS_CJK.search(s)) and not HAS_KANA.search(s)


def _mostly_jp(s: str) -> bool:
    k = len(HAS_KANA.findall(s or ""))
    c = len(HAS_CJK.findall(s or ""))
    return k > 0 and k >= c


def load_xua_dict_prefer_good_cn(path: Path) -> Tuple[dict, int]:
    """Load XUA dict; when multiple CN exist for one JP key, prefer pure CN."""
    candidates: Dict[str, List[str]] = {}
    path = Path(path)
    if not path.is_file():
        return {}, 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        tmp = raw.replace("{{=}}", "\0")
        if "=" not in tmp:
            continue
        k, v = tmp.split("=", 1)
        k = k.replace("\0", "=").replace("\\n", "\n")
        v = v.replace("\0", "=").replace("\\n", "\n")
        if k and v:
            candidates.setdefault(k, []).append(v)
    out: Dict[str, str] = {}
    good_n = 0
    for k, vals in candidates.items():
        good_vals = [v for v in vals if _is_good_cn(v)]
        if good_vals:
            out[k] = good_vals[0]
            good_n += 1
            continue
        hybrid = next((v for v in vals if v and HAS_CJK.search(v)), None)
        if hybrid:
            out[k] = hybrid
        elif vals:
            out[k] = vals[0]
    return out, good_n


def patch_hazy_csv_by_id(text: str, id_to_cn: Dict[str, str]) -> Tuple[str, int]:
    """Set text column for matching ids (preserve line endings)."""
    if not id_to_cn:
        return text, 0
    lines = text.splitlines(keepends=True)
    n = 0
    out: List[str] = []
    for line in lines:
        raw = line
        ended = ""
        if raw.endswith("\r\n"):
            body, ended = raw[:-2], "\r\n"
        elif raw.endswith("\n"):
            body, ended = raw[:-1], "\n"
        else:
            body, ended = raw, ""
        plain = body.lstrip("\ufeff")
        if "," not in plain or plain.startswith("#"):
            out.append(raw)
            continue
        tid, cell = plain.split(",", 1)
        tid = tid.strip()
        cn = id_to_cn.get(tid)
        if cn is not None:
            cn = sanitize_adv_text_payload(cn)
            # Keep any [l]/[p] still on the live cell if incoming CN dropped them
            cn = preserve_click_wait_tags(cell.strip(), cn)
        if cn and cn != cell.strip():
            out.append(f"{tid},{cn}{ended}")
            n += 1
        else:
            out.append(raw)
    return "".join(out), n


def _patch_hazy_csv_aggressive_by_bak(
    text: str,
    bak_by_id: Dict[str, str],
    mapping: dict,
    fuzzy_keys: Optional[List[str]] = None,
    *,
    pure_cn_only: bool = True,
) -> Tuple[str, int, int]:
    """For every bak id, resolve CN via original JP; overwrite live when CN differs.

    When ``pure_cn_only`` (default), only write pure CN (no kana) — never push
    hybrid JP/CN leftovers over live rows.

    Returns (new_text, n_writes, n_forced_overwrites) where forced = live was not
    mostly-JP (hybrid/partial CN) but still got overwritten from dict via bak key.
    """
    if not bak_by_id or not mapping:
        return text, 0, 0
    live_by_id = parse_hazy_script_table(text)
    id_to_cn: Dict[str, str] = {}
    forced = 0
    for tid, bak_jp in bak_by_id.items():
        if not bak_jp:
            continue
        live_cell = live_by_id.get(tid, "")
        if not live_cell:
            continue
        # Prefer bak JP as dict key; fall back to live when bak lacks kana but live is JP
        lookup_key = bak_jp
        if not HAS_KANA.search(bak_jp) and HAS_KANA.search(live_cell):
            lookup_key = live_cell
        cn = _lookup_hazy_cn(lookup_key, mapping, fuzzy_keys)
        if not cn:
            # Still restore waits onto live CN if dict miss but tags were stripped
            restored = preserve_click_wait_tags(bak_jp, live_cell)
            if restored != live_cell:
                id_to_cn[tid] = restored
                forced += 1
            continue
        cn = preserve_click_wait_tags(bak_jp, sanitize_adv_text_payload(cn))
        if cn == live_cell:
            continue
        body = _TRAILING_WAIT_TAGS_RE.sub("", cn).strip()
        if pure_cn_only and not _is_good_cn(body) and not _is_good_cn(cn):
            # Dict weak — only patch missing waits onto existing live text
            restored = preserve_click_wait_tags(bak_jp, live_cell)
            if restored != live_cell:
                id_to_cn[tid] = restored
                forced += 1
            continue
        if _is_good_cn(live_cell) and live_cell == cn:
            continue
        if _cn_prefer_rank(cn) < _cn_prefer_rank(live_cell):
            restored = preserve_click_wait_tags(bak_jp, live_cell)
            if restored != live_cell:
                id_to_cn[tid] = restored
                forced += 1
            continue
        id_to_cn[tid] = cn
        if not _mostly_jp(live_cell):
            forced += 1
    if not id_to_cn:
        return text, 0, 0
    new_text, n = patch_hazy_csv_by_id(text, id_to_cn)
    return new_text, n, forced


def _patch_hazy_csv_by_bak_id(
    text: str,
    bak_by_id: Dict[str, str],
    mapping: dict,
    fuzzy_keys: Optional[List[str]] = None,
) -> Tuple[str, int]:
    """Legacy wrapper — delegates to aggressive bak-id fill."""
    new_text, n, _forced = _patch_hazy_csv_aggressive_by_bak(
        text, bak_by_id, mapping, fuzzy_keys
    )
    return new_text, n


def _load_bak_hazy_table(game_dir: Path, asset_hint: str) -> Dict[str, str]:
    """Read id→text from a036.galautotl.bak TextAsset (lookup only, no restore)."""
    from app.core.unity_bundle_crypto import configure_unitypy_fallback, load_unity_env

    fp = find_hazy_pack(game_dir, "a036")
    if fp is None:
        return {}
    bak = Path(str(fp) + ".galautotl.bak")
    if not bak.is_file():
        return {}
    try:
        configure_unitypy_fallback(game_dir, log=None)
    except Exception:
        pass
    cache = Path(game_dir) / "_galautotl_unity" / "hazy_ab"
    try:
        env, _ = load_unity_env(bak, cache_dir=cache, log=None, game_dir=Path(game_dir))
    except Exception:
        return {}
    hint = asset_hint.lower()
    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        try:
            data = obj.read()
        except Exception:
            continue
        name = str(getattr(data, "m_Name", None) or getattr(data, "name", None) or "")
        if hint not in name.lower():
            continue
        text = _read_ta_text(data)
        if text:
            return parse_hazy_script_table(text)
    return {}


def _load_bak_script_table(game_dir: Path) -> Dict[str, str]:
    """Read id→text from a036.galautotl.bak Hazy_Script_JP (lookup only, no restore)."""
    return _load_bak_hazy_table(game_dir, "hazy_script_jp")


def _iter_a024_windowmessage_payloads(game_dir: Path):
    """Yield WindowMessage payload strings from live a024 (deduped by match span)."""
    from app.core.unity_bundle_crypto import configure_unitypy_fallback, load_unity_env

    fp = find_hazy_pack(game_dir, "a024")
    if fp is None:
        return
    try:
        configure_unitypy_fallback(game_dir, log=None)
    except Exception:
        pass
    cache = Path(game_dir) / "_galautotl_unity" / "hazy_ab"
    try:
        env, _ = load_unity_env(fp, cache_dir=cache, log=None, game_dir=Path(game_dir))
    except Exception:
        return
    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        try:
            data = obj.read()
        except Exception:
            continue
        text = _read_ta_text(data)
        if text is None or "WindowMessage:" not in text:
            continue
        covered: Set[Tuple[int, int]] = set()
        for m in _WINDOW_MSG_TXTID.finditer(text):
            covered.add((m.start(1), m.end(1)))
            yield m.group(1).strip()
        for m in _WINDOW_MSG.finditer(text):
            span = (m.start(1), m.end(1))
            if span in covered:
                continue
            # Skip payloads already counted via txtid pattern
            snippet = text[m.start() : m.end() + 32]
            if "|txtid=" in snippet:
                continue
            chunk = m.group(1).strip()
            if chunk:
                yield chunk


def count_mostly_jp_windowmessage(game_dir: Path) -> int:
    """Count a024 WindowMessage payloads that are still mostly JP (kana >= CJK)."""
    n = 0
    any_payload = False
    for payload in _iter_a024_windowmessage_payloads(game_dir) or ():
        any_payload = True
        if _mostly_jp(payload):
            n += 1
    if not any_payload and find_hazy_pack(game_dir, "a024") is None:
        return -1
    return n


def count_a024_windowmessage_lang_stats(game_dir: Path) -> Dict[str, int]:
    """Count a024 WindowMessage payloads by language mix (mutually exclusive)."""
    stats = {
        "total": 0,
        "mostly_jp": 0,
        "mostly_cn": 0,
        "pure_cn": 0,
        "hybrid": 0,
        "other": 0,
    }
    for payload in _iter_a024_windowmessage_payloads(game_dir) or ():
        stats["total"] += 1
        if _is_good_cn(payload):
            stats["pure_cn"] += 1
            continue
        k = len(HAS_KANA.findall(payload or ""))
        c = len(HAS_CJK.findall(payload or ""))
        if k > 0 and k >= c:
            stats["mostly_jp"] += 1
        elif c > 0 and c > k and k > 0:
            stats["mostly_cn"] += 1
        elif k > 0 and c > 0:
            stats["hybrid"] += 1
        else:
            stats["other"] += 1
    return stats


def _apply_a024_jp_replace(
    game_dir: Path,
    mapping: dict,
    fuzzy_keys: Optional[List[str]] = None,
    log: LogFn = None,
) -> int:
    """JP→CN replace on all a024 TextAssets with WindowMessage / scene scripts."""
    from app.core.unity_bundle_crypto import configure_unitypy_fallback, load_unity_env

    fp = find_hazy_pack(game_dir, "a024")
    if fp is None:
        if log:
            log("未找到 a024 包")
        return 0
    try:
        configure_unitypy_fallback(game_dir, log=None)
    except Exception:
        pass
    cache = Path(game_dir) / "_galautotl_unity" / "hazy_ab"
    try:
        env, _ = load_unity_env(fp, cache_dir=cache, log=None, game_dir=Path(game_dir))
    except Exception as e:
        if log:
            log(f"a024 加载失败: {e}")
        return 0

    changed = False
    total = 0
    asset_hits: Dict[str, int] = {}
    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        try:
            data = obj.read()
        except Exception:
            continue
        name = str(getattr(data, "m_Name", None) or getattr(data, "name", None) or "")
        text = _read_ta_text(data)
        if text is None:
            continue
        if not _is_scene_script(name, text) and "WindowMessage:" not in text and "adv.ssei(" not in text:
            continue
        new_text, n = _replace_scene_script(text, mapping, fuzzy_keys)
        if n <= 0 or new_text == text:
            continue
        if not _write_ta_text(data, new_text):
            continue
        changed = True
        total += n
        asset_hits[name] = asset_hits.get(name, 0) + n

    if not changed:
        if log:
            log("a024 JP→CN 替换: 0 命中")
        return 0
    try:
        top = ", ".join(f"{k}:{v}" for k, v in sorted(asset_hits.items(), key=lambda x: -x[1])[:5])
        _save_hazy_pack_inplace(
            fp, env, game_dir=game_dir, log=log, label=f"JP→CN {total} 命中（{top}）"
        )
    except Exception as e:
        if log:
            log(f"a024 保存失败: {e}")
        return 0
    return total


def _apply_a036_dict_fill(
    game_dir: Path,
    mapping: dict,
    fuzzy_keys: Optional[List[str]] = None,
    log: LogFn = None,
) -> Tuple[int, int, int, int, int]:
    """Fill a036 Hazy_Script_JP / Hazy_Localization_JP from dict (+ bak-id lookup).

    Returns (script_hits, loc_hits, bak_hits, forced_script, forced_loc).
    """
    from app.core.unity_bundle_crypto import configure_unitypy_fallback, load_unity_env

    fp = find_hazy_pack(game_dir, "a036")
    if fp is None:
        if log:
            log("未找到 a036 包")
        return 0, 0, 0, 0, 0
    try:
        configure_unitypy_fallback(game_dir, log=None)
    except Exception:
        pass
    cache = Path(game_dir) / "_galautotl_unity" / "hazy_ab"
    try:
        env, _ = load_unity_env(fp, cache_dir=cache, log=None, game_dir=Path(game_dir))
    except Exception as e:
        if log:
            log(f"a036 加载失败: {e}")
        return 0, 0, 0, 0, 0

    bak_script = _load_bak_script_table(game_dir)
    bak_loc = _load_bak_hazy_table(game_dir, "hazy_localization_jp")
    script_hits = 0
    loc_hits = 0
    bak_hits = 0
    forced_script = 0
    forced_loc = 0
    changed = False
    labels: List[str] = []

    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        try:
            data = obj.read()
        except Exception:
            continue
        name = str(getattr(data, "m_Name", None) or getattr(data, "name", None) or "")
        low = name.lower()
        text = _read_ta_text(data)
        if text is None:
            continue

        new_text = text
        hits = 0
        if "hazy_script_jp" in low:
            t1, h1 = _replace_hazy_csv(text, mapping, fuzzy_keys)
            t2, h2, f2 = _patch_hazy_csv_aggressive_by_bak(t1, bak_script, mapping, fuzzy_keys)
            new_text = t2
            hits = h1 + h2
            script_hits += h1
            bak_hits += h2
            forced_script += f2
        elif "hazy_localization_jp" in low:
            t1, h1 = _replace_hazy_csv(text, mapping, fuzzy_keys)
            t2, h2, f2 = _patch_hazy_csv_aggressive_by_bak(t1, bak_loc, mapping, fuzzy_keys)
            # Loc/UI never parses AdvScript ruby — strip before write.
            t3, h3 = _sanitize_hazy_csv_ruby(t2)
            new_text = t3
            hits = h1 + h2 + h3
            loc_hits += h1 + h2
            forced_loc += f2
        else:
            continue

        if new_text == text:
            continue
        if not _write_ta_text(data, new_text):
            continue
        changed = True
        labels.append(f"{name}:{hits}")

    if not changed:
        if log:
            log("a036 dict fill: 0 命中")
        return 0, 0, 0, 0, 0
    try:
        label = ", ".join(labels[:4])
        if len(labels) > 4:
            label += f" +{len(labels) - 4}"
        _save_hazy_pack_inplace(fp, env, game_dir=game_dir, log=log, label=label)
    except Exception as e:
        if log:
            log(f"a036 保存失败: {e}")
        return 0, 0, 0, 0, 0
    return script_hits, loc_hits, bak_hits, forced_script, forced_loc


def fill_existing_translations(
    game_dir: Path,
    dict_path: Path,
    log: LogFn = None,
) -> Dict[str, int]:
    """Apply EXISTING GalAutoTL.txt translations into live a036/a024 packs (no API).

    Does NOT restore from ``*.galautotl.bak``; uses backup only as JP lookup keys
    when live Hazy_Script_JP rows are hybrid/mostly-JP but dict has CN for original JP.
    """
    game_dir = Path(game_dir)
    dict_path = Path(dict_path)
    stats: Dict[str, int] = {}

    mapping, good_cn = load_xua_dict_prefer_good_cn(dict_path)
    stats["dict_good_cn"] = good_cn
    stats["dict_writeback_keys"] = len(mapping)
    if log:
        log(f"词典: {dict_path.name}，{len(mapping)} 写回键，{good_cn} 条纯中文")

    soft = expand_hazy_mapping(mapping)
    stats["dict_expanded_soft"] = len(soft)
    expanded, expand_stats = expand_hazy_fill_mapping(mapping)
    stats["dict_expanded_fill"] = len(expanded)
    stats["dict_fill_added_display"] = expand_stats.get("added_display", 0)
    stats["dict_fill_added_wm"] = expand_stats.get("added_wm", 0)
    stats["dict_fill_added_strip"] = expand_stats.get("added_strip", 0)
    stats["dict_fill_preferred_pure"] = expand_stats.get("preferred_pure", 0)
    fuzzy_keys = _build_fuzzy_keys(expanded)
    if log:
        log(
            f"词典展开: soft {len(mapping)} → {len(soft)}；"
            f"fill索引 {len(soft)} → {len(expanded)} "
            f"(display+{expand_stats.get('added_display', 0)} "
            f"wm+{expand_stats.get('added_wm', 0)} "
            f"strip+{expand_stats.get('added_strip', 0)} "
            f"prefer_pure×{expand_stats.get('preferred_pure', 0)})"
        )

    # Bak pure-CN match preview (diagnoses wrapper-key vs plain JP gap)
    bak_script = _load_bak_script_table(game_dir)
    if bak_script:
        soft_fuzzy = _build_fuzzy_keys(soft)
        soft_pure = fill_pure = 0
        for _tid, jp in bak_script.items():
            cn_s = _lookup_hazy_cn(jp, soft, soft_fuzzy)
            cn_f = _lookup_hazy_cn(jp, expanded, fuzzy_keys)
            if cn_s and _is_good_cn(cn_s):
                soft_pure += 1
            if cn_f and _is_good_cn(cn_f):
                fill_pure += 1
        stats["bak_pure_cn_match_before_index"] = soft_pure
        stats["bak_pure_cn_match_after_index"] = fill_pure
        if log:
            log(
                f"bak Hazy_Script_JP×纯CN: 索引前 {soft_pure}/{len(bak_script)} → "
                f"索引后 {fill_pure}/{len(bak_script)}"
            )

    before_lang = count_a024_windowmessage_lang_stats(game_dir)
    stats["a024_mostly_jp_wm_before"] = before_lang.get("mostly_jp", -1)
    stats["a024_pure_cn_wm_before"] = before_lang.get("pure_cn", 0)
    stats["a024_mostly_cn_wm_before"] = before_lang.get("mostly_cn", 0)
    if log:
        log(
            f"a024 WindowMessage (before): mostly_jp={before_lang.get('mostly_jp')} "
            f"mostly_cn={before_lang.get('mostly_cn')} pure_cn={before_lang.get('pure_cn')} "
            f"hybrid={before_lang.get('hybrid')} total={before_lang.get('total')}"
        )

    script_hits, loc_hits, bak_hits, forced_script, forced_loc = _apply_a036_dict_fill(
        game_dir, expanded, fuzzy_keys, log
    )
    stats["a036_script_rows"] = script_hits
    stats["a036_loc_rows"] = loc_hits
    stats["a036_bak_id_hits"] = bak_hits
    stats["a036_forced_script"] = forced_script
    stats["a036_forced_loc"] = forced_loc
    if log:
        log(
            f"a036 fill: script={script_hits} loc={loc_hits} "
            f"bak_id={bak_hits} forced_script={forced_script} forced_loc={forced_loc}"
        )

    stats["a024_txtid_sync"] = resync_a024_from_a036_txtid(
        game_dir, log, force=True
    )
    stats["a024_jp_replace"] = _apply_a024_jp_replace(
        game_dir, expanded, fuzzy_keys, log
    )
    stats["glossary_hits"] = patch_hazy_localization_glossary(game_dir, log)

    after_lang = count_a024_windowmessage_lang_stats(game_dir)
    stats["a024_mostly_jp_wm_after"] = after_lang.get("mostly_jp", -1)
    stats["a024_pure_cn_wm_after"] = after_lang.get("pure_cn", 0)
    stats["a024_mostly_cn_wm_after"] = after_lang.get("mostly_cn", 0)
    stats["a024_hybrid_wm_after"] = after_lang.get("hybrid", 0)
    stats["a024_wm_total"] = after_lang.get("total", 0)
    if log:
        log(
            f"a024 WindowMessage (after): mostly_jp={after_lang.get('mostly_jp')} "
            f"mostly_cn={after_lang.get('mostly_cn')} pure_cn={after_lang.get('pure_cn')} "
            f"hybrid={after_lang.get('hybrid')} total={after_lang.get('total')}"
        )

    return stats


def _build_fuzzy_keys(mapping: dict) -> List[str]:
    return sorted((k for k in mapping if len(k) >= 8), key=len, reverse=True)


def _attach_control_tags(key: str, cn: str) -> str:
    """Keep AdvScript control tags from JP when CN lacks them.

    Always re-apply click-wait ``[l]``/``[p]`` from ``key`` via
    :func:`preserve_click_wait_tags` (CN may already have ``[r]``/``[cN]`` and
    still be missing waits — that used to cause auto-skip).
    """
    if not cn:
        return cn
    out = cn
    if "[" in key and "[" not in out:
        tags = _CONTROL_TAG_RE.findall(key)
        if tags:
            out = out + "".join(tags)
    return preserve_click_wait_tags(key, out)


def _lookup_hazy_cn(
    key: str,
    mapping: dict,
    fuzzy_keys: Optional[List[str]] = None,
) -> Optional[str]:
    """Resolve CN for a Hazy script key (exact → normalize → strip tags → fuzzy)."""
    if not key:
        return None

    cn = mapping.get(key)
    if cn and cn != key:
        return _attach_control_tags(key, cn)

    norm = normalize_hazy_key(key)
    if norm != key:
        cn = mapping.get(norm)
        if cn and cn != norm:
            return _attach_control_tags(key, cn)

    stripped = _TAG_RE.sub("", key).strip()
    if stripped and stripped != key:
        cn = mapping.get(stripped)
        if cn and cn != stripped:
            return _attach_control_tags(key, cn)
        norm_stripped = normalize_hazy_key(stripped)
        if norm_stripped != stripped:
            cn = mapping.get(norm_stripped)
            if cn and cn != norm_stripped:
                return _attach_control_tags(key, cn)

    if fuzzy_keys is None:
        fuzzy_keys = _build_fuzzy_keys(mapping)

    best_key: Optional[str] = None
    best_len = 0
    for dk in fuzzy_keys:
        if len(dk) >= 8 and key.startswith(dk):
            if len(dk) > best_len:
                best_key = dk
                best_len = len(dk)
        elif len(key) >= 12 and dk.startswith(key):
            if len(dk) > best_len:
                best_key = dk
                best_len = len(dk)

    if not best_key:
        return None

    cn = mapping.get(best_key)
    if not cn or cn == best_key:
        return None

    if key.startswith(best_key):
        suffix = key[len(best_key) :]
        return _attach_control_tags(key, cn + suffix)
    return _attach_control_tags(key, cn)


def _save_hazy_bundle(env) -> bytes:
    """Prefer lz4 recompression; fall back to original / default."""
    for packer in ("lz4", "original", None):
        try:
            if packer:
                return env.file.save(packer=packer)
            return env.file.save()
        except TypeError:
            continue
    return env.file.save()


def find_hazy_pack(game_dir: Path, name: str) -> Optional[Path]:
    """Return StreamingAssets pack ``a###`` by short name (e.g. a024)."""
    want = name.lower()
    for p in find_hazy_pack_files(game_dir):
        if p.name.lower() == want:
            return p
    return None


def find_hazy_pack_files(game_dir: Path) -> List[Path]:
    """StreamingAssets/a000 … a099 (extensionless UnityFS-with-prefix packs)."""
    out: List[Path] = []
    roots: List[Path] = []
    for data in list(game_dir.glob("*_Data")) + (
        [game_dir / "Data"] if (game_dir / "Data").is_dir() else []
    ):
        sa = data / "StreamingAssets"
        if sa.is_dir():
            roots.append(sa)
    sa2 = game_dir / "StreamingAssets"
    if sa2.is_dir():
        roots.append(sa2)
    seen: Set[str] = set()
    for root in roots:
        for p in root.iterdir():
            if not p.is_file():
                continue
            name = p.name
            if not re.fullmatch(r"a\d{2,4}", name, re.I):
                continue
            if p.stat().st_size < 256:
                continue
            k = str(p.resolve()).lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(p)
    # Prefer known text packs first (a024 scripts, a036 localization)
    def score(p: Path) -> tuple:
        n = p.name.lower()
        prefer = 0 if n in ("a024", "a036") else 1
        return (prefer, p.stat().st_size)
    out.sort(key=score)
    return out


def _parse_hazy_row(line: str) -> Optional[str]:
    """Return JP/display text from 'id,text…' (first comma only)."""
    s = (line or "").lstrip("\ufeff").strip()
    if not s or s.startswith("#"):
        return None
    if "," not in s:
        return None
    _id, text = s.split(",", 1)
    text = text.strip()
    if not text:
        return None
    return text


def _strip_script_markup(text: str) -> List[str]:
    """Pull human-visible bits from WindowMessage / dialog / ssei lines."""
    out: List[str] = []
    t = text or ""
    for m in _WINDOW_MSG.finditer(t):
        chunk = m.group(1).strip()
        if chunk:
            out.append(chunk)
    for m in _DIALOG_TEXT.finditer(t):
        chunk = m.group(1).strip()
        if chunk:
            out.append(chunk)
    for m in _SSEI_TEXT.finditer(t):
        chunk = _urldecode_adv(m.group(2)).strip()
        if chunk:
            out.append(chunk)
    return out


def _want_name(name: str) -> bool:
    n = (name or "").lower()
    if any(h in n for h in _HAZY_NAME_HINTS):
        return True
    # scene script dumps in a024: a0_050, c0_040, …
    if re.fullmatch(r"[a-z]\d_\d{2,4}", n):
        return True
    # intro / docs / characters — game reads these inline, not only a#_###
    if n in ("first", "boot_sequence", "title", "prologue"):
        return True
    if n.endswith("_st") or n.startswith(("doc", "chr", "lclz", "staff", "credit")):
        return True
    return False


def _is_scene_script(name: str, text: str) -> bool:
    """True if TextAsset holds AdvScript with inline WindowMessage (any name)."""
    if "WindowMessage:" in (text or ""):
        return True
    n = (name or "").lower()
    if re.fullmatch(r"[a-z]\d_\d{2,4}", n):
        return True
    if n in ("first", "boot_sequence"):
        return True
    if n.endswith("_st") or n.startswith(("doc", "chr", "lclz")):
        return True
    return False


def parse_hazy_script_table(text: str) -> Dict[str, str]:
    """Parse Hazy_Script_JP CSV (id,text) into dict."""
    out: Dict[str, str] = {}
    for line in (text or "").splitlines():
        plain = line.lstrip("\ufeff").strip()
        if not plain or plain.startswith("#") or "," not in plain:
            continue
        tid, cell = plain.split(",", 1)
        tid = tid.strip()
        cell = cell.strip()
        if tid and cell:
            out[tid] = cell
    return out


_TXTID_LEAK_IN_PAYLOAD_RE = re.compile(r"\|txtid=[A-Za-z0-9_]+\)?")
# Engine TMP / AdvScript font asset names — never translate these identifiers.
_ENGINE_FONT_NAME_FIXES: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r'<font="泰洛普">'), '<font="TELOP">'),
    (re.compile(r"<font=泰洛普>"), "<font=TELOP>"),
    (re.compile(r'<font="主要">'), '<font="MAIN">'),
    (re.compile(r"<font=主要>"), "<font=MAIN>"),
)


def sanitize_adv_text_payload(cn: str) -> str:
    """Strip leaked ``|txtid=`` / bad font-name translations from a text payload.

    Call before writing into WindowMessage / ssei / Hazy CSV cells so table
    contamination cannot duplicate ``|txtid=id)|txtid=id)`` and break AdvScript.
    Never translates contents of ``<font=…>`` / ``[font=…]`` — only restores
    known corrupted engine font names (泰洛普→TELOP, 主要→MAIN).
    """
    if not cn:
        return cn
    out = _TXTID_LEAK_IN_PAYLOAD_RE.sub("", cn)
    for rx, rep in _ENGINE_FONT_NAME_FIXES:
        out = rx.sub(rep, out)
    # Bare transliteration leftover (URL-decoded or outside tags)
    if "泰洛普" in out:
        out = out.replace("泰洛普", "TELOP")
    # MT garble: いっさい / 一切 → 0切3概
    if "0切3概" in out:
        out = out.replace("0切3概关系无关", "一切均无关")
        out = out.replace("0切3概", "一切")
        out = out.replace("与实际存在的一切关系无关", "与实际存在的一切均无关")
        out = out.replace("与实际存在的一切概关系无关", "与实际存在的一切均无关")
    return out


def _replace_window_by_txtid(
    text: str, by_id: Dict[str, str], *, force: bool = False
) -> Tuple[str, int]:
    """Replace WindowMessage payloads using |txtid=ID → Hazy_Script_JP[ID].

    When ``force`` is True, treat a036 table as source of truth and overwrite any
    payload that differs (including hybrid CN/JP inline text).

    Only the payload between ``WindowMessage:`` and ``|txtid=`` is replaced —
    surrounding AdvScript command bytes / trailing ``|txtid=`` are never altered.
    Incoming CN is sanitized so leaked ``|txtid=`` cannot be written into the body.
    """
    if not by_id:
        return text, 0
    n = 0

    def repl(m: re.Match) -> str:
        nonlocal n
        old = m.group(1)
        tid = m.group(2)
        cn = by_id.get(tid)
        if not cn:
            return m.group(0)
        cn = sanitize_adv_text_payload(cn)
        # If a036 table lost [l]/[p], restore from the live/old WindowMessage payload
        cn = preserve_click_wait_tags(old, cn)
        old_stripped = old.strip()
        cn_stripped = cn.strip()
        if not force and cn_stripped == old_stripped:
            return m.group(0)
        if force and cn == old:
            return m.group(0)
        # Keep leading/trailing whitespace of original payload
        lead = old[: len(old) - len(old.lstrip())] if old != old_stripped else ""
        trail = old[len(old.rstrip()) :] if old != old_stripped else ""
        n += 1
        return f"WindowMessage:{lead}{cn}{trail}|txtid={tid}"

    return _WINDOW_MSG_TXTID.sub(repl, text), n


def _ssei_text_needs_encode(original: str) -> bool:
    """True when original ssei text= slot used percent-encoding."""
    return bool(original) and "%" in original and re.search(r"%[0-9A-Fa-f]{2}", original)


def _replace_ssei_by_txtid(
    text: str, by_id: Dict[str, str], *, force: bool = False
) -> Tuple[str, int]:
    """Replace adv.ssei(text=…) display strings using txtid → Hazy_Script_JP."""
    if not by_id:
        return text, 0
    n = 0

    def repl(m: re.Match) -> str:
        nonlocal n
        old_enc = m.group(1)
        mid = m.group(2)
        tid = m.group(3)
        cn = by_id.get(tid)
        if not cn:
            return m.group(0)
        cn = sanitize_adv_text_payload(cn)
        old_plain = _urldecode_adv(old_enc)
        if not force and cn.strip() == old_plain.strip():
            return m.group(0)
        if force and cn == old_plain:
            return m.group(0)
        # Prefer pure CN; skip writing hybrid over already-pure CN unless force+better
        if not force and _is_good_cn(old_plain) and not _is_good_cn(cn):
            return m.group(0)
        # Only replace the text="…" payload; mid + txtid= suffix stay byte-identical.
        if _ssei_text_needs_encode(old_enc):
            new_slot = _urlencode_adv(cn)
        else:
            new_slot = cn
        if new_slot == old_enc:
            return m.group(0)
        n += 1
        return f'adv.ssei(text="{new_slot}"{mid}txtid={tid}'

    return _SSEI_TXTID.sub(repl, text), n


def _replace_ssei_texts(
    text: str, mapping: dict, fuzzy_keys: Optional[List[str]] = None
) -> Tuple[str, int]:
    """Replace adv.ssei(text=) via JP→CN dict (decoded lookup)."""
    n = 0

    def repl(m: re.Match) -> str:
        nonlocal n
        prefix, slot, suffix = m.group(1), m.group(2), m.group(3)
        plain = _urldecode_adv(slot)
        key = plain.strip()
        cn = _lookup_hazy_cn(key, mapping, fuzzy_keys)
        if not cn or cn == key:
            return m.group(0)
        if not _is_good_cn(cn) and _is_good_cn(plain):
            return m.group(0)
        if _ssei_text_needs_encode(slot):
            new_slot = _urlencode_adv(cn)
        else:
            new_slot = cn
        if new_slot == slot:
            return m.group(0)
        n += 1
        return f"{prefix}{new_slot}{suffix}"

    return _SSEI_TEXT.sub(repl, text), n


def collect_hazy_jp_strings(game_dir: Path, log: LogFn = None) -> List[str]:
    """Harvest JP dialogue/UI from Hazy a### packs for runtime dictionary."""
    packs = find_hazy_pack_files(game_dir)
    if not packs:
        return []
    if log:
        log(f"Hazy/StreamingAssets 壳包: {len(packs)} 个（a###）")

    from app.core.unity_bundle_crypto import configure_unitypy_fallback, load_unity_env

    try:
        configure_unitypy_fallback(game_dir, log=None)
    except Exception:
        pass

    seen: Set[str] = set()
    out: List[str] = []

    def add(s: str) -> None:
        s = (s or "").strip("\x00").strip()
        if not s or s in seen:
            return
        if not HAS_KANA.search(s) and not any(
            "\u4e00" <= ch <= "\u9fff" for ch in s[:8]
        ):
            # keep pure JP / JP+kanji; skip EN-only rows from *_EN tables
            if not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", s):
                return
        # drop pure glyph dumps
        if len(s) > 400 and s.count("あ") + s.count("ア") < 3:
            if sum(1 for ch in s if ord(ch) < 128) > len(s) * 0.7:
                return
        seen.add(s)
        out.append(s)

    for fp in packs:
        try:
            env, how = load_unity_env(
                fp,
                cache_dir=Path(game_dir) / "_galautotl_unity" / "hazy_ab",
                log=None,
                game_dir=Path(game_dir),
            )
        except Exception as e:
            if log:
                log(f"  跳过 {fp.name}: {e}")
            continue
        n_ta = 0
        n_hit = 0
        for obj in env.objects:
            if obj.type.name != "TextAsset":
                continue
            try:
                data = obj.read()
            except Exception:
                continue
            name = str(getattr(data, "m_Name", None) or getattr(data, "name", None) or "")
            if not _want_name(name) and "jp" not in name.lower():
                continue
            script = getattr(data, "script", None) or getattr(data, "m_Script", None)
            if script is None:
                continue
            if isinstance(script, bytes):
                try:
                    text = script.decode("utf-8")
                except UnicodeDecodeError:
                    text = script.decode("utf-8", errors="replace")
            else:
                text = str(script)
            n_ta += 1
            # Prefer JP tables / scripts
            is_jp_table = "jp" in name.lower() or re.fullmatch(r"[a-z]\d_\d{2,4}", name.lower())
            if "en" in name.lower() and "jp" not in name.lower():
                continue
            before = len(out)
            if "hazy_script" in name.lower() or "hazy_localization" in name.lower():
                for line in text.splitlines():
                    body = _parse_hazy_row(line)
                    if body:
                        add(body)
            elif re.fullmatch(r"[a-z]\d_\d{2,4}", name.lower()):
                for chunk in _strip_script_markup(text):
                    add(chunk)
                # also id,text style if present
                for line in text.splitlines():
                    body = _parse_hazy_row(line)
                    if body and HAS_KANA.search(body):
                        add(body)
            else:
                for line in text.splitlines():
                    body = _parse_hazy_row(line)
                    if body:
                        add(body)
            n_hit += len(out) - before
        if log and n_ta:
            log(f"  {fp.name}: TextAsset {n_ta}，收入约 {n_hit} 条")

    if log:
        log(f"Hazy 剧本/本地化待译: {len(out)} 条")
    return out


def _replace_hazy_csv(
    text: str,
    mapping: dict,
    fuzzy_keys: Optional[List[str]] = None,
    *,
    ui_sanitize: bool = False,
) -> Tuple[str, int]:
    """Replace text column in id,text rows. Returns (new_text, n_hits).

    ``ui_sanitize=True`` for Localization rows (strip AdvScript markup UI cannot parse).
    Script rows keep tags and always restore click-wait ``[l]``/``[p]`` from JP cell.
    """
    lines = text.splitlines(keepends=True)
    n = 0
    out: List[str] = []
    for line in lines:
        raw = line
        ended = ""
        if raw.endswith("\r\n"):
            body, ended = raw[:-2], "\r\n"
        elif raw.endswith("\n"):
            body, ended = raw[:-1], "\n"
        else:
            body, ended = raw, ""
        plain = body.lstrip("\ufeff")
        if "," not in plain or plain.startswith("#"):
            out.append(raw)
            continue
        _id, cell = plain.split(",", 1)
        key = cell.strip()
        cn = _lookup_hazy_cn(key, mapping, fuzzy_keys)
        if cn and cn != key:
            cn = sanitize_adv_text_payload(cn)
            if ui_sanitize:
                cn = sanitize_advscript_markup_for_ui(cn)
            else:
                cn = preserve_click_wait_tags(key, cn)
            out.append(f"{_id},{cn}{ended}")
            n += 1
        else:
            out.append(raw)
    return "".join(out), n


def _replace_payload(
    text: str,
    pattern: re.Pattern,
    prefix: str,
    mapping: dict,
    fuzzy_keys: Optional[List[str]] = None,
) -> Tuple[str, int]:
    """Replace captured dialogue payloads (WindowMessage / textN=)."""
    n = 0

    def repl(m: re.Match) -> str:
        nonlocal n
        chunk = m.group(1)
        key = chunk.strip()
        cn = _lookup_hazy_cn(key, mapping, fuzzy_keys)
        if not cn or cn == key:
            return m.group(0)
        cn = sanitize_adv_text_payload(cn)
        cn = preserve_click_wait_tags(key, cn)
        n += 1
        lead = chunk[: len(chunk) - len(chunk.lstrip())] if chunk != key else ""
        trail = chunk[len(chunk.rstrip()) :] if chunk != key else ""
        return f"{prefix}{lead}{cn}{trail}"

    return pattern.sub(repl, text), n


def _replace_window_messages(
    text: str, mapping: dict, fuzzy_keys: Optional[List[str]] = None
) -> Tuple[str, int]:
    return _replace_payload(text, _WINDOW_MSG, "WindowMessage:", mapping, fuzzy_keys)


def _replace_dialog_texts_fixed(
    text: str, mapping: dict, fuzzy_keys: Optional[List[str]] = None
) -> Tuple[str, int]:
    """Replace textN= dialog select payloads."""
    n = 0

    def repl(m: re.Match) -> str:
        nonlocal n
        full = m.group(0)
        chunk = m.group(1)
        key = chunk.strip()
        cn = _lookup_hazy_cn(key, mapping, fuzzy_keys)
        if not cn or cn == key:
            return full
        n += 1
        eq = full.index("=")
        lead = chunk[: len(chunk) - len(chunk.lstrip())] if chunk != key else ""
        trail = chunk[len(chunk.rstrip()) :] if chunk != key else ""
        return f"{full[:eq]}={lead}{cn}{trail}"

    return _DIALOG_TEXT.sub(repl, text), n


def _replace_scene_script(
    text: str, mapping: dict, fuzzy_keys: Optional[List[str]] = None
) -> Tuple[str, int]:
    text, n1 = _replace_window_messages(text, mapping, fuzzy_keys)
    text, n2 = _replace_dialog_texts_fixed(text, mapping, fuzzy_keys)
    text, n3 = _replace_ssei_texts(text, mapping, fuzzy_keys)
    return text, n1 + n2 + n3


def apply_hazy_mapping(
    game_dir: Path,
    mapping: dict,
    *,
    log: LogFn = None,
) -> int:
    """Write CN into Hazy_Script_JP / scene scripts inside a### packs (with shell header).

    PARANORMASIGHT shows **inline** ``WindowMessage:`` from a024 AdvScript, not the
    a036 lookup table alone. Flow:
    1) Patch a036 CSV tables (Hazy_Script_JP / Localization_JP)
    2) Sync every a024 TextAsset that contains WindowMessage via ``|txtid=``
       using the updated Hazy_Script_JP id→text map (covers ``first``, DOC*, …)
    3) Fallback: JP-text replace for lines without txtid / missing table rows

    Backs up originals to ``*.galautotl.bak`` when that backup does not yet exist.
    """
    if not mapping:
        return 0

    # Fill expand: index N.ui.oat(WindowMessage:) wrappers → plain JP (PARANORMASIGHT lesson)
    expanded, expand_stats = expand_hazy_fill_mapping(mapping)
    fuzzy_keys = _build_fuzzy_keys(expanded)
    if log:
        log(
            f"Hazy 词典展开(fill): {len(mapping)} → {len(expanded)} 键 "
            f"(wm+{expand_stats.get('added_wm', 0)} "
            f"display+{expand_stats.get('added_display', 0)} "
            f"strip+{expand_stats.get('added_strip', 0)})"
        )

    packs = find_hazy_pack_files(game_dir)
    prefer = {"a024", "a036"}
    packs = [p for p in packs if p.name.lower() in prefer] or packs[:2]
    if not packs:
        return 0
    # a036 first so txtid table is ready for a024
    packs = sorted(packs, key=lambda p: 0 if p.name.lower() == "a036" else 1)

    from app.core.unity_bundle_crypto import (
        configure_unitypy_fallback,
        load_unity_env,
        shell_prefix_bytes,
        write_shelled_unityfs,
    )

    try:
        configure_unitypy_fallback(game_dir, log=None)
    except Exception:
        pass

    cache = Path(game_dir) / "_galautotl_unity" / "hazy_ab"
    written_files = 0
    total_hits = 0
    script_by_id: Dict[str, str] = {}

    def _read_ta_text(data) -> Optional[str]:
        script = getattr(data, "script", None) or getattr(data, "m_Script", None)
        if script is None:
            return None
        if isinstance(script, bytes):
            try:
                return script.decode("utf-8")
            except UnicodeDecodeError:
                return script.decode("utf-8", errors="replace")
        return str(script)

    def _write_ta_text(data, name: str, new_text: str) -> bool:
        if hasattr(data, "script"):
            try:
                data.script = new_text
            except Exception:
                data.script = new_text.encode("utf-8")
        elif hasattr(data, "m_Script"):
            try:
                data.m_Script = new_text
            except Exception:
                data.m_Script = new_text.encode("utf-8")
        else:
            return False
        try:
            data.save()
            return True
        except Exception:
            try:
                raw = new_text.encode("utf-8")
                if hasattr(data, "script"):
                    data.script = raw
                else:
                    data.m_Script = raw
                data.save()
                return True
            except Exception as e2:
                if log:
                    log(f"  TextAsset 保存失败 {name}: {e2}")
                return False

    def _save_pack(fp: Path, env, how: str, file_hits: int, asset_hits: Dict[str, int]) -> None:
        nonlocal written_files, total_hits
        out_blob = _save_hazy_bundle(env)
        prefix = shell_prefix_bytes(fp, cache)
        if not prefix:
            try:
                raw = fp.read_bytes()
                ufs = raw.find(b"UnityFS")
                if 0 < ufs < 4096:
                    prefix = raw[:ufs]
            except OSError:
                prefix = b""
        write_shelled_unityfs(fp, out_blob, prefix=prefix, backup=True)
        written_files += 1
        total_hits += file_hits
        if log:
            top = ", ".join(
                f"{k}:{v}" for k, v in sorted(asset_hits.items(), key=lambda x: -x[1])[:5]
            )
            log(
                f"Hazy 写回 {fp.name}: {file_hits} 命中（{how}，壳头 {len(prefix)}B"
                + (f"，{top}" if top else "")
                + "）"
            )

    for fp in packs:
        try:
            env, how = load_unity_env(fp, cache_dir=cache, log=None, game_dir=Path(game_dir))
        except Exception as e:
            if log:
                log(f"Hazy 写回跳过 {fp.name}: {e}")
            continue
        changed = False
        file_hits = 0
        asset_hits: Dict[str, int] = {}
        is_a024 = fp.name.lower() == "a024"
        is_a036 = fp.name.lower() == "a036"

        for obj in env.objects:
            if obj.type.name != "TextAsset":
                continue
            try:
                data = obj.read()
            except Exception:
                continue
            name = str(getattr(data, "m_Name", None) or getattr(data, "name", None) or "")
            low = name.lower()
            if "en" in low and "jp" not in low and "hazy_" in low:
                continue
            text = _read_ta_text(data)
            if text is None:
                continue

            new_text = text
            hits = 0

            if "hazy_script" in low or "hazy_localization" in low:
                is_loc = "hazy_localization" in low
                new_text, hits = _replace_hazy_csv(
                    text, expanded, fuzzy_keys, ui_sanitize=is_loc
                )
                if "hazy_script_jp" in low:
                    # Prefer post-replace table (CN values, same ids)
                    script_by_id.update(parse_hazy_script_table(new_text if hits else text))
            elif is_a024 or _is_scene_script(name, text):
                if not _is_scene_script(name, text) and not _want_name(name):
                    continue
                n_id = 0
                n_ssei = 0
                if script_by_id:
                    new_text, n_id = _replace_window_by_txtid(text, script_by_id)
                    new_text, n_ssei = _replace_ssei_by_txtid(
                        new_text, script_by_id
                    )
                else:
                    new_text = text
                # JP-text fallback for remaining lines
                new_text2, n_jp = _replace_scene_script(
                    new_text, expanded, fuzzy_keys
                )
                new_text = new_text2
                hits = n_id + n_ssei + n_jp
                if log and name.lower() == "first" and hits:
                    log(
                        f"  开场剧本 first: txtid {n_id} + ssei {n_ssei} + 文本 {n_jp}"
                    )
            elif _want_name(name) or "jp" in low:
                continue
            else:
                continue

            if hits <= 0 or new_text == text:
                continue
            if not _write_ta_text(data, name, new_text):
                continue
            changed = True
            file_hits += hits
            asset_hits[name] = asset_hits.get(name, 0) + hits

        # a036 without prior script_by_id: still parse JP table for a024 next loop
        if is_a036 and not script_by_id:
            for obj in env.objects:
                if obj.type.name != "TextAsset":
                    continue
                try:
                    data = obj.read()
                    name = str(getattr(data, "m_Name", None) or "")
                    if "hazy_script_jp" not in name.lower():
                        continue
                    t = _read_ta_text(data)
                    if t:
                        script_by_id.update(parse_hazy_script_table(t))
                except Exception:
                    continue

        if not changed:
            if log:
                log(f"Hazy {fp.name}: 0 命中，跳过写回")
            continue
        try:
            _save_pack(fp, env, how, file_hits, asset_hits)
        except Exception as e:
            if log:
                log(f"Hazy 保存失败 {fp.name}: {e}")

    if log:
        log(
            f"Hazy 写回合计: {written_files} 包，{total_hits} 命中"
            + (f"（txtid 表 {len(script_by_id)} 条）" if script_by_id else "")
        )
    return total_hits


def rewrite_hazy_from_dict(
    game_dir: Path,
    dict_path: Path,
    log: LogFn = None,
    restore_bak_first: bool = True,
) -> int:
    """Restore optional ``*.galautotl.bak`` then apply XUA dictionary to Hazy packs."""
    game_dir = Path(game_dir)
    dict_path = Path(dict_path)

    if restore_bak_first:
        for name in ("a024", "a036"):
            fp = find_hazy_pack(game_dir, name)
            if fp is None:
                continue
            bak = Path(str(fp) + ".galautotl.bak")
            if bak.is_file():
                shutil.copy2(bak, fp)
                if log:
                    log(f"已从备份恢复 {name}（保留 {bak.name}）")

    raw = load_xua_dict(dict_path)
    if log:
        log(f"XUA 词典: {dict_path.name}，{len(raw)} 条")
    return apply_hazy_mapping(game_dir, raw, log=log)


def finalize_hazy_after_translate(
    game_dir: Path,
    mapping: dict,
    *,
    dict_path: Optional[Path] = None,
    log: LogFn = None,
) -> Dict[str, int]:
    """Full post-translate Hazy harden (PARANORMASIGHT lessons → default Unity path).

    Order (cancel-safe; never restores bak wholesale):
    1. ``apply_hazy_mapping`` (fill-expand + wait-tag preserve + Loc UI sanitize)
    2. ``fill_existing_translations`` when ``dict_path`` exists (wrapper-key recovery)
    3. ``fill_ssei_choices_from_existing`` (URL-encoded choices)
    4. ``scrub_lineno_leaks_in_hazy_packs`` (NNN.ui.oat number leaks)
    5. glossary / hybrid scrub / force txtid resync
    """
    game_dir = Path(game_dir)
    stats: Dict[str, int] = {"apply_hits": 0}

    if not mapping and dict_path and Path(dict_path).is_file():
        mapping = load_xua_dict(Path(dict_path))

    if mapping:
        try:
            stats["apply_hits"] = int(apply_hazy_mapping(game_dir, mapping, log=log) or 0)
        except Exception as e:
            if log:
                log(f"Hazy apply_hazy_mapping 失败: {e}")

    if dict_path and Path(dict_path).is_file():
        try:
            fill_stats = fill_existing_translations(game_dir, Path(dict_path), log=log)
            for k, v in (fill_stats or {}).items():
                if isinstance(v, int):
                    stats[f"fill_{k}"] = v
        except Exception as e:
            if log:
                log(f"Hazy fill_existing 失败: {e}")

    try:
        ssei_stats = fill_ssei_choices_from_existing(game_dir, log=log)
        if isinstance(ssei_stats, dict):
            stats["ssei_fillable"] = int(ssei_stats.get("fillable", 0) or 0)
            stats["ssei_still_kana"] = int(ssei_stats.get("still_kana", 0) or 0)
    except Exception as e:
        if log:
            log(f"Hazy ssei 选项补全失败: {e}")

    try:
        leak = scrub_lineno_leaks_in_hazy_packs(game_dir, log=log)
        if isinstance(leak, dict):
            stats["lineno_scrub"] = int(leak.get("a036_rows", 0) or 0) + int(
                leak.get("a024_inline", 0) or 0
            )
    except Exception as e:
        if log:
            log(f"Hazy 行号泄漏清理失败: {e}")

    try:
        n_ui = patch_hazy_localization_glossary(game_dir, log=log)
        stats["glossary"] = int(n_ui or 0)
    except Exception as e:
        if log:
            log(f"Hazy UI 词表失败: {e}")

    try:
        scrub_hazy_script_jp_fragments(game_dir, log=log)
    except Exception as e:
        if log:
            log(f"Hazy 杂交碎片清理失败: {e}")

    try:
        n_sync = resync_a024_from_a036_txtid(game_dir, log=log, force=True)
        stats["a024_resync"] = int(n_sync or 0)
    except TypeError:
        try:
            n_sync = resync_a024_from_a036_txtid(game_dir, log=log)
            stats["a024_resync"] = int(n_sync or 0)
        except Exception as e:
            if log:
                log(f"Hazy a024 同步失败: {e}")
    except Exception as e:
        if log:
            log(f"Hazy a024 同步失败: {e}")

    if log:
        log(
            "Hazy 收尾完成: "
            f"apply={stats.get('apply_hits', 0)} "
            f"ssei={stats.get('ssei_fillable', 0)} "
            f"lineno={stats.get('lineno_scrub', 0)} "
            f"glossary={stats.get('glossary', 0)} "
            f"resync={stats.get('a024_resync', 0)}"
        )
    return stats


# ---------------------------------------------------------------------------
# PARANORMASIGHT UI glossary + hybrid scrub (a036 localization / script)
# ---------------------------------------------------------------------------

GLOSSARY_UI_JP_CN: Dict[str, str] = {
    # save / load
    "セーブするスロットを選んでください": "请选择要保存的栏位",
    "スロット{0}に 上書きいたしますか？": "确定覆盖存档栏 {0} 吗？",
    "ロードするスロットを選んでください": "请选择要读取的栏位",
    "スロット{0}を ロードいたしますか？": "确定读取存档栏 {0} 吗？",
    "オートセーブ": "自动保存",
    "スロット{0}": "栏位 {0}",
    "データなし": "无数据",
    "数据なし": "无数据",
    "オートセーブを ロードいたしますか？": "确定读取自动存档吗？",
    "セーブデータ全消去": "清除全部存档",
    "セーブ数据全消去": "清除全部存档",
    "セーブデータが破損しているため<br> 読み込みに失敗しました。<br> セーブデータを初期化します。": "存档已损坏<br>无法读取。<br>将初始化存档。",
    "セーブ数据が破損しているため<br> 読み込みに失败しました。<br> セーブ数据を初期化します。": "存档已损坏<br>无法读取。<br>将初始化存档。",
    "全てのデータを消去しました。": "已清除全部数据。",
    "全ての数据が消去されます。[r]本当によろしいですか？<br>": "将全部清除数据。[r]确定吗？<br>",
    # options — general
    "このタブを初期化": "重置此页",
    "ボイス音量": "语音音量",
    "ゲーム設定": "游戏设置",
    "ゲーム设置": "游戏设置",
    "画面の明るさ": "画面亮度",
    "右側の画像の形がギリギリわかる程度に 明るさを調整してください": "请调整亮度至右侧图像轮廓勉强可见的程度",
    "遅い": "慢",
    "速い": "快",
    "暗い": "暗",
    "やや暗い": "较暗",
    "やや明るい": "较亮",
    "明るい": "亮",
    "ビデオ設定": "视频设置",
    "ビデオ设置": "视频设置",
    "アンチエイリアス": "抗锯齿",
    "ポストエフェクト": "后期效果",
    "やや低負荷": "较低负载",
    "画面モード": "画面模式",
    "ウインドウ": "窗口",
    "ボーダーレス": "无边框",
    "フルスクリーン": "全屏",
    "コントローラー持ち方": "手柄握持方式",
    "決定ボタンの設定": "确认键设置",
    "决定按钮の设置": "确认键设置",
    "決定／取消ボタンの割り当てを変更します。<br>": "更改确认／取消键的分配。<br>",
    "决定／取消按钮の割り当てを变更します。<br>": "更改确认／取消键的分配。<br>",
    "メニュー": "菜单",
    "キーボード操作のキーコンフィグ": "键盘按键设置",
    "入力したキーは使用できません。": "无法使用该按键。",
    "設定するキーを入力してください。<br>（ESC で取消）": "请输入要设置的按键。<br>（ESC 取消）",
    "设置するキーを入力してください。<br>（ESC で取消）": "请输入要设置的按键。<br>（ESC 取消）",
    "設定を変更せずに 終了いたしますか？": "不更改设置并退出吗？",
    "更新ページを開く": "打开更新页面",
    "セーブして ゲームを終了いたします。[r]よろしいですか？": "将保存并结束游戏。[r]确定吗？",
    "セーブして ゲームを结束いたします。[r]よろしいですか？": "将保存并结束游戏。[r]确定吗？",
    "表示中のタブの設定を<br>初期状態に戻しますか？": "将当前页设置<br>恢复为初始状态吗？",
    "显示中のタブの设置を<br>初期状态に戻しますか？": "将当前页设置<br>恢复为初始状态吗？",
    "ゲームスタート": "开始游戏",
    "ストーリー図表に 戻ります。よろしいですか？[r][c3]※まだ終了していないチャプターの場合は[r]また最初からやり直しになります。": "将返回故事图表。确定吗？[r][c3]※尚未结束的章节[r]将从头重新开始。",
    "ストーリー图表に 戻ります。よろしいですか？[r][c3]※まだ结束していないチャプターの情况は[r]また最初からやり直しになります。": "将返回故事图表。确定吗？[r][c3]※尚未结束的章节[r]将从头重新开始。",
    "セーブして タイトル画面に戻ります。[r]よろしいですか？": "将保存并返回标题画面。[r]确定吗？",
    "ゲームを終了いたしますか？": "确定结束游戏吗？",
    "演出をスキップいたしますか？": "确定跳过演出吗？",
    "キーコンフィグが競合しているので保存できません。": "按键设置冲突，无法保存。",
    "はじめから": "从头开始",
    "シナリオが更新されましたので[r]チャプターの先頭から始めます。": "剧本已更新，[r]将从章节开头开始。",
    # title / warnings
    "＜未成年者の方へ＞<br> あらかじめ保護者の同意を得てから、 <br>このゲームをご利用ください。 <br><br>ゲーム利用方法（利用時間等）は、<br> 保護者とよく相談して決めてください。": "＜致未成年人＞<br>请在获得监护人同意后再<br>使用本游戏。<br><br>游戏使用方法（使用时间等）<br>请与监护人充分商议后决定。",
    # operation hints
    "AUTO切り替え(ボタン表示時)": "自动切换（显示按钮时）",
    "AUTO切り替え(按钮显示時)": "自动切换（显示按钮时）",
    "スクロール": "滚动",
    "タッチスクリーンでも操作可能<br>(視点移動は2本指でスワイプ)": "也可触屏操作<br>（双指滑动移动视角）",
    "触摸スクリーンでも操作可能<br>(視点移动は2本指でスワイプ)": "也可触屏操作<br>（双指滑动移动视角）",
    # achievements — titles / details (menu-facing)
    "すべての実績を獲得する": "获得全部成就",
    "東京なめどり連合": "东京舔舐联盟",
    "なめどりシールをコンプリートした": "收集了全部舔舐贴纸",
    "ストーリーをコンプリートした": "完成了故事",
    "本所の人脈通": "本所人脉通",
    "人物リストをコンプリートした": "完成了人物列表",
    "本所の情報屋": "本所情报屋",
    "資料をコンプリートした": "完成了资料收集",
    "呪影をコンプリートした": "完成了咒影收集",
    "なめどりエキスパート": "舔舐专家",
    "なめどりシールを１５種類獲得した": "获得了15种舔舐贴纸",
    "興家彰吾のエンディングを迎えた": "迎来了兴家彰吾的结局",
    "なめどりコレクター": "舔舐收藏家",
    "なめどりシールを１０種類獲得した": "获得了10种舔舐贴纸",
    "真夜中の恐怖": "午夜的恐怖",
    "真夜中にゲームをプレイする": "在午夜游玩",
    "ベテラン探究者": "资深探究者",
    "プレイ時間が１０時間に達した": "游玩时间达到10小时",
    "３日後にゲームをプレイする": "3天后再游玩",
    "志岐間春恵の伝説": "志岐间春惠的传说",
    "志岐間春恵のエンディングを迎えた": "迎来了志岐间春惠的结局",
    "襟尾純の選択": "襟尾纯的选择",
    "津詰徹生のエンディングを迎えた": "迎来了津诘彻生的结局",
    "白石美智代の怨讐": "白石美智代的怨仇",
    "逆崎約子のエンディングを迎えた": "迎来了逆崎约子的结局",
    "根島史周の追慕": "根岛史周的追慕",
    "葦宮バッドエンドを迎えた": "迎来了苇宫坏结局",
    "灯野あやめの本懐": "灯野绫的本怀",
    "あやめバッドエンドを迎えた": "迎来了绫坏结局",
    "なめどりビギナー": "舔舐新手",
    "なめどりシールを５種類獲得した": "获得了5种舔舐贴纸",
    "ヒヨっ子なめどり": "雏鸟舔舐",
    "なめどりシールを１種類獲得した": "获得了1种舔舐贴纸",
    "殺したのは誰？": "是谁杀的？",
    "案内人の質問に正解する": "正确回答引导者的问题",
    "序章をクリアした": "完成了序章",
    "志岐間春恵の願い": "志岐间春惠的愿望",
    "１章志岐間篇をクリアした": "完成了第1章志岐间篇",
    "津詰徹生の捜査": "津诘彻生的搜查",
    "１章津詰篇をクリアした": "完成了第1章津诘篇",
    "逆崎約子の友情": "逆崎约子的友情",
    "１章逆崎篇をクリアした": "完成了第1章逆崎篇",
    "志岐間春恵の覚悟": "志岐间春惠的觉悟",
    "２章志岐間篇をクリアした": "完成了第2章志岐间篇",
    "津詰徹生の後悔": "津诘彻生的后悔",
    "２章津詰篇をクリアした": "完成了第2章津诘篇",
    "逆崎約子の正義": "逆崎约子的正义",
    "２章逆崎篇をクリアした": "完成了第2章逆崎篇",
    "蝶澤麻由の脱出": "蝶泽麻由的逃脱",
    "２章蝶澤篇をクリアした": "完成了第2章蝶泽篇",
    "興家彰吾の油断": "兴家彰吾的疏忽",
    "興家彰吾が死亡した": "兴家彰吾死亡",
    "兴家彰吾が死亡した": "兴家彰吾死亡",
    "消えずの興家彰吾": "不消的兴家彰吾",
    "消えずの兴家彰吾": "不消的兴家彰吾",
    "足洗い興家彰吾": "足洗兴家彰吾",
    "足洗い兴家彰吾": "足洗兴家彰吾",
    "送り興家彰吾": "送归兴家彰吾",
    "送り兴家彰吾": "送归兴家彰吾",
    "足洗い津詰徹生": "足洗津诘彻生",
    "足洗い津诘徹生": "足洗津诘彻生",
    "片葉の逆崎約子": "片叶逆崎约子",
    "片葉の逆崎约子": "片叶逆崎约子",
    "消えずの逆崎約子": "不消的逆崎约子",
    "消えずの逆崎约子": "不消的逆崎约子",
    "津诘徹生の捜査": "津诘彻生的搜查",
    "逆崎约子の友情": "逆崎约子的友情",
    # story chart titles
    "「七不思议巡り Part1」": "「七不可思议巡礼 Part1」",
    "「七不思议巡り Part2」": "「七不可思议巡礼 Part2」",
    "「もうひとつの結末」": "「另一个结局」",
    "「夢じゃない」": "「不是梦」",
    "「面白い話」": "「有趣的故事」",
    "「もし このまま」": "「如果就这样」",
    "「そういう世界も ある」": "「也有那样的世界」",
    "「捕まろうが 死のうが」": "「被捕也好 死亡也好」",
    "「あの子……苦手？」": "「那个孩子……不擅长？」",
    "「志岐間春恵の伝説」": "「志岐间春惠的传说」",
    "「灯野あやめの本懐」": "「灯野绫的本怀」",
    "「根島史周の追慕」": "「根岛史周的追慕」",
    "「こっくりさん」": "「占卜盘」",
    "「大事なことは ふたつ」": "「重要的事有两件」",
    "「駒形高校からの脱出」": "「从驹形高中逃脱」",
    "「驹形高中からの脱出」": "「从驹形高中逃脱」",
    "「奥田ちゃんて」": "「奥田同学」",
    "「奥田酱て」": "「奥田同学」",
    "「またね」": "「再见」",
    "「また呢」": "「再见」",
    "「白石美智代の怨讐」": "「白石美智代的怨仇」",
    "「やってやります」": "「看我来搞定」",
    "「決戦の前」": "「决战前」",
    "★出ないテキスト": "★不显示的文本",
    "★出ない文本": "★不显示的文本",
    # doc / lore titles (short UI labels)
    "『置いてけ堀』": "『弃儿堀』",
    "『送り提灯』": "『送归提灯』",
    "『送り拍子木』": "『送归拍子木』",
    "『落葉なき椎』": "『无叶椎』",
    "『津軽の太鼓』": "『津轻太鼓』",
    "『足洗い宅邸』": "『足洗宅邸』",
    "『片葉の芦』": "『片叶芦』",
    "『消えずの行灯』": "『不消的行灯』",
    "禄命簿・陰の書": "禄命簿·阴之书",
    "なめどり": "舔舐",
    "志岐間誘拐事件まとめ": "志岐间诱拐事件汇总",
    "志岐間诱拐事件まとめ": "志岐间诱拐事件汇总",
    # common short UI (from pipeline_harden + leftovers)
    "セーブ": "保存",
    "ロード": "读取",
    "はい": "是",
    "いいえ": "否",
    "タイトルに戻る": "返回标题",
    "ゲーム終了": "结束游戏",
    "コンフィグ": "设置",
    "オプション": "选项",
    "スタート": "开始",
    "オート": "自动",
    "スキップ": "跳过",
    "バックログ": "历史",
    "クイックセーブ": "快速保存",
    "クイックロード": "快速读取",
    "確認": "确认",
    "設定": "设置",
    "取消": "取消",
    "終了": "结束",
    "音量": "音量",
    "明るさ": "亮度",
    "初期化": "初始化",
    "全消去": "全部清除",
    "上書き": "覆盖",
    "読み込み": "读取",
    "保存": "保存",
    "読取": "读取",
    "選択": "选择",
    "決定": "确认",
    "戻る": "返回",
    "開く": "打开",
    "閉じる": "关闭",
    "更新": "更新",
    "初期状態": "初始状态",
    "ボタン": "按钮",
    "キー": "键",
    "キーボード": "键盘",
    "コントローラー": "手柄",
    "フル": "全",
    "画面": "画面",
    "モード": "模式",
    "ウィンドウ": "窗口",
    "エフェクト": "效果",
    "エンディング": "结局",
    "チャプター": "章节",
    "ストーリー": "故事",
    "実績": "成就",
    "リスト": "列表",
    "資料": "资料",
    "人物": "人物",
    "情報": "信息",
    "調整": "调整",
    "変更": "更改",
    "割り当て": "分配",
    "入力": "输入",
    "使用": "使用",
    "使用できません": "无法使用",
    "競合": "冲突",
    "破損": "损坏",
    "失敗": "失败",
    "成功": "成功",
    "よろしいですか": "确定吗",
    "本当に": "真的",
    "すべて": "全部",
    "全て": "全部",
}

SCRIPT_SCRUB_FRAGMENTS: Tuple[Tuple[str, str], ...] = (
    ("大丈夫ですか", "没事吧"),
    ("大丈夫", "没关系"),
    ("ごめんなさい", "对不起"),
    ("すみません", "对不起"),
    ("申し訳ありません", "非常抱歉"),
    ("申し訳ない", "抱歉"),
    ("ありがとうございます", "谢谢"),
    ("ありがとう", "谢谢"),
    ("お疲れ様", "辛苦了"),
    ("失礼します", "失礼了"),
    ("失礼しました", "失礼了"),
    ("待って", "等等"),
    ("待ってください", "请等一下"),
)


def _glossary_keys_longest_first(glossary: Dict[str, str]) -> List[str]:
    return sorted(glossary.keys(), key=len, reverse=True)


def _apply_glossary_value(value: str, glossary: Dict[str, str]) -> Optional[str]:
    """Return CN replacement for a localization cell, preserving {N} placeholders."""
    if not value or not HAS_KANA.search(value):
        return None
    if value in glossary:
        return glossary[value]
    norm = normalize_hazy_key(value)
    if norm in glossary:
        return glossary[norm]
    stripped = _TAG_RE.sub("", value).strip()
    if stripped in glossary:
        cn = glossary[stripped]
        if _TAG_RE.search(value) and not _TAG_RE.search(cn):
            return _attach_control_tags(value, cn)
        return cn
    out = value
    changed = False
    for jp in _glossary_keys_longest_first(glossary):
        if jp in out:
            out = out.replace(jp, glossary[jp])
            changed = True
    if changed and out != value:
        return out
    return None


def _apply_script_scrub(value: str) -> Optional[str]:
    """Conservative JP fragment scrub for hybrid dialogue rows."""
    if not value or not HAS_KANA.search(value) or not HAS_CJK.search(value):
        return None
    kana_n = len(HAS_KANA.findall(value))
    if kana_n >= len(value) * 0.2:
        return None
    out = value
    for jp, cn in SCRIPT_SCRUB_FRAGMENTS:
        if jp in out:
            out = out.replace(jp, cn)
    if out == value:
        return None
    # Reject only if kana increased (shouldn't happen with our fragments)
    if len(HAS_KANA.findall(out)) > kana_n:
        return None
    return out


def _read_ta_text(data) -> Optional[str]:
    script = getattr(data, "script", None) or getattr(data, "m_Script", None)
    if script is None:
        return None
    if isinstance(script, bytes):
        try:
            return script.decode("utf-8")
        except UnicodeDecodeError:
            return script.decode("utf-8", errors="replace")
    return str(script)


def _write_ta_text(data, new_text: str) -> bool:
    if hasattr(data, "script"):
        try:
            data.script = new_text
        except Exception:
            data.script = new_text.encode("utf-8")
    elif hasattr(data, "m_Script"):
        try:
            data.m_Script = new_text
        except Exception:
            data.m_Script = new_text.encode("utf-8")
    else:
        return False
    try:
        data.save()
        return True
    except Exception:
        try:
            raw = new_text.encode("utf-8")
            if hasattr(data, "script"):
                data.script = raw
            else:
                data.m_Script = raw
            data.save()
            return True
        except Exception:
            return False


def _patch_hazy_csv_glossary(text: str, glossary: Dict[str, str]) -> Tuple[str, int]:
    lines = text.splitlines(keepends=True)
    n = 0
    out: List[str] = []
    for line in lines:
        raw = line
        ended = ""
        if raw.endswith("\r\n"):
            body, ended = raw[:-2], "\r\n"
        elif raw.endswith("\n"):
            body, ended = raw[:-1], "\n"
        else:
            body, ended = raw, ""
        plain = body.lstrip("\ufeff")
        if "," not in plain or plain.startswith("#"):
            out.append(raw)
            continue
        _id, cell = plain.split(",", 1)
        key = cell.strip()
        cn = _apply_glossary_value(key, glossary)
        if cn and cn != key:
            out.append(f"{_id},{cn}{ended}")
            n += 1
        else:
            out.append(raw)
    return "".join(out), n


def _patch_hazy_csv_scrub(text: str) -> Tuple[str, int]:
    lines = text.splitlines(keepends=True)
    n = 0
    out: List[str] = []
    for line in lines:
        raw = line
        ended = ""
        if raw.endswith("\r\n"):
            body, ended = raw[:-2], "\r\n"
        elif raw.endswith("\n"):
            body, ended = raw[:-1], "\n"
        else:
            body, ended = raw, ""
        plain = body.lstrip("\ufeff")
        if "," not in plain or plain.startswith("#"):
            out.append(raw)
            continue
        _id, cell = plain.split(",", 1)
        key = cell.strip()
        cn = _apply_script_scrub(key)
        if cn and cn != key:
            out.append(f"{_id},{cn}{ended}")
            n += 1
        else:
            out.append(raw)
    return "".join(out), n


def _save_hazy_pack_inplace(
    fp: Path,
    env,
    *,
    game_dir: Path,
    log: LogFn = None,
    label: str = "",
) -> bool:
    from app.core.unity_bundle_crypto import (
        configure_unitypy_fallback,
        load_unity_env,
        shell_prefix_bytes,
        write_shelled_unityfs,
    )

    try:
        configure_unitypy_fallback(game_dir, log=None)
    except Exception:
        pass
    cache = Path(game_dir) / "_galautotl_unity" / "hazy_ab"
    out_blob = _save_hazy_bundle(env)
    prefix = shell_prefix_bytes(fp, cache)
    if not prefix:
        try:
            raw = fp.read_bytes()
            ufs = raw.find(b"UnityFS")
            if 0 < ufs < 4096:
                prefix = raw[:ufs]
        except OSError:
            prefix = b""
    write_shelled_unityfs(fp, out_blob, prefix=prefix, backup=False)
    if log and label:
        log(f"Hazy 写回 {fp.name}: {label}")
    return True


def _load_a036_text_asset(game_dir: Path, asset_hint: str, log: LogFn = None):
    from app.core.unity_bundle_crypto import configure_unitypy_fallback, load_unity_env

    fp = find_hazy_pack(game_dir, "a036")
    if fp is None:
        if log:
            log("未找到 a036 包")
        return None, None, None
    try:
        configure_unitypy_fallback(game_dir, log=None)
    except Exception:
        pass
    cache = Path(game_dir) / "_galautotl_unity" / "hazy_ab"
    env, how = load_unity_env(fp, cache_dir=cache, log=None, game_dir=Path(game_dir))
    hint = asset_hint.lower()
    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        try:
            data = obj.read()
        except Exception:
            continue
        name = str(getattr(data, "m_Name", None) or getattr(data, "name", None) or "")
        if hint not in name.lower():
            continue
        text = _read_ta_text(data)
        if text is not None:
            return fp, env, (obj, data, name, text, how)
    if log:
        log(f"a036 未找到 TextAsset: {asset_hint}")
    return fp, env, None


def _sanitize_hazy_csv_ruby(text: str) -> Tuple[str, int]:
    """Strip AdvScript UI markup from every id,text cell. Returns (new_text, n_rows)."""
    lines = text.splitlines(keepends=True)
    n = 0
    out: List[str] = []
    for line in lines:
        raw = line
        ended = ""
        if raw.endswith("\r\n"):
            body, ended = raw[:-2], "\r\n"
        elif raw.endswith("\n"):
            body, ended = raw[:-1], "\n"
        else:
            body, ended = raw, ""
        plain = body.lstrip("\ufeff")
        if "," not in plain or plain.startswith("#"):
            out.append(raw)
            continue
        tid, cell = plain.split(",", 1)
        cleaned = sanitize_advscript_markup_for_ui(cell)
        if cleaned != cell:
            out.append(f"{tid},{cleaned}{ended}")
            n += 1
        else:
            out.append(raw)
    return "".join(out), n


def sanitize_hazy_localization_ruby(game_dir: Path, log: LogFn = None) -> int:
    """Strip AdvScript markup from live a036 Hazy_Localization_JP (menu/intel UI)."""
    game_dir = Path(game_dir)
    fp, env, hit = _load_a036_text_asset(game_dir, "hazy_localization_jp", log)
    if not hit:
        return 0
    _obj, data, name, text, _how = hit
    new_text, n = _sanitize_hazy_csv_ruby(text)
    if n <= 0 or new_text == text:
        if log:
            log(f"a036 {name}: 0 条 markup sanitize")
        return 0
    if not _write_ta_text(data, new_text):
        if log:
            log(f"a036 {name}: TextAsset 写入失败")
        return 0
    try:
        _save_hazy_pack_inplace(
            fp, env, game_dir=game_dir, log=log, label=f"{name} markup-sanitize {n} 条"
        )
    except Exception as e:
        if log:
            log(f"a036 保存失败: {e}")
        return 0
    if log:
        log(f"a036 {name}: markup sanitize {n} 条")
    return n


def patch_hazy_localization_glossary(game_dir: Path, log: LogFn = None) -> int:
    """Apply GLOSSARY_UI_JP_CN to a036 Hazy_Localization_JP rows still containing kana."""
    game_dir = Path(game_dir)
    fp, env, hit = _load_a036_text_asset(game_dir, "hazy_localization_jp", log)
    if not hit:
        return 0
    obj, data, name, text, how = hit
    new_text, n = _patch_hazy_csv_glossary(text, GLOSSARY_UI_JP_CN)
    # Always drop AdvScript ruby left by prior writebacks into Loc UI rows.
    new_text, n_ruby = _sanitize_hazy_csv_ruby(new_text)
    n_total = n + n_ruby
    if n_total <= 0 or new_text == text:
        if log:
            log(f"a036 {name}: 0 条 glossary 命中")
        return 0
    if not _write_ta_text(data, new_text):
        if log:
            log(f"a036 {name}: TextAsset 写入失败")
        return 0
    try:
        _save_hazy_pack_inplace(
            fp, env, game_dir=game_dir, log=log, label=f"{name} glossary {n}+ruby {n_ruby}"
        )
    except Exception as e:
        if log:
            log(f"a036 保存失败: {e}")
        return 0
    return n_total


def scrub_hazy_script_jp_fragments(game_dir: Path, log: LogFn = None) -> int:
    """Conservative JP fragment scrub on a036 Hazy_Script_JP hybrid rows."""
    game_dir = Path(game_dir)
    fp, env, hit = _load_a036_text_asset(game_dir, "hazy_script_jp", log)
    if not hit:
        return 0
    obj, data, name, text, how = hit
    new_text, n = _patch_hazy_csv_scrub(text)
    if n <= 0 or new_text == text:
        if log:
            log(f"a036 {name}: 0 条 fragment scrub 命中")
        return 0
    if not _write_ta_text(data, new_text):
        if log:
            log(f"a036 {name}: TextAsset 写入失败")
        return 0
    try:
        _save_hazy_pack_inplace(fp, env, game_dir=game_dir, log=log, label=f"{name} scrub {n} 条")
    except Exception as e:
        if log:
            log(f"a036 保存失败: {e}")
        return 0
    return n


def resync_a024_from_a036_txtid(game_dir: Path, log: LogFn = None, *, force: bool = True) -> int:
    """Sync a024 WindowMessage / adv.ssei payloads from a036 Hazy_Script_JP by txtid."""
    game_dir = Path(game_dir)
    _, _, script_hit = _load_a036_text_asset(game_dir, "hazy_script_jp", log)
    if not script_hit:
        return 0
    _, _, name, script_text, _ = script_hit
    script_by_id = parse_hazy_script_table(script_text)
    if not script_by_id:
        if log:
            log("a036 Hazy_Script_JP 为空，跳过 a024 同步")
        return 0

    from app.core.unity_bundle_crypto import configure_unitypy_fallback, load_unity_env

    fp = find_hazy_pack(game_dir, "a024")
    if fp is None:
        if log:
            log("未找到 a024 包")
        return 0
    try:
        configure_unitypy_fallback(game_dir, log=None)
    except Exception:
        pass
    cache = Path(game_dir) / "_galautotl_unity" / "hazy_ab"
    env, how = load_unity_env(fp, cache_dir=cache, log=None, game_dir=Path(game_dir))

    changed = False
    total = 0
    asset_hits: Dict[str, int] = {}
    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        try:
            data = obj.read()
        except Exception:
            continue
        ta_name = str(getattr(data, "m_Name", None) or getattr(data, "name", None) or "")
        text = _read_ta_text(data)
        if text is None:
            continue
        if "WindowMessage:" not in text and "adv.ssei(" not in text:
            continue
        new_text = text
        n = 0
        if "WindowMessage:" in text:
            new_text, n_w = _replace_window_by_txtid(new_text, script_by_id, force=force)
            n += n_w
        if "adv.ssei(" in text:
            new_text, n_s = _replace_ssei_by_txtid(new_text, script_by_id, force=force)
            n += n_s
        if n <= 0 or new_text == text:
            continue
        if not _write_ta_text(data, new_text):
            continue
        changed = True
        total += n
        asset_hits[ta_name] = asset_hits.get(ta_name, 0) + n

    if not changed:
        if log:
            log("a024 txtid 同步: 0 命中")
        return 0
    try:
        top = ", ".join(f"{k}:{v}" for k, v in sorted(asset_hits.items(), key=lambda x: -x[1])[:5])
        _save_hazy_pack_inplace(
            fp,
            env,
            game_dir=game_dir,
            log=log,
            label=f"txtid 同步 {total} 命中（{top}）",
        )
    except Exception as e:
        if log:
            log(f"a024 保存失败: {e}")
        return 0
    return total
