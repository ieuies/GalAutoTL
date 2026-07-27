# -*- coding: utf-8 -*-
"""KAG / .ks scenario text extract & write-back for Kirikiri."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from app.core.kirikiri_patch import (
    ENGINE_KS_DIRS,
    MACRO_KS_DIRS,
    is_dialogue_ks_relpath,
    is_macro_ks_file,
    is_poison_translation,
    looks_like_ks_script,
)

HAS_KANA = re.compile(r"[\u3040-\u30ff]")
HAS_CJK = re.compile(r"[\u4e00-\u9fff]")
HAS_LATIN = re.compile(r"[A-Za-z]{2,}")
HAS_HANGUL = re.compile(r"[\uac00-\ud7af]")
ALREADY_CN = re.compile(
    r"^[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\s\d，。！？、；：…—·「」『』（）【】《》\.\!\?\-~,\"']+$"
)


def _should_translate_body(
    s: str, source_lang: str = "ja", *, force_jp: bool = False
) -> bool:
    s = s.strip()
    if not s or len(s) < 1:
        return False
    # Skip finished Chinese only — NOT bare JP kanji UI (確認/設定/選択肢).
    if not force_jp:
        try:
            from app.core.pipeline_harden import looks_already_chinese

            if looks_already_chinese(s):
                return False
        except Exception:
            if ALREADY_CN.match(s) and not HAS_KANA.search(s):
                return False
    if source_lang == "ja":
        return bool(HAS_KANA.search(s) or HAS_CJK.search(s))
    if source_lang == "en":
        return bool(HAS_LATIN.search(s))
    if source_lang == "ko":
        return bool(HAS_HANGUL.search(s))
    return bool(HAS_KANA.search(s) or HAS_CJK.search(s) or HAS_LATIN.search(s) or HAS_HANGUL.search(s))


TAG_RE = re.compile(r"\[[^\]]*\]")
# KAG inline escapes: \p \r \l \nwait etc. — keep as placeholders through MT
BACKSLASH_TAG_RE = re.compile(r"\\[a-zA-Z][a-zA-Z0-9]*")
# Attribute text="..." / name='...' / chara="..." (nameplates) / alt / hint=
ATTR_RE = re.compile(
    r"""(?P<prefix>\b(?:text|name|title|caption|msg|message|label|chara|alt|hint|disp|content|value)\s*=\s*)(?P<q>["'])(?P<val>.*?)(?P=q)""",
    re.IGNORECASE,
)
# Safe UI string literals (menus / confirms / iscript hints) — never full TJS rewrite
ASK_YESNO_RE = re.compile(
    r"""(?P<prefix>askYesNo(?:Ex)?\s*\(\s*)(?P<q>['"])(?P<val>.*?)(?P=q)(?P<suffix>\s*\))""",
    re.IGNORECASE,
)
HINT_COLON_RE = re.compile(
    r"""(?P<prefix>\bhint\s*:\s*)(?P<q>["'])(?P<val>.*?)(?P=q)"""
)
# History / UI markers: store("【選択肢】") or store("セーブ")
STORE_UI_RE = re.compile(
    r"""(?P<prefix>\.store\s*\(\s*)(?P<q>["'])(?P<val>[^"']{1,64})(?P=q)(?P<suffix>\s*\))"""
)
# Label display: *start|スタート
LABEL_DISP_RE = re.compile(r"^(?P<prefix>\*[^\|\s]+\|)(?P<val>.+)$")
# Pure command / structural lines
SKIP_LINE = re.compile(
    r"""^\s*(?:;|@|\*|\#|\[iscript\]|\[endscript\]|\[macro\b|\[endmacro\]|\[if\b|\[endif\]|\[else\b|\[elsif\b|\[eval\b|\[emb\b|\[link\b|\[jump\b|\[call\b|\[return\b|\[wait\b|\[quake\b|\[trans\b|\[image\b|\[bg\b|\[se\b|\[bgm\b|\[playse\b|\[playbgm\b|\[stopse\b|\[stopbgm\b|\[fadeinout\b|\[cm\]|\[ct\]|\[er\]|\[clear\b|\[reset\b)""",
    re.IGNORECASE,
)


@dataclass
class KsUnit:
    path: Path
    line_index: int
    kind: str  # line | attr | quoted
    source: str
    encoding: str
    # attr/quoted: original matched span for precise replace
    attr_key: str = ""


def detect_ks_encoding(raw: bytes) -> str:
    if raw.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        return "utf-16-be"
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    # Heuristic: lots of NUL → UTF-16LE without BOM (common in Kirikiri)
    sample = raw[: min(4000, len(raw))]
    if sample and sample[1::2].count(0) > len(sample) * 0.3:
        try:
            sample.decode("utf-16-le")
            return "utf-16-le"
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8", "cp932"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "utf-8"


def read_ks(path: Path) -> Tuple[str, str]:
    raw = path.read_bytes()
    enc = detect_ks_encoding(raw)
    if enc.startswith("utf-16"):
        text = raw.decode(enc)
        # strip BOM char if present
        if text.startswith("\ufeff"):
            text = text[1:]
        return text, enc
    return raw.decode(enc, errors="replace"), enc


def write_ks(path: Path, text: str, encoding: str) -> None:
    # Preserve BOM conventions for UTF-16; fall back to UTF-16-LE when CP932 can't hold CJK/CN.
    if encoding == "utf-16-le":
        path.write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))
        return
    if encoding == "utf-16-be":
        path.write_bytes(b"\xfe\xff" + text.encode("utf-16-be"))
        return
    if encoding == "utf-8-sig":
        path.write_text(text, encoding="utf-8-sig")
        return
    try:
        path.write_text(text, encoding=encoding)
    except UnicodeEncodeError:
        path.write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))


