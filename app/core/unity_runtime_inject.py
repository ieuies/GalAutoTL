# -*- coding: utf-8 -*-
"""Stable Unity injection via BepInEx + XUnity.AutoTranslator (runtime hooks).

Does NOT rewrite data.unity3d — this is the method most CN Unity tutorials use.
Supports Mono (BepInEx 5) and IL2CPP (BepInEx 6 pre + XUA-IL2CPP).
"""
from __future__ import annotations

import json
import re
import shutil
import zipfile
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.request import Request, urlopen

from app.config import appdata_dir

LogFn = Optional[Callable[[str], None]]

Pair = Tuple[str, str]  # source, translated

XUA_REPO = "bbepis/XUnity.AutoTranslator"
BEPINEX_IL2CPP_ASSET = "BepInEx-Unity.IL2CPP-win-x64-6.0.0-pre.2.zip"
BEPINEX_IL2CPP_URL = (
    "https://github.com/BepInEx/BepInEx/releases/download/v6.0.0-pre.2/"
    + BEPINEX_IL2CPP_ASSET
)
BEPINEX_MONO_ASSET = "BepInEx_win_x64_5.4.23.5.zip"
BEPINEX_MONO_URL = (
    "https://github.com/BepInEx/BepInEx/releases/download/v5.4.23.5/" + BEPINEX_MONO_ASSET
)

# ManlyMarco IL2CPP workaround: XUA often misses TextMeshPro/uGUI dirtying on IL2CPP.
BRUTEFORCE_FIX_ASSET = "AutoTranslator.IL2CPP.BruteForceFix_v1.0.zip"
BRUTEFORCE_FIX_URL = (
    "https://github.com/ManlyMarco/RandomPlugins/releases/download/r1/" + BRUTEFORCE_FIX_ASSET
)
BRUTEFORCE_FIX_DLL_NAME = "AutoTranslator.IL2CPP.BruteForceFix.dll"
# Upstream BepInDependency uses SemVer range "5.4" which does NOT accept XUA 5.6.x.
BRUTEFORCE_FIX_DEP_OLD = bytes([0x03]) + b"5.4"
BRUTEFORCE_FIX_DEP_NEW = bytes([0x03]) + b">=0"  # same packed length; widens range


def runtime_cache_dir() -> Path:
    p = appdata_dir() / "unity_runtime"
    p.mkdir(parents=True, exist_ok=True)
    return p


def is_il2cpp(game_dir: Path) -> bool:
    if (game_dir / "GameAssembly.dll").is_file():
        return True
    for d in game_dir.glob("*_Data"):
        if (d / "il2cpp_data" / "Metadata" / "global-metadata.dat").is_file():
            return True
    return False


def is_unity_game(game_dir: Path) -> bool:
    if (game_dir / "UnityPlayer.dll").is_file():
        return True
    return bool(list(game_dir.glob("*_Data")))


def tools_runtime_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "tools" / "unity_runtime"


def detect_unity_version(game_dir: Path) -> Optional[str]:
    """Return full Unity version like '2023.1.15f1' if found in game data."""
    pat = re.compile(rb"(20\d{2}\.\d+\.\d+[abfp]\d+)")
    scan_names = (
        "globalgamemanagers",
        "data.unity3d",
        "resources.assets",
        "sharedassets0.assets",
    )
    for data in game_dir.glob("*_Data"):
        for name in scan_names:
            p = data / name
            if not p.is_file():
                continue
            try:
                with open(p, "rb") as f:
                    blob = f.read(256 * 1024)
            except OSError:
                continue
            m = pat.search(blob)
            if m:
                return m.group(1).decode("ascii", errors="ignore")
    return None


def unity_version_short(full: str) -> str:
    """2023.1.15f1 → 2023.1.15 (BepInEx unity-libs naming)."""
    m = re.match(r"^(20\d{2}\.\d+\.\d+)", full or "")
    return m.group(1) if m else full


def _candidate_urls(url: str) -> List[str]:
    """GitHub / unity-libs hosts may be slow/blocked; try common mirrors."""
    urls = [url]
    hosts = (
        "github.com",
        "objects.githubusercontent.com",
        "unity.bepinex.dev",
    )
    if any(h in url for h in hosts):
        mirrors = [
            "https://ghfast.top/",
            "https://ghproxy.net/",
            "https://mirror.ghproxy.com/",
        ]
        for m in mirrors:
            urls.append(m + url)
    return urls


def _local_package_candidates(dest_name: str) -> List[Path]:
    root = tools_runtime_dir()
    return [
        root / dest_name,
        root / "unity-libs" / dest_name,
        runtime_cache_dir() / dest_name,
        runtime_cache_dir() / "unity-libs" / dest_name,
    ]


