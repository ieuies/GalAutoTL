# -*- coding: utf-8 -*-
"""LC-ScriptEngine one-click localize.

Proven flow (大催眠乱交学院):
  backup → unpack SNX → AI translate → fixed-slot harden (orig size) →
  patch onto ORIGINAL package → GBK display patch on exe → CN launcher.

Critical: never leave variable-length SNX in the final pack — longer GBK lines
softlock mid-game; dialog trailer \\x02\\x03 must stay before NUL when padding.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from app.config import AppConfig, cache_db_path
from app.core.api_client import OpenAICompatClient
from app.core.lcse_display import patch_exe_for_gbk, write_cn_launcher
from app.core.lcse_pack import find_package_pair, patch_package, resolve_keys, unpack_scripts
from app.core.lcse_safe_rewrite import rewrite_snx_fixed_slots
from app.core.lcse_snx import (
    apply_translations,
    collect_translatable,
    parse_script,
    read_raw_snx,
    rewrite_snx_file,
)
from app.core.pipeline_harden import (
    CODEC_GBK,
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


def _backup(game_dir: Path, paths: List[Path], log: LogFn) -> Path:
    dest_root = Path.home() / "Desktop" / "自动翻译备份" / f"lcse_{game_dir.name}"
    dest_root.mkdir(parents=True, exist_ok=True)
    for p in paths:
        if not p.is_file():
            continue
        target = dest_root / p.name
        if not target.exists():
            shutil.copy2(p, target)
            if log:
                log(f"备份: {target}")
        else:
            if log:
                log(f"备份已存在，保留首次原版: {target.name}")
    return dest_root


def _pick_main_exe(game_dir: Path) -> Optional[Path]:
    exes = [
        p
        for p in game_dir.glob("*.exe")
        if not p.name.lower().startswith("unity") and "unins" not in p.name.lower()
    ]
    if not exes:
        return None
    # prefer non-tiny launchers
    exes.sort(key=lambda p: p.stat().st_size, reverse=True)
    return exes[0]


def _harden_and_repack(
    game_dir: Path,
    bak_pkg: Path,
    bak_lst: Path,
    translated_snx_dir: Path,
    key_byte: int,
    snx_key: int,
    log: LogFn,
) -> int:
    """Fit every translated SNX into original slots; patch onto pristine backup pack."""
    import tempfile

    work = game_dir / "_galautotl_lcse"
    orig_dir = work / "snx_orig"
    hard_dir = work / "snx_hardened"
    if orig_dir.exists():
        shutil.rmtree(orig_dir)
    if hard_dir.exists():
        shutil.rmtree(hard_dir)
    hard_dir.mkdir(parents=True)

    unpack_scripts(
        bak_pkg, bak_lst, orig_dir, key_byte=key_byte, snx_key=snx_key, only_snx=True
    )

    changed = 0
    for orig in sorted(orig_dir.glob("*.snx")):
        tr = translated_snx_dir / orig.name
        if not tr.exists():
            shutil.copy2(orig, hard_dir / orig.name)
            continue
        try:
            safe = rewrite_snx_fixed_slots(orig.read_bytes(), tr.read_bytes())
        except Exception as e:
            if log:
                log(f"槽位硬化失败，保留原文 {orig.name}: {e}")
            shutil.copy2(orig, hard_dir / orig.name)
            continue
        if safe != orig.read_bytes():
            changed += 1
        (hard_dir / orig.name).write_bytes(safe)

    # INIT: ＭＳゴシック → 微软雅黑 inside original slot (pad, keep trailer/NUL rules)
    init_o = orig_dir / "INIT.snx"
    init_h = hard_dir / "INIT.snx"
    if init_o.exists():
        try:
            from app.core.lcse_safe_rewrite import _fit_slot
            from app.core.lcse_snx import RawScript, RawString, write_raw_snx

            raw = read_raw_snx(init_o.read_bytes())
            # prefer already-hardened if present
            if init_h.exists():
                raw = read_raw_snx(init_h.read_bytes())
            old = "ＭＳ ゴシック".encode("cp932")
            new_body = "微软雅黑".encode("gbk") + b"\x00"
            patched = False
            new_strings = []
            for s in sorted(raw.strings, key=lambda x: x.offset):
                content = s.content
                if old in content or (
                    content.startswith("微软雅黑".encode("gbk")) and b" " in content
                ):
                    content = _fit_slot(new_body, s.content)
                    patched = True
                new_strings.append(RawString(s.ordinal, s.offset, content))
            if patched:
                init_h.write_bytes(
                    write_raw_snx(RawScript(list(raw.instructions), new_strings))
                )
                if log:
                    log("INIT 字体槽位：微软雅黑（保持原字节长度）")
        except Exception as e:
            if log:
                log(f"INIT 字体槽位处理跳过: {e}")

    td = Path(tempfile.mkdtemp(prefix="lcse_pack_"))
    out_pkg = td / bak_pkg.name
    out_lst = td / bak_lst.name
    n_rep = patch_package(
        bak_pkg,
        bak_lst,
        hard_dir,
        out_pkg,
        out_lst,
        key_byte=key_byte,
        snx_key=snx_key,
        snx_only=True,
    )
    shutil.copy2(out_pkg, game_dir / bak_pkg.name)
    shutil.copy2(out_lst, game_dir / bak_lst.name)
    if log:
        log(f"安全回封完成：硬化 {changed} 个 SNX，替换条目 {n_rep} → 基于原版封包")
    return changed


def run_lcse(
    cfg: AppConfig,
    log: LogFn = None,
    progress: ProgressFn = None,
    should_cancel: CancelFn = None,
) -> None:
    game_dir = Path(cfg.game_dir or cfg.text_dir)
    if not game_dir.is_dir():
        raise FileNotFoundError(f"游戏目录无效: {game_dir}")

    pkg, lst = find_package_pair(game_dir)
    if log:
        log(f"封包: {pkg.name} / {lst.name}")

    work = game_dir / "_galautotl_lcse"
    snx_dir = work / "snx"
    if snx_dir.exists():
        shutil.rmtree(snx_dir)
    snx_dir.mkdir(parents=True, exist_ok=True)

    main_exe = _pick_main_exe(game_dir)
    bak_files = [pkg, lst]
    if main_exe:
        bak_files.append(main_exe)
    bak_root = _backup(game_dir, bak_files, log)
    bak_pkg = bak_root / pkg.name
    bak_lst = bak_root / lst.name
    if not bak_pkg.is_file() or not bak_lst.is_file():
        raise FileNotFoundError(f"备份失败，无法安全回封: {bak_root}")

    if log:
        log("探测 XOR 密钥…")
    key_byte, snx_key = resolve_keys(bak_lst, bak_pkg)
    if log:
        log(f"密钥: lst=0x{key_byte:02X} snx=0x{snx_key:02X}")

    if log:
        log("解包 SNX…")
    unpack_pkg, unpack_lst = bak_pkg, bak_lst
    if remain_filter_set(cfg) is not None:
        unpack_pkg, unpack_lst = pkg, lst
        if log:
            log("仅译漏句: 从当前封包解包（保留已有汉化；槽位仍按原版备份硬化）")
    _kb, _sk, count = unpack_scripts(
        unpack_pkg, unpack_lst, snx_dir, key_byte=key_byte, snx_key=snx_key, only_snx=True
    )
    if log:
        log(f"已解出 SNX: {count} 个 → {snx_dir}")

    file_items: List[Tuple[Path, object, List[Tuple[str, str, int]]]] = []
    sources: List[str] = []
    meta: List[Tuple[int, str, int]] = []

    for path in sorted(snx_dir.glob("*.snx")):
        if should_cancel and should_cancel():
            return
        try:
            raw = read_raw_snx(path.read_bytes())
            parsed = parse_script(raw)
        except Exception as e:
            if log:
                log(f"跳过无法解析: {path.name} ({e})")
            continue
        units = collect_translatable(parsed)
        if not units:
            continue
        fi = len(file_items)
        file_items.append((path, parsed, units))
        for kind, text, ordinal in units:
            sources.append(text)
            meta.append((fi, kind, ordinal))

    if not sources:
        raise RuntimeError("没有从 SNX 中提取到可翻译对白/选项（密钥或格式可能不匹配）")

    if log:
        log(f"待译条目: {len(sources)}")

    source_lang = getattr(cfg, "source_lang", "auto") or "auto"
    if source_lang == "auto":
        source_lang = "ja"

    encoding = "gbk"
    if cfg.cp932_safe:
        encoding = "cp932"
        if log:
            log("已开 CP932：不推荐用于 LCSE；请关闭并使用默认 GBK+显示补丁")

    # Translate sanitize = GBK (keep 简体). Never API cp932=True before GBK slots.
    client = OpenAICompatClient(cfg.api_base, cfg.api_key, cfg.api_model, cfg.temperature)
    cache = TranslateCache(cache_db_path())
    try:
        if log:
            log("LCSE: 翻译 codec=gbk（禁 CP932 打点）；写回按槽位硬化")
        mapping = translate_to_mapping(
            sources,
            client,
            cfg.lang,
            codec=CODEC_GBK,
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
        if should_cancel and should_cancel():
            return
        remain = second_pass_sources(sources, mapping, max_n=600, allow=remain_filter_set(cfg))
        if remain:
            mapping = run_second_pass(
                remain,
                mapping,
                client,
                cfg.lang,
                codec=CODEC_GBK,
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
        rfilt = remain_filter_set(cfg)
        write_remainder_report(
            Path(cfg.game_dir or cfg.text_dir or snx_dir),
            "lcse",
            sources,
            mapping,
            log=log,
            allow=rfilt,
        )
        per_file: List[List[Tuple[str, int, str]]] = [[] for _ in file_items]
        for (fi, kind, ordinal), src in zip(meta, sources):
            dst = mapping.get(src)
            if not dst or dst == src:
                continue
            if rfilt is not None and src not in rfilt:
                continue
            per_file[fi].append((kind, ordinal, dst))

        changed = 0
        for (path, parsed, _units), updates in zip(file_items, per_file):
            if not updates:
                continue
            apply_translations(parsed, updates)
            rewrite_snx_file(path, parsed, encoding=encoding)
            changed += 1
        if log:
            log(f"译文已写入工作 SNX: {changed} 个文件（下一步槽位硬化）")

        _harden_and_repack(
            game_dir, bak_pkg, bak_lst, snx_dir, key_byte, snx_key, log
        )

        # display patches on game exe (from backup original first if we overwrote)
        bak_exe = bak_root / main_exe.name if main_exe else None
        if main_exe and bak_exe and bak_exe.is_file():
            shutil.copy2(bak_exe, main_exe)
        if encoding == "gbk":
            for exe in game_dir.glob("*.exe"):
                name = exe.name.lower()
                if name.startswith("unity") or "unins" in name:
                    continue
                try:
                    patch_exe_for_gbk(exe, log)
                except Exception as e:
                    if log:
                        log(f"跳过 exe 补丁 {exe.name}: {e}")
            if main_exe:
                write_cn_launcher(game_dir, main_exe.name, log)

        if getattr(cfg, "auto_copy_font", True):
            from app.core.fonts import copy_cjk_font_to_game

            ok, tip = copy_cjk_font_to_game(str(game_dir))
            if log:
                log(tip if ok else f"字体: {tip}")

        if log:
            log("==== LCSE 汉化完成 ====")
            log("请用「点我启动_中文汉化版.bat」启动；不要用日语 Locale Emulator。")
            log("请开「新游戏」；旧半截存档可能对不上。")
            log(f"原版备份: {bak_root}")
            log(f"工作目录: {work}")
    finally:
        cache.close()
