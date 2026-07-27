# -*- coding: utf-8 -*-
"""Translate plain text packs: txt / json / csv."""
from __future__ import annotations

import csv
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from app.config import AppConfig, cache_db_path
from app.core.api_client import OpenAICompatClient
from app.core.pipeline_harden import (
    CODEC_CP932,
    CODEC_UNICODE,
    mapping_aligned,
    remain_filter_set,
    run_second_pass,
    second_pass_sources,
    translate_to_mapping,
    write_remainder_report,
)
from app.core.translate import TranslateCache

LogFn = Optional[Callable[[str], None]]
ProgressFn = Optional[Callable[[int, int], None]]
CancelFn = Optional[Callable[[], bool]]

SKIP_LINE_PREFIXES = ("//", "#", ";", "@", "*", "\\", "{", "}")
LINE_TAG_RE = re.compile(r"^(<\d+>\s*)(.*)$")
# Any script that may need translating into Chinese
SRC_HINT = re.compile(
    r"["
    r"\u3040-\u30ff"  # JP kana
    r"\u4e00-\u9fff"  # CJK (JP/ZH/etc.)
    r"\uac00-\ud7af"  # Korean Hangul
    r"\u0400-\u04ff"  # Cyrillic
    r"A-Za-z"  # Latin (English etc.)
    r"]"
)
# Mostly already Chinese (han + punct, little kana/latin)
ALREADY_CN = re.compile(r"^[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\s\d，。！？、；：…—·「」『』（）【】《》\.\!\?\-~,\"']+$")
HAS_KANA = re.compile(r"[\u3040-\u30ff]")
HAS_HANGUL = re.compile(r"[\uac00-\ud7af]")
HAS_CYRILLIC = re.compile(r"[\u0400-\u04ff]")
HAS_LATIN = re.compile(r"[A-Za-z]{2,}")

JSON_TEXT_KEYS = ("message", "text", "Text", "Message", "msg", "dialogue", "Dialog")

# Unity / engine identity files — translating these breaks boot (reflection names).
SKIP_FILE_NAMES = {
    "runtimeinitializeonloads.json",
    "scriptingassemblies.json",
    "boot.config",
    "globalgamemanagers",
    "unity default resources",
}
SKIP_JSON_KEYS = {
    "classname",
    "methodname",
    "assemblyname",
    "namespacename",
    "fullnamepath",
    "typename",
    "fullnameid",
}
# CamelCase identifiers / dotted type names (e.g. Config.Initialize)
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")
CAMEL_RE = re.compile(r"^[A-Z][A-Za-z0-9]+$")


@dataclass
class WorkItem:
    kind: str  # txt_line | json_field | csv_cell
    path: Path
    meta: Any
    source: str


def _backup_root(game_dir: Path) -> Path:
    # Desktop/自动翻译备份/<name>
    desktop = Path.home() / "Desktop"
    return desktop / "自动翻译备份" / game_dir.name


def backup_file(src: Path, game_dir: Path, do_backup: bool, log: LogFn = None) -> None:
    if not do_backup:
        return
    root = _backup_root(game_dir)
    try:
        rel = src.relative_to(game_dir)
    except ValueError:
        rel = Path(src.name)
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.copy2(src, dest)
        if log:
            log(f"备份: {dest}")


def _should_translate_body(s: str, source_lang: str = "auto") -> bool:
    s = s.strip()
    if not s:
        return False
    if s.startswith(SKIP_LINE_PREFIXES):
        return False
    if re.fullmatch(r"[…\.．。！？!\?\s\-─—・～~♡♥★☆♪]+", s):
        return False
    if re.fullmatch(r"[\d\s\.,;:_\-\[\]\(\)]+", s):
        return False
    # Skip finished Chinese only — keep bare JP kanji (確認/設定) collectable
    try:
        from app.core.pipeline_harden import looks_already_chinese

        if looks_already_chinese(s):
            return False
    except Exception:
        if ALREADY_CN.match(s) and not HAS_KANA.search(s):
            return False
    if source_lang == "ja":
        return bool(HAS_KANA.search(s) or re.search(r"[\u4e00-\u9fff]", s))
    if source_lang == "en":
        return bool(HAS_LATIN.search(s))
    if source_lang == "ko":
        return bool(HAS_HANGUL.search(s))
    if source_lang == "ru":
        return bool(HAS_CYRILLIC.search(s))
    # auto / other: any non-Chinese script signal
    return bool(SRC_HINT.search(s))


