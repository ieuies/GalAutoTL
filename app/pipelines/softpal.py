# -*- coding: utf-8 -*-
"""Classic SoftPal one-click (tutorial-aligned).

Flow: data.pac → SCRIPT.SRC + TEXT.DAT → AI → write loose data\\ override
(engine prefers data\\ over data.pac; no forced full PAC rebuild).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Optional

from app.config import AppConfig, cache_db_path
from app.core.api_client import OpenAICompatClient
from app.core.pipeline_harden import (
    softpal_codecs_for_lang,
    translate_to_mapping,
    remain_filter_set,
    run_second_pass,
    second_pass_sources,
    mapping_aligned,
    write_remainder_report,
)
from app.core.softpal_pac import (
    extract_named,
    find_data_pac,
    pac_has_script_pair,
)
from app.core.softpal_script import SoftPalScriptBundle, load_bundle, save_export_json
from app.core.translate import TranslateCache

LogFn = Optional[Callable[[str], None]]
ProgressFn = Optional[Callable[[int, int], None]]
CancelFn = Optional[Callable[[], bool]]


def _backup(game_dir: Path, paths: list[Path], log: LogFn) -> None:
    dest = Path.home() / "Desktop" / "自动翻译备份" / f"softpal_{game_dir.name}"
    dest.mkdir(parents=True, exist_ok=True)
    for p in paths:
        if p.is_file():
            t = dest / p.name
            if not t.exists():
                shutil.copy2(p, t)
                if log:
                    log(f"备份: {t}")


def _find_existing_pair(game_dir: Path) -> Optional[tuple[Path, Path]]:
    candidates = [
        (game_dir / "SCRIPT.SRC", game_dir / "TEXT.DAT"),
        (game_dir / "data" / "SCRIPT.SRC", game_dir / "data" / "TEXT.DAT"),
        (game_dir / "source" / "SCRIPT.SRC", game_dir / "source" / "TEXT.DAT"),
        (game_dir / "source" / "script.src", game_dir / "source" / "text.dat"),
    ]
    for s, t in candidates:
        if s.is_file() and t.is_file():
            return s, t
    # case-insensitive search
    for folder in (game_dir, game_dir / "data", game_dir / "source"):
        if not folder.is_dir():
            continue
        smap = {p.name.upper(): p for p in folder.iterdir() if p.is_file()}
        if "SCRIPT.SRC" in smap and "TEXT.DAT" in smap:
            return smap["SCRIPT.SRC"], smap["TEXT.DAT"]
    return None


def run_softpal(
    cfg: AppConfig,
    log: LogFn = None,
    progress: ProgressFn = None,
    should_cancel: CancelFn = None,
) -> None:
    game_dir = Path(cfg.game_dir or cfg.text_dir or "").expanduser()
    if not game_dir.is_dir():
        raise RuntimeError("请先选择 SoftPal 游戏根目录（含 data.pac 或已解出的 SCRIPT.SRC）")

    work = game_dir / "_galautotl_softpal"
    src_dir = work / "source"
    if src_dir.exists():
        shutil.rmtree(src_dir)
    src_dir.mkdir(parents=True)

    script_path: Optional[Path] = None
    text_path: Optional[Path] = None
    pac = find_data_pac(game_dir)

    existing = _find_existing_pair(game_dir)
    rfilt = remain_filter_set(cfg)
    # 仅译漏句：优先已有松散 CN，避免从 data.pac 日文重建冲掉汉化
    if rfilt is not None and existing:
        s, t = existing
        script_path = src_dir / "SCRIPT.SRC"
        text_path = src_dir / "TEXT.DAT"
        shutil.copy2(s, script_path)
        shutil.copy2(t, text_path)
        if log:
            log(f"仅译漏句: 使用已有脚本 {s.parent.name}/{s.name}（保留汉化）")
    elif pac:
        # Full re-run: prefer PAC (JP) + cache over loose CN (防二次翻坏)
        if log:
            log(f"SoftPal PAC: {pac.name}")
        if getattr(cfg, "do_backup", True):
            _backup(game_dir, [pac], log)
        found = extract_named(
            pac,
            ["SCRIPT.SRC", "TEXT.DAT", "POINT.DAT"],
            src_dir,
            decrypt=True,
        )
        if "SCRIPT.SRC" not in found or "TEXT.DAT" not in found:
            found = extract_named(
                pac,
                ["SCRIPT.SRC", "TEXT.DAT", "POINT.DAT"],
                src_dir,
                decrypt=False,
            )
        if "SCRIPT.SRC" not in found or "TEXT.DAT" not in found:
            if existing:
                s, t = existing
                script_path = src_dir / "SCRIPT.SRC"
                text_path = src_dir / "TEXT.DAT"
                shutil.copy2(s, script_path)
                shutil.copy2(t, text_path)
                if log:
                    log(f"PAC 无脚本对，回退已有: {s.parent.name}/{s.name}")
            else:
                raise RuntimeError(
                    f"{pac.name} 内未找到 SCRIPT.SRC / TEXT.DAT。"
                    "请确认是经典 SoftPal（非 Kagura Lua/.scb）。"
                )
        else:
            script_path = found["SCRIPT.SRC"]
            text_path = found["TEXT.DAT"]
    elif existing:
        s, t = existing
        script_path = src_dir / "SCRIPT.SRC"
        text_path = src_dir / "TEXT.DAT"
        shutil.copy2(s, script_path)
        shutil.copy2(t, text_path)
        if log:
            log(f"使用已有脚本: {s.parent.name}/{s.name}")
    else:
        raise RuntimeError(
            "未找到 data.pac 或 SCRIPT.SRC+TEXT.DAT。\n"
            "经典 SoftPal：用 GARbro 解出后也可把两文件放到游戏目录/data/ 再跑。"
        )

    assert script_path and text_path
    # Try decrypt again if still looks encrypted (high entropy head)
    for p in (script_path, text_path):
        raw = p.read_bytes()
        if raw and raw[0] not in (0, 0x53, 0x54) and len(raw) > 32:
            # SoftPal encrypted often has non-zero flag byte; SoftPal-Tool decrypts always from pac
            pass

    bundle = load_bundle(script_path, text_path)
    if not bundle.refs:
        # maybe need decrypt of already-extracted files
        from app.core.softpal_pac import softpal_decrypt

        script_path.write_bytes(softpal_decrypt(script_path.read_bytes()))
        text_path.write_bytes(softpal_decrypt(text_path.read_bytes()))
        bundle = load_bundle(script_path, text_path)
    if not bundle.refs:
        raise RuntimeError(
            "未能解析 SoftPal 对白指令。可能是加密变种或非 SoftPal 脚本；"
            "可先用 SoftPal-Tool / GARbro 手动确认。"
        )

    texts = bundle.collect_units()
    if not texts:
        raise RuntimeError("未提取到可翻译文本")
    if log:
        log(f"解析对白 {len(bundle.refs)} 条指令 / 去重文本 {len(texts)} 条")

    save_export_json(bundle, work / "script_export_orig.json")

    # zh → GBK write: never CP932-mangle AI output. JP locale → CP932.
    enc, codec = softpal_codecs_for_lang(cfg.lang or "")
    if log:
        log(f"SoftPal 编码策略: write={enc}, translate_codec={codec}")

    client = OpenAICompatClient(
        cfg.api_base, cfg.api_key, cfg.api_model, cfg.temperature
    )
    cache = TranslateCache(cache_db_path())
    try:
        mapping = translate_to_mapping(
            texts,
            client,
            cfg.lang,
            codec=codec,
            cache=cache,
            chunk=cfg.batch_size or 24,
            log=log,
            progress=progress,
            should_cancel=should_cancel,
            source_lang=getattr(cfg, "source_lang", "ja") or "ja",
            game_dir=game_dir,
            do_polish=getattr(cfg, "mt_polish", True),
            label="主译",
            remain_filter=remain_filter_set(cfg),
        )
        if not mapping:
            raise RuntimeError("翻译结果为空，请检查 API / 模型名")

        n = bundle.apply_translations(mapping)
        if log:
            log(f"套用译文 {n} 处")

        remain = second_pass_sources(bundle.collect_units(), mapping, max_n=600, allow=remain_filter_set(cfg))
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
                source_lang=getattr(cfg, "source_lang", "ja") or "ja",
                game_dir=game_dir,
                do_polish=getattr(cfg, "mt_polish", True),
                remain_filter=remain_filter_set(cfg),
            )
            n2 = bundle.apply_translations(mapping)
            if log:
                log(f"二扫再套用 {n2} 处")
        write_remainder_report(
            game_dir, "softpal", bundle.collect_units(), mapping, log=log, allow=remain_filter_set(cfg)
        )
    finally:
        cache.close()

    out_data = game_dir / "data"
    out_data.mkdir(parents=True, exist_ok=True)
    out_script = out_data / "SCRIPT.SRC"
    out_text = out_data / "TEXT.DAT"
    # also keep POINT.DAT if extracted
    point_src = src_dir / "POINT.DAT"
    if point_src.is_file():
        shutil.copy2(point_src, out_data / "POINT.DAT")

    if getattr(cfg, "do_backup", True):
        _backup(game_dir, [script_path, text_path], log)

    bundle.rebuild(out_script, out_text, encoding=enc)
    save_export_json(bundle, work / "script_export.json")

    if log:
        log(f"已写入松散覆盖: {out_script.relative_to(game_dir)}")
        log(f"已写入松散覆盖: {out_text.relative_to(game_dir)}")
        log(
            "完成。经典 SoftPal 会优先读 data\\ 下文件（无需回封 data.pac）。"
            "请用日语区域启动；字体可在设置里选ゴシック/明朝或按教程改 SYSTEM.INI。"
        )

    try:
        from app.core.review_table import export_review_table

        export_review_table(
            game_dir,
            texts,
            [mapping.get(s, s) for s in texts],
            header_note="SoftPal SCRIPT.SRC / TEXT.DAT",
        )
        if log:
            log("对照表: GalAutoTL_review.txt")
    except Exception as ex:
        if log:
            log(f"对照表跳过: {ex}")