def _mask_tags(s: str) -> Tuple[str, List[str]]:
    tags: List[str] = []

    def repl_bracket(m: re.Match) -> str:
        tags.append(m.group(0))
        return f"{{{{T{len(tags) - 1}}}}}"

    def repl_bs(m: re.Match) -> str:
        tags.append(m.group(0))
        return f"{{{{T{len(tags) - 1}}}}}"

    masked = TAG_RE.sub(repl_bracket, s)
    masked = BACKSLASH_TAG_RE.sub(repl_bs, masked)
    return masked, tags


def _unmask_tags(s: str, tags: List[str]) -> str:
    out = s
    for i, tag in enumerate(tags):
        for token in (
            f"{{{{T{i}}}}}",
            f"{{T{i}}}",
            f"[T{i}]",
            f"(T{i})",
            f"⟦T{i}⟧",
        ):
            if token in out:
                out = out.replace(token, tag)
        # Use lambda repl — KAG tags may contain `\` and break re.sub backrefs
        pat = re.compile(r"\{\{\s*T\s*" + str(i) + r"\s*\}\}")
        out = pat.sub(lambda _m, t=tag: t, out)
    return out


def _append_attr_units(
    units: List[KsUnit],
    path: Path,
    li: int,
    line: str,
    enc: str,
    source_lang: str,
) -> None:
    for m in ATTR_RE.finditer(line):
        val = m.group("val")
        # chara= nameplates: 俺/地 etc. must not be skipped as "already CN"
        force = m.group("prefix").lower().startswith("chara")
        if _should_translate_body(val, source_lang, force_jp=force):
            units.append(
                KsUnit(
                    path=path,
                    line_index=li,
                    kind="attr",
                    source=val,
                    encoding=enc,
                    attr_key=m.group(0),
                )
            )


def _append_quoted_ui_units(
    units: List[KsUnit],
    path: Path,
    li: int,
    line: str,
    enc: str,
    source_lang: str,
) -> None:
    """Extract askYesNo / hint: / store(【】) / *label|display — safe UI only."""
    for rx in (ASK_YESNO_RE, HINT_COLON_RE, STORE_UI_RE):
        for m in rx.finditer(line):
            val = m.group("val")
            if not _should_translate_body(val, source_lang, force_jp=True):
                continue
            units.append(
                KsUnit(
                    path=path,
                    line_index=li,
                    kind="quoted",
                    source=val,
                    encoding=enc,
                    attr_key=m.group(0),
                )
            )
    m = LABEL_DISP_RE.match(line.strip())
    if m:
        val = m.group("val").strip()
        if _should_translate_body(val, source_lang, force_jp=True):
            units.append(
                KsUnit(
                    path=path,
                    line_index=li,
                    kind="quoted",
                    source=val,
                    encoding=enc,
                    attr_key=m.group(0),
                )
            )