def _skip_path(path: Path) -> bool:
    name = path.name.lower()
    if name in SKIP_FILE_NAMES:
        return True
    # Unity player data / IL2CPP junk under *_Data
    parts = {p.lower() for p in path.parts}
    if any(p.endswith("_data") for p in parts) and name.endswith(
        (".json", ".config", ".assets", ".resS", ".resource")
    ):
        # allow only clearly narrative json under StreamingAssets later via Unity pipe
        if "streamingassets" not in parts and name != "readme.txt":
            if name.endswith((".json", ".config")):
                return True
    return False


def collect_items(root: Path, log: LogFn = None, source_lang: str = "auto") -> List[WorkItem]:
    items: List[WorkItem] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if _skip_path(path):
            continue
        suf = path.suffix.lower()
        if suf in (".txt", ".utf", ".ks", ".tsv", ".rpy", ".yml", ".yaml", ".srt", ".ssa", ".ass"):
            try:
                text = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                text = path.read_text(encoding="cp932", errors="replace")
            for i, line in enumerate(text.splitlines()):
                # gettext / po handled below
                if suf == ".rpy" and (line.lstrip().startswith("$") or line.lstrip().startswith("define ")):
                    continue
                m = LINE_TAG_RE.match(line)
                body = m.group(2) if m else line
                # tsv: translate last column if multi-col
                if suf == ".tsv" and "\t" in body:
                    cols = body.split("\t")
                    body = cols[-1]
                    if _should_translate_body(body, source_lang):
                        items.append(WorkItem("tsv_cell", path, (i, len(cols) - 1), body))
                    continue
                if _should_translate_body(body, source_lang):
                    items.append(WorkItem("txt_line", path, i, body))
        elif suf == ".po":
            try:
                text = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                text = path.read_text(encoding="cp932", errors="replace")
            # msgid "..." blocks (simple single-line)
            for i, line in enumerate(text.splitlines()):
                mm = re.match(r'^msgid\s+"(.*)"\s*$', line)
                if not mm:
                    continue
                body = mm.group(1).encode("utf-8").decode("unicode_escape")
                if body and _should_translate_body(body, source_lang):
                    items.append(WorkItem("po_msgid", path, i, body))
        elif suf == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception as e:
                if log:
                    log(f"跳过损坏 JSON: {path.name} ({e})")
                continue
            _walk_json(data, path, [], items, source_lang)
        elif suf == ".csv":
            try:
                raw = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                raw = path.read_text(encoding="cp932", errors="replace")
            rows = list(csv.reader(raw.splitlines()))
            for r_i, row in enumerate(rows):
                for c_i, cell in enumerate(row):
                    if _should_translate_body(cell, source_lang):
                        items.append(WorkItem("csv_cell", path, (r_i, c_i, rows), cell))
    if log:
        log(f"扫描到待译条目: {len(items)}")
    return items


def _is_engine_ident(s: str) -> bool:
    s = s.strip()
    if not s or " " in s:
        return False
    if IDENT_RE.match(s) or CAMEL_RE.match(s):
        return True
    # Namespace.Type.Method style without spaces
    if re.fullmatch(r"[A-Za-z0-9_.:/\\-]+", s) and ("." in s or "/" in s or "\\" in s):
        return len(s) < 120
    return False


def _walk_json(
    node: Any, path: Path, trail: list, items: List[WorkItem], source_lang: str = "auto"
) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str):
                if k.lower() in SKIP_JSON_KEYS or _is_engine_ident(v):
                    continue
                if k in JSON_TEXT_KEYS or _should_translate_body(v, source_lang):
                    if _should_translate_body(v, source_lang):
                        items.append(WorkItem("json_field", path, (trail + [k], node, k), v))
            else:
                _walk_json(v, path, trail + [k], items, source_lang)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_json(v, path, trail + [i], items, source_lang)