def _download(url: str, dest: Path, log: LogFn = None, *, min_size: int = 100_000) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Local drop-in (user can place zip under tools/unity_runtime)
    for local in _local_package_candidates(dest.name):
        if local.is_file() and local.stat().st_size > max(1000, min_size // 10):
            if local.resolve() != dest.resolve():
                shutil.copy2(local, dest)
            if log:
                log(f"使用本地包: {local}")
            return dest
    if dest.is_file() and dest.stat().st_size > min_size:
        if log:
            log(f"使用缓存: {dest.name}")
        return dest
    # remove truncated caches
    if dest.is_file() and dest.stat().st_size <= min_size:
        dest.unlink()

    last_err: Optional[Exception] = None
    for cand in _candidate_urls(url):
        try:
            if log:
                log(f"下载: {cand}")
            req = Request(cand, headers={"User-Agent": "GalAutoTL/1.0"})
            with urlopen(req, timeout=180) as resp, open(dest, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
            if dest.stat().st_size < min_size:
                raise RuntimeError(f"下载文件过小: {dest.stat().st_size}")
            if log:
                log(f"已保存 {dest.name} ({dest.stat().st_size // 1024} KB)")
            # keep a tools/ mirror for offline reinstalls
            tools_copy = tools_runtime_dir() / dest.name
            if "unity-libs" in str(dest).replace("\\", "/"):
                tools_copy = tools_runtime_dir() / "unity-libs" / dest.name
            try:
                tools_copy.parent.mkdir(parents=True, exist_ok=True)
                if not tools_copy.is_file() or tools_copy.stat().st_size != dest.stat().st_size:
                    shutil.copy2(dest, tools_copy)
            except OSError:
                pass
            return dest
        except Exception as e:
            last_err = e
            if dest.is_file():
                dest.unlink(missing_ok=True)
            if log:
                log(f"下载失败，试下一源: {e}")
            continue
    raise RuntimeError(
        f"无法下载依赖包 {dest.name}。\n"
        f"请手动下载后放到:\n  {tools_runtime_dir()}\n"
        f"或:\n  {tools_runtime_dir() / 'unity-libs'}\n"
        f"最后错误: {last_err}"
    )


def ensure_unity_base_libs(game_dir: Path, log: LogFn = None) -> Optional[Path]:
    """Pre-place BepInEx unity-libs zip so IL2CPP does not hang on first download.

    Stuck log line: [InteropManager] Downloading unity base libraries
    Fix: put {version}.zip into BepInEx/unity-libs (do NOT extract).
    """
    if not is_il2cpp(game_dir):
        return None
    full = detect_unity_version(game_dir)
    if not full:
        if log:
            log("警告: 未能探测 Unity 版本，无法预置 unity-libs（首次启动可能卡住下载）")
        return None
    short = unity_version_short(full)
    if log:
        log(f"探测到 Unity {full} → 预置 unity-libs/{short}.zip")
    dest = game_dir / "BepInEx" / "unity-libs" / f"{short}.zip"
    url = f"https://unity.bepinex.dev/libraries/{short}.zip"
    cache = runtime_cache_dir() / "unity-libs" / f"{short}.zip"
    # Prefer cache path then copy into game
    try:
        _download(url, cache, log, min_size=50_000)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.is_file() or dest.stat().st_size != cache.stat().st_size:
            shutil.copy2(cache, dest)
        if log:
            log(f"已预置离线基库: {dest.relative_to(game_dir)} ({dest.stat().st_size // 1024} KB)")
        return dest
    except Exception as e:
        if log:
            log(f"预置 unity-libs 失败（游戏首次启动可能卡在联网下载）: {e}")
            log(f"可手动下载 {url}")
            log(f"放到: {dest}")
        return None


def _github_latest_asset_url(repo: str, name_substr: str) -> str:
    api = f"https://api.github.com/repos/{repo}/releases/latest"
    req = Request(api, headers={"User-Agent": "GalAutoTL/1.0", "Accept": "application/vnd.github+json"})
    with urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    for a in data.get("assets") or []:
        if name_substr in a.get("name", ""):
            return a["browser_download_url"]
    raise RuntimeError(f"GitHub {repo} 未找到含 {name_substr!r} 的资源")


def _extract_zip(zip_path: Path, game_dir: Path, log: LogFn = None) -> None:
    with zipfile.ZipFile(zip_path, "r") as zf:
        # Prevent path traversal
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                continue
            target = game_dir / name
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
    if log:
        log(f"已解压到游戏目录: {zip_path.name}")


def escape_at_key(s: str) -> str:
    """XUnity.AutoTranslator dictionary escaping."""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("=", "{{=}}")
    # keep newlines as literal \n for single-line dict entries
    s = s.replace("\n", "\\n")
    return s


_TMP_TAG_RE = re.compile(r"</?[^>\n]+>")
_ESC_U_RE = re.compile(r"\\u([0-9a-fA-F]{4})")
_LEADING_PUA_RE = re.compile(r"^[\ue000-\uf8ff\uf000-\uf0ff\s　]+")
_SIZE_FIRST_RE = re.compile(
    r"^(?P<pre>\s*)<size[^>]*>(?P<a>.)</size>(?P<b>.*)$",
    re.DOTALL | re.IGNORECASE,
)


def unescape_extracted(s: str) -> str:
    """Turn extracted '\\ue9b0' literals into real PUA chars."""

    def _repl(m: re.Match) -> str:
        try:
            return chr(int(m.group(1), 16))
        except ValueError:
            return m.group(0)

    return _ESC_U_RE.sub(_repl, s)


def expand_pair_variants(
    src: str, dst: str, *, pua_icons: Optional[Sequence[str]] = None
) -> List[Pair]:
    """Generate keys XUA may actually see (TMP splits, no tags, no icons)."""
    out: List[Pair] = []
    seen: Set[str] = set()

    def add(a: str, b: str) -> None:
        a = (a or "").strip("\x00")
        b = (b or "").strip("\x00")
        if not a or not b or a == b:
            return
        if a in seen:
            return
        seen.add(a)
        out.append((a, b))

    src0 = unescape_extracted(src)
    dst0 = unescape_extracted(dst)
    add(src0, dst0)

    # TMP often splits <size=N>X</size>REST into "X" and "REST"
    ms = _SIZE_FIRST_RE.match(src0)
    md = _SIZE_FIRST_RE.match(dst0)
    if ms and md:
        # Never harvest single-digit / punctuation TMP fragments (causes 1→一)
        a, da = ms.group("a"), md.group("a")
        if not re.fullmatch(r"[0-9０-９\W_]", a or ""):
            add(a, da)
        add(ms.group("b"), md.group("b"))
        add(ms.group("pre") + a, md.group("pre") + da)

    # Strip rich-text tags
    src_plain = _TMP_TAG_RE.sub("", src0)
    dst_plain = _TMP_TAG_RE.sub("", dst0)
    add(src_plain, dst_plain)

    # Strip leading private-use icon glyphs (different codepoints per font)
    src_noicon = _LEADING_PUA_RE.sub("", src_plain).lstrip()
    dst_noicon = _LEADING_PUA_RE.sub("", dst_plain).lstrip()
    add(src_noicon, dst_noicon)

    # Online TMP fonts put Material icons before short UI labels — try harvested PUAs
    # Prefer XUA sr: splitter for icons (see write_xua_splitters); only add a few top icons
    if pua_icons and src_noicon and len(src_noicon) <= 24 and src_noicon == src_plain:
        for icon in pua_icons[:4]:
            if not icon:
                continue
            add(f"{icon} {src_noicon}", f"{icon} {dst_noicon}" if dst_noicon else dst_plain)
            add(f"{icon}{src_noicon}", f"{icon}{dst_noicon}" if dst_noicon else dst_plain)

    # Newline forms
    for a, b in list(out):
        if "\r\n" in a:
            add(a.replace("\r\n", "\n"), b.replace("\r\n", "\n"))
        elif "\n" in a:
            add(a.replace("\n", "\r\n"), b.replace("\n", "\r\n"))
    return out


# Short UI words often hooked alone (not as long sentences)
DEFAULT_UI_PAIRS: List[Pair] = [
    ("スタート", "开始"),
    ("閉じる", "关闭"),
    ("敗北", "失败"),
    ("回想", "回想"),
    ("体験版", "试玩版"),
    ("イベント", "活动"),
    ("エンディング", "结局"),
    ("カード図鑑", "卡片图鉴"),
    ("プレイ記録", "游玩记录"),
    ("画像", "图像"),
    ("クレジット", "制作人员"),
    ("ファイル", "文件"),
    ("ボイス再生", "语音播放"),
    ("テキストをコピーします", "复制文本"),
    ("ドラッグ", "毒品"),
    ("クリア！", "通关！"),
]


def _junk_runtime_key(src: str) -> bool:
    from app.core.xua_match_rules import is_poison_dict_key

    s = src.strip()
    if not s:
        return True
    if is_poison_dict_key(s):
        return True
    low = s.lower()
    if any(
        x in low
        for x in (
            "mscorlib",
            "system.",
            "unityengine",
            "il2cpp",
            "assembly-",
            ".dll",
            "___",
        )
    ):
        return True
    # IL2CPP heap glued monsters
    if s.count("{") > 40 or s.count("\n") > 80:
        return True
    return False


def write_translation_file(
    game_dir: Path,
    pairs: Sequence[Pair],
    lang: str = "zh-CN",
    log: LogFn = None,
    *,
    merge: bool = False,
) -> Path:
    """Write high-priority manual translation file under BepInEx/Translation.

    If ``merge``, keep existing GalAutoTL.txt entries and overlay new pairs
    (仅译漏句 must not shrink the XUA dictionary to leftovers only).
    """
    # Prefer BepInEx path; also mirror under AutoTranslator for ReiPatcher layouts
    text_dir = game_dir / "BepInEx" / "Translation" / lang / "Text"
    text_dir.mkdir(parents=True, exist_ok=True)
    out = text_dir / "GalAutoTL.txt"
    lines: List[str] = [
        "# Generated by GalAutoTL — runtime inject (does not modify data.unity3d)",
        "# Format: Japanese=Chinese",
    ]
    seen = set()
    n = 0

    def _add(key_src: str, val_src: str) -> None:
        nonlocal n
        key = escape_at_key(key_src)
        if key in seen:
            # later pairs win — replace line if already present
            for i, line in enumerate(lines):
                if line.startswith(key + "="):
                    lines[i] = f"{key}={escape_at_key(val_src)}"
                    return
            return
        seen.add(key)
        lines.append(f"{key}={escape_at_key(val_src)}")
        n += 1

    # Merge prior dictionary first so remain_only overlays instead of wiping
    if merge and out.is_file():
        try:
            for line in out.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if not k:
                    continue
                if k not in seen:
                    seen.add(k)
                    lines.append(f"{k}={v}")
                    n += 1
        except OSError:
            pass

    from app.core.il2cpp_stringliteral import harvest_pua_icons
    from app.core.xua_match_rules import scrub_translation_pair

    cleaned: List[Pair] = []
    for src, dst in list(DEFAULT_UI_PAIRS) + list(pairs):
        fixed = scrub_translation_pair(src, dst)
        if fixed:
            cleaned.append(fixed)
    # Harvest TMP icon codepoints so short UI labels match カード図鑑 style
    pua_icons = harvest_pua_icons([s for s, _ in cleaned])
    for src, dst in cleaned:
        if _junk_runtime_key(src) or len(src) > 2500:
            continue
        for vs, vd in expand_pair_variants(src, dst, pua_icons=pua_icons):
            fixed = scrub_translation_pair(vs, vd)
            if not fixed:
                continue
            vs, vd = fixed
            if _junk_runtime_key(vs) or len(vs) > 2500:
                continue
            # Hard gate: never ship 2→两 style lines into XUA dict
            from app.core.xua_match_rules import digits_eaten, preserve_arabic_digits

            vd = preserve_arabic_digits(vs, vd)
            if digits_eaten(vs, vd):
                continue
            _add(vs, vd)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Refresh seeded auto file so Alt+R always has fallbacks
    auto = text_dir / "_AutoGeneratedTranslations.txt"
    auto.write_text(
        "# seeded by GalAutoTL\n" + "\n".join(lines[2:]) + "\n",
        encoding="utf-8",
    )
    if log:
        mode = "合并" if merge else "重写"
        log(f"已写运行时译文表 {n} 条（{mode}，含 TMP/图标变体）→ {out.relative_to(game_dir)}")
    write_xua_helper_files(game_dir, lang, log)
    return out


def write_xua_helper_files(game_dir: Path, lang: str = "zh-CN", log: LogFn = None) -> None:
    """Write splitter regex + enable online matching tips (sr:, untranslatable dump)."""
    from app.core.xua_match_rules import XUA_SPLITTER_RULES

    text_dir = game_dir / "BepInEx" / "Translation" / lang / "Text"
    text_dir.mkdir(parents=True, exist_ok=True)
    split = text_dir / "GalAutoTL_Splitters.txt"
    split.write_text(XUA_SPLITTER_RULES.lstrip() + "\n", encoding="utf-8")
    # Preprocessors: normalize before endpoint (also helps static path in some builds)
    pre = text_dir / "_Preprocessors.txt"
    if not pre.is_file() or pre.stat().st_size < 20:
        pre.write_text(
            "# normalize fullwidth spaces\n"
            "　= \n",
            encoding="utf-8",
        )
    en_dir = game_dir / "BepInEx" / "Translation" / "en" / "Text"
    if en_dir.is_dir() or True:
        en_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(split, en_dir / "GalAutoTL_Splitters.txt")
    # Config knobs used by tutorials for partial / whitespace / miss dump
    cfg = game_dir / "BepInEx" / "config" / "AutoTranslatorConfig.ini"
    if cfg.is_file():
        patch_autotranslator_ini_keys(
            cfg,
            {
                "IgnoreWhitespaceInDialogue": "True",
                "CacheRegexLookups": "True",
                "CacheRegexPatternResults": "True",
                # Need 2 so icon/number prefix + body can both resolve (long TMP lines)
                "MaxTextParserRecursion": "2",
                # False: typewriter partial keys cause same line drawn twice (stacked ghost)
                "GeneratePartialTranslations": "False",
                "HandleRichText": "False",
                "OutputUntranslatableText": "True",
                "EnableSilentMode": "False",
            },
        )
    if log:
        log(f"已写 XUA 拆分规则 → {split.relative_to(game_dir)}")


def patch_autotranslator_ini_keys(path: Path, updates: dict) -> None:
    """Update keys in existing AutoTranslatorConfig.ini without wiping migrated sections."""
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(
        r"(?ms)(^\[Custom\]\s*\r?\n)Url=\s*\r?\n\s*http://127\.0\.0\.1:8765/\s*\r?\n",
        r"\1Url=http://127.0.0.1:8765/\n",
        text,
        count=1,
    )
    for key, value in updates.items():
        if key == "Url":
            pat = re.compile(r"(?ms)(^\[Custom\]\s*\r?\n)(.*?)(?=^\[|\Z)")
            m = pat.search(text)
            if m and re.search(r"(?m)^Url\s*=", m.group(2)):
                block = re.sub(r"(?m)^Url\s*=\s*.*$", f"Url={value}", m.group(2), count=1)
                text = text[: m.start(2)] + block + text[m.end(2) :]
                continue
        pat = re.compile(rf"(?m)^({re.escape(key)}\s*=\s*).*$")
        if pat.search(text):
            text = pat.sub(rf"\g<1>{value}", text)
        else:
            # append under [Behaviour] if present
            if "[Behaviour]" in text:
                text = text.replace("[Behaviour]", f"[Behaviour]\n{key}={value}", 1)
            else:
                text += f"\n{key}={value}\n"
    path.write_text(text, encoding="utf-8")


def write_autotranslator_config(
    game_dir: Path,
    target_lang: str = "zh-CN",
    source_lang: str = "ja",
    *,
    mode: str = "offline",
) -> Path:
    """Write/patch XUA config.

    mode:
      offline — Endpoint empty, only GalAutoTL.txt (no API, no lag)
      harvest — CustomTranslate local proxy (online 补采, mirrors社区游玩采集流程)
    """
    cfg_dir = game_dir / "BepInEx" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "AutoTranslatorConfig.ini"
    # Lang folder name must match Translation/{Lang}/
    lang = target_lang if target_lang.startswith("zh") else "zh-CN"
    if target_lang in ("zh_cn", "zh-cn", "zh"):
        lang = "zh-CN"
    elif target_lang in ("zh_tw", "zh-tw"):
        lang = "zh-TW"
    from_lang = source_lang if source_lang not in ("auto", "other") else "ja"
    harvest = mode == "harvest"
    proxy = "http://127.0.0.1:8765/"

    updates = {
        "Endpoint": "CustomTranslate" if harvest else "",
        "FallbackEndpoint": "",
        "Language": lang,
        "FromLanguage": from_lang,
        "ForceMonoModHooks": "False",
        "InitializeHarmonyDetourBridge": "False",
        "UseStaticTranslations": "True",
        "UseTextMeshPro": "True",
        "UseUGUI": "True",
        "UseIMGUI": "False",
        "ReloadTranslationsOnFileChange": "True",
        "CacheParsedTranslations": "True",
        "EnableBatching": "False",
        "IgnoreWhitespaceInDialogue": "True",
        "CacheRegexLookups": "True",
        "CacheRegexPatternResults": "True",
        "MaxTextParserRecursion": "2",
        # Partial keys during typewriter = same line drawn twice (stacked ghost text)
        "GeneratePartialTranslations": "False",
        "HandleRichText": "False",
        "OutputUntranslatableText": "True",
        "EnableSilentMode": "False",
        "EnableUIResizing": "False",
        "ForceUIResizing": "False",
    }
    if harvest:
        updates["Url"] = proxy
        updates["EnableShortDelay"] = "True"
        updates["DisableSpamChecks"] = "True"

    if path.is_file() and path.stat().st_size > 500:
        patch_autotranslator_ini_keys(path, updates)
        text = path.read_text(encoding="utf-8", errors="replace")
        if harvest and "[Custom]" not in text:
            path.write_text(
                text + f"\n[Custom]\nUrl={proxy}\nEnableShortDelay=True\nDisableSpamChecks=True\n",
                encoding="utf-8",
            )
        return path

    custom_block = (
        f"[Custom]\nUrl={proxy}\nEnableShortDelay=True\nDisableSpamChecks=True\n\n"
        if harvest
        else ""
    )
    content = f"""\
[Service]
Endpoint={"CustomTranslate" if harvest else ""}
FallbackEndpoint=

[General]
Language={lang}
FromLanguage={from_lang}
Directory=Translation
OutputFile=Translation\\{{Lang}}\\Text\\_AutoGeneratedTranslations.txt

{custom_block}[Behaviour]
MaxCharactersPerTranslation=2500
MinLength=1
MaxLength=-1
ForceMonoModHooks=False
InitializeHarmonyDetourBridge=False
UseTextMeshPro=True
UseUGUI=True
UseIMGUI=False
UseNGUI=True
UseFairyGUI=True
EnableIMGUITextureTranslation=False
EnableUGUITextureTranslation=False
EnableNGUITextureTranslation=False
EnableTextMeshProTextureTranslation=False
ReloadTranslationsOnFileChange=True
CacheRegexLookups=True
CacheParsedTranslations=True
UseStaticTranslations=True
EnableBatching=False
GeneratePartialTranslations=False
HandleRichText=False
EnableUIResizing=False
ForceUIResizing=False
"""
    path.write_text(content, encoding="utf-8")
    return path


def _parse_xua_dict_file(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        tmp = raw.replace("{{=}}", "\0")
        if "=" not in tmp:
            continue
        k, v = tmp.split("=", 1)
        k = k.replace("\0", "=").replace("\\n", "\n")
        v = v.replace("\0", "=").replace("\\n", "\n")
        if k and v and k not in out:
            out[k] = v
    return out


def merge_xua_dictionaries(game_dir: Path, lang: str = "zh-CN", log: LogFn = None) -> int:
    """Merge all Translation text dumps into GalAutoTL.txt (社区：采集文件并入静态包)."""
    game_dir = Path(game_dir)
    text_dirs = [
        game_dir / "BepInEx" / "Translation" / lang / "Text",
        game_dir / "BepInEx" / "Translation" / "en" / "Text",
    ]
    merged: Dict[str, str] = {}
    for td in text_dirs:
        if not td.is_dir():
            continue
        for fp in sorted(td.glob("*.txt")):
            if fp.name.startswith("_Pre") or fp.name.startswith("_Post") or fp.name.startswith("_Sub"):
                continue
            part = _parse_xua_dict_file(fp)
            for k, v in part.items():
                if k not in merged:
                    merged[k] = v
    pairs = list(merged.items())
    write_translation_file(game_dir, pairs, lang, log)
    # mirror for Language=en fallback
    en_dir = game_dir / "BepInEx" / "Translation" / "en" / "Text"
    zh_dir = game_dir / "BepInEx" / "Translation" / lang / "Text"
    if zh_dir.is_dir():
        en_dir.mkdir(parents=True, exist_ok=True)
        for name in ("GalAutoTL.txt", "_AutoGeneratedTranslations.txt"):
            src = zh_dir / name
            if src.is_file():
                shutil.copy2(src, en_dir / name)
    if log:
        log(f"已合并词典 → GalAutoTL（唯一键约 {len(merged)}）")
    return len(merged)


def set_xua_runtime_mode(
    game_dir: Path,
    mode: str = "offline",
    *,
    target_lang: str = "zh-CN",
    source_lang: str = "ja",
    log: LogFn = None,
) -> None:
    """offline = 静态词典；harvest = 游玩补采（本地 API 代理）— 对标网上两阶段流程。"""
    game_dir = Path(game_dir)
    if mode == "offline":
        n = merge_xua_dictionaries(game_dir, target_lang if target_lang.startswith("zh") else "zh-CN", log)
        if log:
            log(f"离线锁定：已合并 {n} 条，关闭 Endpoint")
    write_autotranslator_config(game_dir, target_lang, source_lang, mode=mode)
    write_cn_launcher(game_dir, log, mode=mode)
    if log:
        if mode == "harvest":
            log("补采模式已开：用「点我启动_中文汉化_Unity.bat」多玩一会，漏句会经 API 写入词典")
            log("玩完后在工具点「合并并锁定离线」即可不再调 API")
        else:
            log("已锁定离线静态词典（不再调 API）")


def write_cn_launcher(game_dir: Path, log: LogFn = None, *, mode: str = "offline") -> Optional[Path]:
    exes = [
        p
        for p in game_dir.glob("*.exe")
        if p.name.lower() not in ("unitycrashhandler64.exe", "unitycrashhandler32.exe")
        and "setup" not in p.name.lower()
        and "crash" not in p.name.lower()
    ]
    if not exes:
        return None
    exes.sort(key=lambda p: p.stat().st_size, reverse=True)
    main = exes[0]
    bat = game_dir / "点我启动_中文汉化_Unity.bat"
    if mode == "harvest":
        tool_root = Path(__file__).resolve().parents[2]
        local_proxy = game_dir / "_galautotl_xua_proxy.py"
        local_proxy.write_text(
            "# -*- coding: utf-8 -*-\n"
            "import sys\n"
            "from pathlib import Path\n"
            "GAME = Path(__file__).resolve().parent\n"
            f"TOOL = Path({str(tool_root)!r})\n"
            "sys.path.insert(0, str(TOOL))\n"
            "from app.core.xua_custom_server import serve\n"
            'if __name__ == "__main__":\n'
            "    serve(GAME, int(sys.argv[1]) if len(sys.argv) > 1 else 8765)\n",
            encoding="utf-8",
        )
        (game_dir / "_galautotl_xua_proxy.bat").write_text(
            "@echo off\nchcp 65001 >nul\n"
            'cd /d "%~dp0"\n'
            'py -3 "%~dp0_galautotl_xua_proxy.py" %*\n',
            encoding="utf-8",
        )
        bat.write_text(
            "@echo off\n"
            "chcp 65001 >nul\n"
            'cd /d "%~dp0"\n'
            'start "GalAutoTL-XUA" /MIN "%~dp0_galautotl_xua_proxy.bat"\n'
            "timeout /t 1 /nobreak >nul\n"
            f'start "" "{main.name}"\n',
            encoding="utf-8",
        )
        if log:
            log(f"启动器: {bat.name}（补采模式：代理+{main.name}）")
    else:
        bat.write_text(
            "@echo off\n"
            "chcp 65001 >nul\n"
            'cd /d "%~dp0"\n'
            f'start "" "{main.name}"\n',
            encoding="utf-8",
        )
        if log:
            log(f"启动器: {bat.name} → {main.name}（仅静态词典，不调 API）")
    return bat


def _patch_bruteforce_fix_dependency(dll_bytes: bytes) -> bytes:
    """Widen BepInDependency version so XUA 5.6.1 satisfies the range."""
    if BRUTEFORCE_FIX_DEP_NEW in dll_bytes and BRUTEFORCE_FIX_DEP_OLD not in dll_bytes:
        return dll_bytes
    if b"5.6.1" in dll_bytes and b"gravydevsupreme.xunity.autotranslator" in dll_bytes:
        # Already retargeted (e.g. Mono.Cecil patched copy).
        if BRUTEFORCE_FIX_DEP_OLD not in dll_bytes:
            return dll_bytes
    if BRUTEFORCE_FIX_DEP_OLD not in dll_bytes:
        return dll_bytes
    return dll_bytes.replace(BRUTEFORCE_FIX_DEP_OLD, BRUTEFORCE_FIX_DEP_NEW, 1)


def ensure_il2cpp_bruteforce_fix(game_dir: Path, log: LogFn = None) -> Optional[Path]:
    """Install AutoTranslator.IL2CPP.BruteForceFix with XUA 5.6.x-compatible dependency."""
    if not is_il2cpp(game_dir):
        return None
    plugins = game_dir / "BepInEx" / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    dest = plugins / BRUTEFORCE_FIX_DLL_NAME

    # Prefer a pre-patched DLL from tools/cache (Cecil 5.6.1 retarget) or tools/unity_runtime.
    candidates = [
        tools_runtime_dir().parent / "cache" / "AutoTranslator.IL2CPP.BruteForceFix.patched.dll",
        tools_runtime_dir().parent / "cache" / BRUTEFORCE_FIX_DLL_NAME,
        tools_runtime_dir() / BRUTEFORCE_FIX_DLL_NAME,
        runtime_cache_dir() / BRUTEFORCE_FIX_DLL_NAME,
    ]
    for cand in candidates:
        if cand.is_file() and cand.stat().st_size > 1000:
            data = _patch_bruteforce_fix_dependency(cand.read_bytes())
            dest.write_bytes(data)
            if log:
                log(f"已安装 BruteForceFix (patched): {dest}")
            return dest

    cache = runtime_cache_dir()
    zip_path = _download(
        BRUTEFORCE_FIX_URL,
        cache / BRUTEFORCE_FIX_ASSET,
        log,
        min_size=5_000,
    )
    dll_bytes: Optional[bytes] = None
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if name.endswith(BRUTEFORCE_FIX_DLL_NAME):
                dll_bytes = zf.read(name)
                break
    if not dll_bytes:
        raise RuntimeError(f"zip 中未找到 {BRUTEFORCE_FIX_DLL_NAME}")
    dll_bytes = _patch_bruteforce_fix_dependency(dll_bytes)
    # Mirror patched bytes into tools cache for offline reinstalls
    mirror = tools_runtime_dir().parent / "cache" / BRUTEFORCE_FIX_DLL_NAME
    try:
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_bytes(dll_bytes)
    except OSError:
        pass
    dest.write_bytes(dll_bytes)
    if log:
        log(f"已安装 BruteForceFix (dep widened): {dest}")
    return dest


def ensure_runtime_plugins(game_dir: Path, log: LogFn = None) -> str:
    """Download+extract BepInEx + XUnity.AutoTranslator. Returns backend tag."""
    cache = runtime_cache_dir()
    il2cpp = is_il2cpp(game_dir)
    if il2cpp:
        if log:
            log("检测到 IL2CPP → 安装 BepInEx 6 (Unity.IL2CPP) + AutoTranslator-IL2CPP")
        bep = _download(BEPINEX_IL2CPP_URL, cache / BEPINEX_IL2CPP_ASSET, log)
        xua_url = _github_latest_asset_url(XUA_REPO, "BepInEx-IL2CPP")
        xua = _download(xua_url, cache / Path(xua_url).name, log)
        backend = "il2cpp"
    else:
        if log:
            log("检测到 Mono → 安装 BepInEx 5 + AutoTranslator")
        bep = _download(BEPINEX_MONO_URL, cache / BEPINEX_MONO_ASSET, log)
        xua_url = _github_latest_asset_url(XUA_REPO, "BepInEx-5.")
        # asset name is XUnity.AutoTranslator-BepInEx-5.6.1.zip (not IL2CPP)
        # fix substr: use exact filter
        api = "https://api.github.com/repos/bbepis/XUnity.AutoTranslator/releases/latest"
        req = Request(api, headers={"User-Agent": "GalAutoTL/1.0"})
        with urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        xua_url = None
        for a in data["assets"]:
            n = a["name"]
            if n.startswith("XUnity.AutoTranslator-BepInEx-") and "IL2CPP" not in n and "Developer" not in n:
                xua_url = a["browser_download_url"]
                break
        if not xua_url:
            raise RuntimeError("未找到 XUnity.AutoTranslator-BepInEx 包")
        xua = _download(xua_url, cache / Path(xua_url).name, log)
        backend = "mono"

    _extract_zip(bep, game_dir, log)
    _extract_zip(xua, game_dir, log)
    if backend == "il2cpp":
        ensure_unity_base_libs(game_dir, log)
        ensure_il2cpp_bruteforce_fix(game_dir, log)
    return backend


def deploy_runtime_inject(
    game_dir: Path,
    pairs: Sequence[Pair],
    *,
    target_lang: str = "zh_cn",
    source_lang: str = "ja",
    log: LogFn = None,
    merge_dict: bool = False,
) -> None:
    """Full stable inject: plugins + translation dict + config + launcher."""
    game_dir = Path(game_dir)
    if not is_unity_game(game_dir):
        raise RuntimeError("不是 Unity 游戏目录")

    # Map cfg lang
    lang_map = {"zh_cn": "zh-CN", "zh_tw": "zh-TW", "zh": "zh-CN"}
    at_lang = lang_map.get(target_lang, "zh-CN")

    ensure_runtime_plugins(game_dir, log)
    write_autotranslator_config(game_dir, at_lang, source_lang or "ja", mode="offline")
    write_translation_file(game_dir, pairs, at_lang, log, merge=merge_dict)
    write_cn_launcher(game_dir, log, mode="offline")

    readme = game_dir / "汉化启动说明_Unity.txt"
    readme.write_text(
        "GalAutoTL Unity 稳定注入说明\n"
        "==========================\n"
        "方式：BepInEx + XUnity.AutoTranslator（静态词典运行时替换，不改 data.unity3d）\n"
        "\n"
        "1. 用「点我启动_中文汉化_Unity.bat」或直接开游戏 exe\n"
        "2. 译文包：BepInEx\\Translation\\zh-CN\\Text\\GalAutoTL.txt（一键汉化时写好）\n"
        "3. 游玩时不调用 API，只查静态表；词典未覆盖的句子会保持原文\n"
        "4. 若卡在 Downloading unity base libraries：把版本 zip 放到 BepInEx\\unity-libs\\\n"
        "5. Alt+R 重载 / Alt+U 手动扫描\n"
        "6. 台词叠两层/重影：打开 BepInEx\\config\\AutoTranslatorConfig.ini，确认\n"
        "   GeneratePartialTranslations=False、HandleRichText=False、UseIMGUI=False，\n"
        "   删掉 Translation\\zh-CN\\Text\\_AutoGeneratedTranslations.txt 后重开游戏\n"
        "7. 卸载：删除 winhttp.dll、doorstop_config.ini、BepInEx 文件夹\n",
        encoding="utf-8",
    )
    if log:
        log(f"说明已写: {readme.name}")
        log("稳定注入完成（未改动 data.unity3d）")