def collect_ks_units(root: Path, source_lang: str = "ja") -> List[KsUnit]:
    units: List[KsUnit] = []
    root_parts_l = {p.lower() for p in root.parts}
    root_in_work_dir = (
        "_galautotl_kirikiri" in root_parts_l or "_galautotl_lcse" in root_parts_l
    )
    # Engine / UI logic — translating breaks TJS inside [iscript] (boot failure)
    skip_dir_names = set(ENGINE_KS_DIRS) | set(MACRO_KS_DIRS)
    all_ks = [p for p in sorted(root.rglob("*.ks")) if p.is_file()]
    has_dialogue_tree = any(
        is_dialogue_ks_relpath(p.relative_to(root)) for p in all_ks
    )
    for path in all_ks:
        if not root_in_work_dir:
            parts_l = {p.lower() for p in path.parts}
            if "_galautotl_kirikiri" in parts_l or "_galautotl_lcse" in parts_l:
                continue
        rel = path.relative_to(root)
        rel_parts_l = {p.lower() for p in rel.parts}
        if rel_parts_l & skip_dir_names:
            continue
        # macro/first.ks under scenario/: safe UI strings only (never full dialogue rewrite)
        safe_ui_only = is_macro_ks_file(path)
        if has_dialogue_tree and not is_dialogue_ks_relpath(rel) and not safe_ui_only:
            continue
        raw = path.read_bytes()
        if not looks_like_ks_script(raw):
            continue
        try:
            text, enc = read_ks(path)
        except Exception:
            continue
        lines = text.splitlines()
        in_iscript = False
        for li, line in enumerate(lines):
            stripped = line.strip()
            low = stripped.lower()
            if low.startswith("[iscript]") or low == "[iscript]" or low.startswith("@iscript"):
                in_iscript = True
                continue
            if (
                low.startswith("[endscript]")
                or low == "[endscript]"
                or low.startswith("@endscript")
            ):
                in_iscript = False
                continue
            if not stripped:
                continue
            # Inside iscript / macro files: only safe UI quoted strings
            if in_iscript or safe_ui_only:
                _append_quoted_ui_units(units, path, li, line, enc, source_lang)
                _append_attr_units(units, path, li, line, enc, source_lang)
                continue
            if SKIP_LINE.match(stripped):
                _append_attr_units(units, path, li, line, enc, source_lang)
                _append_quoted_ui_units(units, path, li, line, enc, source_lang)
                continue

            if stripped.startswith("[") and stripped.endswith("]") and ATTR_RE.search(line):
                _append_attr_units(units, path, li, line, enc, source_lang)
                continue

            masked, tags = _mask_tags(line)
            body_check = TAG_RE.sub("", line)
            body_check = BACKSLASH_TAG_RE.sub("", body_check).strip()
            if not body_check:
                continue
            if not _should_translate_body(body_check, source_lang):
                continue
            units.append(
                KsUnit(
                    path=path,
                    line_index=li,
                    kind="line",
                    source=masked if tags else line,
                    encoding=enc,
                    attr_key="\x00".join(tags),
                )
            )
    return units


def _rebuild_quoted(attr_key: str, dst: str) -> Optional[str]:
    """Rebuild askYesNo/hint/store/label span with translated value."""
    for rx in (ASK_YESNO_RE, HINT_COLON_RE, STORE_UI_RE):
        m = rx.search(attr_key)
        if m:
            q = m.group("q")
            suffix = m.groupdict().get("suffix") or ""
            return f"{m.group('prefix')}{q}{dst}{q}{suffix}"
    m = LABEL_DISP_RE.match(attr_key.strip())
    if m:
        return f"{m.group('prefix')}{dst}"
    return None


def apply_ks_units(units: List[KsUnit], translated: List[str]) -> int:
    """Apply translations in-place. CN overrides always UTF-16-LE; poison kept as JP."""
    from collections import defaultdict

    from app.core.pipeline_harden import looks_already_chinese

    by_file: dict[Path, List[Tuple[KsUnit, str]]] = defaultdict(list)
    for u, t in zip(units, translated):
        if not t or t == u.source:
            continue  # unchanged — skip (critical for 仅译漏句)
        # Do not replace finished Chinese with a different AI string on re-run
        if looks_already_chinese(u.source):
            continue
        # AI refusal / meta lines — keep Japanese source (do not rewrite file for these)
        if is_poison_translation(t) or is_poison_translation(u.source):
            continue
        by_file[u.path].append((u, t))

    touched = 0
    for path, group in by_file.items():
        text, _ = read_ks(path)
        lines = text.splitlines()
        group_sorted = sorted(
            group,
            key=lambda x: (
                x[0].line_index,
                0 if x[0].kind in ("attr", "quoted") else 1,
            ),
        )
        for u, dst in group_sorted:
            if u.line_index >= len(lines):
                continue
            if u.kind == "line":
                tags = u.attr_key.split("\x00") if u.attr_key else []
                lines[u.line_index] = _unmask_tags(dst, tags) if tags else dst
            elif u.kind == "attr":
                line = lines[u.line_index]
                if u.attr_key and u.attr_key in line:
                    m = ATTR_RE.search(u.attr_key)
                    if m:
                        q = m.group("q")
                        new_attr = f"{m.group('prefix')}{q}{dst}{q}"
                        lines[u.line_index] = line.replace(u.attr_key, new_attr, 1)
                    else:
                        lines[u.line_index] = line.replace(u.source, dst, 1)
                else:
                    lines[u.line_index] = line.replace(u.source, dst, 1)
            elif u.kind == "quoted":
                line = lines[u.line_index]
                rebuilt = _rebuild_quoted(u.attr_key, dst) if u.attr_key else None
                if rebuilt and u.attr_key and u.attr_key in line:
                    lines[u.line_index] = line.replace(u.attr_key, rebuilt, 1)
                elif u.source in line:
                    # fallback: replace quoted form
                    for q in ("'", '"'):
                        old = f"{q}{u.source}{q}"
                        new = f"{q}{dst}{q}"
                        if old in line:
                            lines[u.line_index] = line.replace(old, new, 1)
                            break

        out = "\n".join(lines)
        if text.endswith("\n"):
            out += "\n"
        # Always UTF-16-LE for CN so archive CP932 JP never mixes into the same file
        write_ks(path, out, "utf-16-le")
        touched += 1
    return touched
