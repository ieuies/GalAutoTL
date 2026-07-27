# -*- coding: utf-8 -*-
"""Artemis .ast / .txt script text extract & write-back.

Lessons locked in (do not regress):
1. Never skip units just because absolute path contains ``_galautotl_`` —
   collect root is often ``_galautotl_artemis/scripts`` itself.
2. Dialogue lives in trailing ``text={...}``; do NOT translate command-head
   quotes (ch=/file=/path=) or asset lookups break.
3. Deploy loose files under ``script/`` (strip ``root`` / ``root.pfs`` prefix);
   Artemis prefers disk over PFS.
4. Never translate ``system/**/*.lua`` (engine glue).
5. Reject AI poison placeholders on write-back.
6. ``name={"name", name="…"}`` is the **name plate layer**; the next ``{ "…" }``
   is the **adv body layer**. Putting the full line in both → stacked duplicate
   text on screen (嫁の妹とえっちな関係…). Keep name= as short speaker only;
   always restore speakers from JP after AI write-back.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.core.workdirs import is_nested_galautotl_part, rel_parts_under

HAS_KANA = re.compile(r"[\u3040-\u30ff]")
HAS_CJK = re.compile(r"[\u4e00-\u9fff]")
Q_RE = re.compile(r"""(?P<q>["'])(?P<body>(?:\\.|(?!\1).)*)(?P=q)""")
TAG_RE = re.compile(r"\[[^\]]*\]")

POISON_MARKERS = (
    "无法识别",
    "疑似乱码",
    "按原文输出",
    "［无法翻译］",
    "[无法翻译]",
)

SKIP_CMD_WORDS = frozenset(
    {"text", "name", "vo", "bg", "fg", "bgm", "se", "msgoff", "extrans", "cgdel"}
)

# Speaker plate: short labels only (灯织 / 主人公 …)
_SPEAKER_MAX_LEN = 12


@dataclass
class AstUnit:
    path: Path
    kind: str  # quote | name | line
    meta: object  # match span or line index
    source: str
    encoding: str


def _enc_read(path: Path) -> Tuple[str, str]:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe"):
        return raw.decode("utf-16-le"), "utf-16-le"
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    for enc in ("utf-8", "cp932"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8"


def _enc_write(path: Path, text: str, encoding: str) -> None:
    if encoding == "utf-16-le":
        path.write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))
    elif encoding == "utf-8-sig":
        path.write_text(text, encoding="utf-8-sig")
    else:
        path.write_text(text, encoding=encoding)


def _want(s: str) -> bool:
    s = s.strip()
    if len(s) < 1:
        return False
    if re.fullmatch(r"[\d\s\.\-_:/\\]+", s):
        return False
    return bool(HAS_KANA.search(s) or HAS_CJK.search(s))


def _is_poison(s: str) -> bool:
    if not s or not s.strip():
        return True
    return any(m in s for m in POISON_MARKERS)


def _looks_like_dialogue_name(s: str) -> bool:
    """True if string belongs in adv body, not the name plate."""
    s = (s or "").strip()
    if not s:
        return False
    if s.startswith("|") and len(s) > 4:
        return True
    if "「" in s or "」" in s or "『" in s or "』" in s:
        return True
    if any(ch in s for ch in "。！？?!…"):
        return True
    return len(s) > _SPEAKER_MAX_LEN


def _is_name_attr_quote(region: str, quote_start: int) -> bool:
    before = region[max(0, quote_start - 24) : quote_start]
    return bool(re.search(r"\bname\s*=\s*$", before, re.I))


def _iter_script_files(root: Path) -> List[Path]:
    if root.is_file():
        return [root]
    return sorted(p for p in root.rglob("*") if p.is_file())


def _ast_text_region(text: str) -> Tuple[str, int]:
    """Return (``text={...}`` region, start offset). Empty region → (\"\", -1)."""
    idx = text.rfind("\n\ttext={")
    if idx < 0:
        idx = text.rfind("\ntext={")
    if idx < 0:
        idx = text.find("text={")
    if idx < 0:
        return "", -1
    return text[idx:], idx


def normalize_artemis_rel(rel: Path) -> Path:
    """Strip extract dump prefixes: root / root.pfs / arc.pfs → in-game path."""
    parts = list(rel.parts)
    while parts and (
        parts[0].lower() in {"root", "root.pfs", "arc", "arc.pfs"}
        or parts[0].lower().endswith(".pfs")
    ):
        parts = parts[1:]
    return Path(*parts) if parts else Path()


