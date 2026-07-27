# -*- coding: utf-8 -*-
"""Cross-pipeline hardening — lessons from Kagura/Kirikiri/Unity field work.

Correctness rules (do NOT violate):
  1. Encoding is engine-specific. Never run to_cp932_safe / cp932=True on
     Unicode engines (Kirikiri UTF-16, Unity UTF-8, Artemis, Sakana, LCSE→GBK).
  2. translate_batch(cp932=..., cache=...) MUST use keyword args only.
  3. Prefer backup-original re-runs; never double-patch dirty binaries blindly.
  4. Do not translate Kirikiri engine/macro/.tjs code for “coverage”.
  5. Reject AI poison / empty / (CP932-only) ・-mangled strings.
  6. Second pass only on content that still looks Japanese; cap size.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from app.core.api_client import OpenAICompatClient
from app.core.translate import TranslateCache, translate_batch

LogFn = Optional[Callable[[str], None]]
ProgressFn = Optional[Callable[[int, int], None]]
CancelFn = Optional[Callable[[], bool]]

# Engine text codec for post-AI sanitize / API flag
CODEC_UNICODE = "unicode"  # UTF-8 / UTF-16-LE — never CP932-mangle
CODEC_CP932 = "cp932"  # Softpal-JP / Kagura / some BGI
CODEC_GBK = "gbk"  # LCSE CN display path

# Declared translate codecs per pipeline (tests + softpal helper share this).
PIPELINE_TRANSLATE_CODEC: Dict[str, str] = {
    "kirikiri": CODEC_UNICODE,
    "unity": CODEC_UNICODE,
    "artemis": CODEC_UNICODE,
    "sakana": CODEC_UNICODE,
    "lcse": CODEC_GBK,
    "kagura": CODEC_CP932,
    "bgi": CODEC_CP932,
    "yuris": CODEC_CP932,
}


def softpal_codecs_for_lang(lang: str) -> tuple[str, str]:
    """SoftPal: zh → GBK write + GBK sanitize; else CP932.

    Returns (file_encoding, translate_codec).
    """
    if (lang or "").startswith("zh"):
        return "gbk", CODEC_GBK
    return "cp932", CODEC_CP932


def expected_translate_codec(pipeline: str, lang: str = "zh_cn") -> str:
    if pipeline == "softpal":
        return softpal_codecs_for_lang(lang)[1]
    if pipeline == "generic":
        # generic follows UI checkbox; default unicode
        return CODEC_UNICODE
    return PIPELINE_TRANSLATE_CODEC[pipeline]

_CONTENT_HIRA = re.compile(r"[\u3040-\u309f]{2,}")
_KATA_UI = re.compile(r"^[\u30a0-\u30ffー・\s/%\d]+$")
_HAS_KANA = re.compile(r"[\u3040-\u30ff]")
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")
# Finished CN dialogue / UI (no kana). Keep short kanji-only JP UI (選択肢/終了) translateable.
_ALREADY_CN_LINE = re.compile(
    r"^[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\s\d，。！？、；：…—·「」『』（）【】《》\.\!\?\-~,\"']+$"
)
# Multi-char / simplified-only markers. NEVER bare「了/的」— they sit inside JP 終了/了解.
# NEVER「取消」alone — JP UI uses the same kanji.
_CLEAR_CN_MARK = re.compile(
    r"(吗|吧|呢|您|谢谢|请问|请求|请您|关闭|确定|"
    r"返回标题|返回|读取|保存游戏|设置|是的|不是|继续|"
    r"开始游戏|结束游戏|加载|存档|读档|"
    r"你好|你们|我们|他们|这里|那里|什么|怎么|这样|那样)"
)


def looks_already_chinese(s: str) -> bool:
    """True when *s* already looks like finished Chinese (safe to leave alone on re-run).

    Used so a second「开始汉化」that accidentally harvests CN files does not
    re-send Chinese to the API and mangle it. Short kanji-only JP UI without
    CN markers (e.g. 選択肢、終了、了解) stays eligible for translation.
    """
    s = (s or "").strip()
    if not s or _HAS_KANA.search(s):
        return False
    if not _HAS_CJK.search(s):
        return False
    if _CLEAR_CN_MARK.search(s):
        return True
    # Explicit CN meta / short phrases (二扫勿把「已是中文」当漏翻)
    if re.search(r"(中文|汉化|已是|已经|简体)", s):
        return True
    if _ALREADY_CN_LINE.match(s) and re.search(r"[这哪吗吧呢您们啥还]", s):
        return True
    # Long pure-CJK lines without kana ≈ already localized dialogue
    if _ALREADY_CN_LINE.match(s) and len(s) >= 10:
        return True
    return False


def _zh_target(lang: str) -> bool:
    return (lang or "").lower().startswith("zh")


def _ja_source(source_lang: str) -> bool:
    return (source_lang or "ja").lower() in ("ja", "auto", "")

# Short system/UI labels — Unicode-safe (Kirikiri / Unity / Artemis…).
# Kagura CP932 overlays live in kagura_glossary (読取 etc.).
COMMON_UI_GLOSSARY: Dict[str, str] = {
    "セーブ": "保存",
    "ロード": "读取",
    "はい": "是",
    "いいえ": "否",
    "タイトルに戻る": "返回标题",
    "ゲーム終了": "结束游戏",
    "コンフィグ": "设置",
    "コンフィグレーション": "设置",
    "オプション": "选项",
    "スタート": "开始",
    "ギャラリー": "鉴赏",
    "ＣＧモード": "CG模式",
    "CGモード": "CG模式",
    "シーン回想": "场景回想",
    "音楽モード": "音乐模式",
    "おまけ": "附录",
    "ヘルプ": "说明",
    "オート": "自动",
    "スキップ": "跳过",
    "バックログ": "历史",
    "クイックセーブ": "快速保存",
    "クイックロード": "快速读取",
    "はい／いいえ": "是／否",
}


def dedupe_preserve(texts: Sequence[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for t in texts:
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def looks_untranslated(s: str) -> bool:
    """True if string still looks like Japanese content worth a second pass."""
    if not s or not str(s).strip():
        return False
    s = str(s).strip()
    if looks_already_chinese(s):
        return False
    if _CONTENT_HIRA.search(s):
        return True
    if _KATA_UI.fullmatch(s) and 1 < len(s) <= 24:
        return True
    # Mixed kata + kanji UI (ゲーム終了) — not covered by kana-only or kanji-only branches
    if re.search(r"[\u30a0-\u30ff]", s) and _HAS_CJK.search(s):
        return True
    # Kanji-only JP UI (確認/設定/選択肢) — previously missed forever
    if _HAS_CJK.search(s) and not _HAS_KANA.search(s) and len(s) <= 64:
        return True
    # Mixed with JP punctuation
    if re.search(r"[。、！？「」『』…]", s) and re.search(
        r"[\u3040-\u30ff\u4e00-\u9fff]", s
    ):
        return True
    return False


def _is_poison(dst: str) -> bool:
    try:
        from app.core.kirikiri_patch import is_poison_translation

        if is_poison_translation(dst):
            return True
    except Exception:
        pass
    markers = ("无法识别", "疑似乱码", "按原文输出", "无法翻译", "I cannot", "as an AI")
    return any(m in (dst or "") for m in markers)


def sanitize_dst(dst: str, src: str, codec: str) -> Optional[str]:
    """Return cleaned translation or None to keep source / skip."""
    if not dst or not str(dst).strip():
        return None
    d = str(dst).strip()
    if _is_poison(d):
        return None
    if d == src:
        return None
    try:
        from app.core.glossary import has_glossary_leak, scrub_glossary_artifacts

        if has_glossary_leak(d):
            d = scrub_glossary_artifacts(d, src=src or "")
        if has_glossary_leak(d):
            return None
    except Exception:
        pass

    if codec == CODEC_CP932:
        try:
            from app.core.cp932_safe import to_cp932_safe

            d = to_cp932_safe(d)
        except Exception:
            pass
        core = d.replace(" ", "").replace("　", "")
        if core and core.count("・") >= max(3, len(core) // 2):
            return None
        try:
            d.encode("cp932")
        except UnicodeEncodeError:
            return None
    elif codec == CODEC_GBK:
        # Keep 简体; do NOT run to_cp932_safe (would smash 简→日异体).
        try:
            d.encode("gbk")
        except UnicodeEncodeError:
            # drop only unencodable chars
            d2 = "".join(ch if ch.encode("gbk", errors="ignore") else "" for ch in d)
            if not d2.strip() or d2 == src:
                return None
            d = d2
    # UNICODE: no encoding smash; poison already checked

    if not d or d == src:
        return None
    return d


def zip_to_mapping(
    sources: Sequence[str],
    translated: Sequence[str],
    *,
    codec: str,
    log: LogFn = None,
) -> Dict[str, str]:
    out: Dict[str, str] = {}
    dropped = 0
    for src, dst in zip(sources, translated):
        if not src:
            continue
        clean = sanitize_dst(str(dst) if dst else "", src, codec)
        if clean is None:
            if dst and str(dst).strip() and str(dst).strip() != src:
                dropped += 1
            continue
        out[src] = clean
    if log and dropped:
        log(f"质量门禁丢弃劣质译文 {dropped} 条（codec={codec}）")
    return out


def merge_glossary(
    mapping: Dict[str, str],
    *glossaries: Optional[Dict[str, str]],
    remain_filter: Optional[set] = None,
) -> Dict[str, str]:
    """Later glossaries win. If remain_filter set, only keys in the allow-list merge."""
    out = dict(mapping)
    for g in glossaries:
        if not g:
            continue
        if remain_filter is None:
            out.update(g)
        else:
            for k, v in g.items():
                if k in remain_filter:
                    out[k] = v
    return out


def apply_common_ui(
    mapping: Dict[str, str], remain_filter: Optional[set] = None
) -> Dict[str, str]:
    """Overlay COMMON_UI_GLOSSARY; respect 仅译漏句 allow-list when set."""
    return merge_glossary(mapping, COMMON_UI_GLOSSARY, remain_filter=remain_filter)


def mapping_aligned(sources: Sequence[str], mapping: Dict[str, str]) -> List[str]:
    return [mapping.get(s, s) for s in sources]


def translate_to_mapping(
    texts: Sequence[str],
    client: OpenAICompatClient,
    lang: str,
    *,
    codec: str,
    cache: Optional[TranslateCache] = None,
    chunk: int = 24,
    log: LogFn = None,
    progress: ProgressFn = None,
    should_cancel: CancelFn = None,
    source_lang: str = "ja",
    game_dir=None,
    do_polish: bool = True,
    label: str = "翻译",
    glossary: Optional[Dict[str, str]] = None,
    remain_filter: Optional[set] = None,
) -> Dict[str, str]:
    """One AI pass → sanitized mapping. cp932 API flag only for CODEC_CP932.

    When ``remain_filter`` is set (仅译漏句), prior CN is seeded from cache +
    review table for non-filter lines so JP-rebuild pipelines do not wipe CN.

    Always protect strings that already look Chinese (ja→zh): identity-map so
    a full re-run on CN loose files cannot re-API and ruin them.
    """
    uniq_all = dedupe_preserve(list(texts))
    mapping: Dict[str, str] = {}
    src_lang = source_lang or "ja"

    if remain_filter is not None:
        seeded = _seed_prior_cn(
            uniq_all,
            remain_filter,
            mapping,
            codec=codec,
            cache=cache,
            lang=lang,
            model=getattr(client, "model", "") or "",
            source_lang=src_lang,
            game_dir=game_dir,
        )
        if log and seeded:
            log(f"仅译漏句: 灌回已有译文 {seeded} 条（防重建冲掉汉化）")
        uniq = [t for t in uniq_all if t in remain_filter]
        if log:
            log(f"仅译漏句过滤: {len(uniq_all)} → {len(uniq)} 条待 API")
    else:
        uniq = uniq_all

    # Second「开始汉化」safety: never re-translate finished Chinese as if it were JP
    if _zh_target(lang) and _ja_source(src_lang):
        kept = 0
        kept_set = set()
        for t in list(uniq):
            if t in mapping:
                continue
            if looks_already_chinese(t):
                mapping[t] = t
                kept_set.add(t)
                kept += 1
        if kept_set:
            uniq = [t for t in uniq if t not in kept_set]
        if log and kept:
            log(f"跳过已是中文的句子 {kept} 条（防二次全量翻坏）")

    if not uniq and not mapping:
        return apply_common_ui({}, remain_filter)
    if uniq:
        use_cp932 = codec == CODEC_CP932
        if log:
            log(f"{label}: {len(uniq)} 条（codec={codec}, cp932_api={use_cp932}）")
        translated = translate_batch(
            uniq,
            client,
            lang,
            cp932=use_cp932,
            cache=cache,
            chunk=chunk or 24,
            log=log,
            progress=progress,
            should_cancel=should_cancel,
            source_lang=src_lang,
            game_dir=game_dir,
            do_polish=do_polish,
        )
        mapping.update(zip_to_mapping(uniq, translated, codec=codec, log=log))
    mapping = apply_common_ui(mapping, remain_filter)
    if glossary:
        mapping = merge_glossary(mapping, glossary, remain_filter=remain_filter)
    return mapping


def _seed_prior_cn(
    texts: Sequence[str],
    remain_filter: set,
    mapping: Dict[str, str],
    *,
    codec: str,
    cache: Optional[TranslateCache],
    lang: str,
    model: str,
    source_lang: str,
    game_dir=None,
) -> int:
    """Fill mapping for sources outside remain_filter from review + cache."""
    review: Dict[str, str] = {}
    if game_dir:
        try:
            from app.core.review_table import load_review_overrides

            review = load_review_overrides(game_dir) or {}
        except Exception:
            review = {}
    seeded = 0
    for src in texts:
        if not src or src in remain_filter or src in mapping:
            continue
        dst = None
        if src in review and review[src] and review[src] != src:
            dst = review[src]
            try:
                from app.core.glossary import has_glossary_leak

                if has_glossary_leak(dst):
                    dst = None
            except Exception:
                pass
        elif cache is not None and model:
            try:
                hit = cache.get(src, lang, model, source_lang)
            except Exception:
                hit = None
            if hit and hit != src:
                dst = hit
        if not dst:
            continue
        clean = sanitize_dst(str(dst), src, codec)
        if clean:
            mapping[src] = clean
            seeded += 1
    return seeded


def second_pass_sources(
    candidates: Iterable[str],
    mapping: Dict[str, str],
    *,
    max_n: int = 800,
    allow: Optional[set] = None,
) -> List[str]:
    """Sources still untranslated or whose mapped value still looks Japanese.

    Only retry when the *source* still looks Japanese (or mapped dst still has
    kana). Pure Chinese src==dst must NOT enter second pass.
    If ``allow`` is set (仅译漏句), only those sources may enter the retry list.
    """
    remain: List[str] = []
    seen = set()
    for src in candidates:
        if not src or src in seen:
            continue
        if allow is not None and src not in allow:
            continue
        if src in COMMON_UI_GLOSSARY:
            continue
        dst = mapping.get(src, src)
        need = False
        if dst == src:
            need = looks_untranslated(src)
        elif looks_untranslated(dst):
            need = True
        if not need:
            continue
        seen.add(src)
        remain.append(src)
        if len(remain) >= max_n:
            break
    return remain


def run_second_pass(
    remain: Sequence[str],
    mapping: Dict[str, str],
    client: OpenAICompatClient,
    lang: str,
    *,
    codec: str,
    cache: Optional[TranslateCache] = None,
    chunk: int = 24,
    log: LogFn = None,
    progress: ProgressFn = None,
    should_cancel: CancelFn = None,
    source_lang: str = "ja",
    game_dir=None,
    do_polish: bool = True,
    glossary: Optional[Dict[str, str]] = None,
    remain_filter: Optional[set] = None,
) -> Dict[str, str]:
    if remain_filter is not None:
        remain = [s for s in remain if s in remain_filter]
    if not remain:
        return mapping
    if log:
        log(f"漏翻二扫: {len(remain)} 条")
    map2 = translate_to_mapping(
        remain,
        client,
        lang,
        codec=codec,
        cache=cache,
        chunk=chunk,
        log=log,
        progress=progress,
        should_cancel=should_cancel,
        source_lang=source_lang,
        game_dir=game_dir,
        do_polish=do_polish,
        label="二扫",
        glossary=glossary,
        remain_filter=remain_filter,
    )
    if not map2:
        return mapping
    out = dict(mapping)
    out.update(map2)
    return apply_common_ui(
        merge_glossary(out, glossary, remain_filter=remain_filter),
        remain_filter,
    )


REMAIN_REPORT_NAME = "GalAutoTL_remain.txt"

_PATH_LIKE = re.compile(
    r"[\\/]|\.(?:png|tlg|jpg|jpeg|bmp|webp|ogg|wav|mp3|ks|tjs|snx|csv|txt)$",
    re.I,
)
_SHORT_SFX = re.compile(r"^[\u30a0-\u30ffー・ぁ-ん]{1,8}$")


def classify_remain_line(s: str) -> str:
    """Bucket leftover JP for the remainder report."""
    t = (s or "").strip()
    if not t:
        return "other"
    if _PATH_LIKE.search(t):
        return "path"
    if t in COMMON_UI_GLOSSARY or (_KATA_UI.fullmatch(t) and len(t) <= 16):
        return "ui"
    if _SHORT_SFX.fullmatch(t) and not _CONTENT_HIRA.search(t):
        return "sfx"
    if _CONTENT_HIRA.search(t) and len(t) >= 8:
        return "dialogue"
    if len(t) <= 20:
        return "ui"
    return "other"


def remain_filter_set(cfg) -> Optional[set]:
    """Optional JP allow-list from cfg.extra['remain_filter'] (仅译漏句)."""
    extra = getattr(cfg, "extra", None) or {}
    raw = extra.get("remain_filter")
    if not raw:
        return None
    return {str(x) for x in raw if x}


def parse_remainder_report(path) -> List[str]:
    """Read JP: lines from GalAutoTL_remain.txt."""
    from pathlib import Path

    p = Path(path)
    if not p.is_file():
        return []
    out: List[str] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("JP: "):
            out.append(line[4:])
    return out


def load_remain_filter_from_game(game_dir) -> List[str]:
    from pathlib import Path

    return parse_remainder_report(Path(game_dir) / REMAIN_REPORT_NAME)


def write_remainder_report(
    out_dir,
    pipeline: str,
    sources: Sequence[str],
    mapping: Dict[str, str],
    *,
    max_n: int = 2000,
    log: LogFn = None,
    allow: Optional[set] = None,
) -> int:
    """Write still-JP / unmapped lines to GalAutoTL_remain.txt. Returns count.

    If ``allow`` is set (仅译漏句刚跑完), only report leftovers still in that
    allow-list — do not replace the report with a huge JP rebuild dump.
    """
    from pathlib import Path
    from collections import Counter

    if not out_dir:
        return 0
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    remain = second_pass_sources(sources, mapping, max_n=max_n, allow=allow)
    buckets = Counter(classify_remain_line(s) for s in remain)
    path = root / REMAIN_REPORT_NAME
    lines = [
        f"# GalAutoTL remainder — pipeline={pipeline}",
        f"# count={len(remain)} (cap={max_n})",
        f"# buckets: " + ", ".join(f"{k}={v}" for k, v in sorted(buckets.items())),
        "# categories: dialogue | ui | sfx | path | other",
        "# 仅译漏句：界面点「仅译漏句」会读本文件 JP: 行",
        "",
    ]
    for i, src in enumerate(remain, 1):
        dst = mapping.get(src, src)
        cat = classify_remain_line(src)
        lines.append(f"--- {i} [{cat}] ---")
        lines.append(f"JP: {src}")
        if dst != src:
            lines.append(f"CN: {dst}")
        else:
            lines.append("CN: (same)")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    if log:
        log(f"漏句报告: {len(remain)} 条 → {REMAIN_REPORT_NAME} ({dict(buckets)})")
    return len(remain)
