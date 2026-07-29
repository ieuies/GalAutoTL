# -*- coding: utf-8 -*-
"""YU-RIS one-click: gather .ybn (GARbro YPF if needed) → decode YSTB → AI → loose inject."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, List, Optional

from app.config import AppConfig, cache_db_path
from app.core.api_client import OpenAICompatClient
from app.core.garbro_cli import extract_with_garbro, find_garbro
from app.core.pipeline_harden import (
    mapping_aligned,
    remain_filter_set,
    run_second_pass,
    second_pass_sources,
    softpal_codecs_for_lang,
    translate_to_mapping,
    write_remainder_report,
)
from app.core.translate import TranslateCache
from app.core.ystb import (
    YstbError,
    YstbUnit,
    apply_units_to_ystb,
    is_ystb_file,
    process_ybn_collect,
)

LogFn = Optional[Callable[[str], None]]
ProgressFn = Optional[Callable[[int, int], None]]
CancelFn = Optional[Callable[[], bool]]


def _bak_dir(game_dir: Path) -> Path:
    return Path.home() / "Desktop" / "自动翻译备份" / f"yuris_{game_dir.name}"


def _backup(game_dir: Path, paths: List[Path], log: LogFn) -> None:
    dest = _bak_dir(game_dir)
    dest.mkdir(parents=True, exist_ok=True)
    for p in paths:
        if not p.is_file():
            continue
        t = dest / p.name
        if not t.exists():
            shutil.copy2(p, t)
            if log:
                log(f"备份: {t}")


def _find_ybn(root: Path) -> List[Path]:
    skip = {"_galautotl_yuris", "_galautotl_kirikiri", "_galautotl_lcse"}
    out: List[Path] = []
    for p in root.rglob("*.ybn"):
        if any(x in {s.lower() for s in p.parts} for x in skip):
            continue
        # scenario scripts are usually yst#####.ybn
        name = p.name.lower()
        if name.startswith("yst") and name[3:4].isdigit():
            out.append(p)
        elif name.startswith("yst") and "list" not in name:
            out.append(p)
    # if none matched filter, take all YSTB magics
    if not out:
        for p in root.rglob("*.ybn"):
            if is_ystb_file(p):
                out.append(p)
    return sorted(out)


def _find_ypf(root: Path) -> List[Path]:
    ypfs = list(root.glob("*.ypf")) + list(root.glob("pac/*.ypf"))
    ypfs += list((root / "pac").glob("*.ypf")) if (root / "pac").is_dir() else []
    seen = set()
    uniq = []
    for p in ypfs:
        k = str(p.resolve()).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def _ensure_ybn(
    game_dir: Path,
    work: Path,
    log: LogFn,
    tools_dir: str,
    *,
    remain_only: bool = False,
) -> Path:
    """Return directory containing .ybn scripts.

    Full run: prefer YPF from desktop backup / game (JP), not loose CN ybn.
    仅译漏句: prefer existing loose .ybn (already CN).
    """
    scripts = work / "ysbin"
    if scripts.exists():
        shutil.rmtree(scripts)
    scripts.mkdir(parents=True)

    loose = _find_ybn(game_dir)
    ypfs = _find_ypf(game_dir)
    bak = _bak_dir(game_dir)
    bak_ypfs = _find_ypf(bak) if bak.is_dir() else []

    if remain_only and loose:
        if log:
            log(f"仅译漏句: 使用已有 .ybn {len(loose)} 个（保留汉化）")
        for p in loose:
            try:
                rel = p.relative_to(game_dir)
            except ValueError:
                rel = Path(p.name)
            dest = scripts / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
        return scripts

    source_ypfs = bak_ypfs or ypfs
    if source_ypfs:
        if bak_ypfs and log:
            log(f"从备份解包 YPF（防二次冲盘）: {bak}")
        extra = [game_dir, game_dir / "tools", game_dir / "_tools"]
        if tools_dir.strip():
            extra.insert(0, Path(tools_dir.strip()))
        garbro = find_garbro(extra)
        if not garbro:
            if loose and not remain_only:
                if log:
                    log("无 garbro-cli，回退已有 .ybn（可能已是中文，依赖缓存）")
            elif not loose:
                raise RuntimeError(
                    "脚本在 .ypf 里且本机无 garbro-cli。\n"
                    "请安装 garbro-cli 并加入 PATH / 工具目录，或先用 GARbro 解出 ysbin/*.ybn。"
                )
        if garbro:
            for arc in source_ypfs:
                if log:
                    log(f"GARbro 解包: {arc.name}")
                sub = scripts / arc.stem
                extract_with_garbro(arc, sub, garbro, log)
            ybns = list(scripts.rglob("*.ybn"))
            if ybns:
                if log:
                    log(f"解出 .ybn {len(ybns)} 个")
                return scripts

    if loose:
        if log:
            log(f"发现明文 .ybn {len(loose)} 个")
        for p in loose:
            try:
                rel = p.relative_to(game_dir)
            except ValueError:
                rel = Path(p.name)
            dest = scripts / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
        return scripts

    raise RuntimeError("未找到 .ybn 或 .ypf。请把游戏根目录指到 YU-RIS 文件夹。")


def _deploy(game_dir: Path, patched: Path, log: LogFn) -> None:
    """Copy patched ybn next to originals / under ysbin for loose override."""
    # Prefer mirroring relative path under game_dir/ysbin and game_dir/
    targets = 0
    for p in patched.rglob("*.ybn"):
        rel = p.relative_to(patched)
        # drop leading archive stem if present and game has ysbin/
        candidates = [
            game_dir / "ysbin" / rel.name,
            game_dir / rel,
            game_dir / "ysbin" / rel,
        ]
        # also flat ysbin
        for c in candidates:
            c.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, c)
            targets += 1
            break
    readme = game_dir / "汉化启动说明_YURIS.txt"
    readme.write_text(
        "GalAutoTL YU-RIS 汉化说明\n"
        "========================\n"
        "1. 已解码 YSTB、翻译对白，并以松散 .ybn 覆盖（引擎优先读磁盘文件）\n"
        "2. 原版备份在桌面「自动翻译备份\\yuris_游戏名」\n"
        "3. 中文按 GBK 写入（CP932 缺「你」等字）。若游戏仍乱码/无字，需另打 YU-RIS GBK 引擎补丁\n"
        "4. 工作目录: 游戏目录\\_galautotl_yuris\\\n",
        encoding="utf-8",
    )
    if log:
        log(f"已部署松散脚本（约 {targets} 处写入），见 {readme.name}")


def run_yuris(
    cfg: AppConfig,
    log: LogFn = None,
    progress: ProgressFn = None,
    should_cancel: CancelFn = None,
) -> None:
    game_dir = Path(cfg.game_dir or cfg.text_dir)
    if not game_dir.is_dir():
        raise FileNotFoundError(f"目录无效: {game_dir}")

    work = game_dir / "_galautotl_yuris"
    work.mkdir(parents=True, exist_ok=True)

    ypfs = _find_ypf(game_dir)
    if ypfs and cfg.do_backup:
        _backup(game_dir, ypfs, log)

    rfilt = remain_filter_set(cfg)
    text_dir = Path(cfg.text_dir) if cfg.text_dir.strip() else None
    tools = getattr(cfg, "tools_dir", "") or ""

    if rfilt is not None:
        scripts = _ensure_ybn(game_dir, work, log, tools, remain_only=True)
    elif ypfs or (_bak_dir(game_dir).is_dir() and _find_ypf(_bak_dir(game_dir))):
        if log and text_dir and list(text_dir.rglob("*.ybn")):
            log("全量汉化: 从 YPF/备份取日文源（防二次冲盘）")
        scripts = _ensure_ybn(game_dir, work, log, tools, remain_only=False)
    elif text_dir and text_dir.is_dir() and list(text_dir.rglob("*.ybn")):
        scripts = work / "ysbin"
        if scripts.exists():
            shutil.rmtree(scripts)
        shutil.copytree(text_dir, scripts, ignore=shutil.ignore_patterns("_galautotl_*"))
        if log:
            log(f"使用文本文件夹 .ybn: {text_dir}")
    else:
        scripts = _ensure_ybn(game_dir, work, log, tools, remain_only=False)

    # collect
    all_units: List[YstbUnit] = []
    cache: dict[Path, tuple] = {}
    for ybn in sorted(scripts.rglob("*.ybn")):
        if not is_ystb_file(ybn):
            continue
        try:
            units, data, key, meta = process_ybn_collect(ybn)
        except YstbError as e:
            if log:
                log(f"跳过 {ybn.name}: {e}")
            continue
        if units:
            cache[ybn] = (data, key, meta, units)
            all_units.extend(units)

    if not all_units:
        raise RuntimeError("未从 YSTB 提取到对白（密钥/版本不兼容或脚本为空）")

    if log:
        log(f"待翻译条目: {len(all_units)}（来自 {len(cache)} 个 ybn）")

    client = OpenAICompatClient(cfg.api_base, cfg.api_key, cfg.api_model, cfg.temperature)
    tcache = TranslateCache(cache_db_path())
    source_lang = getattr(cfg, "source_lang", "ja") or "ja"
    # zh → GBK sanitize (keep 简体); non-zh → CP932. Write still tries CP932 then GBK in ystb.
    _enc, codec = softpal_codecs_for_lang(cfg.lang or "")
    try:
        if log:
            log(f"YU-RIS: translate_codec={codec}（{_enc} 槽；简体勿走 CP932 消毒）")
        sources = [u.source for u in all_units]
        mapping = translate_to_mapping(
            sources,
            client,
            cfg.lang,
            codec=codec,
            cache=tcache,
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
                cache=tcache,
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
            "yuris",
            sources,
            mapping,
            log=log,
            allow=remain_filter_set(cfg),
        )
        translated = mapping_aligned(sources, mapping)
    finally:
        tcache.close()

    if should_cancel and should_cancel():
        return

    # apply per file
    from collections import defaultdict

    by_file: dict[Path, List[tuple[YstbUnit, str]]] = defaultdict(list)
    for u, t in zip(all_units, translated):
        by_file[u.path].append((u, t))

    out_root = work / "patched"
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True)

    for path, pairs in by_file.items():
        data, key, meta, units = cache[path]
        # keep unit order as in collect
        unit_list = [u for u, _ in pairs]
        trans_list = [t for _, t in pairs]
        try:
            new_bytes = apply_units_to_ystb(data, meta, key, unit_list, trans_list)
        except Exception as e:
            if log:
                log(f"回写失败 {path.name}: {e}")
            continue
        rel = path.relative_to(scripts)
        dest = out_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(new_bytes)
        if log:
            log(f"已写: {rel}")

    _deploy(game_dir, out_root, log)
    if log:
        log("YU-RIS 管线完成")
