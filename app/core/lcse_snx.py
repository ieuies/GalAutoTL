# -*- coding: utf-8 -*-
"""LC-ScriptEngine SNX script parse / rewrite.

Ported from cqjjjzr/LCSELocalizationTools LCSESNXUtility (Kotlin).
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union

INSTRUCTION_LENGTH = 12  # 3 * int32

STRING_REF_INST = 0x00000011
STRING_REF_PARAM1 = 0x00000002
DISPLAY_TEXT_INST = 0x0000000D
DISPLAY_TEXT_PARAM1 = 0x0000002C
DISPLAY_TEXT_PARAM2 = 0x00000000
CHOICE_INST = 0x0000000D
CHOICE_PARAM1 = 0x0000004F
CHOICE_PARAM2 = 0x00000000


@dataclass
class RawInstruction:
    instruction: int
    param1: int
    param2: int


@dataclass
class RawString:
    ordinal: int
    offset: int
    content: bytes


@dataclass
class RawScript:
    instructions: List[RawInstruction] = field(default_factory=list)
    strings: List[RawString] = field(default_factory=list)


@dataclass
class Speaker:
    ordinal: int
    name: str


@dataclass
class DialogString:
    ordinal: int
    speaker_ordinal: int
    content: str
    with_trailer: bool = False
    raw_content: Optional[bytes] = None


@dataclass
class ChoiceString:
    ordinal: int
    content: str
    raw_content: Optional[bytes] = None


@dataclass
class SystemString:
    ordinal: int
    content: str
    raw_content: Optional[bytes] = None


StringItem = Union[DialogString, ChoiceString, SystemString]


@dataclass
class ParsedScript:
    strings: List[StringItem] = field(default_factory=list)
    # parallel raw instruction list kept for rebuild
    raw_instructions: List[RawInstruction] = field(default_factory=list)
    speakers: List[Speaker] = field(default_factory=list)
    # map: instruction index -> referenced string ordinal for STRING_REF
    string_ref_at: List[Optional[int]] = field(default_factory=list)
    # original string table bytes by ordinal (for untranslated keep)
    raw_by_ordinal: dict = field(default_factory=dict)


def try_parse_snx(data: bytes) -> bool:
    try:
        read_raw_snx(data)
        return True
    except Exception:
        return False


def read_raw_snx(data: bytes) -> RawScript:
    if len(data) < 8:
        raise ValueError("SNX too small")
    inst_count, str_table_len = struct.unpack_from("<II", data, 0)
    expect = 8 + inst_count * INSTRUCTION_LENGTH + str_table_len
    if len(data) != expect:
        raise ValueError(f"SNX size mismatch: {len(data)} != {expect}")
    instructions: List[RawInstruction] = []
    pos = 8
    for _ in range(inst_count):
        a, b, c = struct.unpack_from("<III", data, pos)
        instructions.append(RawInstruction(a, b, c))
        pos += INSTRUCTION_LENGTH
    table_start = pos
    strings: List[RawString] = []
    ordinal = 0
    while pos + 4 <= len(data):
        # offset = start of length field relative to string table
        offset = pos - table_start
        length = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if pos + length > len(data):
            raise ValueError("string table overrun")
        content = data[pos : pos + length]
        pos += length
        strings.append(RawString(ordinal, offset, content))
        ordinal += 1
    if pos != len(data):
        raise ValueError("trailing bytes in string table")
    return RawScript(instructions, strings)


def write_raw_snx(script: RawScript) -> bytes:
    str_sorted = sorted(script.strings, key=lambda s: s.offset)
    table_len = sum(4 + len(s.content) for s in str_sorted)
    out = bytearray()
    out += struct.pack("<II", len(script.instructions), table_len)
    for ins in script.instructions:
        out += struct.pack("<III", ins.instruction, ins.param1, ins.param2)
    for s in str_sorted:
        out += struct.pack("<I", len(s.content))
        out += s.content
    return bytes(out)


def _bytes_to_text(arr: bytes) -> str:
    """Decode SNX string bytes: original JP is CP932; rewritten CN uses GBK."""
    end = arr.find(b"\x00")
    if end < 0:
        end = len(arr)
    raw = arr[:end]
    if not raw:
        return ""

    def score(s: str) -> int:
        bad = s.count("\ufffd")
        good = sum(
            1
            for ch in s
            if ("\u3040" <= ch <= "\u30ff")
            or ("\u4e00" <= ch <= "\u9fff")
            or ("\uac00" <= ch <= "\ud7af")
        )
        return good - bad * 8

    best = ""
    best_score = -10**9
    for enc in ("cp932", "gbk"):
        s = raw.decode(enc, errors="replace")
        sc = score(s)
        if sc > best_score:
            best_score = sc
            best = s
    return best


def parse_script(raw: RawScript) -> ParsedScript:
    dialog_ords: List[int] = []
    choice_ords: List[int] = []
    string_ref_at: List[Optional[int]] = []
    stack: List[int] = []

    offset_to_ord = {s.offset: s.ordinal for s in raw.strings}

    for ins in raw.instructions:
        ref_ord: Optional[int] = None
        if ins.instruction == STRING_REF_INST and ins.param1 == STRING_REF_PARAM1:
            ref_ord = offset_to_ord.get(ins.param2)
            if ref_ord is not None:
                stack.append(ref_ord)
        elif (
            ins.instruction == DISPLAY_TEXT_INST
            and ins.param1 == DISPLAY_TEXT_PARAM1
            and ins.param2 == DISPLAY_TEXT_PARAM2
        ):
            if stack:
                dialog_ords.append(stack.pop())
        elif (
            ins.instruction == CHOICE_INST
            and ins.param1 == CHOICE_PARAM1
            and ins.param2 == CHOICE_PARAM2
        ):
            if len(stack) >= 2:
                str2 = stack.pop()
                str1 = stack.pop()
                choice_ords.extend([str1, str2])
        string_ref_at.append(ref_ord)

    parsed = ParsedScript(
        raw_instructions=list(raw.instructions),
        string_ref_at=string_ref_at,
        raw_by_ordinal={s.ordinal: s.content for s in raw.strings},
    )
    dialog_set = set(dialog_ords)
    choice_set = set(choice_ords)

    for s in raw.strings:
        text = _bytes_to_text(s.content)
        if s.ordinal in dialog_set:
            parsed.strings.append(_parse_dialog(text, parsed, s.ordinal, s.content))
        elif s.ordinal in choice_set:
            parsed.strings.append(ChoiceString(s.ordinal, text, raw_content=s.content))
        else:
            parsed.strings.append(SystemString(s.ordinal, text, raw_content=s.content))
    return parsed


def _parse_dialog(
    text: str, context: ParsedScript, ordinal: int, raw_content: bytes
) -> StringItem:
    with_trailer = text.endswith("\u0002\u0003")
    body = text.replace("\u0001", "\n")
    if with_trailer:
        body = body[: -len("\u0002\u0003")]
    parts = body.split("\n", 1)
    if len(parts) < 2:
        return SystemString(ordinal, parts[0] if parts else "", raw_content=raw_content)
    speaker_name, content = parts[0], parts[1]
    sp = next((x for x in context.speakers if x.name == speaker_name), None)
    if sp is None:
        sp = Speaker(len(context.speakers), speaker_name)
        context.speakers.append(sp)
    return DialogString(
        ordinal, sp.ordinal, content, with_trailer, raw_content=raw_content
    )


def collect_translatable(parsed: ParsedScript) -> List[Tuple[str, str, int]]:
    """Return list of (kind, text, string_ordinal). kind: speaker|dialog|choice|system"""
    items: List[Tuple[str, str, int]] = []
    for sp in parsed.speakers:
        if sp.name.strip():
            items.append(("speaker", sp.name, sp.ordinal))
    for s in parsed.strings:
        if isinstance(s, DialogString) and s.content.strip():
            items.append(("dialog", s.content, s.ordinal))
        elif isinstance(s, ChoiceString) and s.content.strip():
            items.append(("choice", s.content, s.ordinal))
        elif isinstance(s, SystemString) and _is_system_ui(s.content):
            items.append(("system", s.content.strip(), s.ordinal))
    return items


_SYS_PATH = re.compile(r"[\\/]|\.(?:snx|png|ogg|wav|bmp|jpg|jpeg|tga|mp3|avi)$", re.I)
_SYS_IDENT = re.compile(r"^[A-Za-z0-9_./\-:%]+$")
_SYS_KANA = re.compile(r"[\u3040-\u30ff]")


def _is_system_ui(text: str) -> bool:
    """True for JP-looking system/UI labels; skip paths and code identifiers."""
    from app.core.pipeline_harden import looks_untranslated

    t = (text or "").strip()
    if not t or len(t) > 96:
        return False
    if "\x00" in t or "\x01" in t:
        return False
    if _SYS_PATH.search(t):
        return False
    if _SYS_IDENT.fullmatch(t):
        return False
    if looks_untranslated(t):
        return True
    # Short kanji/kana UI without punctuation (設定, 戻る)
    if 1 < len(t) <= 24 and _SYS_KANA.search(t):
        return True
    return False


def apply_translations(
    parsed: ParsedScript,
    updates: List[Tuple[str, int, str]],
) -> None:
    """updates: (kind, ordinal, new_text)"""
    speaker_map = {o: t for k, o, t in updates if k == "speaker"}
    dialog_map = {o: t for k, o, t in updates if k == "dialog"}
    choice_map = {o: t for k, o, t in updates if k == "choice"}
    system_map = {o: t for k, o, t in updates if k == "system"}

    dirty_speakers = set(speaker_map.keys())
    for sp in parsed.speakers:
        if sp.ordinal in speaker_map:
            sp.name = speaker_map[sp.ordinal]

    new_strings: List[StringItem] = []
    for s in parsed.strings:
        if isinstance(s, DialogString) and s.ordinal in dialog_map:
            new_strings.append(
                DialogString(
                    s.ordinal,
                    s.speaker_ordinal,
                    dialog_map[s.ordinal],
                    s.with_trailer,
                    raw_content=None,  # force rebuild
                )
            )
        elif isinstance(s, ChoiceString) and s.ordinal in choice_map:
            new_strings.append(ChoiceString(s.ordinal, choice_map[s.ordinal], raw_content=None))
        elif isinstance(s, SystemString) and s.ordinal in system_map:
            new_strings.append(
                SystemString(s.ordinal, system_map[s.ordinal], raw_content=None)
            )
        elif isinstance(s, DialogString) and s.speaker_ordinal in dirty_speakers:
            # speaker name changed → rebuild dialog blob
            new_strings.append(
                DialogString(
                    s.ordinal,
                    s.speaker_ordinal,
                    s.content,
                    s.with_trailer,
                    raw_content=None,
                )
            )
        else:
            new_strings.append(s)
    parsed.strings = new_strings


def _encode_string(text: str, encoding: str) -> bytes:
    return text.encode(encoding, errors="replace")


def string_to_bytes(item: StringItem, parsed: ParsedScript, encoding: str) -> bytes:
    # Prefer untouched original bytes
    if getattr(item, "raw_content", None) is not None:
        return item.raw_content  # type: ignore
    if isinstance(item, DialogString):
        speaker = next(s for s in parsed.speakers if s.ordinal == item.speaker_ordinal)
        body = speaker.name + "\u0001" + item.content.replace("\n", "\u0001")
        if item.with_trailer:
            body += "\u0002\u0003"
        body += "\u0000"
        return _encode_string(body, encoding)
    # choice / system
    return _encode_string(item.content + "\u0000", encoding)


def build_raw_from_parsed(parsed: ParsedScript, encoding: str = "gbk") -> RawScript:
    """Rebuild raw SNX; update STRING_REF offsets to new string table."""
    # Keep strings in original ordinal order
    ordered = sorted(parsed.strings, key=lambda s: s.ordinal)
    raw_strings: List[RawString] = []
    ptr = 0
    for item in ordered:
        content = string_to_bytes(item, parsed, encoding)
        raw_strings.append(RawString(item.ordinal, ptr, content))
        ptr += 4 + len(content)

    ord_to_offset = {s.ordinal: s.offset for s in raw_strings}
    instructions: List[RawInstruction] = []
    for i, ins in enumerate(parsed.raw_instructions):
        ref = parsed.string_ref_at[i] if i < len(parsed.string_ref_at) else None
        if (
            ins.instruction == STRING_REF_INST
            and ins.param1 == STRING_REF_PARAM1
            and ref is not None
            and ref in ord_to_offset
        ):
            instructions.append(
                RawInstruction(STRING_REF_INST, STRING_REF_PARAM1, ord_to_offset[ref])
            )
        else:
            instructions.append(ins)

    return RawScript(instructions, raw_strings)


def extract_snx_units(path) -> Tuple[ParsedScript, List[Tuple[str, str, int]]]:
    data = Path_read(path)
    raw = read_raw_snx(data)
    parsed = parse_script(raw)
    return parsed, collect_translatable(parsed)


def Path_read(path) -> bytes:
    from pathlib import Path

    return Path(path).read_bytes()


def rewrite_snx_file(path, parsed: ParsedScript, encoding: str = "gbk") -> None:
    from pathlib import Path

    raw = build_raw_from_parsed(parsed, encoding=encoding)
    Path(path).write_bytes(write_raw_snx(raw))
