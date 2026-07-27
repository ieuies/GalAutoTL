# -*- coding: utf-8 -*-
"""SakanaGL one-click: extract .sxstorage → AI translate → size-preserving patch."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable, Dict, List, Optional

from app.config import AppConfig, cache_db_path
from app.core.api_client import OpenAICompatClient
from app.core.ensure_deps import ensure_package
from app.core.sakana_sx import (
    extract_sakana_pkg,
    find_sakana_pkg,
    open_sakana_pkg,
    write_entry,
)
from app.core.sakana_text import apply_sakana_units, collect_sakana_units
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


def _bak_dir(game_dir: Path) -> Path:
    return Path.home() / "Desktop" / "自动翻译备份" / f"sakana_{game_dir.name}"


def _backup_pkg(game_dir: Path, pkg: Path, log: LogFn) -> None:
    dest = _bak_dir(game_dir)
    dest.mkdir(parents=True, exist_ok=True)
    for p in list(pkg.glob("*.sx")) + list(pkg.glob("*.sxstorage")) + list(pkg.glob("*.json")):
        if p.is_file() and not (dest / p.name).exists():
            shutil.copy2(p, dest / p.name)
            if log:
                log(f"备份: {dest / p.name}")


def _entry_map(arc) -> Dict[str, object]:
    return {e.name.replace("\\", "/"): e for e in arc.entries}


def run_sakana(
    cfg: AppConfig,
    log: LogFn = None,
    progress: ProgressFn = None,
    should_cancel: CancelFn = None,
) -> None:
    game_dir = Path(cfg.game_dir or cfg.text_dir)
    if not game_dir.is_dir():
        raise FileNotFoundError(f"目录无效: {game_dir}")

    # zstandard required
    try:
        import zstandard  # noqa: F401
    except ImportError:
        if not ensure_package("zstandard", "zstandard", log=log):
            raise RuntimeError("缺少 zstandard，请执行: pip install zstandard")
        import zstandard  # noqa: F401

    pkg = find_sakana_pkg(game_dir)
    if not pkg:
        raise RuntimeError("未找到 SakanaGL 封包（pkg/*.sx + *.sxstorage）")

    if cfg.do_backup:
        _backup_pkg(game_dir, pkg, log)

    rfilt = remain_filter_set(cfg)
    bak = _bak_dir(game_dir)
    # Full re-run: extract JP from first-run backup; 仅译漏句: patch current (CN) pkg
    extract_pkg = pkg
    write_pkg = pkg
    if rfilt is None and bak.is_dir():
        bak_pkg = find_sakana_pkg(bak)
        if bak_pkg:
            extract_pkg = bak_pkg
            if log:
                log(f"从备份解包 Sakana（防二次冲盘）: {bak}")
    elif rfilt is not None and log:
        log("仅译漏句: 从当前封包解包（保留已有汉化）")

    work = game_dir / "_galautotl_sakana"
    if work.exists():
        shutil.rmtree(work)
    scripts = work / "extract"
    scripts.mkdir(parents=True)

    if log:
        log(f"SakanaGL 解包: {extract_pkg}")
    n, arc = extract_sakana_pkg(extract_pkg, scripts, only_text=True)
    if log:
        log(f"解出文本相关文件 {n} 个（共索引 {len(arc.entries)} 项）")
    if n == 0:
        # retry all files then filter
        n, arc = extract_sakana_pkg(extract_pkg, scripts, only_text=False)
        if log:
            log(f"全量解出 {n} 个，再筛对白")

    source_lang = getattr(cfg, "source_lang", "ja") or "ja"
    units = collect_sakana_units(scripts, source_lang=source_lang)
    if not units:
        raise RuntimeError(
            "解包成功但未找到可翻译日文台词。\n"
            "可打开 _galautotl_sakana/extract 查看文件；若剧本是专用二进制，需再加强解析。"
        )
    if log:
        log(f"待翻译条目: {len(units)}")

    # manifest for writeback (always the live game pkg)
    write_arc = arc
    if Path(extract_pkg).resolve() != Path(write_pkg).resolve():
        write_arc = open_sakana_pkg(write_pkg)
        if log:
            log("回封目标: 游戏目录当前封包（译文来自备份日文源+缓存）")
    manifest = {
        "pkg": str(write_pkg),
        "extract_pkg": str(extract_pkg),
        "entries": [
            {
                "name": e.name,
                "arc_index": e.arc_index,
                "offset": e.offset,
                "size": e.size,
                "flags": e.flags,
            }
            for e in write_arc.entries
        ],
    }
    (work / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    client = OpenAICompatClient(cfg.api_base, cfg.api_key, cfg.api_model, cfg.temperature)
    cache = TranslateCache(cache_db_path())
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
            game_dir=str(game_dir),
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
                game_dir=str(game_dir),
                do_polish=getattr(cfg, "mt_polish", True),
                remain_filter=remain_filter_set(cfg),
            )
        write_remainder_report(
            game_dir,
            "sakana",
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

    nfiles = apply_sakana_units(scripts, units, translated)
    if log:
        log(f"已写回解包文件 {nfiles} 个")

    # patch storages (size-preserving) — always write live game pkg
    emap = _entry_map(write_arc)
    ok = fail = 0
    changed_rels = {u.rel for u in units}
    for rel in sorted(changed_rels):
        # entry names may use backslash
        e = emap.get(rel) or emap.get(rel.replace("/", "\\"))
        if e is None:
            # try match by suffix path
            for name, ent in emap.items():
                if name.replace("\\", "/").endswith(rel) or name.replace("\\", "/") == rel:
                    e = ent
                    break
        if e is None:
            fail += 1
            continue
        raw = (scripts / rel).read_bytes()
        try:
            write_entry(write_arc, e, raw)
            ok += 1
        except Exception as ex:
            fail += 1
            if log and fail <= 8:
                log(f"回封跳过 {rel}: {ex}")

    readme = game_dir / "汉化启动说明_SakanaGL.txt"
    readme.write_text(
        "GalAutoTL SakanaGL 汉化说明\n"
        "==========================\n"
        "1. 已备份 pkg 内 .sx/.sxstorage 到桌面「自动翻译备份\\sakana_游戏名」\n"
        "2. 解包 → AI 译文本 → 等长/垫零写回 .sxstorage（槽位夹紧）\n"
        "3. 工作目录: _galautotl_sakana\\\n"
        "4. 直接运行游戏 exe 验证；若某句过长会跳过回封并保留日文\n"
        f"5. 本次成功回封 {ok} 个文件，失败/跳过 {fail}\n",
        encoding="utf-8",
    )
    if log:
        log(f"回封完成: 成功 {ok}，跳过 {fail} → {readme.name}")
        log("SakanaGL 管线完成")
