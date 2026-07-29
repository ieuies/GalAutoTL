# -*- coding: utf-8 -*-
"""Proper-noun glossary with hard consistency guarantees.

Mask SRC terms as ⟦GALTL_A⟧ (letter codes, never digits), send to the model,
then unmask to fixed DST. Digit placeholders like {{GALTL0}} were collapsed by
models into bare「0」(0个人/0夏) and could crash script engines.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

GLOSSARY_NAMES = (
    "GalAutoTL_glossary.txt",
    "glossary.txt",
    "术语表.txt",
)

AUTO_GLOSSARY_NAME = "GalAutoTL_glossary_auto.txt"
CANDIDATE_NAME = "GalAutoTL_glossary_candidates.txt"
PERSONA_RULES_NAME = "GalAutoTL_persona.txt"

# Built-in default (users override by editing GalAutoTL_persona.txt in the game dir).
DEFAULT_PERSONA_RULES = (
    "【默认人设规则 — 中文对白】\n"
    "1. 日文「彼・彼女」等出现在原文里时，中文叙述/转述用「他/她」，"
    "不要写成日式「彼/彼女」当面称呼。\n"
    "2. 角色自称（僕/俺/あたし/私 等）译成自然中文（我/咱/俺…），同一角色前后一致。\n"
    "3. 对称（君/お前/あなた 等）按关系译成你/您等，避免生硬直译日文称呼腔。\n"
    "4. 口吻、敬体/常体、口癖贴合角色；人设说明文字不得写进台词。\n"
    "5. 下方「角色备注」若与本默认冲突，以该角色备注为准。\n"
)

# Ignore trivial / asset-like "names"
_SKIP_DST = re.compile(r"^[\s\d\._\-/\\:]+$")
_HAS_WORD = re.compile(r"[\u3040-\u30ff\u4e00-\u9fffA-Za-z]")

# Placeholder design notes:
# - Old form {{GALTL0}} ends with a digit; models often "helpfully" collapse it to bare「0」,
#   producing 0个人 / 0夏 / 0声 — and leftover {{GALTL0} can crash Sakana script parsers.
# - New form uses letter codes only: ⟦GALTL_A⟧ (no digits).
_PH_OPEN = "⟦"
_PH_CLOSE = "⟧"


def _idx_to_code(idx: int) -> str:
    """0→A, 25→Z, 26→AA (bijective base-26, no digits)."""
    n = idx + 1
    letters: List[str] = []
    while n > 0:
        n, r = divmod(n - 1, 26)
        letters.append(chr(ord("A") + r))
    return "".join(reversed(letters))


def _code_to_idx(code: str) -> int:
    n = 0
    for ch in (code or "").upper():
        if not ("A" <= ch <= "Z"):
            return -1
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def placeholder_token(idx: int) -> str:
    return f"{_PH_OPEN}GALTL_{_idx_to_code(idx)}{_PH_CLOSE}"


# Well-formed + broken leftovers (new letter form + legacy digit form)
_GALTL_NEW = re.compile(r"⟦\s*GALTL_([A-Z]+)\s*⟧", re.I)
_GALTL_NEW_BROKEN = re.compile(r"⟦\s*GALTL_([A-Z]*)\s*⟧?", re.I)
_GALTL_LEGACY = re.compile(r"\{\{\s*GALTL\s*(\d+)\s*\}\}", re.I)
_GALTL_LEGACY_BROKEN = re.compile(r"\{\{\s*GALTL\s*(\d*)\s*\}?\s*", re.I)
_GALTL_BARE = re.compile(r"GALTL[_]?([A-Z]+|\d+)", re.I)
# Digit-collapse corruption after a mangled {{GALTL0}}
_CORRUPT_ZERO = re.compile(
    r"0夏|0个人|0声|另0个|这样0个|0件需要|但0想|只有我0|遇到的0"
)
_HAS_KANA = re.compile(r"[\u3040-\u30ff]")
_HAS_KANJI = re.compile(r"[\u4e00-\u9fff]")

# Common dialogue / filler — never treat as proper nouns
_STOP_NAMES = frozenset(
    {
        "ああ",
        "ええ",
        "うん",
        "はい",
        "いいえ",
        "そう",
        "でも",
        "それ",
        "これ",
        "あれ",
        "ここ",
        "そこ",
        "あそこ",
        "なに",
        "何",
        "誰",
        "私",
        "僕",
        "俺",
        "あたし",
        "あなた",
        "あんた",
        "君",
        "お前",
        "みんな",
        "二人",
        "一人",
        "今日",
        "明日",
        "昨日",
        "本当",
        "大丈夫",
        "ごめん",
        "すみません",
        "ありがとう",
        "お願い",
        "ちょっと",
        "もっと",
        "きっと",
        "ずっと",
        "こんな",
        "そんな",
        "あんな",
        "どう",
        "どうして",
        "なぜ",
        "ええっ",
        "あっ",
        "えっ",
        "はっ",
        "んっ",
        "ふふ",
        "うふふ",
        "わあっ",
        "システム",
        "テスト",
        "スタート",
        "タイトル",
        "コンフィグ",
        "ロード",
        "セーブ",
        "エンド",
    }
)

_NAME_ATTR_RE = re.compile(
    r"""(?:name|ch|speaker|char|heroine)\s*=\s*["']([^"']{1,40})["']""",
    re.IGNORECASE,
)
_KAG_NAME_RE = re.compile(
    r"\[name\s+[^\]]*?(?:name|text)\s*=\s*[\"']([^\"']+)[\"']",
    re.I,
)


@dataclass(frozen=True)
class Glossary:
    """Ordered SRC→DST map; sources unique, longer keys preferred when matching.

    Optional ``notes`` are prompt-only (gender / speech / persona). They are never
    masked into placeholders — only shown to the model so pronouns and tone stay
    consistent. Format is GalAutoTL's own (``SRC=DST ;; note``), not third-party.
    """

    pairs: Tuple[Tuple[str, str], ...]  # longest-src first
    notes: Tuple[Tuple[str, str], ...] = ()  # src → freeform note

    def __bool__(self) -> bool:
        return bool(self.pairs) or bool(self.notes)

    @property
    def size(self) -> int:
        return len(self.pairs)

    def note_for(self, src: str) -> str:
        for s, n in self.notes:
            if s == src:
                return n
        return ""

    def as_prompt_block(self) -> str:
        if not self.pairs and not self.notes:
            return ""
        note_map = {s: n for s, n in self.notes}
        lines: List[str] = []
        if self.pairs:
            lines.append("【强制术语 — 译名固定；引擎会硬替换占位符，模型勿另造译名】")
            for src, dst in sorted(self.pairs, key=lambda x: (-len(x[0]), x[0])):
                note = (note_map.get(src) or "").strip()
                if note:
                    lines.append(f"  {src} → {dst}（{note}）")
                else:
                    lines.append(f"  {src} → {dst}")
        # Notes whose SRC is not in pairs (rare) still guide tone
        orphan = [(s, n) for s, n in self.notes if s not in {a for a, _ in self.pairs}]
        if orphan:
            lines.append("【人设提示 — 无固定译名条目，仅约束人称/语气】")
            for src, note in sorted(orphan, key=lambda x: x[0]):
                lines.append(f"  {src}：{note.strip()}")
        if note_map:
            lines.append(
                "【角色备注】以上括号/专条为人设补充；与默认人设规则冲突时以备注为准；"
                "勿把备注原文写进台词。"
            )
        return "\n".join(lines)


def _split_dst_note(dst_raw: str) -> Tuple[str, str]:
    """Split ``译文 ;; 人设备注`` (GalAutoTL). Empty note if absent."""
    raw = (dst_raw or "").strip()
    if ";;" in raw:
        dst, note = raw.split(";;", 1)
        return dst.strip(), note.strip()
    return raw, ""


def _norm_pair(src: str, dst: str) -> Optional[Tuple[str, str]]:
    src = (src or "").strip()
    dst = (dst or "").strip()
    if not src or not dst:
        return None
    if src == dst and not _HAS_WORD.search(src):
        return None
    if _SKIP_DST.match(dst):
        return None
    if len(src) > 80 or len(dst) > 80:
        return None
    return src, dst


def parse_glossary_text(text: str) -> Glossary:
    """Parse ``SRC=DST`` / ``SRC|DST`` / ``SRC→DST`` / ``SRC=DST ;; note`` / JSON.

    Persona notes (optional):
      - ``あや=绫 ;; 女，学生，自称「我」``
      - ``あや|绫|女，学生，自称「我」`` (three fields)
    Notes are prompt-only; mask/unmask still uses SRC→DST only.
    """
    text = text.lstrip("\ufeff")
    mapping: Dict[str, str] = {}
    notes: Dict[str, str] = {}

    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, dict):
                        pair = _norm_pair(str(k), str(v.get("dst") or v.get("cn") or ""))
                        if pair:
                            mapping[pair[0]] = pair[1]
                        note = str(v.get("note") or v.get("persona") or "").strip()
                        if note and pair:
                            notes[pair[0]] = note
                        continue
                    dst, note = _split_dst_note(str(v))
                    pair = _norm_pair(str(k), dst)
                    if pair:
                        mapping[pair[0]] = pair[1]
                        if note:
                            notes[pair[0]] = note
                return _freeze(mapping, notes)
        except json.JSONDecodeError:
            pass

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//") or line.startswith(";"):
            continue
        src = dst_raw = ""
        if "→" in line:
            src, dst_raw = line.split("→", 1)
        elif "->" in line:
            src, dst_raw = line.split("->", 1)
        elif "|" in line:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3:
                src, dst_raw = parts[0], parts[1]
                note = "|".join(parts[2:]).strip()
                pair = _norm_pair(src, dst_raw)
                if pair:
                    mapping[pair[0]] = pair[1]
                    if note:
                        notes[pair[0]] = note
                continue
            src, dst_raw = parts[0], parts[1] if len(parts) > 1 else ""
        elif "=" in line:
            src, dst_raw = line.split("=", 1)
        elif "\t" in line:
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) >= 3:
                src, dst_raw, note = parts[0], parts[1], "\t".join(parts[2:]).strip()
                pair = _norm_pair(src, dst_raw)
                if pair:
                    mapping[pair[0]] = pair[1]
                    if note:
                        notes[pair[0]] = note
                continue
            src, dst_raw = parts[0], parts[1] if len(parts) > 1 else ""
        else:
            continue
        dst, note = _split_dst_note(dst_raw)
        pair = _norm_pair(src, dst)
        if pair:
            mapping[pair[0]] = pair[1]
            if note:
                notes[pair[0]] = note
    return _freeze(mapping, notes)


def _freeze(
    mapping: Dict[str, str], notes: Optional[Dict[str, str]] = None
) -> Glossary:
    pairs = tuple(sorted(mapping.items(), key=lambda kv: (-len(kv[0]), kv[0])))
    note_items = tuple(
        sorted(
            ((s, n) for s, n in (notes or {}).items() if s and n),
            key=lambda kv: kv[0],
        )
    )
    return Glossary(pairs=pairs, notes=note_items)


def load_glossary(path: Path | str) -> Glossary:
    p = Path(path)
    if not p.is_file():
        return Glossary(pairs=())
    return parse_glossary_text(p.read_text(encoding="utf-8", errors="replace"))


def find_glossary_file(game_dir: Optional[Path | str]) -> Optional[Path]:
    if not game_dir:
        return None
    root = Path(game_dir)
    if not root.is_dir():
        return None
    for name in GLOSSARY_NAMES:
        p = root / name
        if p.is_file():
            return p
    return None


def load_glossary_for_game(game_dir: Optional[Path | str]) -> Tuple[Glossary, Optional[Path]]:
    path = find_glossary_file(game_dir)
    if not path:
        return Glossary(pairs=()), None
    return load_glossary(path), path


def segment_by_glossary(text: str, glossary: Glossary) -> List[Tuple[str, Optional[str]]]:
    """
    Split text into (fragment, fixed_dst_or_None).
    Fragments with fixed_dst must NOT be sent to the model; others are free text.
    """
    if not text or not glossary:
        return [(text or "", None)]
    spans: List[Tuple[int, int, str]] = []
    for src, dst in glossary.pairs:
        start = 0
        while True:
            i = text.find(src, start)
            if i < 0:
                break
            spans.append((i, i + len(src), dst))
            start = i + len(src)
    if not spans:
        return [(text, None)]
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    chosen: List[Tuple[int, int, str]] = []
    last_end = -1
    for a, b, dst in spans:
        if a < last_end:
            continue
        chosen.append((a, b, dst))
        last_end = b
    out: List[Tuple[str, Optional[str]]] = []
    cur = 0
    for a, b, dst in chosen:
        if a > cur:
            out.append((text[cur:a], None))
        out.append((text[a:b], dst))
        cur = b
    if cur < len(text):
        out.append((text[cur:], None))
    return out


def translate_with_glossary(
    texts: Sequence[str],
    glossary: Glossary,
    translate_fn,
) -> List[str]:
    """
    Hard consistency: glossary terms are never retranslated.
    translate_fn(List[str]) -> List[str] translates free fragments only.
    """
    if not texts:
        return []
    if not glossary:
        return list(translate_fn(list(texts)))

    plans = [segment_by_glossary(t or "", glossary) for t in texts]
    free_payload: List[str] = []
    free_index: List[Tuple[int, int]] = []
    for ti, segs in enumerate(plans):
        for si, (frag, fixed) in enumerate(segs):
            if fixed is None and frag:
                free_index.append((ti, si))
                free_payload.append(frag)

    translated_free: List[str] = []
    if free_payload:
        translated_free = list(translate_fn(free_payload))
        if len(translated_free) != len(free_payload):
            raise RuntimeError(
                f"glossary translate length mismatch: {len(translated_free)} != {len(free_payload)}"
            )

    free_iter = iter(translated_free)
    rebuilt: List[str] = []
    for segs in plans:
        parts: List[str] = []
        for frag, fixed in segs:
            if fixed is not None:
                parts.append(fixed)
            elif not frag:
                parts.append("")
            else:
                parts.append(next(free_iter))
        rebuilt.append("".join(parts))
    return rebuilt


def verify_glossary_consistency(
    originals: Sequence[str],
    translations: Sequence[str],
    glossary: Glossary,
) -> List[str]:
    """Return human-readable inconsistency notes (empty = ok)."""
    notes: List[str] = []
    if not glossary or len(originals) != len(translations):
        return notes
    for i, (src, dst) in enumerate(zip(originals, translations)):
        if not src or dst is None:
            continue
        for term_src, term_dst in glossary.pairs:
            if term_src not in src:
                continue
            need = src.count(term_src)
            got = dst.count(term_dst)
            if got < need:
                notes.append(
                    f"#{i + 1}: 「{term_src}」应译为「{term_dst}」"
                    f"（原文出现 {need} 次，译文仅 {got} 次）"
                )
    return notes


def enforce_glossary_consistency(
    originals: Sequence[str],
    translations: Sequence[str],
    glossary: Glossary,
) -> List[str]:
    """
    Best-effort post-pass: if SRC term appears in original but DST term is missing
    in translation, inject DST by replacing any wrong leftover SRC in the translation.
    """
    if not glossary or len(originals) != len(translations):
        return list(translations)
    out: List[str] = []
    for src, dst in zip(originals, translations):
        text = dst if dst is not None else ""
        if not src:
            out.append(text)
            continue
        for term_src, term_dst in glossary.pairs:
            if term_src not in src:
                continue
            if term_dst in text:
                continue
            if term_src in text:
                text = text.replace(term_src, term_dst)
        out.append(text)
    return out


def find_persona_rules_file(game_dir: Optional[Path | str]) -> Optional[Path]:
    if not game_dir:
        return None
    root = Path(game_dir)
    if not root.is_dir():
        return None
    p = root / PERSONA_RULES_NAME
    return p if p.is_file() else None


def _strip_hash_comments(text: str) -> str:
    lines = []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or s.startswith("//") or s.startswith(";"):
            continue
        lines.append(raw.rstrip())
    return "\n".join(lines).strip()


def ensure_persona_rules_file(game_dir: Path | str) -> Path:
    """Create editable default persona rules if missing."""
    root = Path(game_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / PERSONA_RULES_NAME
    if path.is_file():
        return path
    path.write_text(
        "# GalAutoTL 人设规则（改此文件即覆盖内置默认；删掉文件则恢复内置）\n"
        "# 以 # 开头的行为注释，不会送进模型。\n"
        "#\n"
        "# 角色专属备注请写在 GalAutoTL_glossary.txt，例如：\n"
        "#   あや=绫 ;; 女性，自称「我」，对主角用「你」，叙述用「她」\n"
        "#\n"
        f"{DEFAULT_PERSONA_RULES}",
        encoding="utf-8",
    )
    return path


def load_persona_rules(game_dir: Optional[Path | str]) -> str:
    """Return persona rules for the system prompt: user file if non-empty, else built-in."""
    if game_dir:
        root = Path(game_dir)
        if root.is_dir():
            try:
                ensure_persona_rules_file(root)
            except Exception:
                pass
            path = root / PERSONA_RULES_NAME
            if path.is_file():
                try:
                    body = _strip_hash_comments(
                        path.read_text(encoding="utf-8", errors="replace")
                    )
                    if body:
                        return body
                except Exception:
                    pass
    return DEFAULT_PERSONA_RULES.strip()


def build_glossary_prompt_block(
    glossary: Optional[Glossary],
    game_dir: Optional[Path | str] = None,
) -> str:
    """Default persona rules + optional character notes / term list."""
    parts = [load_persona_rules(game_dir)]
    if glossary:
        extra = glossary.as_prompt_block()
        if extra:
            parts.append(extra)
    return "\n".join(parts).strip()


def write_glossary_template(game_dir: Path | str) -> Path:
    """Create optional manual override file if none exists (auto glossary is primary)."""
    root = Path(game_dir)
    root.mkdir(parents=True, exist_ok=True)
    try:
        ensure_persona_rules_file(root)
    except Exception:
        pass
    existing = find_glossary_file(root)
    if existing:
        return existing
    path = root / "GalAutoTL_glossary.txt"
    path.write_text(
        "# GalAutoTL 手动术语表（原文=译文）\n"
        "# 不填也行：工具会生成 GalAutoTL_glossary_auto.txt；此处可覆盖某条。\n"
        "#\n"
        "# 全局人设规则默认在 GalAutoTL_persona.txt（可改）；此处写角色专属备注。\n"
        "# 基础：\n"
        "# あや=绫\n"
        "# ひなたちゃん=日向酱\n"
        "#\n"
        "# 角色备注（可选，覆盖/补充默认人设规则）：\n"
        "# あや=绫 ;; 女性，自称「我」，对主角用「你」，叙述用「她」\n"
        "# または: あや|绫|女性，自称「我」，对主角用「你」\n"
        "#\n"
        "# 建议：角色名/昵称分条写；专名前后一致；短选项里的名字也要进表。\n"
        "#\n",
        encoding="utf-8",
    )
    return path


def merge_glossaries(*glosses: Glossary) -> Glossary:
    """Later glossaries override earlier ones on the same SRC (pairs and notes)."""
    mapping: Dict[str, str] = {}
    notes: Dict[str, str] = {}
    for g in glosses:
        if not g:
            continue
        for src, dst in g.pairs:
            mapping[src] = dst
        for src, note in g.notes:
            if note:
                notes[src] = note
    return _freeze(mapping, notes)


def save_glossary(path: Path | str, glossary: Glossary, header: str = "") -> Path:
    p = Path(path)
    lines = []
    if header:
        for h in header.strip().splitlines():
            lines.append(h if h.startswith("#") else f"# {h}")
    note_map = {s: n for s, n in glossary.notes}
    for src, dst in sorted(glossary.pairs, key=lambda kv: (-len(kv[0]), kv[0])):
        note = (note_map.get(src) or "").strip()
        if note:
            lines.append(f"{src}={dst} ;; {note}")
        else:
            lines.append(f"{src}={dst}")
    p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return p


def is_plausible_proper_noun(name: str) -> bool:
    name = (name or "").strip()
    if not name or len(name) < 2 or len(name) > 16:
        return False
    if name in _STOP_NAMES:
        return False
    if _SKIP_DST.match(name):
        return False
    if not _HAS_WORD.search(name):
        return False
    # pure ascii short ids
    if name.isascii() and (len(name) <= 3 or name.islower() and len(name) < 5):
        return False
    # require some kana or kanji for JP titles
    if not (_HAS_KANA.search(name) or _HAS_KANJI.search(name)):
        if not (name[:1].isupper() and name.isascii() and 4 <= len(name) <= 20):
            return False
    # reject lines that look like sentences (punctuation / too many particles)
    if any(ch in name for ch in "。！？!?、，,.;；：:"):
        return False
    return True


def harvest_name_candidates(texts: Iterable[str], limit: int = 120) -> List[str]:
    """Collect likely character-name strings; attr hits weighted higher."""
    score: Dict[str, int] = {}
    for t in texts:
        if not t:
            continue
        for rx, w in ((_NAME_ATTR_RE, 8), (_KAG_NAME_RE, 8)):
            for m in rx.finditer(t):
                name = m.group(1).strip()
                if not is_plausible_proper_noun(name):
                    continue
                score[name] = score.get(name, 0) + w
        for m in re.finditer(r'["\']([\u3040-\u30ff\u4e00-\u9fffA-Za-z]{2,12})["\']', t):
            name = m.group(1).strip()
            if not is_plausible_proper_noun(name):
                continue
            score[name] = score.get(name, 0) + 1
        # bare repeated kanji/kana tokens 2–8 (weak signal)
        for m in re.finditer(r"([\u4e00-\u9fff]{2,6}|[\u30a0-\u30ff]{3,10})", t):
            name = m.group(1)
            if not is_plausible_proper_noun(name):
                continue
            score[name] = score.get(name, 0) + 1

    # keep attr-strong or repeatedly seen names
    kept = [(n, s) for n, s in score.items() if s >= 2 or s >= 8]
    # if too few, relax to score>=2 already; if still few take top by score
    if len(kept) < 8:
        kept = list(score.items())
    ranked = sorted(kept, key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
    out: List[str] = []
    for n, _ in ranked:
        # drop if shorter name is only a suffix of longer kept name later — keep both;
        # longest-first match handles it
        out.append(n)
        if len(out) >= limit:
            break
    return out


def write_candidates_file(game_dir: Path | str, names: Sequence[str]) -> Optional[Path]:
    if not names:
        return None
    root = Path(game_dir)
    path = root / CANDIDATE_NAME
    lines = [
        "# 自动扫描到的疑似专名（仅供查看）",
        "# 实际生效表见 GalAutoTL_glossary_auto.txt；手动覆盖用 GalAutoTL_glossary.txt",
        "#",
    ]
    for n in names:
        lines.append(f"# {n}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def find_auto_glossary_file(game_dir: Optional[Path | str]) -> Optional[Path]:
    if not game_dir:
        return None
    p = Path(game_dir) / AUTO_GLOSSARY_NAME
    return p if p.is_file() else None


def load_auto_glossary(game_dir: Optional[Path | str]) -> Glossary:
    path = find_auto_glossary_file(game_dir)
    if not path:
        return Glossary(pairs=())
    return load_glossary(path)


def glossary_from_mapping(mapping: Dict[str, str]) -> Glossary:
    clean: Dict[str, str] = {}
    for k, v in mapping.items():
        pair = _norm_pair(k, v)
        if pair:
            clean[pair[0]] = pair[1]
    return _freeze(clean)


def mask_glossary_terms(text: str, glossary: Glossary) -> Tuple[str, List[str]]:
    """Replace glossary SRC with ⟦GALTL_A⟧-style placeholders (longest-first)."""
    if not text or not glossary:
        return text or "", []
    spans: List[Tuple[int, int, str]] = []
    for src, dst in glossary.pairs:
        if not src or src == dst:
            continue
        start = 0
        while True:
            i = text.find(src, start)
            if i < 0:
                break
            spans.append((i, i + len(src), src))
            start = i + len(src)
    if not spans:
        return text, []
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    chosen: List[Tuple[int, int, str]] = []
    last_end = -1
    for a, b, src in spans:
        if a < last_end:
            continue
        chosen.append((a, b, src))
        last_end = b
    parts: List[str] = []
    keys: List[str] = []
    cur = 0
    for a, b, src in chosen:
        if a > cur:
            parts.append(text[cur:a])
        parts.append(placeholder_token(len(keys)))
        keys.append(src)
        cur = b
    if cur < len(text):
        parts.append(text[cur:])
    return "".join(parts), keys


def has_glossary_leak(text: str) -> bool:
    """True if CN still has placeholder debris or classic 0-corruption."""
    if not text:
        return False
    if _GALTL_NEW.search(text) or _GALTL_LEGACY.search(text):
        return True
    if _GALTL_BARE.search(text):
        return True
    if "⟦GALTL" in text or "{{GALTL" in text or "GALTL" in text:
        return True
    if _CORRUPT_ZERO.search(text):
        return True
    return False


def scrub_glossary_artifacts(
    text: str,
    *,
    src: str = "",
    glossary: Optional[Glossary] = None,
    keys: Sequence[str] = (),
) -> str:
    """Strip leftover placeholders and repair common 0-collapse corruptions."""
    if not text:
        return ""
    s = text
    # Remove any remaining placeholder forms (already unmasked or mangled)
    s = _GALTL_NEW.sub("", s)
    s = _GALTL_NEW_BROKEN.sub("", s)
    s = _GALTL_LEGACY.sub("", s)
    s = _GALTL_LEGACY_BROKEN.sub("", s)
    s = _GALTL_BARE.sub("", s)
    s = s.replace("⟦", "").replace("⟧", "")
    s = s.replace("{{", "").replace("}}", "")

    dst_map = {a: b for a, b in glossary.pairs} if glossary else {}
    # Prefer keys order when repairing
    terms: List[Tuple[str, str]] = []
    if keys:
        for k in keys:
            terms.append((k, dst_map.get(k, k)))
    if glossary:
        for a, b in glossary.pairs:
            if a not in {t[0] for t in terms}:
                terms.append((a, b))

    jp = src or ""
    # Digit-collapse repairs (guided by JP / glossary SRC)
    if "千夏" in jp or any(t[0] == "千夏" for t in terms):
        s = s.replace("0夏小姐", "千夏小姐").replace("0夏", "千夏")
    if "二人" in jp or any(t[0] == "二人" for t in terms):
        s = s.replace("0个人", "两个人")
    elif "一人" in jp or "私しか" in jp or any(t[0] == "一人" for t in terms):
        s = s.replace("0个人", "一个人")
    if "一声" in jp or "と声を" in jp:
        s = s.replace("发出0声", "发出一声").replace("0声", "一声")
    if "別" in jp or "他の" in jp or "另" in s:
        s = s.replace("另0个", "另一个").replace("另0人", "另一个人")
    s = s.replace("这样0个", "这样一种")
    s = s.replace("0件需要", "一件需要")
    s = s.replace("但0想", "但一想")

    # Generic: if a glossary DST was supposed to appear, fix remaining 0+suffix
    for term_src, term_dst in terms:
        if not term_src or (jp and term_src not in jp):
            continue
        if not term_dst:
            continue
        if term_dst.endswith("个人") and "0个人" in s:
            s = s.replace("0个人", term_dst)
        if "夏" in term_src and "0夏" in s:
            s = s.replace("0夏" + term_dst[term_dst.find("夏") + 1 :], term_dst)
            s = s.replace("0夏", term_src)

    return s.strip() or text


def unmask_glossary_terms(text: str, glossary: Glossary, keys: Sequence[str]) -> str:
    """Restore placeholders to glossary DST (fallback: original SRC). Scrub leftovers."""
    if not text:
        return ""
    dst_map = {src: dst for src, dst in glossary.pairs} if keys else {}

    def repl_letter(m: re.Match) -> str:
        raw = (m.group(1) or "").upper()
        if not raw or not keys:
            return ""
        idx = _code_to_idx(raw)
        if 0 <= idx < len(keys):
            src = keys[idx]
            return dst_map.get(src, src)
        return ""

    def repl_digit(m: re.Match) -> str:
        raw = m.group(1)
        if raw == "" or not keys:
            return ""
        idx = int(raw)
        if 0 <= idx < len(keys):
            src = keys[idx]
            return dst_map.get(src, src)
        return ""

    # Prefer well-formed tokens, then broken / legacy forms
    out = _GALTL_NEW.sub(repl_letter, text)
    out = _GALTL_NEW_BROKEN.sub(repl_letter, out)
    out = _GALTL_LEGACY.sub(repl_digit, out)
    out = _GALTL_LEGACY_BROKEN.sub(repl_digit, out)
    out = _GALTL_BARE.sub(
        lambda m: repl_letter(m)
        if (m.group(1) or "").isalpha()
        else repl_digit(m),
        out,
    )
    return out


# Back-compat alias used by older call sites / comments
_GALTL_LOOSE = _GALTL_LEGACY
_GALTL_BROKEN = _GALTL_LEGACY_BROKEN
