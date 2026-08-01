# -*- coding: utf-8 -*-
"""Kirikiri (XP3 / .ks) one-click localize.

Safe flow (洗脳航路 + FREAKSTRIKE lessons):
  backup XP3 → extract plaintext .ks (skip protected stubs; ENC-bit may be fake) →
  AI translate dialogue only (scenario/ / k_scenario/; never script/k_others/macros) →
  UTF-16-LE → deploy patch2 + loose dialogue folders (never rewrite data.xp3).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, List, Optional

from app.config import AppConfig, cache_db_path
from app.core.api_client import OpenAICompatClient
from app.core.garbro_cli import extract_with_garbro, find_garbro
from app.core.ks_descramble import descramble_tree
from app.core.ks_script import apply_ks_units, collect_ks_units
from app.core.kirikiri_patch import (
    count_plain_ks,
    deploy_unencrypted_overrides,
    ensure_kirikiri_tools,
    find_plaintext_source,
    force_tree_utf16_le,
    looks_like_ks_script,
    normalize_kag_relpath,
    stage_normalized_tree,
)
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
from app.core.xp3_io import (
    XP3Error,
    extract_xp3_try_schemes,
    find_xp3_archives,
    list_xp3,
    pack_xp3,
)

LogFn = Optional[Callable[[str], None]]
ProgressFn = Optional[Callable[[int, int], None]]
CancelFn = Optional[Callable[[], bool]]


def _backup(game_dir: Path, paths: List[Path], log: LogFn) -> Path:
    dest_root = Path.home() / "Desktop" / "自动翻译备份" / f"kirikiri_{game_dir.name}"
    dest_root.mkdir(parents=True, exist_ok=True)
    for p in paths:
        if not p.is_file():
            continue
        target = dest_root / p.name
        if not target.exists():
            shutil.copy2(p, target)
            if log:
                log(f"备份: {target}")
        elif log:
            log(f"备份已存在，保留首次原版: {target.name}")
    return dest_root


def _find_loose_ks(game_dir: Path) -> List[Path]:
    skip = {"_galautotl_kirikiri", "_galautotl_lcse", "cn_scenario", "unencrypted"}
    out: List[Path] = []
    for p in game_dir.rglob("*.ks"):
        if not p.is_file():
            continue
        if any(part.lower() in skip for part in p.parts):
            continue
        out.append(p)
    return out


def _archive_has_ks(xp3: Path) -> bool:
    try:
        for e in list_xp3(xp3):
            if e.path.lower().endswith(".ks"):
                return True
    except Exception:
        return False
    return False


def _postprocess_scripts(scripts: Path, log: LogFn) -> None:
    n = descramble_tree(scripts)
    if n and log:
        log(f"已自动还原 FE FE 混淆脚本: {n} 个")


def _copy_plain_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for p in src.rglob("*.ks"):
        if not p.is_file():
            continue
        if not looks_like_ks_script(p.read_bytes()):
            continue
        rel = normalize_kag_relpath(p.relative_to(src))
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, out)


def _extract_scripts(
    game_dir: Path, work: Path, log: LogFn, tools_dir: str = ""
) -> Path:
    """Return directory containing plaintext .ks ready to translate."""
    scripts = work / "scripts"
    if scripts.exists():
        shutil.rmtree(scripts)
    scripts.mkdir(parents=True)

    loose = _find_loose_ks(game_dir)
    plain_loose = [p for p in loose if looks_like_ks_script(p.read_bytes())]
    # Deployed CN scenario/ must NOT become the JP source on a second full run
    if plain_loose:
        from app.core.kirikiri_patch import ks_tree_looks_already_chinese

        probe = work / "_loose_probe"
        if probe.exists():
            shutil.rmtree(probe)
        probe.mkdir(parents=True)
        for p in plain_loose[:40]:
            try:
                rel = normalize_kag_relpath(p.relative_to(game_dir))
            except Exception:
                rel = Path(p.name)
            dest = probe / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
        archives = find_xp3_archives(game_dir)
        unenc = game_dir / "unencrypted"
        can_get_jp = bool(archives) or (
            unenc.is_dir() and not ks_tree_looks_already_chinese(unenc)
        )
        if can_get_jp and ks_tree_looks_already_chinese(probe):
            if log:
                log("检测到目录内已是中文剧本，改从 XP3/unencrypted 取日文源（防二次翻坏）")
            shutil.rmtree(probe, ignore_errors=True)
            plain_loose = []
        else:
            shutil.rmtree(probe, ignore_errors=True)

    if plain_loose:
        if log:
            log(f"发现明文 .ks {len(plain_loose)} 个，直接使用")
        for p in plain_loose:
            rel = normalize_kag_relpath(p.relative_to(game_dir))
            dest = scripts / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
        _postprocess_scripts(scripts, log)
        return scripts
    if loose and log:
        log(f"目录有 {len(loose)} 个 .ks 但均为密文，改从 XP3 / unencrypted 解包…")

    archives = find_xp3_archives(game_dir)
    if not archives:
        raise RuntimeError("未找到 .xp3 或明文 .ks。请确认游戏根目录是否正确。")

    ks_archives = [a for a in archives if _archive_has_ks(a)]
    targets = ks_archives or archives
    extracted_any = False
    enc_fail: List[str] = []
    extra = [
        Path(game_dir),
        Path(game_dir) / "tools",
        Path(game_dir) / "_tools",
    ]
    if tools_dir.strip():
        extra.insert(0, Path(tools_dir.strip()))
    garbro = find_garbro(extra)
    if garbro and log:
        log(f"检测到外部解包器: {garbro}")
    else:
        # 内置解不开的封包才需要 GARbro；先标记，遇到失败再自动下载
        garbro = None

    for arc in targets:
        name_l = arc.name.lower()
        # 跳过汉化产物封包：patch*.xp3 与 unencrypted.xp3（后者是 version.dll
        # 导出的部分明文，非完整源；解它会把完整解包结果污染成残缺）
        if "patch" in name_l or "unencrypted" in name_l:
            continue
        if log:
            log(f"尝试解包: {arc.name}")
        # Preserve internal XP3 paths (scenario/…) — do NOT nest under archive stem.
        try:
            n, mode = extract_xp3_try_schemes(
                arc, scripts, only_suffixes={".ks", ".tjs", ".csv", ".txt"}
            )
            if log:
                log(f"  内置解包: {n} 文件，模式={mode}")
            if n:
                extracted_any = True
                continue
        except XP3Error as e:
            if log:
                log(f"  内置解包失败: {e}")

        # 内置解不开且尚无 GARbro → 自动下载官方版（MIT 许可，可自动分发）
        if not garbro:
            from app.core.garbro_cli import ensure_garbro

            garbro = ensure_garbro(log)
            if garbro and log:
                log(f"已自动获取 GARbro: {garbro}")

        if garbro:
            sub = work / "_garbro" / arc.stem
            if sub.exists():
                shutil.rmtree(sub)
            ok = extract_with_garbro(arc, sub, garbro, log)
            if ok:
                for p in sub.rglob("*.ks"):
                    if not p.is_file():
                        continue
                    rel = normalize_kag_relpath(p.relative_to(sub))
                    dest = scripts / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, dest)
                if list(scripts.rglob("*.ks")):
                    extracted_any = True
                    continue
        enc_fail.append(arc.name)

    _postprocess_scripts(scripts, log)
    ks_now = list(scripts.rglob("*.ks"))
    if not ks_now:
        tip = (
            "自动解包未拿到 .ks。\n"
            "已尝试：明文 XP3 / 常见 XOR（Neko 系）/ FE FE 还原"
            + (" / GARbro" if garbro else "（未找到 garbro-cli）")
            + "。\n"
            "厂商 cxdec 等专用密钥尚未内置：请安装 garbro-cli 并加入 PATH，"
            "或把 GARbro 放进游戏目录 tools\\，或手动解出 .ks 填到「文本文件夹」。\n"
        )
        if enc_fail:
            tip += "涉及: " + ", ".join(enc_fail)
        raise RuntimeError(tip)

    from app.core.kirikiri_patch import count_deployable_ks

    n_plain, n_total = count_deployable_ks(scripts)
    # 部分明文（如 cxdec 内容层只解出一部分）：解出文件但内容是乱码
    # → 需要 GARbro 解内容层。若本机没有 GARbro，自动下载官方版重解。
    if n_plain > 0 and n_plain < n_total and n_total >= 3:
        if log:
            log(
                f"剧本内容层仅明文 {n_plain}/{n_total}（其余为 cxdec 密文乱码），"
                "需要 GARbro 解内容层…"
            )
        from app.core.garbro_cli import ensure_garbro

        garbro = ensure_garbro(log)
        if garbro:
            if log:
                log(f"用 GARbro 重解 data.xp3 内容层: {garbro}")
            # 用 GARbro 解所有封包，覆盖明文剧本
            for arc in find_xp3_archives(game_dir):
                name_l = arc.name.lower()
                if "patch" in name_l or "unencrypted" in name_l:
                    continue
                sub = work / "_garbro_full" / arc.stem
                if sub.exists():
                    shutil.rmtree(sub)
                ok = extract_with_garbro(arc, sub, garbro, log)
                if ok:
                    for p in sub.rglob("*.ks"):
                        if not p.is_file():
                            continue
                        rel = normalize_kag_relpath(p.relative_to(sub))
                        dest = scripts / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(p, dest)
            n_plain2, n_total2 = count_plain_ks(scripts)
            if log:
                log(f"GARbro 重解后明文: {n_plain2}/{n_total2}")

    n_plain, n_total = count_plain_ks(scripts)
    if n_plain == 0:
        ensure_kirikiri_tools(game_dir, log, create_extract_marker=False)
        raise RuntimeError(
            "XP3 已解出但 .ks 正文仍是厂商加密（cxdec 等）。\n"
            "请按下列步骤导出明文后再汉化：\n"
            "  1) 确认游戏目录有 version.dll 与 extract-unencrypted.txt\n"
            "  2) 运行 sennokoro.exe，进标题/菜单点几下再退出\n"
            "  3) 检查是否生成 unencrypted/ 文件夹（内有可读 .ks）\n"
            "  4) GalAutoTL「文本文件夹」指向 unencrypted/，再点一键汉化\n"
            "若无 unencrypted/，需 GARbro 解 data.xp3 后指定解包目录。"
        )

    if log:
        log(
            f"共准备明文 .ks {n_plain}/{n_total} 个"
            + ("（含自动解包）" if extracted_any else "")
        )
    return scripts


def _require_plaintext(units_count: int, scripts: Path, log: LogFn) -> None:
    n_plain, n_total = count_plain_ks(scripts)
    if units_count < 5 and n_plain < max(1, n_total // 10):
        raise RuntimeError(
            f"仅提取到 {units_count} 条台词，且 {n_plain}/{n_total} 个 .ks 像明文。"
            "请勿把「文本文件夹」指到仍含密文的目录；改用 unencrypted/ 或 GARbro 解包结果。"
        )
    if log and n_plain < n_total:
        log(f"警告: {n_total - n_plain} 个 .ks 仍为密文，已跳过")


def _deploy_to_game(game_dir: Path, patch_root: Path, log: LogFn) -> None:
    """Safe deploy: never rewrite data.xp3 / never stub root Config.tjs / never disable sigs."""
    from app.core.kirikiri_patch import (
        build_after_init2_loader,
        deploy_loose_kag_folders,
        dialogue_top_folders,
        is_dialogue_ks_relpath,
    )

    ensure_kirikiri_tools(game_dir, log, create_extract_marker=False)

    # NEVER write a stub Config.tjs at game root — it shadows system/Config.tjs and breaks boot
    bad_cfg = game_dir / "Config.tjs"
    if bad_cfg.is_file():
        raw = bad_cfg.read_bytes()
        probe = (
            raw[2:].decode("utf-16-le", errors="ignore")
            if raw[:2] == b"\xff\xfe"
            else raw.decode("utf-8", errors="ignore")
        )
        if "GalAutoTL" in probe and "config_version" not in probe:
            bad_cfg.rename(game_dir / "Config.tjs.galautotl_bak")
            if log:
                log("已移除错误的根目录 Config.tjs（会遮蔽原版配置导致无法启动）")

    tops = dialogue_top_folders(patch_root)

    # 1) Loose dialogue folders
    n_loose = deploy_loose_kag_folders(game_dir, patch_root, log)
    if log:
        log(f"免封包覆盖: {n_loose} 个 .ks")

    # 2) unencrypted/（version.dll）
    deploy_unencrypted_overrides(game_dir, patch_root, log)

    # 3) cn_scenario 备份树
    loose = game_dir / "cn_scenario"
    if loose.exists():
        shutil.rmtree(loose)
    shutil.copytree(patch_root, loose)

    # 4) patch2.xp3 + unencrypted.xp3 — dialogue trees only
    pack_tmp = game_dir / "_galautotl_kirikiri" / "_pack_scenario"
    if pack_tmp.exists():
        shutil.rmtree(pack_tmp)
    pack_tmp.mkdir(parents=True)
    for p in patch_root.rglob("*.ks"):
        rel = p.relative_to(patch_root)
        if not is_dialogue_ks_relpath(rel):
            continue
        dest = pack_tmp / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)
    for name in ("patch2.xp3", "unencrypted.xp3"):
        try:
            n = pack_xp3(pack_tmp, game_dir / name, zero_adler=True)
            if log:
                log(f"已生成 {name}（{n} 文件，剧本目录）")
        except XP3Error as e:
            if log:
                log(f"打包 {name} 失败: {e}")
    old = game_dir / "patch.xp3"
    if old.is_file():
        try:
            from app.core.xp3_io import list_xp3 as _list

            ents = _list(old)
            if ents and all(e.adler32 == 0 for e in ents[: min(5, len(ents))]):
                old.unlink()
                if log:
                    log("已移除无效的 patch.xp3")
        except Exception:
            pass

    # 5) AfterInit2 — dialogue folders only
    after = game_dir / "AfterInit2.tjs"
    after.write_bytes(
        b"\xff\xfe" + build_after_init2_loader(tops or ["scenario"]).encode("utf-16-le")
    )
    if log:
        log(f"已写入 AfterInit2.tjs（{', '.join(tops) or 'scenario'}）")

    # Do NOT rewrite data.xp3 / Do NOT disable *.sig / sigcheck.dll

    marker = game_dir / "extract-unencrypted.txt"
    if marker.is_file():
        marker.unlink()

    readme = game_dir / "汉化启动说明_Kirikiri.txt"
    readme.write_text(
        "GalAutoTL Kirikiri 汉化说明\n"
        "==========================\n"
        "1. 原版 data.xp3 未改动（不要改写/注入 data.xp3，也不要删 *.sig）\n"
        "2. 中文在免封包剧本目录、unencrypted/、patch2.xp3、unencrypted.xp3\n"
        "   （不覆盖 script/system/config，避免 [iscript] 被译坏导致无法启动）\n"
        "3. 保留 version.dll；日文游戏请用 Locale Emulator / 系统区域=日语 启动\n"
        "4. 若无法启动: 删除 AfterInit2.tjs、剧本免封包目录、unencrypted、\n"
        "   patch2.xp3、unencrypted.xp3；若有根目录 Config.tjs 也删掉\n"
        "5. 中途崩/乱码: 勿把密文 .ks 放进免封包目录；中文脚本须 UTF-16-LE\n"
        "6. 原版备份: 桌面\\自动翻译备份\\kirikiri_游戏名\\\n",
        encoding="utf-8",
    )
    if log:
        log(f"说明: {readme.name}")


def run_kirikiri(
    cfg: AppConfig,
    log: LogFn = None,
    progress: ProgressFn = None,
    should_cancel: CancelFn = None,
) -> None:
    game_dir = Path(cfg.game_dir or cfg.text_dir)
    if not game_dir.is_dir():
        raise FileNotFoundError(f"游戏目录无效: {game_dir}")

    from app.core.kirikiri_patch import warn_if_bad_game_path

    warn_if_bad_game_path(game_dir, log)

    text_dir = Path(cfg.text_dir) if cfg.text_dir.strip() else None
    if text_dir and text_dir.resolve() == game_dir.resolve():
        text_dir = None  # avoid re-using encrypted loose .ks at game root

    work = game_dir / "_galautotl_kirikiri"
    work.mkdir(parents=True, exist_ok=True)

    archives = find_xp3_archives(game_dir)
    if archives and cfg.do_backup:
        _backup(game_dir, archives, log)

    ensure_kirikiri_tools(game_dir, log, create_extract_marker=False)

    src = find_plaintext_source(game_dir, text_dir)
    scripts = work / "scripts"
    if src:
        if log:
            n_plain, n_total = count_plain_ks(src)
            log(f"使用明文脚本目录: {src} ({n_plain}/{n_total} 个可读 .ks)")
        if scripts.exists():
            shutil.rmtree(scripts)
        _copy_plain_tree(src, scripts)
        _postprocess_scripts(scripts, log)
    else:
        scripts = _extract_scripts(game_dir, work, log, getattr(cfg, "tools_dir", "") or "")

    if should_cancel and should_cancel():
        return

    source_lang = getattr(cfg, "source_lang", "ja") or "ja"
    units = collect_ks_units(scripts, source_lang=source_lang)
    if not units:
        raise RuntimeError("未从 .ks 中提取到可翻译台词（可尝试源语言=自动）")

    from app.core.scenario_sidecar import collect_scenario_sidecars
    from app.pipelines.generic_text import apply_translations as apply_sidecar

    side_items = collect_scenario_sidecars(scripts, source_lang=source_lang)
    if side_items and log:
        log(f"scenario 旁路文本: {len(side_items)} 条（txt/csv/tsv）")

    _require_plaintext(len(units), scripts, log)

    if log:
        log(f"待翻译条目: {len(units)}" + (f" + sidecar {len(side_items)}" if side_items else ""))
        log("Kirikiri: UTF-16 对白层（禁 CP932；不译 macro/engine/.tjs）")

    rfilt = remain_filter_set(cfg)
    client = OpenAICompatClient(cfg.api_base, cfg.api_key, cfg.api_model, cfg.temperature)
    cache = TranslateCache(cache_db_path())
    try:
        sources = [u.source for u in units] + [it.source for it in side_items]
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
            remain_filter=rfilt,
        )
        if should_cancel and should_cancel():
            return
        if not mapping and not rfilt:
            raise RuntimeError("翻译结果为空（可能 API/模型失败）。请检查密钥与模型名。")
        if not mapping and rfilt:
            raise RuntimeError(
                "仅译漏句：remain 中的句子未命中本局收集结果。可先完整汉化，或检查 GalAutoTL_remain.txt。"
            )
        nfiles = apply_ks_units(units, mapping_aligned([u.source for u in units], mapping))
        if log:
            log(f"已写回 {nfiles} 个 .ks（毒译文已回退；UTF-16-LE）")
        if side_items:
            apply_sidecar(
                side_items,
                mapping_aligned([it.source for it in side_items], mapping),
                Path(cfg.game_dir or cfg.text_dir or scripts),
                False,
                log,
            )

        # Second pass: re-harvest lines that still look Japanese (dialogue only)
        units2 = collect_ks_units(scripts, source_lang=source_lang)
        side2 = collect_scenario_sidecars(scripts, source_lang=source_lang)
        remain = second_pass_sources(
            [u.source for u in units2] + [it.source for it in side2],
            mapping,
            max_n=800,
            allow=rfilt,
        )
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
                remain_filter=rfilt,
            )
            src2 = [u.source for u in units2]
            n2 = apply_ks_units(units2, mapping_aligned(src2, mapping))
            if log:
                log(f"二扫写回 {n2} 个 .ks")
            if side2:
                apply_sidecar(
                    side2,
                    mapping_aligned([it.source for it in side2], mapping),
                    Path(cfg.game_dir or cfg.text_dir or scripts),
                    False,
                    log,
                )
        final_sources = [u.source for u in collect_ks_units(scripts, source_lang=source_lang)]
        final_sources += [it.source for it in collect_scenario_sidecars(scripts, source_lang=source_lang)]
        out_root = Path(cfg.game_dir or cfg.text_dir or scripts)
        write_remainder_report(out_root, "kirikiri", final_sources, mapping, log=log, allow=rfilt)
        try:
            from app.core.image_ui_scan import scan_image_ui_refs, write_image_ui_report

            hits = scan_image_ui_refs(scripts)
            if hits:
                write_image_ui_report(out_root, hits, log=log)
        except Exception as e:
            if log:
                log(f"图片 UI 扫描跳过: {e}")
    finally:
        cache.close()

    n_u16 = force_tree_utf16_le(scripts, only_scenario=True)
    if log and n_u16:
        log(f"已强制 UTF-16-LE: {n_u16} 个 scenario .ks")

    patch_root = work / "patch_tree"
    n_patch = stage_normalized_tree(scripts, patch_root)
    if log:
        n_plain, n_total = count_plain_ks(scripts)
        log(f"补丁树: {n_patch} 个明文 .ks（路径 scenario/…；跳过 {n_total - n_patch} 个仍加密）")
    if n_patch == 0:
        raise RuntimeError(
            "没有可打包的明文脚本。cxdec 游戏请先运行游戏生成 unencrypted/，"
            "或 GARbro 解包后指定「文本文件夹」。"
        )
    _deploy_to_game(game_dir, patch_root, log)

    if log:
        log("Kirikiri 管线完成 — 请运行游戏验证；cxdec 标题需保留 version.dll")