def collect_artemis_units(root: Path) -> List[AstUnit]:
    """Extract dialogue from Artemis scripts under root."""
    units: List[AstUnit] = []
    root_res = root.resolve()
    all_files = _iter_script_files(root)
    has_script_tree = any(
        "script" in {p.lower() for p in (rel_parts_under(root_res, f) or ())}
        for f in all_files
        if f.suffix.lower() == ".ast"
    )
    for path in all_files:
        rel_parts = rel_parts_under(root_res, path)
        if rel_parts is None:
            continue
        if is_nested_galautotl_part(rel_parts):
            continue
        suf = path.suffix.lower()
        if suf not in (".ast", ".txt", ".lua", ".csv"):
            continue
        low_parts = {p.lower() for p in rel_parts}
        if suf == ".lua" and ("system" in low_parts or "adv" in low_parts or "ui" in low_parts):
            continue
        if (
            suf == ".ast"
            and has_script_tree
            and "script" not in low_parts
            and path.name.lower() != "_text.ast"
        ):
            continue
        try:
            text, enc = _enc_read(path)
        except Exception:
            continue
        if suf == ".ast":
            region, region_off = _ast_text_region(text)
            if region_off < 0 or not region:
                continue
            for m in Q_RE.finditer(region):
                body = m.group("body")
                plain = body.replace("\\n", "\n").replace('\\"', '"').replace("\\'", "'")
                if plain.isascii() and not HAS_CJK.search(plain):
                    continue
                if plain in SKIP_CMD_WORDS:
                    continue
                if not _want(plain):
                    continue
                is_name = _is_name_attr_quote(region, m.start())
                if is_name:
                    # Only short speaker labels; never send full lines to the model
                    if _looks_like_dialogue_name(plain):
                        continue
                    kind = "name"
                else:
                    kind = "quote"
                units.append(
                    AstUnit(
                        path,
                        kind,
                        (region_off + m.start("body"), region_off + m.end("body"), m.group("q")),
                        plain,
                        enc,
                    )
                )
        elif suf == ".lua":
            for m in Q_RE.finditer(text):
                body = m.group("body")
                plain = body.replace("\\n", "\n").replace('\\"', '"').replace("\\'", "'")
                if plain.startswith(":") or (plain.isascii() and "/" in plain):
                    continue
                if _want(plain):
                    units.append(
                        AstUnit(
                            path,
                            "quote",
                            (m.start("body"), m.end("body"), m.group("q")),
                            plain,
                            enc,
                        )
                    )
        else:
            lines = text.splitlines()
            for i, line in enumerate(lines):
                body = TAG_RE.sub("", line).strip()
                if not _want(body):
                    continue
                m = Q_RE.fullmatch(line.strip())
                if m:
                    units.append(
                        AstUnit(
                            path,
                            "quote",
                            ("linequote", i, m.group("q")),
                            m.group("body").replace("\\n", "\n"),
                            enc,
                        )
                    )
                else:
                    units.append(AstUnit(path, "line", i, line, enc))
    return units


def _clamp_name_translation(src: str, dst: str) -> str:
    """If model expanded a speaker into a full line, keep JP/CN source label."""
    if _is_poison(dst):
        return src
    if _looks_like_dialogue_name(dst) and not _looks_like_dialogue_name(src):
        return src
    # strip accidental quotes/pipes around short names
    t = (dst or "").strip().strip("|").strip("「」『』\"'")
    if t and not _looks_like_dialogue_name(t) and len(t) <= _SPEAKER_MAX_LEN + 2:
        return t
    if _looks_like_dialogue_name(dst):
        return src
    return dst


def apply_artemis_units(units: List[AstUnit], translated: List[str]) -> int:
    from collections import defaultdict

    from app.core.pipeline_harden import looks_already_chinese

    by_file: dict[Path, List[Tuple[AstUnit, str]]] = defaultdict(list)
    for u, t in zip(units, translated):
        if not t or t == u.source:
            continue
        if looks_already_chinese(u.source):
            continue
        if u.kind == "name":
            t = _clamp_name_translation(u.source, t)
        elif _is_poison(t):
            continue
        by_file[u.path].append((u, t))
    if not by_file:
        return 0
    touched = 0
    for path, group in by_file.items():
        text, enc = _enc_read(path)
        quote_spans = [
            (u, t)
            for u, t in group
            if u.kind in ("quote", "name")
            and isinstance(u.meta, tuple)
            and len(u.meta) == 3
            and isinstance(u.meta[0], int)
        ]
        quote_spans.sort(key=lambda x: x[0].meta[0], reverse=True)
        for u, t in quote_spans:
            start, end, q = u.meta
            esc = t.replace("\\", "\\\\").replace(q, f"\\{q}").replace("\n", "\\n")
            text = text[:start] + esc + text[end:]

        lines = text.splitlines()
        for u, t in group:
            if u.kind == "line" and isinstance(u.meta, int):
                if u.meta < len(lines):
                    lines[u.meta] = t
            elif (
                u.kind == "quote"
                and isinstance(u.meta, tuple)
                and u.meta
                and u.meta[0] == "linequote"
            ):
                _, idx, q = u.meta
                if idx < len(lines):
                    esc = t.replace("\\", "\\\\").replace(q, f"\\{q}").replace("\n", "\\n")
                    lines[idx] = f"{q}{esc}{q}"
        if quote_spans:
            base_lines = text.splitlines()
            for u, t in group:
                if u.kind == "line" and isinstance(u.meta, int) and u.meta < len(base_lines):
                    base_lines[u.meta] = t
                elif (
                    u.kind == "quote"
                    and isinstance(u.meta, tuple)
                    and u.meta
                    and u.meta[0] == "linequote"
                ):
                    _, idx, q = u.meta
                    if idx < len(base_lines):
                        esc = (
                            t.replace("\\", "\\\\")
                            .replace(q, f"\\{q}")
                            .replace("\n", "\\n")
                        )
                        base_lines[idx] = f"{q}{esc}{q}"
            out = "\n".join(base_lines)
        else:
            out = "\n".join(lines)
        if text.endswith("\n"):
            out += "\n"
        _enc_write(path, out, enc)
        touched += 1
    return touched


