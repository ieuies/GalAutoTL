# -*- coding: utf-8 -*-
"""Unity MonoBehaviour raw UTF-8 extract/patch (IL2CPP / stripped TypeTrees).

When typetrees are missing, dialogue still sits as UTF-8 inside get_raw_data().
Patch by same-byte-length replace + UnityPy set_raw_data + bundle save(packer=original).
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

LogFn = Optional[Callable[[str], None]]

# UTF-8 byte sequences that look like printable text (ASCII + multi-byte UTF-8)
_U8_RUN = re.compile(rb"(?:[\x09\x0a\x0d\x20-\x7e]|[\xc2-\xf4][\x80-\xbf]{1,3}){2,4000}")
_HIRA_U8 = re.compile(rb"\xe3(?:\x81[\x81-\xbf]|\x82[\x80-\x9f])")
_KATA_U8 = re.compile(rb"\xe3(?:\x82[\xa0-\xbf]|\x83[\x80-\xbf])")
_BRACKET_U8 = re.compile(rb"\xe3\x80\x8c|\xe3\x80\x8d")  # 「」

HAS_KANA = re.compile(r"[\u3040-\u30ff]")
HAS_CJK = re.compile(r"[\u4e00-\u9fff]")

# Prefer these MonoScript class names; also accept any MB with enough JP density.
CLASS_WHITELIST = {
    "Card",
    "BasicEnemyCard",
    "HoverHelp",
    "State",
    "Relic",
    "EnemyModel",
    "TextMeshProUGUI",
    "TextMeshPro",
    "Text",
    "TMP_Text",
    "LocalizationLabel",
    "LocalizedResources",
    "Dialog",
    "DialogItem",
    "MessageWindow",
}

# Skip huge font / glyph tables
SKIP_NAME_HINTS = ("Font", "Atlas", "Glyph", "Charset", "LineBreaking")


@dataclass
class MbUnit:
    asset_path: str  # path to .unity3d / .assets
    path_id: int
    assets_file_name: str  # identity inside bundle
    classname: str
    obj_name: str
    offset: int
    length: int  # utf-8 byte length
    source: str


def fit_utf8_bytes(text: str, nbytes: int, pad: bytes = b"\x00") -> bytes:
    """Fit translated text into exactly nbytes UTF-8.

    Pad with NUL (not spaces) so we don't bleed into adjacent binary / next
    IL2CPP string-heap slots.
    """
    if nbytes <= 0:
        return b""
    if not pad:
        pad = b"\x00"
    pad_b = pad[:1]
    raw = text.encode("utf-8")
    if len(raw) == nbytes:
        return raw
    if len(raw) < nbytes:
        return raw + pad_b * (nbytes - len(raw))
    s = text
    while s and len(s.encode("utf-8")) > nbytes:
        s = s[:-1]
    raw = s.encode("utf-8")
    if len(raw) > nbytes:
        raw = raw[:nbytes]
        while raw and (raw[-1] & 0xC0) == 0x80:
            raw = raw[:-1]
        while raw and (raw[-1] & 0xC0) == 0xC0:
            raw = raw[:-1]
    return raw + pad_b * (nbytes - len(raw))


def _want_jp(s: str, *, loose: bool = False) -> bool:
    s = s.strip()
    min_len = 2 if loose else 2
    if len(s) < min_len:
        return False
    if s.startswith(("Assets/", "Character/", "Background/", "Audio/", "SE/", "BGM/")):
        return False
    if s.startswith(("get_", "set_", "m_")):
        return False
    if re.fullmatch(r"[A-Za-z0-9_./\\:-]+", s):
        return False
    # font charset dumps: too many unique kana, little punctuation
    kana = HAS_KANA.findall(s)
    if len(s) > 100 and len(set(kana)) > 40 and s.count("。") + s.count("「") < 2:
        return False
    if HAS_KANA.search(s) or ("「" in s and HAS_CJK.search(s)):
        return True
    # First-pass runtime dict: short UI often is kata/CJK only fragments
    if loose and HAS_CJK.search(s) and len(s) <= 40:
        return True
    return False


def _extract_runs(raw: bytes, *, loose: bool = False) -> List[Tuple[int, str]]:
    """Return (byte_offset, decoded_text) for JP-looking UTF-8 runs."""
    out: List[Tuple[int, str]] = []
    if not raw:
        return out
    for m in _U8_RUN.finditer(raw):
        frag = m.group(0)
        if not (
            _HIRA_U8.search(frag)
            or _KATA_U8.search(frag)
            or _BRACKET_U8.search(frag)
            or (loose and re.search(rb"[\xe4-\xe9][\x80-\xbf]{2}", frag))
        ):
            continue
        try:
            text = frag.decode("utf-8")
        except UnicodeDecodeError:
            continue
        text_s = text.strip("\x00")
        if not _want_jp(text_s, loose=loose):
            continue
        # trim leading/trailing whitespace in match but keep offset of stripped content
        lead = len(text) - len(text.lstrip(" \t\r\n"))
        core = text.strip(" \t\r\n")
        if not _want_jp(core, loose=loose):
            continue
        off = m.start() + lead
        # re-verify exact bytes exist at offset
        enc = core.encode("utf-8")
        if raw[off : off + len(enc)] != enc:
            # fall back to full match bytes
            off = m.start()
            enc = frag
            try:
                core = frag.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if not _want_jp(core, loose=loose):
                continue
            enc = core.encode("utf-8")
            # find first occurrence within frag
            idx = frag.find(enc)
            if idx < 0:
                continue
            off = m.start() + idx
        out.append((off, core))
    return out


def find_unity_asset_files(game_dir: Path) -> List[Path]:
    """Classic *_Data assets + encrypted/plain .bundle / Addressables packs."""
    from app.core.unity_bundle_crypto import expand_asset_globs

    files = expand_asset_globs(game_dir)
    seen: Set[str] = set()
    out: List[Path] = []
    for f in files:
        if not f.is_file() or f.stat().st_size < 64:
            continue
        k = str(f.resolve()).lower()
        if k in seen:
            continue
        # skip pure audio/video dumps
        if f.suffix.lower() in (".resource", ".ress", ".mp4", ".wav", ".ogg"):
            continue
        # skip enormous packs > 800MB unless named like data/shared (still allow but deprioritize)
        seen.add(k)
        out.append(f)
    # Prefer medium/large text-ish packs; deprioritize multi-GB
    def score(p: Path) -> tuple:
        sz = p.stat().st_size
        name = p.name.lower()
        bonus = 0
        if any(x in name for x in ("data", "shared", "script", "text", "loc", "story", "adv")):
            bonus -= 1
        if p.suffix.lower() in (".bundle", ".ab"):
            bonus += 0
        # huge cinematic bundles last
        huge = 1 if sz > 400_000_000 else 0
        return (huge, bonus, -min(sz, 200_000_000), name)

    out.sort(key=score)
    # Cap extreme counts (Addressables can be thousands)
    if len(out) > 400:
        out = out[:400]
    return out


def _unity_load(fp: Path, game_dir: Path, log: LogFn = None):
    from app.core.unity_bundle_crypto import load_unity_env

    cache = game_dir / "_galautotl_unity" / "ab_dec"
    return load_unity_env(fp, cache_dir=cache, log=log, game_dir=Path(game_dir))


def _scripts_map(env) -> Dict[int, Tuple[str, str]]:
    scripts: Dict[int, Tuple[str, str]] = {}
    for obj in env.objects:
        if obj.type.name != "MonoScript":
            continue
        try:
            d = obj.read()
            scripts[obj.path_id] = (
                getattr(d, "m_ClassName", "") or "",
                getattr(d, "m_Namespace", "") or "",
            )
        except Exception:
            continue
    return scripts


def _mb_head(obj, scripts: Dict[int, Tuple[str, str]]) -> Tuple[str, str, str]:
    name, classname, namespace = "", "", ""
    try:
        head = obj.parse_monobehaviour_head()
    except Exception:
        return name, classname, namespace
    if isinstance(head, dict):
        name = head.get("m_Name") or ""
        ms = head.get("m_Script")
    else:
        name = getattr(head, "m_Name", "") or ""
        ms = getattr(head, "m_Script", None)
    pid = None
    if isinstance(ms, dict):
        pid = ms.get("m_PathID") or ms.get("path_id")
    else:
        pid = getattr(ms, "m_PathID", None) or getattr(ms, "path_id", None)
    if pid in scripts:
        classname, namespace = scripts[pid]
    return str(name), classname, namespace


def collect_mb_units(
    game_dir: Path, log: LogFn = None, *, for_runtime: bool = False
) -> List[MbUnit]:
    try:
        import UnityPy
    except ImportError:
        if log:
            log("未安装 UnityPy，跳过 MonoBehaviour 原始字串（pip install UnityPy）")
        return []

    units: List[MbUnit] = []
    asset_files = find_unity_asset_files(game_dir)
    if not asset_files:
        if log:
            log("未找到 .unity3d / .assets / .bundle")
        return []
    if log:
        log(f"Unity 资源文件: {len(asset_files)} 个（含 bundle/Addressables，优先可解包）")
        try:
            from app.core.unity_bundle_crypto import apply_unity_cn_keys, discover_unity_cn_keys

            keys = discover_unity_cn_keys(game_dir)
            if keys:
                apply_unity_cn_keys(keys, log)
        except Exception:
            pass

    for fp in asset_files:
        if log:
            log(f"  扫描 MonoBehaviour: {fp.name} ({fp.stat().st_size // (1024 * 1024)} MB)")
        try:
            env, how = _unity_load(fp, game_dir, log)
            if log and how not in ("plain", "undecrypted"):
                log(f"    加载方式: {how}")
        except Exception as e:
            if log:
                log(f"  加载失败: {e}")
            continue
        scripts = _scripts_map(env)
        n_mb = 0
        n_hit = 0
        for obj in env.objects:
            if obj.type.name != "MonoBehaviour":
                continue
            n_mb += 1
            try:
                obj_name, classname, namespace = _mb_head(obj, scripts)
                if any(h.lower() in (obj_name or "").lower() for h in SKIP_NAME_HINTS):
                    continue
                if any(h.lower() in (classname or "").lower() for h in SKIP_NAME_HINTS):
                    continue
                raw = obj.get_raw_data()
                if not raw or len(raw) < 8:
                    continue
                runs = _extract_runs(raw, loose=for_runtime)
                if not runs:
                    continue
                # Strict for asset rewrite; much looser for AutoTranslator runtime dict
                ok_class = classname in CLASS_WHITELIST
                if for_runtime:
                    # Any JP-bearing MB is useful as a dictionary source
                    ok_class = True
                elif not ok_class:
                    ok_class = False
                if not ok_class:
                    continue
                # Drop glued mega-blobs for asset rewrite; allow longer for runtime dict
                max_len = 2500 if for_runtime else 240
                max_brackets = 40 if for_runtime else 4
                runs = [
                    (off, text)
                    for off, text in runs
                    if len(text.encode("utf-8")) <= max_len and text.count("「") <= max_brackets
                ]
                if not runs:
                    continue
                assets_name = ""
                try:
                    assets_name = getattr(obj.assets_file, "name", "") or ""
                except Exception:
                    assets_name = ""
                for off, text in runs:
                    enc = text.encode("utf-8")
                    if raw[off : off + len(enc)] != enc:
                        continue
                    # Prefer length-prefixed Unity string: int32 LE length == utf8 len at off-4
                    # (informational only; same-byte replace keeps header valid)
                    units.append(
                        MbUnit(
                            asset_path=str(fp.resolve()),
                            path_id=obj.path_id,
                            assets_file_name=str(assets_name),
                            classname=classname or "?",
                            obj_name=obj_name or "",
                            offset=off,
                            length=len(enc),
                            source=text,
                        )
                    )
                    n_hit += 1
            except Exception:
                continue
        if log:
            log(f"  MonoBehaviour {n_mb}，命中字串片段 {n_hit}")

    # dedupe identical (asset, path_id, offset)
    seen: Set[Tuple[str, int, int]] = set()
    uniq: List[MbUnit] = []
    for u in units:
        k = (u.asset_path.lower(), u.path_id, u.offset)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(u)
    if log:
        log(f"MonoBehaviour 待译条目: {len(uniq)}")
    return uniq


def collect_runtime_jp_corpus(game_dir: Path, log: LogFn = None) -> List[str]:
    """Aggressive unique JP strings across ALL Unity objects (first-pass dictionary).

    Online-style broader harvest: not just MonoBehaviour whitelist — any object raw
    with JP UTF-8. Deduped by exact text for AutoTranslator dict only (no writeback).
    """
    try:
        import UnityPy
    except ImportError:
        return []

    seen: Set[str] = set()
    out: List[str] = []

    def add(text: str) -> None:
        t = (text or "").strip("\x00").strip()
        if not t or t in seen:
            return
        if not _want_jp(t, loose=True):
            return
        if len(t) > 2500:
            # still take sentence crumbs
            for part in re.split(r"(?<=[。！？\n、])", t):
                p = part.strip()
                if p and p not in seen and _want_jp(p, loose=True) and len(p) <= 2500:
                    seen.add(p)
                    out.append(p)
            return
        seen.add(t)
        out.append(t)

    asset_files = find_unity_asset_files(game_dir)
    try:
        from app.core.unity_bundle_crypto import apply_unity_cn_keys, discover_unity_cn_keys

        keys = discover_unity_cn_keys(game_dir)
        if keys:
            apply_unity_cn_keys(keys, log)
    except Exception:
        pass
    for fp in asset_files:
        if log:
            log(f"  深扫全对象字串: {fp.name}")
        try:
            env, how = _unity_load(fp, game_dir, log)
            if log and how not in ("plain", "undecrypted"):
                log(f"    加载方式: {how}")
        except Exception as e:
            if log:
                log(f"  加载失败: {e}")
            continue
        n_obj = 0
        for obj in env.objects:
            n_obj += 1
            try:
                tname = obj.type.name or ""
                if tname in ("Texture2D", "AudioClip", "Mesh", "Shader", "Font", "Cubemap"):
                    continue
                if any(h.lower() in tname.lower() for h in SKIP_NAME_HINTS):
                    continue
                raw = obj.get_raw_data()
                if not raw or len(raw) < 4:
                    continue
                for _off, text in _extract_runs(raw, loose=True):
                    add(text)
            except Exception:
                continue
        if log:
            log(f"  对象 {n_obj}，当前字串池 {len(out)}")
    if log:
        log(f"深扫唯一 JP 字串: {len(out)}")
    return out


def apply_mb_units(units: List[MbUnit], translations: List[str], log: LogFn = None) -> int:
    """Same-length UTF-8 patch via surgical UnityFS block rebuild (no UnityPy.save)."""
    if not units:
        return 0

    from collections import defaultdict

    from app.core.unity_fs_patch import patch_unityfs_file

    by_asset: Dict[str, List[Tuple[MbUnit, str]]] = defaultdict(list)
    for u, t in zip(units, translations):
        by_asset[u.asset_path].append((u, t))

    written = 0
    for asset_path, pairs in by_asset.items():
        path = Path(asset_path)
        if not path.is_file():
            continue
        bak = Path(str(path) + ".galautotl.bak")
        bak_alt = path.with_suffix(path.suffix + ".galautotl.bak")
        if not bak.exists() and bak_alt.exists():
            bak = bak_alt
        if not bak.exists():
            shutil.copy2(path, bak)
            if log:
                log(f"备份: {bak.name}")

        repl: List[Tuple[bytes, bytes]] = []
        seen: Set[bytes] = set()
        for u, t in sorted(pairs, key=lambda x: -x[0].length):
            old = u.source.encode("utf-8")
            if old in seen:
                continue
            new = fit_utf8_bytes(t, len(old), pad=b"\x00")
            if len(new) != len(old) or old == new:
                continue
            seen.add(old)
            repl.append((old, new))

        if not repl:
            if log:
                log(f"{path.name}: 无有效等长译文")
            continue

        # Prefer surgical UnityFS rebuild for .unity3d; fallback plaintext
        ok = False
        if path.suffix.lower() == ".unity3d" or path.name.lower().endswith(".unity3d"):
            ok = patch_unityfs_file(path, repl, bak=bak, log=log)
        if not ok:
            # plaintext fallback (rare)
            data = bytearray(bak.read_bytes())
            n = 0
            for old, new in repl:
                c = data.count(old)
                if 0 < c <= 12:
                    data = bytearray(bytes(data).replace(old, new))
                    n += c
            if n and len(data) == bak.stat().st_size:
                path.write_bytes(bytes(data))
                ok = True
                if log:
                    log(f"已明文写回 {path.name}: {n} 处")
        if ok:
            written += 1
    return written
