# -*- coding: utf-8 -*-
"""Classic SoftPal SCRIPT.SRC + TEXT.DAT extract / rebuild.

Logic aligned with community SoftPal-Tool (luoyily) / SoftPal ADV tutorials:
parse dialog opcodes in SCRIPT.SRC, strings live in TEXT.DAT; rebuild can
lengthen lines (engine ~128-byte draw limit still applies in-game).
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass
class ScriptRef:
    offset: int  # position of instruction block in SCRIPT.SRC
    text_offset: int
    name_offset: Optional[int]
    kind: str  # "show" | "select"
    size: int  # bytecode block size


@dataclass
class TextEntry:
    offset: int
    index: bytes  # 4 bytes
    text: str
    raw_body: bytes  # index+text without trailing NUL of entry stream


def _decode_payload(payload: bytes) -> str:
    """Pick cp932/gbk/utf-8 by script heuristic (avoid GBK↔CP932 mojibake)."""
    import re

    best = None
    best_score = -10**9
    for enc in ("cp932", "gbk", "utf-8"):
        try:
            s = payload.decode(enc)
        except UnicodeDecodeError:
            continue
        score = 0
        score += 4 * len(re.findall(r"[\u3040-\u309f\u30a0-\u30ff]", s))
        score += 2 * len(re.findall(r"[\u4e00-\u9fff]", s))
        score -= 3 * len(re.findall(r"[\uff61-\uff9f]", s))
        score -= 8 * s.count("\ufffd")
        if score > best_score:
            best_score = score
            best = s
    return best if best is not None else payload.decode("cp932", errors="replace")


class SoftPalScriptBundle:
    def __init__(self, script: bytes, text: bytes):
        self.script = bytearray(script)
        self.text_raw = bytes(text)
        self.texts: List[TextEntry] = []
        self.offset_to_id: Dict[int, int] = {}
        self.refs: List[ScriptRef] = []
        # Orphan TEXT.DAT rows (no show/select ref) get their CN here instead of
        # overwriting self.texts[].text — that way the JP source is preserved and
        # a second collect_units()/apply_translations() never re-translates CN.
        self._orphan_overrides: Dict[int, str] = {}
        self._parse_text()
        self._parse_script()

    def _parse_text(self) -> None:
        data = self.text_raw
        offset = 16
        i = 0
        while offset < len(data):
            end = data.find(b"\x00", offset + 4)
            if end < 0:
                chunk = data[offset:]
                if len(chunk) < 4:
                    break
                body = chunk
                next_off = len(data)
            else:
                body = data[offset:end]
                next_off = end + 1
            if len(body) < 4:
                break
            idx, payload = body[:4], body[4:]
            s = _decode_payload(payload)
            self.offset_to_id[offset] = i
            self.texts.append(TextEntry(offset=offset, index=idx, text=s, raw_body=body))
            i += 1
            offset = next_off

    def _parse_script(self) -> None:
        content = bytes(self.script)
        dialog_lo = {
            b"\x02\x00",
            b"\x0f\x00",
            b"\x10\x00",
            b"\x11\x00",
            b"\x12\x00",
            b"\x13\x00",
            b"\x14\x00",
        }
        for i in range(0, len(content) - 4, 4):
            if content[i : i + 4] != b"\x17\x00\x01\x00":
                continue
            after_lo = content[i + 4 : i + 6]
            after_hi = content[i + 6 : i + 8]
            if after_hi == b"\x02\x00" and after_lo in dialog_lo:
                # PalScriptTextShow: 32 bytes ending at i+8 → start i-24
                start = i - 24
                if start < 0:
                    continue
                block = content[start : i + 8]
                if len(block) < 32:
                    continue
                text_off = struct.unpack_from("<I", block, 4)[0]
                name_off = struct.unpack_from("<I", block, 12)[0]
                has_name = name_off != 0x0FFFFFFF
                if text_off not in self.offset_to_id:
                    continue
                if has_name and name_off not in self.offset_to_id:
                    has_name = False
                self.refs.append(
                    ScriptRef(
                        offset=start,
                        text_offset=text_off,
                        name_offset=name_off if has_name else None,
                        kind="show",
                        size=32,
                    )
                )
            elif after_hi == b"\x06\x00" and after_lo == b"\x02\x00":
                start = i - 8
                if start < 0:
                    continue
                block = content[start : i + 8]
                if len(block) < 16:
                    continue
                text_off = struct.unpack_from("<I", block, 4)[0]
                if text_off not in self.offset_to_id:
                    continue
                self.refs.append(
                    ScriptRef(
                        offset=start,
                        text_offset=text_off,
                        name_offset=None,
                        kind="select",
                        size=16,
                    )
                )

    def collect_units(self) -> List[str]:
        """Unique JP strings: dialog/select refs first, then orphan TEXT.DAT UI."""
        from app.core.pipeline_harden import looks_untranslated

        out: List[str] = []
        seen = set()
        referenced: set = set()
        for ref in self.refs:
            for off in (ref.name_offset, ref.text_offset):
                if off is None:
                    continue
                referenced.add(off)
                tid = self.offset_to_id.get(off)
                if tid is None:
                    continue
                s = self.texts[tid].text
                if not s or s in seen:
                    continue
                seen.add(s)
                out.append(s)
        for t in self.texts:
            if t.offset in referenced:
                continue
            s = t.text
            if not s or s in seen:
                continue
            if not looks_untranslated(s):
                continue
            seen.add(s)
            out.append(s)
        return out

    def export_json_objs(self) -> List[dict]:
        objs: List[dict] = []
        for ref in self.refs:
            tid = self.offset_to_id[ref.text_offset]
            text = self.texts[tid].text
            item = {
                "Text": {
                    "Original": text,
                    "Translate": text,
                    "TextOffset": ref.text_offset,
                },
                "Name": None,
                "ScriptOffset": ref.offset,
                "Kind": ref.kind,
            }
            if ref.name_offset is not None:
                nid = self.offset_to_id[ref.name_offset]
                name = self.texts[nid].text
                item["Name"] = {
                    "Original": name,
                    "Translate": name,
                    "TextOffset": ref.name_offset,
                }
            objs.append(item)
        return objs

    def apply_translations(self, mapping: Dict[str, str]) -> int:
        """Fill Translate fields from JP→CN map; returns changed count."""
        from app.core.pipeline_harden import looks_already_chinese

        n = 0
        objs = self.export_json_objs()
        for item in objs:
            o = item["Text"]["Original"]
            if (
                o in mapping
                and mapping[o]
                and mapping[o] != o
                and not looks_already_chinese(o)
            ):
                item["Text"]["Translate"] = mapping[o]
                n += 1
            if item["Name"]:
                o = item["Name"]["Original"]
                if (
                    o in mapping
                    and mapping[o]
                    and mapping[o] != o
                    and not looks_already_chinese(o)
                ):
                    item["Name"]["Translate"] = mapping[o]
                    n += 1
        # Orphan TEXT.DAT rows (no show/select ref): record CN in _orphan_overrides
        # so rebuild can emit them WITHOUT clobbering the JP source text (a second
        # collect_units() must keep returning JP, otherwise 二扫 re-translates CN).
        referenced = set()
        for ref in self.refs:
            referenced.add(ref.text_offset)
            if ref.name_offset is not None:
                referenced.add(ref.name_offset)
        for t in self.texts:
            if t.offset in referenced:
                continue
            dst = mapping.get(t.text)
            if dst and dst != t.text and not looks_already_chinese(t.text):
                self._orphan_overrides[t.offset] = dst
                n += 1
        self._pending_json = objs
        return n

    def rebuild(
        self,
        out_script: Path,
        out_text: Path,
        *,
        encoding: str = "gbk",
        json_objs: Optional[List[dict]] = None,
    ) -> None:
        objs = json_objs if json_objs is not None else getattr(self, "_pending_json", None)
        if objs is None:
            objs = self.export_json_objs()

        # SoftPal-Tool style: convert all to target encoding, append modified
        # copies, retarget SCRIPT pointers to new_offset.
        texts = [
            TextEntry(t.offset, t.index, t.text, t.raw_body) for t in self.texts
        ]
        # Apply orphan overrides for writeback (JP source stays intact in texts).
        for off, cn in self._orphan_overrides.items():
            tid = self.offset_to_id.get(off)
            if tid is not None:
                texts[tid].text = cn
        offset_id = dict(self.offset_to_id)
        modified_ids: List[int] = []

        def _encode_entry(index: bytes, s: str) -> bytes:
            body = s.encode(encoding, errors="replace").replace(b"?", b"??")
            # SoftPal-Tool strips some draw-unsupported pairs
            body = body.replace(b"\xa1\xa1", b"")
            return index + body + b"\x00"

        # mark modifications from json
        for item in objs:
            toff = int(item["Text"]["TextOffset"])
            tid = offset_id[toff]
            new_t = str(item["Text"].get("Translate") or item["Text"]["Original"])
            if new_t != texts[tid].text:
                texts[tid].text = new_t
                modified_ids.append(tid)
            if item.get("Name"):
                noff = int(item["Name"]["TextOffset"])
                nid = offset_id[noff]
                new_n = str(item["Name"].get("Translate") or item["Name"]["Original"])
                if new_n != texts[nid].text:
                    texts[nid].text = new_n
                    modified_ids.append(nid)

        # rebuild TEXT.DAT: clear encrypt flag like SoftPal-Tool (byte0 = 0)
        header = bytearray(self.text_raw[:16])
        if header:
            header[0] = 0
        new_text = bytearray(header)
        new_offsets: Dict[int, int] = {}
        cursor = 16
        for i, t in enumerate(texts):
            blob = _encode_entry(t.index, t.text)
            new_offsets[t.offset] = cursor
            new_text += blob
            cursor += len(blob)

        # append duplicates for modified (SoftPal-Tool keeps old slot + appends)
        for tid in modified_ids:
            t = texts[tid]
            blob = _encode_entry(t.index, t.text)
            new_offsets[t.offset] = cursor
            new_text += blob
            cursor += len(blob)

        # patch SCRIPT pointers
        script = bytearray(self.script)
        for item in objs:
            soff = int(item["ScriptOffset"])
            # find matching ref for size/kind
            ref = next((r for r in self.refs if r.offset == soff), None)
            if not ref:
                continue
            new_text_off = new_offsets[int(item["Text"]["TextOffset"])]
            if ref.kind == "show":
                struct.pack_into("<I", script, soff + 4, new_text_off)
                if item.get("Name") and ref.name_offset is not None:
                    new_name_off = new_offsets[int(item["Name"]["TextOffset"])]
                    struct.pack_into("<I", script, soff + 12, new_name_off)
            else:
                struct.pack_into("<I", script, soff + 4, new_text_off)

        out_script.parent.mkdir(parents=True, exist_ok=True)
        out_text.parent.mkdir(parents=True, exist_ok=True)
        out_script.write_bytes(bytes(script))
        out_text.write_bytes(bytes(new_text))


def load_bundle(script_path: Path, text_path: Path) -> SoftPalScriptBundle:
    return SoftPalScriptBundle(script_path.read_bytes(), text_path.read_bytes())


def save_export_json(bundle: SoftPalScriptBundle, path: Path) -> Path:
    path.write_text(
        json.dumps(bundle.export_json_objs(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