def _write_txt_updates(path: Path, pairs: List[Tuple[int, str]]) -> None:
    try:
        text = path.read_text(encoding="utf-8-sig")
        enc = "utf-8"
    except UnicodeDecodeError:
        text = path.read_text(encoding="cp932", errors="replace")
        enc = "utf-8"
    lines = text.splitlines()
    for idx, new_body in pairs:
        if idx >= len(lines):
            continue
        old = lines[idx]
        m = LINE_TAG_RE.match(old)
        if m:
            lines[idx] = m.group(1) + new_body
        else:
            lines[idx] = new_body
    out = "\n".join(lines)
    if text.endswith("\n"):
        out += "\n"
    path.write_text(out, encoding=enc)


def apply_translations(items: List[WorkItem], translated: List[str], game_dir: Path, do_backup: bool, log: LogFn = None) -> None:
    # group by file
    from collections import defaultdict

    from app.core.pipeline_harden import looks_already_chinese

    by_file: dict[Path, list[tuple[WorkItem, str]]] = defaultdict(list)
    for it, dst in zip(items, translated):
        if looks_already_chinese(it.source):
            continue
        if not dst or dst == it.source:
            continue
        by_file[it.path].append((it, dst))
    if not by_file:
        return

    for path, group in by_file.items():
        backup_file(path, game_dir, do_backup, log)
        kind = group[0][0].kind
        if kind == "txt_line":
            pairs = [(it.meta, dst) for it, dst in group]
            _write_txt_updates(path, pairs)
        elif kind == "tsv_cell":
            try:
                text = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                text = path.read_text(encoding="cp932", errors="replace")
            lines = text.splitlines()
            for it, dst in group:
                r_i, c_i = it.meta
                if r_i >= len(lines):
                    continue
                cols = lines[r_i].split("\t")
                if c_i < len(cols):
                    cols[c_i] = dst
                lines[r_i] = "\t".join(cols)
            out = "\n".join(lines)
            if text.endswith("\n"):
                out += "\n"
            path.write_text(out, encoding="utf-8")
        elif kind == "po_msgid":
            try:
                text = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                text = path.read_text(encoding="cp932", errors="replace")
            lines = text.splitlines()
            for it, dst in group:
                idx = it.meta
                if idx >= len(lines):
                    continue
                # msgstr often on next line after msgid
                esc = (
                    dst.replace("\\", "\\\\")
                    .replace('"', '\\"')
                    .replace("\n", "\\n")
                )
                if idx + 1 < len(lines) and lines[idx + 1].lstrip().startswith("msgstr"):
                    lines[idx + 1] = f'msgstr "{esc}"'
                else:
                    lines.insert(idx + 1, f'msgstr "{esc}"')
            out = "\n".join(lines)
            if text.endswith("\n"):
                out += "\n"
            path.write_text(out, encoding="utf-8")
        elif kind == "json_field":
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            # re-walk apply via stored node refs — refs may be stale; re-apply by trail
            for it, dst in group:
                trail, _node, key = it.meta
                cur: Any = data
                for p in trail[:-1]:
                    cur = cur[p]
                last = trail[-1]
                if isinstance(cur, dict):
                    cur[last] = dst
                else:
                    cur[last] = dst
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        elif kind == "csv_cell":
            # all cells share same rows object in meta[2] — take first
            rows = group[0][0].meta[2]
            for it, dst in group:
                r_i, c_i, _ = it.meta
                rows[r_i][c_i] = dst
            with path.open("w", encoding="utf-8", newline="") as f:
                csv.writer(f).writerows(rows)
        if log:
            log(f"已写回: {path}")


