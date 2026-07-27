# -*- coding: utf-8 -*-
"""Batch translate with SQLite cache, neighbor context, glossary + review table."""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from app.core.api_client import OpenAICompatClient
from app.core.cp932_safe import to_cp932_safe
from app.core.glossary import (
    AUTO_GLOSSARY_NAME,
    Glossary,
    enforce_glossary_consistency,
    glossary_from_mapping,
    harvest_name_candidates,
    has_glossary_leak,
    load_auto_glossary,
    load_glossary_for_game,
    mask_glossary_terms,
    merge_glossaries,
    save_glossary,
    scrub_glossary_artifacts,
    unmask_glossary_terms,
    verify_glossary_consistency,
    write_candidates_file,
)
from app.core.review_table import (
    REVIEW_NAME,
    export_review_table,
    load_review_maps,
    resolve_review_override,
)

LogFn = Optional[Callable[[str], None]]
ProgressFn = Optional[Callable[[int, int], None]]
CancelFn = Optional[Callable[[], bool]]

SOURCE_LANG_LABELS = {
    "auto": "原文（自动识别语言）",
    "ja": "日文",
    "en": "英文",
    "ko": "韩文",
    "ru": "俄文",
    "other": "其它语言",
}

_NAME_CACHE_TAG = "proper_noun"
_CTX_CACHE_TAG = "ctx"
# Bump when batch/review bugs may have poisoned SQLite hits (wrong CN for JP).
_CACHE_VER = "v5"


def _target_label(lang: str) -> str:
    return "简体中文（大陆用字）" if lang == "zh_cn" else "繁体中文（台湾用字）"


def _system_prompt(lang: str, source_lang: str = "auto", glossary_block: str = "") -> str:
    src = SOURCE_LANG_LABELS.get(source_lang, SOURCE_LANG_LABELS["auto"])
    style = _target_label(lang)
    gloss = f"\n{glossary_block}\n" if glossary_block else ""
    extra = ""
    try:
        from app.core.mt_polish import polish_prompt_rules

        extra = polish_prompt_rules(lang)
    except Exception:
        pass
    return (
        "你是资深游戏 / Galgame / 视觉小说汉化译者，进行对照原文的精翻（不是机翻糊弄）。\n"
        f"源语言：{src}。\n"
        f"目标语言：{style}。\n"
        f"{gloss}"
        "精翻要求：\n"
        "1. 严格对照原文意思与信息量，不漏译、不乱添、不篡改剧情；\n"
        "2. 语气、敬语、吐槽、口癖、暧昧感要贴合角色，写成能进游戏的自然台词；\n"
        "3. 结合给出的上文/下文理解指代与语气，但只翻译「本句」；\n"
        "4. 专有名词占位符 ⟦GALTL_A⟧ / ⟦GALTL_B⟧ 等必须原样保留，不要翻译、删改或改成数字；\n"
        "5. 脚本标记占位符 {{T0}}、[p]、\\p 等必须原样保留；\n"
        "6. 避免翻译腔、字对字硬译、整句被简化成摘要；\n"
        "7. 若原文已是目标中文，原样输出；\n"
        "8. 原文中的阿拉伯数字、全角数字、拉丁字母、版本号必须原样保留；\n"
        "9. 禁止输出元说明或占位（如「无法识别」「疑似乱码」「按原文输出」「无法翻译」）；\n"
        f"{extra}"
        "输出：批量时严格「编号|本句译文」每行一条，不要解释、不加引号外壳。"
    )


_NUM_PREFIX_RE = re.compile(r"^\d+[\.、\)]\s*")


def _strip_batch_number_prefix(txt: str) -> str:
    t = (txt or "").strip()
    if not t:
        return ""
    return _NUM_PREFIX_RE.sub("", t, count=1).strip()


def _clip_ctx(s: Optional[str], limit: int = 80) -> str:
    if not s:
        return ""
    t = re.sub(r"\s+", " ", str(s)).strip()
    if len(t) <= limit:
        return t
    return t[: limit - 1] + "…"


