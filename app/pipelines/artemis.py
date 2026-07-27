# -*- coding: utf-8 -*-
"""Artemis one-click: unpack PFS → translate .ast text={} → loose script/ inject.

Lessons (嫁いもどり / 222 / 嫁の妹とえっちな関係):
  - Collect relative to work root (never absolute ``_galautotl_`` skip).
  - Only translate ``text={}`` dialogue; deploy under ``script/``.
  - ``name=`` is the name-plate layer — never put full dialogue there or text stacks.
  - Always keep a true JP snapshot (prefer PFS extract) and restore speakers after AI.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, List, Optional

from app.config import AppConfig, cache_db_path
from app.core.api_client import OpenAICompatClient
from app.core.artemis_text import (
    apply_artemis_units,
    collect_artemis_units,
    normalize_artemis_rel,
    restore_artemis_speakers,
    scrub_artemis_duplicate_name_plates,
)
from app.core.garbro_cli import extract_with_garbro, find_garbro
from app.core.pfs_io import PFSError, extract_pfs_scripts, find_pfs_archives
from app.core.pipeline_harden import (
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


def _backup(game_dir: Path, paths: List[Path], log: LogFn) -> None:
    dest = Path.home() / "Desktop" / "自动翻译备份" / f"artemis_{game_dir.name}"
    dest.mkdir(parents=True, exist_ok=True)
    for p in paths:
        if p.is_file() and not (dest / p.name).exists():
            shutil.copy2(p, dest / p.name)
            if log:
                log(f"备份: {dest / p.name}")


def _artemis_tree_looks_cn(root: Path) -> bool:
    """Sample .ast/.txt dialogue; True if mostly already Chinese."""
    from app.core.pipeline_harden import looks_already_chinese
    import re

    files = list(root.rglob("*.ast")) + list(root.rglob("*.txt"))
    files = [p for p in files if p.is_file() and "_galautotl_" not in str(p).lower()][:30]
    if not files:
        return False
    cn = jp = 0
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[:16000]
        except Exception:
            continue
        for m in re.finditer(r'text\s*=\s*\{([^{}]{2,400})\}', text):
            body = m.group(1).strip().strip("\"'")
            if looks_already_chinese(body):
                cn += 1
            elif re.search(r"[\u3040-\u30ff]", body):
                jp += 1
            if cn + jp >= 60:
                break
        if cn + jp >= 60:
            break
    if cn + jp < 6:
        return False
    return cn >= jp * 2 and cn >= 4


def _prepare_scripts(game_dir: Path, work: Path, log: LogFn, tools_dir: str) -> Path:
    scripts = work / "scripts"
    if scripts.exists():
        shutil.rmtree(scripts)
    scripts.mkdir(parents=True)

    # already-loose scripts (may already be CN — JP snap must come from PFS)
    loose = []
    for suf in ("*.ast", "*.txt"):
        loose.extend(game_dir.rglob(suf))
    loose = [p for p in loose if "_galautotl_" not in str(p).lower()]
    if loose and not find_pfs_archives(game_dir):
        for p in loose:
            try:
                rel = p.relative_to(game_dir)
            except ValueError:
                rel = Path(p.name)
            dest = scripts / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
        if log:
            log(f"使用明文脚本 {len(loose)} 个")
        return scripts

    archives = find_pfs_archives(game_dir)
    if not archives:
        raise RuntimeError("未找到 .pfs / 明文 .ast。请确认 Artemis 游戏根目录。")

    if log:
        log(f"PFS: {', '.join(a.name for a in archives[:6])}")

    extracted = 0
    for arc in archives:
        sub = scripts / arc.stem
        try:
            n = extract_pfs_scripts(arc, sub)
            if log:
                log(f"  解包 {arc.name}: {n} 脚本文件")
            extracted += n
        except PFSError as e:
            if log:
                log(f"  内置解包失败 {arc.name}: {e}")
            garbro = find_garbro([Path(tools_dir)] if tools_dir.strip() else None)
            if garbro:
                extract_with_garbro(arc, sub, garbro, log)

    if not list(scripts.rglob("*.ast")) and not list(scripts.rglob("*.txt")):
        raise RuntimeError("未能解出 .ast/.txt。可装 garbro-cli 或手动解 PFS 后再跑。")
    return scripts


def _build_jp_snapshot(
    game_dir: Path,
    work: Path,
    scripts: Path,
    log: LogFn,
    tools_dir: str,
) -> Path:
    """
    True Japanese tree for name= restore.
    Prefer PFS extract (even when loose script/ is already CN).
    """
    jp_snap = work / "scripts_jp"
    if jp_snap.exists():
        shutil.rmtree(jp_snap)
    jp_snap.mkdir(parents=True)

    archives = find_pfs_archives(game_dir)
    # Also try desktop backup if game PFS was replaced
    if not archives:
        bak = Path.home() / "Desktop" / "自动翻译备份" / f"artemis_{game_dir.name}"
        if bak.is_dir():
            archives = find_pfs_archives(bak)

    if archives:
        got = 0
        for arc in archives:
            sub = jp_snap / arc.stem
            try:
                got += extract_pfs_scripts(arc, sub)
            except PFSError as e:
                if log:
                    log(f"JP 快照解包失败 {arc.name}: {e}")
                garbro = find_garbro([Path(tools_dir)] if tools_dir.strip() else None)
                if garbro:
                    extract_with_garbro(arc, sub, garbro, log)
                    got += len(list(sub.rglob("*.ast")))
        if list(jp_snap.rglob("*.ast")):
            if log:
                log(f"JP 角色名快照: 从 PFS 解出 {got} 个脚本")
            return jp_snap

    # Fallback: copy current scripts (only valid on first JP extract run)
    shutil.rmtree(jp_snap)
    shutil.copytree(scripts, jp_snap)
    if log:
        log("JP 角色名快照: 使用当前脚本副本（无 PFS 时；若已是中文请从备份还原 PFS）")
    return jp_snap


def _deploy(game_dir: Path, scripts: Path, log: LogFn) -> None:
    """Deploy translated scripts as loose files under script/ (disk > PFS)."""
    n = 0
    for p in scripts.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in (".ast", ".txt", ".ini"):
            continue
        rel = p.relative_to(scripts)
        parts = [x.lower() for x in rel.parts]
        if "system" in parts and p.suffix.lower() != ".ini":
            continue
        out_rel = normalize_artemis_rel(rel)
        if not out_rel.parts:
            continue
        dest = game_dir.joinpath(*out_rel.parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)
        n += 1
    readme = game_dir / "汉化启动说明_Artemis.txt"
    readme.write_text(
        "GalAutoTL Artemis 汉化说明\n"
        "=========================\n"
        "1. 已从 PFS 解出并翻译 .ast 的 text={} 对白，以松散 script/ 覆盖\n"
        "2. 未改动原 .pfs；备份在桌面「自动翻译备份\\artemis_游戏名」\n"
        "3. 未汉化 system/*.lua / 命令参数 ch=file=；编译型 .asb 暂不支持\n"
        "4. name= 只保留短角色名（名字层）；整句台词只在正文层——避免叠字\n"
        "5. 工作目录: 游戏目录\\_galautotl_artemis\\\n"
        "6. 日文环境建议用 Locale Emulator / 系统区域=日语 启动\n",
        encoding="utf-8",
    )
    if log:
        log(f"已部署松散脚本约 {n} 个 → {readme.name}")


def run_artemis(
    cfg: AppConfig,
    log: LogFn = None,
    progress: ProgressFn = None,
    should_cancel: CancelFn = None,
) -> None:
    game_dir = Path(cfg.game_dir or cfg.text_dir)
    if not game_dir.is_dir():
        raise FileNotFoundError(f"目录无效: {game_dir}")

    work = game_dir / "_galautotl_artemis"
    work.mkdir(parents=True, exist_ok=True)
    archives = find_pfs_archives(game_dir)
    tools_dir = getattr(cfg, "tools_dir", "") or ""
    if archives and cfg.do_backup:
        _backup(game_dir, archives, log)

    text_dir = Path(cfg.text_dir) if cfg.text_dir.strip() else None
    rfilt = remain_filter_set(cfg)
    loose_script = game_dir / "script"
    archives = find_pfs_archives(game_dir)
    if rfilt is not None and loose_script.is_dir() and (
        list(loose_script.rglob("*.ast")) or list(loose_script.rglob("*.txt"))
    ):
        scripts = work / "scripts"
        if scripts.exists():
            shutil.rmtree(scripts)
        shutil.copytree(
            loose_script, scripts, ignore=shutil.ignore_patterns("_galautotl_*")
        )
        if log:
            log("仅译漏句: 使用现有 script/（保留已有汉化）")
    elif (
        text_dir
        and text_dir.is_dir()
        and (list(text_dir.rglob("*.ast")) or list(text_dir.rglob("*.txt")))
        and not (
            archives
            and _artemis_tree_looks_cn(text_dir)
        )
    ):
        scripts = work / "scripts"
        if scripts.exists():
            shutil.rmtree(scripts)
        shutil.copytree(text_dir, scripts, ignore=shutil.ignore_patterns("_galautotl_*"))
        if log:
            log(f"使用文本文件夹: {text_dir}")
    else:
        if archives and text_dir and _artemis_tree_looks_cn(text_dir) and log:
            log("文本目录已是中文，改从 PFS 解日文源（防二次翻坏）")
        scripts = _prepare_scripts(game_dir, work, log, tools_dir)

    jp_snap = _build_jp_snapshot(game_dir, work, scripts, log, tools_dir)

    units = collect_artemis_units(scripts)
    if not units:
        raise RuntimeError("未从 .ast/.txt 提取到对白（若只有 .asb 编译脚本，暂需其它工具）")
    if log:
        names = sum(1 for u in units if u.kind == "name")
        log(f"待翻译条目: {len(units)}（其中角色名 {names}）")

    client = OpenAICompatClient(cfg.api_base, cfg.api_key, cfg.api_model, cfg.temperature)
    cache = TranslateCache(cache_db_path())
    source_lang = getattr(cfg, "source_lang", "ja") or "ja"
    try:
        sources = [u.source for u in units]
        mapping = translate_to_mapping(
            sources,
            client,
            cfg.lang,
            codec=CODEC_UNICODE,
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
                codec=CODEC_UNICODE,
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
            Path(cfg.game_dir or cfg.text_dir or game_dir),
            "artemis",
            sources,
            mapping,
            log=log,
            allow=remain_filter_set(cfg),
        )
        translated = mapping_aligned(sources, mapping)
    finally:
        cache.close()
    if should_cancel and should_cancel():
        return
    translated = mapping_aligned(sources, mapping)
    n = apply_artemis_units(units, translated)
    if log:
        log(f"已写回 {n} 个脚本文件")

    # name plate ≠ body (prevent stacked duplicate dialogue)
    try:
        from app.core.glossary import load_auto_glossary, load_glossary_for_game, merge_glossaries

        manual, _ = load_glossary_for_game(game_dir)
        auto = load_auto_glossary(game_dir)
        gloss = merge_glossaries(auto, manual)
        smap = {s: d for s, d in gloss.pairs}
        n_fix = restore_artemis_speakers(scripts, jp_snap, smap)
        n_scrub = scrub_artemis_duplicate_name_plates(scripts)
        if log:
            if n_fix:
                log(f"已还原角色名框 {n_fix} 处（对照 JP，避免叠字）")
            if n_scrub:
                log(f"已清除 name=/正文重复 {n_scrub} 处")
    except Exception as e:
        if log:
            log(f"角色名框修复跳过: {e}")
        try:
            n_scrub = scrub_artemis_duplicate_name_plates(scripts)
            if log and n_scrub:
                log(f"已清除 name=/正文重复 {n_scrub} 处")
        except Exception:
            pass

    _deploy(game_dir, scripts, log)
    if log:
        log("Artemis 管线完成")
