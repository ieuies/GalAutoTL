# -*- coding: utf-8 -*-
"""Kagura / Debonosu Softpal PAK one-click: unpack → AI → repack → EXE UI.

Hardened from reimeiki_25 localization:
  - Always CP932-safe translate + writeback (never UTF-8 into Lua slots)
  - Harvest .scb + btText.dat
  - Force UI glossary (セーブ/ロード/难度…)
  - Second pass on leftover JP after apply
  - Patch kagura*.exe system strings from clean backup
  - Reject ・-mangled translations
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from app.config import AppConfig, cache_db_path
from app.core.api_client import OpenAICompatClient
from app.core.kagura_bt_text import apply_bt_texts, collect_bt_texts
from app.core.kagura_exe_ui import find_kagura_exe, patch_kagura_exe
from app.core.kagura_glossary import UI_GLOSSARY, apply_ui_glossary
from app.core.kagura_pak import (
    apply_scb_units,
    collect_units_from_scb_dir,
    extract_game_scripts,
    find_game_pak,
    is_lua_scb,
    is_translatable_line,
    open_pak,
    read_entry,
    rebuild_game_pak,
)
from app.core.pipeline_harden import (
    CODEC_CP932,
    looks_untranslated,
    remain_filter_set,
    run_second_pass,
    translate_to_mapping,
    write_remainder_report,
)
from app.core.translate import TranslateCache

LogFn = Optional[Callable[[str], None]]
ProgressFn = Optional[Callable[[int, int], None]]
CancelFn = Optional[Callable[[], bool]]


def _bak_dir(game_dir: Path) -> Path:
    return Path.home() / "Desktop" / "自动翻译备份" / f"kagura_{game_dir.name}"


def _backup(game_dir: Path, pak: Path, log: LogFn) -> Path:
    dest = _bak_dir(game_dir)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / pak.name
    if not target.exists():
        shutil.copy2(pak, target)
        if log:
            log(f"备份 PAK: {target}")
    exe = find_kagura_exe(game_dir)
    if exe:
        et = dest / exe.name
        if not et.exists():
            shutil.copy2(exe, et)
            if log:
                log(f"备份 EXE: {et}")
    return dest


def _still_jp_content(s: str) -> bool:
    if not s or not is_translatable_line(s):
        return False
    return looks_untranslated(s)

def _collect_remainders(scb_dir: Path, bt_blob: Optional[bytes]) -> List[str]:
    texts, _ = collect_units_from_scb_dir(scb_dir)
    seen: Set[str] = set()
    out: List[str] = []
    for s in texts:
        if _still_jp_content(s) and s not in seen:
            seen.add(s)
            out.append(s)
    if bt_blob:
        for s in collect_bt_texts(bt_blob):
            if _still_jp_content(s) and s not in seen:
                seen.add(s)
                out.append(s)
    return out


def _apply_all(
    paths: List[Path],
    bt_blob: Optional[bytes],
    mapping: Dict[str, str],
    log: LogFn,
) -> tuple[Dict[str, bytes], int, int, Optional[bytes]]:
    file_blobs: Dict[str, bytes] = {}
    total_hit = 0
    for p in paths:
        data = p.read_bytes()
        if not is_lua_scb(data):
            continue
        new_data, n = apply_scb_units(
            data, mapping, soft_fit=True, prefer_cp932=True
        )
        if n:
            p.write_bytes(new_data)
            file_blobs[p.name] = new_data
            total_hit += n
        else:
            file_blobs[p.name] = data

    bt_hit = 0
    new_bt = bt_blob
    if bt_blob is not None:
        new_bt, bt_hit = apply_bt_texts(bt_blob, mapping)
    if log:
        log(f"写回 Lua {total_hit} 处 / btText {bt_hit} 处")
    return file_blobs, total_hit, bt_hit, new_bt


def _audit_utf8_leak(paths: List[Path], log: LogFn) -> int:
    """Count Lua payloads that decode as UTF-8 but not CP932 (mojibake risk)."""
    leaks = 0
    for p in paths:
        data = p.read_bytes()
        if not is_lua_scb(data):
            continue
        from app.core.kagura_pak import iter_lua_string_spans

        for _sz, _off, payload in iter_lua_string_spans(data):
            if not payload:
                continue
            try:
                payload.decode("cp932")
                continue
            except UnicodeDecodeError:
                pass
            try:
                t = payload.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if re.search(r"[\u4e00-\u9fff]", t):
                leaks += 1
    if log and leaks:
        log(f"警告: 仍有 {leaks} 处疑似 UTF-8 载荷（应已杜绝）")
    return leaks


def run_kagura(
    cfg: AppConfig,
    log: LogFn = None,
    progress: ProgressFn = None,
    should_cancel: CancelFn = None,
) -> None:
    game_dir = Path(cfg.game_dir or cfg.text_dir or "").expanduser()
    if not game_dir.is_dir():
        raise RuntimeError("请先选择 Kagura 游戏根目录（含 game.pak / kagura*.exe）")

    pak = find_game_pak(game_dir)
    if not pak:
        raise RuntimeError("未找到 game.pak。请确认是 Debonosu/Kagura 目录。")

    if log:
        log(f"Kagura PAK: {pak.name} ({pak.stat().st_size} bytes)")
        log("模式: 强制 CP932 + btText + EXE UI + 漏翻二扫")

    bak = _bak_dir(game_dir)
    if getattr(cfg, "do_backup", True):
        bak = _backup(game_dir, pak, log)

    # Prefer original pak from backup so re-runs are clean — except 仅译漏句
    # which must patch the current (possibly already CN) pak.
    src_pak = bak / pak.name
    rfilt = remain_filter_set(cfg)
    if rfilt is not None:
        work_pak = pak
        if log:
            log("仅译漏句: 从当前 game.pak 解包（保留已有汉化）")
    elif src_pak.exists():
        if log:
            log(f"从备份解包: {src_pak}")
        work_pak = src_pak
    else:
        work_pak = pak

    work = game_dir / "_galautotl_kagura"
    scb_dir = work / "scb"
    if scb_dir.exists():
        shutil.rmtree(scb_dir)
    scb_dir.mkdir(parents=True)

    if log:
        log("解包 script/*.scb …")
    paths = extract_game_scripts(work_pak, scb_dir)
    if not paths:
        raise RuntimeError("game.pak 内未找到 .scb 脚本")
    if log:
        log(f"已解出 {len(paths)} 个 .scb")

    texts, details = collect_units_from_scb_dir(scb_dir)
    if not texts:
        raise RuntimeError("未从 .scb（Lua）中提取到可翻译日文。")
    if log:
        log(f"待译台词 {len(texts)} 条（去重） / 引用 {len(details)} 处")

    arc = open_pak(work_pak)
    bt_entry = next((e for e in arc.files if e.name == "btText.dat"), None)
    bt_blob: Optional[bytes] = None
    bt_texts: List[str] = []
    if bt_entry is not None:
        bt_blob = read_entry(arc, bt_entry)
        bt_texts = collect_bt_texts(bt_blob)
        if log:
            log(f"btText.dat UI/战斗字串 {len(bt_texts)} 条（去重）")

    seen = set(texts)
    all_texts: List[str] = list(texts)
    for s in bt_texts:
        if s not in seen:
            seen.add(s)
            all_texts.append(s)
    if log and bt_texts:
        log(f"合计待译 {len(all_texts)} 条（台词+UI 去重）")

    client = OpenAICompatClient(
        cfg.api_base, cfg.api_key, cfg.api_model, cfg.temperature
    )
    cache = TranslateCache(cache_db_path())
    try:
        mapping = translate_to_mapping(
            all_texts,
            client,
            cfg.lang,
            codec=CODEC_CP932,
            cache=cache,
            chunk=cfg.batch_size or 24,
            log=log,
            progress=progress,
            should_cancel=should_cancel,
            source_lang=getattr(cfg, "source_lang", "ja") or "ja",
            game_dir=game_dir,
            do_polish=getattr(cfg, "mt_polish", True),
            label="主译",
            glossary=UI_GLOSSARY,
            remain_filter=remain_filter_set(cfg),
        )
        mapping = apply_ui_glossary(mapping, remain_filter_set(cfg))

        if not mapping:
            raise RuntimeError("翻译结果为空（可能 API/模型失败）。请检查密钥与模型名。")

        file_blobs, _hit, _bt_hit, bt_blob = _apply_all(
            paths, bt_blob, mapping, log
        )
        if bt_blob is not None:
            (work / "btText.dat").write_bytes(bt_blob)
            file_blobs["btText.dat"] = bt_blob

        # Second pass: leftovers still looking Japanese
        remain = _collect_remainders(scb_dir, bt_blob)
        remain = [s for s in remain if s not in UI_GLOSSARY][:800]
        rfilt = remain_filter_set(cfg)
        if rfilt is not None:
            remain = [s for s in remain if s in rfilt]
        if remain:
            mapping = run_second_pass(
                remain,
                mapping,
                client,
                cfg.lang,
                codec=CODEC_CP932,
                cache=cache,
                chunk=cfg.batch_size or 24,
                log=log,
                progress=progress,
                should_cancel=should_cancel,
                source_lang=getattr(cfg, "source_lang", "ja") or "ja",
                game_dir=game_dir,
                do_polish=getattr(cfg, "mt_polish", True),
                glossary=UI_GLOSSARY,
                remain_filter=rfilt,
            )
            mapping = apply_ui_glossary(mapping, rfilt)
            file_blobs, _hit, _bt_hit, bt_blob = _apply_all(
                paths, bt_blob, mapping, log
            )
            if bt_blob is not None:
                (work / "btText.dat").write_bytes(bt_blob)
                file_blobs["btText.dat"] = bt_blob

        _audit_utf8_leak(paths, log)

        # Final remainder stats (SFX/onomatopoeia may remain under CP932)
        left = _collect_remainders(scb_dir, bt_blob)
        # Disk leftovers (soft-fit may have dropped CN) — report sources as still JP
        write_remainder_report(
            game_dir, "kagura", left, {s: s for s in left}, log=log, allow=rfilt
        )
        if log:
            log(
                f"收尾: 仍含假名内容约 {len(left)} 条"
                "（多为拟声/娇喘，CP932 下难以写成纯汉字）"
            )
    finally:
        cache.close()

    out_pak = work / "game.pak"
    if log:
        log("重建 game.pak …")
    rebuild_game_pak(arc, file_blobs, out_pak)

    live = game_dir / "game.pak"
    shutil.copy2(out_pak, live)
    if log:
        log(f"已替换 {live}")

    # EXE system UI (はい/いいえ/終了…)
    try:
        n_exe, exe_path = patch_kagura_exe(game_dir, bak_dir=bak)
        if log:
            if exe_path and n_exe:
                log(f"已修补 EXE 系统 UI {n_exe} 处: {exe_path.name}")
            elif exe_path:
                log(f"EXE 未命中系统字串（可能已汉化）: {exe_path.name}")
            else:
                log("未找到 kagura*.exe，跳过 EXE UI")
    except Exception as ex:
        if log:
            log(f"EXE UI 修补跳过: {ex}")

    if log:
        log(
            "完成。请用 Locale Emulator（日语）启动检查。"
            "菜单/难度/道具应已进 btText；退出确认在 EXE。"
        )

    try:
        from app.core.review_table import export_review_table

        ordered = list(dict.fromkeys(all_texts + list(mapping.keys())))
        export_review_table(
            game_dir, ordered, [mapping.get(s, s) for s in ordered]
        )
        if log:
            log("对照表: GalAutoTL_review.txt")
    except Exception as ex:
        if log:
            log(f"对照表写入跳过: {ex}")