class TranslateCache:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, src TEXT, dst TEXT, lang TEXT)"
        )
        self._conn.commit()

    def key(self, text: str, lang: str, model: str, source_lang: str = "auto") -> str:
        return hashlib.sha256(
            f"{_CACHE_VER}|{source_lang}|{lang}|{model}|{text}".encode("utf-8")
        ).hexdigest()

    def get(self, text: str, lang: str, model: str, source_lang: str = "auto") -> Optional[str]:
        k = self.key(text, lang, model, source_lang)
        row = self._conn.execute("SELECT dst FROM cache WHERE key=?", (k,)).fetchone()
        return row[0] if row else None

    def put(
        self, text: str, lang: str, model: str, dst: str, source_lang: str = "auto"
    ) -> None:
        k = self.key(text, lang, model, source_lang)
        self._conn.execute(
            "INSERT OR REPLACE INTO cache(key, src, dst, lang) VALUES (?,?,?,?)",
            (k, text, dst, lang),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _parse_numbered(raw: str, n: int) -> List[str]:
    """Parse「编号|译文」. Require leading digits so RealLive '|' name lines are not mis-split."""
    lines: Dict[int, str] = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)\s*[|\.、\)]\s*(.*)$", line)
        if not m:
            continue
        try:
            num = int(m.group(1))
        except ValueError:
            continue
        lines[num] = _strip_batch_number_prefix(m.group(2).strip())
    if len(lines) >= n and all(lines.get(i + 1) is not None for i in range(n)):
        return [lines[i + 1] for i in range(n)]
    # incomplete / non-contiguous numbering → fail closed (caller retries per-line)
    return []


def _finalize(
    dst: str,
    cp932: bool,
    src: str = "",
    lang: str = "zh_cn",
    do_polish: bool = True,
) -> str:
    if src:
        try:
            from app.core.xua_match_rules import preserve_arabic_digits

            dst = preserve_arabic_digits(src, dst)
        except Exception:
            pass
        try:
            from app.core.kirikiri_patch import is_poison_translation

            if is_poison_translation(dst):
                dst = src
        except Exception:
            poison = ("无法识别", "疑似乱码", "按原文输出", "无法翻译")
            if any(m in (dst or "") for m in poison):
                dst = src
    if do_polish:
        try:
            from app.core.mt_polish import polish_mt_text

            dst = polish_mt_text(dst, lang=lang or "zh_cn", soft_cp932=False, src=src or "")
        except Exception:
            pass
    if cp932:
        dst = to_cp932_safe(dst)
        if do_polish:
            try:
                from app.core.mt_polish import polish_mt_text

                dst = polish_mt_text(
                    dst, lang=lang or "zh_cn", soft_cp932=True, src=src or ""
                )
            except Exception:
                pass
    return dst


def _cache_payload(prev: str, text: str) -> str:
    p = _clip_ctx(prev, 60)
    return f"{p}\n{text}" if p else text


def _format_context_item(j: int, prev: str, text: str, nxt: str) -> str:
    parts = [f"{j + 1}."]
    if prev:
        parts.append(f"  上文：{prev}")
    parts.append(f"  本句：{text}")
    if nxt:
        parts.append(f"  下文：{nxt}")
    return "\n".join(parts)