_BLOCK_RE = re.compile(r"\[(\d+)\]\s*=\s*\{", re.S)
_NAME_IN_BLOCK = re.compile(
    r'(name=\{\"name\",\s*name=\")(.*?)(\"\})',
    re.S,
)
_BODY_QUOTE_IN_BLOCK = re.compile(
    r'name=\{\"name\",\s*name=\"[^\"]*\"\}\s*,?\s*\{\s*\"([^\"]*)\"',
    re.S,
)


def _ast_blocks(text: str) -> Dict[int, Tuple[int, int]]:
    hits = list(_BLOCK_RE.finditer(text))
    out: Dict[int, Tuple[int, int]] = {}
    for i, m in enumerate(hits):
        idx = int(m.group(1))
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        out[idx] = (m.start(), end)
    return out


def _name_in_span(text: str, a: int, b: int) -> Optional[str]:
    m = _NAME_IN_BLOCK.search(text[a:b])
    return m.group(2) if m else None


def _body_in_span(text: str, a: int, b: int) -> Optional[str]:
    m = _BODY_QUOTE_IN_BLOCK.search(text[a:b])
    return m.group(1) if m else None


def _set_name_in_span(text: str, a: int, b: int, new_name: str) -> str:
    seg = text[a:b]
    m = _NAME_IN_BLOCK.search(seg)
    if not m:
        return text
    esc = new_name.replace("\\", "\\\\").replace('"', '\\"')
    return text[:a] + seg[: m.start(2)] + esc + seg[m.end(2) :] + text[b:]


def restore_artemis_speakers(
    cn_root: Path,
    jp_root: Path,
    speaker_map: Optional[dict] = None,
) -> int:
    """
    Restore short speaker names from JP into CN scripts.
    Artemis draws name= and body on separate layers — full-line name= stacks text.
    """
    speaker_map = speaker_map or {}
    cn_files = {
        p.relative_to(cn_root).as_posix().lower(): p
        for p in cn_root.rglob("*.ast")
        if p.is_file()
    }
    fixed = 0
    for jp_path in jp_root.rglob("*.ast"):
        if not jp_path.is_file():
            continue
        rel = jp_path.relative_to(jp_root).as_posix().lower()
        cn_path = cn_files.get(rel)
        if not cn_path:
            cn_path = next(
                (p for r, p in cn_files.items() if Path(r).name == jp_path.name.lower()),
                None,
            )
        if not cn_path:
            continue
        jp = jp_path.read_text(encoding="utf-8", errors="replace")
        cn = cn_path.read_text(encoding="utf-8", errors="replace")
        jp_blocks = _ast_blocks(jp)
        changed = 0
        for idx in sorted(jp_blocks.keys(), reverse=True):
            cn_blocks = _ast_blocks(cn)
            if idx not in cn_blocks:
                continue
            ca, cb = cn_blocks[idx]
            ja, jb = jp_blocks[idx]
            jp_name = _name_in_span(jp, ja, jb)
            cn_name = _name_in_span(cn, ca, cb)
            if jp_name is None:
                continue
            new_name = speaker_map.get(jp_name, jp_name)
            if _looks_like_dialogue_name(jp_name):
                # Rare: JP used name slot as line — don't duplicate CN body into name
                new_name = "" if _looks_like_dialogue_name(cn_name or "") else (cn_name or "")
            elif _looks_like_dialogue_name(new_name):
                new_name = jp_name
            if (cn_name or "") != new_name:
                cn = _set_name_in_span(cn, ca, cb, new_name)
                changed += 1
        if changed:
            cn_path.write_text(cn, encoding="utf-8")
            fixed += changed
    return fixed


def scrub_artemis_duplicate_name_plates(cn_root: Path) -> int:
    """
    Safety net without JP: if name= equals (or pipe-equals) the following body quote,
    clear name= so only the adv layer shows the line.
    """
    fixed = 0
    for path in cn_root.rglob("*.ast"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        blocks = _ast_blocks(text)
        changed = 0
        for idx in sorted(blocks.keys(), reverse=True):
            blocks = _ast_blocks(text)
            if idx not in blocks:
                continue
            a, b = blocks[idx]
            name = _name_in_span(text, a, b)
            body = _body_in_span(text, a, b)
            if name is None or body is None:
                continue
            nn, bb = name.lstrip("|"), body.lstrip("|")
            if name == body or nn == bb or (
                _looks_like_dialogue_name(name) and (nn in bb or bb in nn)
            ):
                if name != "":
                    text = _set_name_in_span(text, a, b, "")
                    changed += 1
        if changed:
            path.write_text(text, encoding="utf-8")
            fixed += changed
    return fixed
