# -*- coding: utf-8 -*-
"""Unity one-click:

1) StreamingAssets / loose text (json/csv/txt/…)
2) UnityPy TextAsset (when typetrees allow)
3) MonoBehaviour raw UTF-8 in .unity3d/.assets (IL2CPP-friendly)
4) IL2CPP global-metadata.dat literals (story/endings)

Longer CN than JP is length-fitted (pad/truncate) for binary safety.

Also harvests .bundle / Addressables packs; tries header XOR recovery and
UnityCN AssetBundle keys (GalAutoTL_unity_ab_key.txt / env).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from app.config import AppConfig, cache_db_path
from app.core.api_client import OpenAICompatClient
from app.core.il2cpp_meta_text import apply_meta_units, collect_meta_units, find_metadata
from app.core.pipeline_harden import (
    CODEC_UNICODE,
    remain_filter_set,
    run_second_pass,
    second_pass_sources,
    translate_to_mapping,
    write_remainder_report,
)
from app.core.translate import TranslateCache
from app.core.unity_raw_text import apply_mb_units, collect_mb_units, collect_runtime_jp_corpus
from app.core.il2cpp_stringliteral import collect_il2cpp_string_literals
from app.core.unity_typetree_text import collect_typetree_jp_strings
from app.core.unity_hazy_text import collect_hazy_jp_strings, finalize_hazy_after_translate
from app.core.xua_match_rules import split_glued_game_strings
from app.pipelines.generic_text import (
    WorkItem,
    apply_translations,
    collect_items,
    _should_translate_body,
)

LogFn = Optional[Callable[[str], None]]
ProgressFn = Optional[Callable[[int, int], None]]
CancelFn = Optional[Callable[[], bool]]


def _unity_roots(game_dir: Path) -> List[Path]:
    roots = []
    for name in ("StreamingAssets", "Data/StreamingAssets", "data/StreamingAssets"):
        p = game_dir / name if "/" not in name else game_dir / Path(name)
        if p.is_dir():
            roots.append(p)
    for p in game_dir.glob("*_Data"):
        sa = p / "StreamingAssets"
        if sa.is_dir():
            roots.append(sa)
    seen = set()
    out = []
    for r in roots:
        k = str(r.resolve()).lower()
        if k not in seen and r.is_dir():
            seen.add(k)
            out.append(r)
    return out


def _try_unitypy_textassets(
    game_dir: Path, work: Path, log: LogFn
) -> Tuple[List[WorkItem], dict]:
    try:
        import UnityPy
    except ImportError:
        if log:
            log("未安装 UnityPy：跳过 TextAsset（pip install UnityPy）")
        return [], {}

    from app.core.unity_raw_text import find_unity_asset_files, _unity_load
    from app.core.unity_bundle_crypto import apply_unity_cn_keys, discover_unity_cn_keys

    items: List[WorkItem] = []
    meta_map: dict = {}
    export_dir = work / "textassets_export"
    if export_dir.exists():
        shutil.rmtree(export_dir)
    export_dir.mkdir(parents=True)

    try:
        keys = discover_unity_cn_keys(game_dir)
        if keys:
            apply_unity_cn_keys(keys, log)
    except Exception:
        pass

    for fp in find_unity_asset_files(game_dir):
        try:
            env, how = _unity_load(fp, game_dir, log)
            if log and how not in ("plain", "undecrypted"):
                log(f"  TextAsset 包解密: {fp.name} ({how})")
        except Exception:
            continue
        for obj in env.objects:
            try:
                if obj.type.name != "TextAsset":
                    continue
                data = obj.read()
                script = getattr(data, "script", None) or getattr(data, "m_Script", None)
                if script is None:
                    continue
                if isinstance(script, bytes):
                    try:
                        text = script.decode("utf-8")
                    except UnicodeDecodeError:
                        try:
                            text = script.decode("utf-8-sig")
                        except UnicodeDecodeError:
                            text = script.decode("cp932", errors="replace")
                else:
                    text = str(script)
                name = getattr(data, "name", None) or getattr(data, "m_Name", None) or f"ta_{obj.path_id}"
                if "LineBreaking" in str(name) or "Font" in str(name):
                    continue
                if not _should_translate_body(text[:2000], "ja") and not any(
                    x in text for x in ("「", "」", "の", "を", "です", "ます")
                ):
                    if not (
                        text.lstrip().startswith("{")
                        or text.lstrip().startswith("[")
                        or "\t" in text[:200]
                    ):
                        continue
                rel = f"{fp.stem}_{obj.path_id}_{name}.txt"
                rel = "".join(c if c.isalnum() or c in "._-" else "_" for c in rel)
                outp = export_dir / rel
                outp.write_text(text, encoding="utf-8")
                meta_map[str(outp)] = {
                    "asset": str(fp),
                    "path_id": obj.path_id,
                    "name": name,
                }
                for i, line in enumerate(text.splitlines()):
                    if _should_translate_body(line, "ja"):
                        items.append(WorkItem("txt_line", outp, i, line))
            except Exception:
                continue

    if log:
        log(f"UnityPy TextAsset 可译行: {len(items)}")
    return items, meta_map


def _writeback_unitypy(meta_map: dict, log: LogFn) -> int:
    try:
        import UnityPy
    except ImportError:
        return 0
    from collections import defaultdict

    by_asset: dict[str, list] = defaultdict(list)
    for export_path, info in meta_map.items():
        p = Path(export_path)
        if not p.exists():
            continue
        by_asset[info["asset"]].append((info["path_id"], p))

    from app.core.unity_raw_text import _unity_load

    written = 0
    for asset_path, entries in by_asset.items():
        try:
            ap = Path(asset_path)
            # best-effort: *_Data parent → game root
            root = ap.parent.parent if ap.parent.name.endswith("_Data") or ap.parent.name == "Data" else ap.parent
            env, _how = _unity_load(ap, root, log)
        except Exception as e:
            if log:
                log(f"重载失败 {asset_path}: {e}")
            continue
        obj_map = {o.path_id: o for o in env.objects}
        changed = False
        for path_id, txt_path in entries:
            obj = obj_map.get(path_id)
            if not obj:
                continue
            try:
                data = obj.read()
                new_text = txt_path.read_text(encoding="utf-8")
                if hasattr(data, "script"):
                    data.script = new_text.encode("utf-8")
                elif hasattr(data, "m_Script"):
                    data.m_Script = new_text.encode("utf-8")
                else:
                    continue
                data.save()
                changed = True
            except Exception as e:
                if log:
                    log(f"TextAsset 写回失败 path_id={path_id}: {e}")
        if changed:
            bak = Path(asset_path + ".galautotl.bak")
            if not bak.exists():
                shutil.copy2(asset_path, bak)
            try:
                try:
                    blob = env.file.save(packer="original")
                except TypeError:
                    blob = env.file.save()
                with open(asset_path, "wb") as f:
                    f.write(blob)
                written += 1
                if log:
                    log(f"已写回 TextAsset 资源: {Path(asset_path).name}")
            except Exception as e:
                if log:
                    log(f"保存资源失败 {asset_path}: {e}")
    return written


def run_unity(
    cfg: AppConfig,
    log: LogFn = None,
    progress: ProgressFn = None,
    should_cancel: CancelFn = None,
) -> None:
    from app.core.ensure_deps import ensure_runtime_deps

    ensure_runtime_deps(log)

    game_dir = Path(cfg.game_dir or cfg.text_dir)
    if not game_dir.is_dir():
        raise FileNotFoundError(f"目录无效: {game_dir}")

    try:
        from app.core.unity_bundle_crypto import configure_unitypy_fallback

        configure_unitypy_fallback(game_dir, log=log)
    except Exception as e:
        if log:
            log(f"警告: UnityPy 版本回退设置失败: {e}")

    work = game_dir / "_galautotl_unity"
    work.mkdir(parents=True, exist_ok=True)

    source_lang = getattr(cfg, "source_lang", "ja") or "ja"
    items: List[WorkItem] = []

    # 1) StreamingAssets
    roots = _unity_roots(game_dir)
    if cfg.text_dir.strip():
        td = Path(cfg.text_dir)
        if td.is_dir() and td.resolve() != game_dir.resolve():
            # only add if it looks like a text pack, not whole game dump under *_Data engine files
            if "streamingassets" in str(td).lower() or not list(td.glob("*_Data")):
                roots.insert(0, td)
    if log:
        labels = []
        for r in roots[:6]:
            try:
                labels.append(str(r.relative_to(game_dir)))
            except ValueError:
                labels.append(str(r))
        log("扫描明文: " + (", ".join(labels) or "(无 StreamingAssets)"))

    for root in roots:
        try:
            items.extend(collect_items(root, log, source_lang=source_lang))
        except Exception as e:
            if log:
                log(f"扫描失败 {root}: {e}")

    # 2) TextAsset lines (credits etc.) — feed into runtime dict, do not rewrite assets
    ta_items, _meta_map = _try_unitypy_textassets(game_dir, work, log)
    if ta_items:
        items.extend(ta_items)
        if log:
            log(f"并入 TextAsset 可译行 {len(ta_items)}（走运行时注入）")

    # 3) Collect strings for AI → BepInEx/XUA runtime inject (stable)
    mb_units = collect_mb_units(game_dir, log, for_runtime=True)
    meta_units = collect_meta_units(game_dir, log, for_runtime=True)
    # 3b) Deep first-pass corpus (all objects) — 网上也会尽量先挖全再采游玩缺口
    corpus = collect_runtime_jp_corpus(game_dir, log)
    # 3c) Il2CppDumper stringliteral.json — 教程标准「代码字面量全表」
    lit_rows = collect_il2cpp_string_literals(game_dir, log)
    # 3d) 汉化组标准：TypeTree 结构化读 Card/Hover/TMP 等字段（比盲扫全）
    tt_rows = collect_typetree_jp_strings(game_dir, log)
    # 3e) PARANORMASIGHT 等：StreamingAssets/a### 壳包里的 Hazy_Script_JP
    hazy_rows = collect_hazy_jp_strings(game_dir, log)

    enable_assets = bool(getattr(cfg, "unity_patch_assets", False))
    enable_meta_write = bool(getattr(cfg, "unity_patch_metadata", False))
    if log:
        log(
            "默认稳定注入：BepInEx + XUnity.AutoTranslator（不修改 data.unity3d）"
            + ("；另已开启实验性写包" if enable_assets else "")
        )

    # Dedupe before API: same line may appear in MB + meta + deep scan
    # Prefer display-level strings (WindowMessage body) — script shells never match TMP
    from app.core.xua_display_text import scrub_sources_for_translate

    seen_src: set = set()
    unique_sources: List[str] = []

    def _add_src(s: str) -> None:
        s = (s or "").strip("\x00")
        if not s or s in seen_src:
            return
        # Online tip: split welded GAME OVER / shader tails before translate
        parts = split_glued_game_strings(s) or [s]
        for p in scrub_sources_for_translate(parts):
            if not p or p in seen_src:
                continue
            seen_src.add(p)
            unique_sources.append(p)

    for it in items:
        _add_src(it.source)
    for u in mb_units:
        _add_src(u.source)
    for u in meta_units:
        _add_src(u.source)
    for s in corpus:
        _add_src(s)
    for s in lit_rows:
        _add_src(s)
    for s in tt_rows:
        _add_src(s)
    for s in hazy_rows:
        _add_src(s)

    total = len(unique_sources)
    if total == 0:
        raise RuntimeError(
            "未找到可翻译文本。请确认目录含 UnityPlayer.dll / xxx_Data，并已安装 UnityPy。"
        )

    if log:
        log(
            f"待翻译去重合计: {total} "
            f"（明文 {len(items)} + MB {len(mb_units)} + 元数据 {len(meta_units)} "
            f"+ 深扫 {len(corpus)} + Il2Cpp字面量 {len(lit_rows)} + TypeTree字段 {len(tt_rows)} "
            f"+ Hazy剧本 {len(hazy_rows)}；已剥离脚本壳/不可见键）"
        )

    client = OpenAICompatClient(cfg.api_base, cfg.api_key, cfg.api_model, cfg.temperature)
    cache = TranslateCache(cache_db_path())
    all_sources = unique_sources
    try:
        if log:
            log("Unity: UTF-8 词典注入（禁 CP932；默认不改 data.unity3d）")
        mapping = translate_to_mapping(
            all_sources,
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
        cancelled = bool(should_cancel and should_cancel())
        if not cancelled:
            remain = second_pass_sources(all_sources, mapping, max_n=800, allow=remain_filter_set(cfg))
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
            cancelled = bool(should_cancel and should_cancel())

        write_remainder_report(
            Path(cfg.game_dir or cfg.text_dir or game_dir),
            "unity",
            all_sources,
            mapping,
            log=log,
            allow=remain_filter_set(cfg),
        )

        tmap = mapping
        t_items = [tmap.get(it.source, it.source) for it in items]
        t_mb = [tmap.get(u.source, u.source) for u in mb_units]
        t_meta = [tmap.get(u.source, u.source) for u in meta_units]

        if items:
            apply_translations(items, t_items, game_dir, cfg.do_backup, log)

        export = work / "translations.json"
        import json

        export_rows = []
        for u, t in zip(mb_units, t_mb):
            export_rows.append(
                {
                    "kind": "monobehaviour",
                    "class": u.classname,
                    "name": u.obj_name,
                    "path_id": u.path_id,
                    "source": u.source,
                    "translated": t,
                }
            )
        for u, t in zip(meta_units, t_meta):
            export_rows.append({"kind": "metadata", "source": u.source, "translated": t})
        for s in corpus:
            if s in tmap:
                export_rows.append({"kind": "corpus", "source": s, "translated": tmap[s]})
        for s in lit_rows:
            if s in tmap:
                export_rows.append({"kind": "il2cpp_literal", "source": s, "translated": tmap[s]})
        if export_rows:
            export.write_text(json.dumps(export_rows, ensure_ascii=False, indent=2), encoding="utf-8")
            if log:
                log(f"已导出 JSON {len(export_rows)} 条 → {export}")

        from app.core.unity_runtime_inject import deploy_runtime_inject

        pairs = [(s, tmap[s]) for s in unique_sources if s in tmap and tmap[s] and tmap[s] != s]
        # Also include seeded prior CN so merge has full set when remain_only
        for s, t in tmap.items():
            if t and t != s and (s, t) not in pairs:
                pairs.append((s, t))
        if cancelled and log:
            log(f"已取消：仍注入已译部分（词典候选 {len(pairs)} 条），未译句保持原文")
        elif log:
            log(f"开始部署运行时汉化（词典候选 {len(pairs)} 条）…")
        # Partial cancel: merge so a later full run / resume does not wipe prior CN
        deploy_runtime_inject(
            game_dir,
            pairs,
            target_lang=cfg.lang,
            source_lang=source_lang,
            log=log,
            merge_dict=cancelled or remain_filter_set(cfg) is not None,
        )

        # PARANORMASIGHT-class Hazy/AdvScript: full post-translate harden
        # (fill-expand, wait tags, ssei choices, lineno scrub, txtid resync).
        if hazy_rows and tmap and pairs:
            try:
                dict_path = (
                    game_dir
                    / "BepInEx"
                    / "Translation"
                    / "zh-CN"
                    / "Text"
                    / "GalAutoTL.txt"
                )
                finalize_hazy_after_translate(
                    game_dir,
                    tmap,
                    dict_path=dict_path if dict_path.is_file() else None,
                    log=log,
                )
            except Exception as e:
                if log:
                    log(f"Hazy 写回失败（词典仍可用）: {e}")

        if not cancelled:
            if mb_units and enable_assets:
                if log:
                    log("⚠ 实验性写回 data.unity3d…")
                apply_mb_units(mb_units, t_mb, log)
            if meta_units and enable_meta_write:
                if log:
                    log("⚠ 实验性写回 global-metadata.dat…")
                apply_meta_units(meta_units, t_meta, log)
    finally:
        cache.close()

    if log:
        if should_cancel and should_cancel():
            log("Unity 已取消并完成部分注入 — 可用启动 bat 试玩；再跑会续译缓存缺口")
        else:
            log("Unity 管线完成 — 请用「点我启动_中文汉化_Unity.bat」启动")