def _translate_ordered_with_context(
    items: List[Tuple[str, str, str]],
    client: OpenAICompatClient,
    lang: str,
    cache: Optional[TranslateCache],
    chunk: int,
    log: LogFn,
    progress: ProgressFn,
    should_cancel: CancelFn,
    source_lang: str,
    glossary_block: str,
    do_polish: bool = True,
) -> List[str]:
    """
    items: list of (prev_ctx, text, next_ctx) in game order.
    Returns translations aligned with items.
    """
    if not items:
        return []
    src_label = SOURCE_LANG_LABELS.get(source_lang, SOURCE_LANG_LABELS["auto"])
    tgt = "简体" if lang == "zh_cn" else "繁体"
    prompt = _system_prompt(lang, source_lang, glossary_block)
    cache_lang = f"{_CTX_CACHE_TAG}|{source_lang}"

    results: List[Optional[str]] = [None] * len(items)
    pending_idx: List[int] = []

    for i, (prev, text, _nxt) in enumerate(items):
        if cache:
            hit = cache.get(_cache_payload(prev, text), lang, client.model, cache_lang)
            if hit is None:
                # fallback: same text without context (older cache)
                hit = cache.get(text, lang, client.model, source_lang)
            if hit is not None and has_glossary_leak(hit):
                hit = None  # poisoned cache from older placeholder bugs
            if hit is not None:
                results[i] = hit
                continue
        pending_idx.append(i)

    if log:
        log(
            f"邻行上下文精翻：{len(items)} 条，缓存 {len(items) - len(pending_idx)}，"
            f"待请求 {len(pending_idx)}"
        )

    total = len(pending_idx)
    done = 0
    for start in range(0, len(pending_idx), chunk):
        if should_cancel and should_cancel():
            if log:
                log("已取消")
            break
        batch_ids = pending_idx[start : start + chunk]
        numbered = "\n".join(
            _format_context_item(
                j,
                _clip_ctx(items[i][0]),
                items[i][1],
                _clip_ctx(items[i][2]),
            )
            for j, i in enumerate(batch_ids)
        )
        user = (
            f"下列{src_label}按游戏出现顺序排列。请结合上文/下文语境，"
            f"只把「本句」精翻为{tgt}中文；占位符原样保留。"
            f"只输出 编号|本句译文：\n{numbered}"
        )
        part_texts = [items[i][1] for i in batch_ids]
        part_prevs = [items[i][0] for i in batch_ids]
        try:
            raw = client.chat(prompt, user)
            parsed = _parse_numbered(raw, len(batch_ids))
            if len(parsed) != len(batch_ids):
                raise RuntimeError("编号解析条数不匹配")
        except Exception as e:
            if log:
                log(f"上下文批次失败: {e}，逐条重试…")
            parsed = []
            for i in batch_ids:
                prev, text, nxt = items[i]
                one_user = (
                    f"结合语境精翻「本句」为{tgt}中文，只输出一句译文。\n"
                    f"上文：{_clip_ctx(prev) or '（无）'}\n"
                    f"本句：{text}\n"
                    f"下文：{_clip_ctx(nxt) or '（无）'}"
                )
                try:
                    one = client.chat(prompt, one_user)
                    parsed.append(_strip_batch_number_prefix(one.splitlines()[0].strip()))
                except Exception as e2:
                    if log:
                        log(f"单条失败，保留原文: {e2}")
                    parsed.append(text)
                time.sleep(0.12)

        for i, dst in zip(batch_ids, parsed):
            prev, text, _ = items[i]
            if not dst:
                dst = text
            dst = _finalize(dst, False, text, lang, do_polish)
            results[i] = dst
            # Never cache placeholder debris / 0-collapse — it poisons later runs
            if cache and not has_glossary_leak(dst):
                cache.put(_cache_payload(prev, text), lang, client.model, dst, cache_lang)
                cache.put(text, lang, client.model, dst, source_lang)
        done += len(batch_ids)
        if progress:
            progress(done, max(total, 1))
        if log and total:
            log(f"进度 {done}/{total}")
        time.sleep(0.12)

    out: List[str] = []
    for i, (prev, text, _) in enumerate(items):
        dst = results[i]
        out.append(text if dst is None else dst)
    return out