def run_generic(cfg: AppConfig, log: LogFn = None, progress: ProgressFn = None, should_cancel: CancelFn = None) -> None:
    game_dir = Path(cfg.game_dir)
    root = Path(cfg.text_dir) if cfg.text_dir.strip() else game_dir
    if not root.is_dir():
        raise FileNotFoundError(f"文本目录无效: {root}")

    # Don't meat-cleaver a full game tree with generic — route to the real engine pipe.
    probe = game_dir if game_dir.is_dir() else root
    try:
        from app.core.detect import detect_engine

        det = detect_engine(probe)
        if det.pipeline in (
            "unity",
            "lcse",
            "kirikiri",
            "yuris",
            "artemis",
            "bgi",
            "kagura",
            "softpal",
        ):
            if log:
                log(f"目录像 {det.engine}，自动改走「{det.pipeline}」管线（避免误改引擎文件）")
            if det.pipeline == "unity":
                from app.pipelines.unity import run_unity

                return run_unity(cfg, log, progress, should_cancel)
            if det.pipeline == "lcse":
                from app.pipelines.lcse import run_lcse

                return run_lcse(cfg, log, progress, should_cancel)
            if det.pipeline == "kirikiri":
                from app.pipelines.kirikiri import run_kirikiri

                return run_kirikiri(cfg, log, progress, should_cancel)
            if det.pipeline == "yuris":
                from app.pipelines.yuris import run_yuris

                return run_yuris(cfg, log, progress, should_cancel)
            if det.pipeline == "artemis":
                from app.pipelines.artemis import run_artemis

                return run_artemis(cfg, log, progress, should_cancel)
            if det.pipeline == "bgi":
                from app.pipelines.bgi import run_bgi

                return run_bgi(cfg, log, progress, should_cancel)
            if det.pipeline == "kagura":
                from app.pipelines.kagura import run_kagura

                return run_kagura(cfg, log, progress, should_cancel)
            if det.pipeline == "softpal":
                from app.pipelines.softpal import run_softpal

                return run_softpal(cfg, log, progress, should_cancel)
    except Exception:
        pass

    source_lang = getattr(cfg, "source_lang", "auto") or "auto"
    items = collect_items(root, log, source_lang=source_lang)
    if not items:
        if log:
            log("没有可翻译条目（若是英文游戏，请把源语言选成「英文」）")
        return

    client = OpenAICompatClient(cfg.api_base, cfg.api_key, cfg.api_model, cfg.temperature)
    cache = TranslateCache(cache_db_path())
    try:
        sources = [it.source for it in items]
        codec = CODEC_CP932 if cfg.cp932_safe else CODEC_UNICODE
        mapping = translate_to_mapping(
            sources,
            client,
            cfg.lang,
            codec=codec,
            cache=cache,
            chunk=cfg.batch_size or 24,
            log=log,
            progress=progress,
            should_cancel=should_cancel,
            source_lang=source_lang,
            game_dir=cfg.game_dir or cfg.text_dir,
            do_polish=getattr(cfg, "mt_polish", True),
            label="主译",
            remain_filter=remain_filter_set(cfg),
        )
        remain = second_pass_sources(sources, mapping, max_n=600, allow=remain_filter_set(cfg))
        if remain:
            mapping = run_second_pass(
                remain,
                mapping,
                client,
                cfg.lang,
                codec=codec,
                cache=cache,
                chunk=cfg.batch_size or 24,
                log=log,
                progress=progress,
                should_cancel=should_cancel,
                source_lang=source_lang,
                game_dir=cfg.game_dir or cfg.text_dir,
                do_polish=getattr(cfg, "mt_polish", True),
                remain_filter=remain_filter_set(cfg),
            )
        write_remainder_report(
            game_dir if game_dir.is_dir() else root,
            "generic",
            sources,
            mapping,
            log=log,
            allow=remain_filter_set(cfg),
        )
        translated = mapping_aligned(sources, mapping)
        if should_cancel and should_cancel():
            return
        apply_translations(items, translated, game_dir if game_dir.is_dir() else root, cfg.do_backup, log)
        if log:
            log("通用文本管线完成")
    finally:
        cache.close()
