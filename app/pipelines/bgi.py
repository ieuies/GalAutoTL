# -*- coding: utf-8 -*-
"""BGI / Ethornell one-click: unpack ARC → patch scenario scripts → write back."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, List, Optional

from app.config import AppConfig, cache_db_path
from app.core.api_client import OpenAICompatClient
from app.core.bgi_script import apply_bgi_units, collect_bgi_units, find_bgi_scripts
from app.core.garbro_cli import extract_with_garbro, find_garbro
from app.core.pipeline_harden import (
    CODEC_CP932,
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


def _bak_dir(game_dir: Path) -> Path:
    return Path.home() / "Desktop" / "自动翻译备份" / f"bgi_{game_dir.name}"


def _backup(game_dir: Path, paths: List[Path], log: LogFn) -> None:
    dest = _bak_dir(game_dir)
    dest.mkdir(parents=True, exist_ok=True)
    for p in paths:
        if p.is_file() and not (dest / p.name).exists():
            shutil.copy2(p, dest / p.name)
            if log:
                log(f"备份: {dest / p.name}")


def _find_arcs(game_dir: Path) -> List[Path]:
    arcs = list(game_dir.glob("*.arc")) + list(game_dir.glob("data*.arc"))
    seen = set()
    out = []
    for p in arcs:
        k = str(p.resolve()).lower()
        if k not in seen:
            seen.add(k)
            out.append(p)
    # prefer scenario-ish archives first
    def score(p: Path) -> tuple:
        n = p.name.lower()
        if "data01" in n or "script" in n or "scene" in n:
            return (0, n)
        return (1, n)

    out.sort(key=score)
    return out


def _copy_scripts_into(src_files: List[Path], base: Path, scripts: Path) -> int:
    n = 0
    for p in src_files:
        try:
            rel = p.relative_to(base)
        except ValueError:
            rel = Path(p.name)
        dest = scripts / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)
        n += 1
    return n


def _extract_arcs(
    arcs: List[Path], scripts: Path, game_dir: Path, tools_dir: str, log: LogFn
) -> None:
    extra = [game_dir, game_dir / "tools"]
    if tools_dir.strip():
        extra.insert(0, Path(tools_dir.strip()))
    garbro = find_garbro(extra)
    if not garbro:
        raise RuntimeError(
            "脚本在 .arc 内且未找到 garbro-cli。\n"
            "请安装 garbro-cli 或先解包 data*.arc 再把脚本目录填到「文本文件夹」。"
        )
    for arc in arcs:
        if log:
            log(f"GARbro 解包: {arc.name}")
        extract_with_garbro(arc, scripts / arc.stem, garbro, log)


def _prepare(
    game_dir: Path,
    work: Path,
    log: LogFn,
    tools_dir: str,
    *,
    remain_only: bool = False,
) -> Path:
    """Prepare script tree.

    Full run: prefer JP from desktop backup arcs / game arcs (not CN loose).
    仅译漏句: prefer cn_bgi_scripts / existing loose CN.
    """
    scripts = work / "scripts"
    if scripts.exists():
        shutil.rmtree(scripts)
    scripts.mkdir(parents=True)

    existing = find_bgi_scripts(game_dir)
    existing = [p for p in existing if "_galautotl_" not in str(p).lower()]
    cn_flat = game_dir / "cn_bgi_scripts"
    arcs = _find_arcs(game_dir)
    bak = _bak_dir(game_dir)
    bak_arcs = _find_arcs(bak) if bak.is_dir() else []

    if remain_only:
        if cn_flat.is_dir() and find_bgi_scripts(cn_flat):
            n = _copy_scripts_into(find_bgi_scripts(cn_flat), cn_flat, scripts)
            if log:
                log(f"仅译漏句: 使用 cn_bgi_scripts（{n} 个，保留汉化）")
            return scripts
        if existing:
            n = _copy_scripts_into(existing, game_dir, scripts)
            if log:
                log(f"仅译漏句: 使用已有脚本 {n} 个（保留汉化）")
            return scripts

    # Full run — JP from backup arcs if present, else game arcs
    source_arcs = bak_arcs or arcs
    if source_arcs:
        if bak_arcs and log:
            log(f"从备份解包 BGI（防二次冲盘）: {bak}")
        try:
            _extract_arcs(source_arcs, scripts, game_dir, tools_dir, log)
        except RuntimeError:
            if not existing:
                raise
            if log:
                log("解包失败，回退已有松散脚本")
            _copy_scripts_into(existing, game_dir, scripts)
    elif existing:
        _copy_scripts_into(existing, game_dir, scripts)
        if log:
            log(f"使用已解包脚本 {len(existing)} 个")
    else:
        raise RuntimeError("未找到 data*.arc / Buriko 脚本。请确认 BGI/Ethornell 游戏目录。")

    found = find_bgi_scripts(scripts)
    if not found:
        raise RuntimeError("解包后未识别到 BGI 剧情脚本（BurikoCompiledScript / 无扩展名场景文件）")
    if log:
        log(f"准备脚本 {len(found)} 个")
    return scripts


def _deploy(game_dir: Path, patched_root: Path, log: LogFn) -> None:
    n = 0
    for p in patched_root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(patched_root)
        # try mirror into game
        candidates = [
            game_dir / rel,
            game_dir / rel.name,
        ]
        # if came from data010/xxx strip first part
        if len(rel.parts) > 1:
            candidates.insert(0, game_dir / Path(*rel.parts[1:]))
        for c in candidates:
            # only overwrite if original-ish path exists or name match in tree
            if c.parent.exists() or c.name == rel.name:
                c.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, c)
                n += 1
                break
    # also drop a flat patched folder for manual copy
    flat = game_dir / "cn_bgi_scripts"
    if flat.exists():
        shutil.rmtree(flat)
    shutil.copytree(patched_root, flat)
    readme = game_dir / "汉化启动说明_BGI.txt"
    readme.write_text(
        "GalAutoTL BGI/Ethornell 汉化说明\n"
        "================================\n"
        "1. 已翻译剧情脚本；尽量写回游戏目录，并保留完整副本 cn_bgi_scripts\\\n"
        "2. 若游戏仍读封包：用 GARbro/原包装工具把 cn_bgi_scripts 回封进 data*.arc\n"
        "3. 中文可能需 GBK/字体处理；._bp 系统脚本未改\n"
        "4. 备份在桌面「自动翻译备份\\bgi_游戏名」\n"
        "5. 工作目录: 游戏目录\\_galautotl_bgi\\\n",
        encoding="utf-8",
    )
    if log:
        log(f"部署约 {n} 处 + cn_bgi_scripts\\ → {readme.name}")


def run_bgi(
    cfg: AppConfig,
    log: LogFn = None,
    progress: ProgressFn = None,
    should_cancel: CancelFn = None,
) -> None:
    game_dir = Path(cfg.game_dir or cfg.text_dir)
    if not game_dir.is_dir():
        raise FileNotFoundError(f"目录无效: {game_dir}")

    work = game_dir / "_galautotl_bgi"
    work.mkdir(parents=True, exist_ok=True)
    arcs = _find_arcs(game_dir)
    if arcs and cfg.do_backup:
        _backup(game_dir, arcs, log)

    rfilt = remain_filter_set(cfg)
    text_dir = Path(cfg.text_dir) if cfg.text_dir.strip() else None
    tools = getattr(cfg, "tools_dir", "") or ""

    if rfilt is not None:
        scripts = _prepare(game_dir, work, log, tools, remain_only=True)
    elif arcs:
        if log and text_dir and find_bgi_scripts(text_dir):
            log("全量汉化: 从 arc/备份取日文源（防二次冲盘）")
        scripts = _prepare(game_dir, work, log, tools, remain_only=False)
    elif text_dir and text_dir.is_dir() and find_bgi_scripts(text_dir):
        scripts = work / "scripts"
        if scripts.exists():
            shutil.rmtree(scripts)
        shutil.copytree(text_dir, scripts, ignore=shutil.ignore_patterns("_galautotl_*"))
        if log:
            log(f"使用文本文件夹: {text_dir}")
    else:
        scripts = _prepare(game_dir, work, log, tools, remain_only=False)

    all_units = []
    file_units = {}
    for sp in find_bgi_scripts(scripts):
        units = collect_bgi_units(sp)
        if units:
            file_units[sp] = units
            all_units.extend(units)
    if not all_units:
        raise RuntimeError("未从 BGI 脚本提取到对白（版本/边界不匹配时可先手动解包再试）")
    if log:
        log(f"待翻译条目: {len(all_units)}（{len(file_units)} 文件）")

    client = OpenAICompatClient(cfg.api_base, cfg.api_key, cfg.api_model, cfg.temperature)
    cache = TranslateCache(cache_db_path())
    source_lang = getattr(cfg, "source_lang", "ja") or "ja"
    try:
        # BGI text pool encodes CP932 first — force CP932-safe AI output
        if log:
            log("BGI: translate_codec=cp932（与脚本池编码一致）")
        sources = [u.source for u in all_units]
        mapping = translate_to_mapping(
            sources,
            client,
            cfg.lang,
            codec=CODEC_CP932,
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
                codec=CODEC_CP932,
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
            "bgi",
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

    from collections import defaultdict

    by_file = defaultdict(list)
    for u, t in zip(all_units, translated):
        by_file[u.path].append((u, t))

    patched = work / "patched"
    if patched.exists():
        shutil.rmtree(patched)
    patched.mkdir(parents=True)
    for path, pairs in by_file.items():
        units = [u for u, _ in pairs]
        texts = [t for _, t in pairs]
        try:
            new_bytes = apply_bgi_units(path, units, texts)
        except Exception as e:
            if log:
                log(f"回写失败 {path.name}: {e}")
            continue
        rel = path.relative_to(scripts)
        dest = patched / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(new_bytes)
        if log:
            log(f"已写: {rel}")

    _deploy(game_dir, patched, log)
    if log:
        log("BGI/Ethornell 管线完成")