def _translate_proper_nouns(
    names: Sequence[str],
    client: OpenAICompatClient,
    lang: str,
    cache: Optional[TranslateCache],
    log: LogFn,
    should_cancel: CancelFn,
    source_lang: str,
) -> Dict[str, str]:
    if not names:
        return {}
    src_label = SOURCE_LANG_LABELS.get(source_lang, SOURCE_LANG_LABELS["auto"])
    style = _target_label(lang)
    cache_lang = f"{_NAME_CACHE_TAG}|{source_lang}"
    prompt = (
        "你是游戏汉化译名专家。任务：把专有名词（人名/地名/组织名/称号）译成固定中文译名。\n"
        f"源语言：{src_label}。目标：{style}。\n"
        "要求：译名简洁稳定、适合反复出现；不要解释、不要加敬称后缀；"
        "已是目标中文则原样输出；批量输出「编号|译名」。"
    )

    out: Dict[str, str] = {}
    pending: List[str] = []
    for n in names:
        if not n or not n.strip():
            continue
        if cache:
            hit = cache.get(n, lang, client.model, cache_lang)
            if hit is not None and hit.strip():
                out[n] = hit.strip()
                continue
        pending.append(n)

    if log:
        log(
            f"自动专名：共 {len(names)} 个，缓存命中 {len(out)}，待译 {len(pending)}"
        )

    for start in range(0, len(pending), 40):
        if should_cancel and should_cancel():
            if log:
                log("已取消（专名译名）")
            break
        part = pending[start : start + 40]
        numbered = "\n".join(f"{j + 1}. {s}" for j, s in enumerate(part))
        user = f"请将下列专有名词译为固定{style}译名。只输出 编号|译名：\n{numbered}"
        try:
            raw = client.chat(prompt, user)
            parsed = _parse_numbered(raw, len(part))
            if len(parsed) != len(part):
                raise RuntimeError("专名条数不匹配")
        except Exception as e:
            if log:
                log(f"专名批次失败: {e}，逐条重试…")
            parsed = []
            for s in part:
                try:
                    one = client.chat(prompt, f"只输出该专名的固定中文译名：\n{s}")
                    parsed.append(_strip_batch_number_prefix(one.splitlines()[0].strip()))
                except Exception:
                    parsed.append(s)
                time.sleep(0.1)

        for src, dst in zip(part, parsed):
            dst = (dst or src).strip() or src
            if len(dst) >= 2 and dst[0] == dst[-1] and dst[0] in "\"'「」":
                dst = dst[1:-1].strip() or src
            out[src] = dst
            if cache:
                cache.put(src, lang, client.model, dst, cache_lang)
        time.sleep(0.08)

    return out


def _build_auto_glossary(
    texts: List[str],
    client: OpenAICompatClient,
    lang: str,
    cache: Optional[TranslateCache],
    log: LogFn,
    should_cancel: CancelFn,
    source_lang: str,
    game_dir: Path,
) -> Glossary:
    existing = load_auto_glossary(game_dir)
    existing_map = {s: d for s, d in existing.pairs}
    cands = harvest_name_candidates(texts)
    try:
        write_candidates_file(game_dir, cands)
    except Exception:
        pass

    if not cands and not existing:
        return Glossary(pairs=())

    need = [n for n in cands if n not in existing_map]
    mapping = dict(existing_map)
    if need:
        translated = _translate_proper_nouns(
            need, client, lang, cache, log, should_cancel, source_lang
        )
        mapping.update(translated)

    # Drop identity pairs — they only pollute {{GALTL}} masking
    mapping = {k: v for k, v in mapping.items() if k and v and k != v}
    auto = glossary_from_mapping(mapping)
    try:
        save_glossary(
            game_dir / AUTO_GLOSSARY_NAME,
            auto,
            header=(
                "自动生成的专有名词表（勿手改也可；想覆盖某条请写到 GalAutoTL_glossary.txt）\n"
                "格式：原文=译文"
            ),
        )
        if log and auto:
            log(f"已自动生成术语表 {AUTO_GLOSSARY_NAME}（{auto.size} 条）")
    except Exception as e:
        if log:
            log(f"写入自动术语表失败: {e}")
    return auto


def _neighbor_jp(texts: Sequence[Optional[str]], i: int) -> Tuple[str, str]:
    prev = ""
    nxt = ""
    if i > 0 and texts[i - 1] and str(texts[i - 1]).strip():
        prev = str(texts[i - 1])
    if i + 1 < len(texts) and texts[i + 1] and str(texts[i + 1]).strip():
        nxt = str(texts[i + 1])
    return prev, nxt


def translate_batch(
    texts: List[str],
    client: OpenAICompatClient,
    lang: str,
    *,
    cp932: bool = False,
    cache: Optional[TranslateCache] = None,
    chunk: int = 24,
    log: LogFn = None,
    progress: ProgressFn = None,
    should_cancel: CancelFn = None,
    source_lang: str = "auto",
    game_dir: Optional[str | Path] = None,
    glossary: Optional[Glossary] = None,
    do_polish: bool = True,
) -> List[str]:
    """
    Translate in game order with neighbor context.
    Glossary terms → ⟦GALTL_A⟧ placeholders; optional GalAutoTL_review.txt overrides.

    cp932 / cache 必须用关键字传入，避免把 TranslateCache 误当成 cp932=True
    （会把汉字打成「・」且完全不写缓存）。
    """
    if not isinstance(cp932, bool):
        # 兼容旧误用：第4参传了 cache 对象
        if isinstance(cp932, TranslateCache) and cache is None:
            if log:
                log("警告: cp932 收到了缓存对象，已纠正为 cp932=False 并启用缓存")
            cache = cp932
            cp932 = False
        else:
            raise TypeError(
                f"cp932 必须是 bool，收到 {type(cp932).__name__}。"
                "请使用 translate_batch(..., cp932=False, cache=cache)"
            )
    if cache is not None and not isinstance(cache, TranslateCache):
        raise TypeError(
            f"cache 必须是 TranslateCache | None，收到 {type(cache).__name__}"
        )
    gloss = glossary
    source_note = ""
    root: Optional[Path] = Path(game_dir) if game_dir else None

    if gloss is None and root:
        manual, manual_path = load_glossary_for_game(root)
        auto = Glossary(pairs=())
        try:
            auto = _build_auto_glossary(
                texts,
                client,
                lang,
                cache,
                log,
                should_cancel,
                source_lang,
                root,
            )
        except Exception as e:
            if log:
                log(f"自动术语表构建失败，继续翻译: {e}")
            auto = load_auto_glossary(root)
        gloss = merge_glossaries(auto, manual)
        if gloss:
            bits = []
            if auto:
                bits.append(f"自动 {auto.size}")
            if manual:
                where = manual_path.name if manual_path else "手动"
                bits.append(f"手动覆盖 {manual.size}（{where}）")
            source_note = " + ".join(bits)

    if gloss is None:
        gloss = Glossary(pairs=())

    try:
        from app.core.mt_polish import builtin_sfx_glossary

        # 拟声硬替换优先于模型，避免「粗糙」类误译；手动术语表仍可覆盖同 SRC
        gloss = merge_glossaries(builtin_sfx_glossary(), gloss)
    except Exception:
        pass

    overrides_idx, overrides_src = load_review_maps(root) if root else ({}, {})
    if log and (overrides_idx or overrides_src):
        log(
            f"已加载对照表 {REVIEW_NAME}："
            f"编号条目 {len(overrides_idx)} + 原文映射 {len(overrides_src)}"
            f"（编号仅在 JP 一致时灌回，避免多场景错位）"
        )

    if log and gloss:
        extra = f"（{source_note}）" if source_note else ""
        log(f"术语表生效 {gloss.size} 条{extra} — 占位符保护专名")

    gloss_block = gloss.as_prompt_block() if gloss else ""

    results: List[str] = [""] * len(texts)
    need_idx: List[int] = []
    masked_for_idx: Dict[int, Tuple[str, List[str]]] = {}
    review_hits = 0
    already_cn_hits = 0

    try:
        from app.core.pipeline_harden import looks_already_chinese
    except Exception:
        looks_already_chinese = None  # type: ignore

    protect_cn = (
        looks_already_chinese is not None
        and (lang or "").lower().startswith("zh")
        and (source_lang or "ja").lower() in ("ja", "auto", "")
    )

    for i, t in enumerate(texts):
        if t is None:
            results[i] = t  # type: ignore
            continue
        if not str(t).strip():
            results[i] = t
            continue
        ov = resolve_review_override(i, t, overrides_idx, overrides_src)
        if ov is not None:
            results[i] = _finalize(ov, cp932, t, lang, do_polish)
            review_hits += 1
            continue
        # Re-run safety: do not API/cache-mangle finished Chinese harvested as "JP"
        if protect_cn and looks_already_chinese(str(t)):
            results[i] = t
            already_cn_hits += 1
            continue
        masked, keys = mask_glossary_terms(t, gloss) if gloss else (t, [])
        masked_for_idx[i] = (masked, keys)
        need_idx.append(i)

    if log and review_hits:
        log(f"对照表直接灌回 {review_hits} 条")
    if log and already_cn_hits:
        log(f"跳过已是中文 {already_cn_hits} 条（防翻坏）")

    if need_idx:
        items: List[Tuple[str, str, str]] = []
        for i in need_idx:
            prev, nxt = _neighbor_jp(texts, i)
            masked, _keys = masked_for_idx[i]
            items.append((prev, masked, nxt))

        translated = _translate_ordered_with_context(
            items,
            client,
            lang,
            cache,
            chunk,
            log,
            progress,
            should_cancel,
            source_lang,
            gloss_block,
            do_polish,
        )

        work_src = [texts[i] for i in need_idx]
        work_dst: List[str] = []
        leak_fallback = 0
        for i, dst in zip(need_idx, translated):
            src = texts[i]
            _masked, keys = masked_for_idx[i]
            if gloss and keys:
                dst = unmask_glossary_terms(dst, gloss, keys)
            if has_glossary_leak(dst):
                dst = scrub_glossary_artifacts(
                    dst, src=src or "", glossary=gloss, keys=keys
                )
            if has_glossary_leak(dst):
                # Safer than shipping {{GALTL}} / 0夏 into scripts (can crash engines)
                leak_fallback += 1
                dst = src
            work_dst.append(dst)
        if log and leak_fallback:
            log(f"术语占位符损坏 {leak_fallback} 条，已回退原文（避免写入坏句）")

        if gloss:
            work_dst = enforce_glossary_consistency(work_src, work_dst, gloss)
            notes = verify_glossary_consistency(work_src, work_dst, gloss)
            if notes and log:
                for n in notes[:5]:
                    log(f"术语校验告警: {n}")
                if len(notes) > 5:
                    log(f"术语校验告警另有 {len(notes) - 5} 条…")

        for i, src, dst in zip(need_idx, work_src, work_dst):
            results[i] = _finalize(dst, cp932, src, lang, do_polish)
            # Cache good unmasked CN under original JP so later runs skip re-mask debris
            if cache and src and dst and dst != src and not has_glossary_leak(dst):
                try:
                    cache.put(src, lang, client.model, dst, source_lang)
                except Exception:
                    pass

    if root:
        try:
            # fill None holes for export
            export_src = [t if t is not None else "" for t in texts]
            export_dst = [
                (results[i] if results[i] is not None else "") for i in range(len(texts))
            ]
            path = export_review_table(
                root,
                export_src,
                export_dst,
                header_note="改 CN 后重新开始汉化即可灌回",
            )
            if log:
                log(f"已导出对照表 → {path.name}（可人工校对后重跑灌回）")
        except Exception as e:
            if log:
                log(f"导出对照表失败: {e}")

    return results
