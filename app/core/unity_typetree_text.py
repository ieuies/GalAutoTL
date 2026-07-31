# -*- coding: utf-8 -*-
"""汉化组式结构化抽取：UnityPy + TypeTreeGenerator 解析 MonoBehaviour 字段。

IL2CPP 资源常 strip TypeTree，盲扫 UTF-8 会漏字段分隔后的短句 / 模板句。
网上教程做法：Il2CppDumper / GameAssembly → TypeTree → AssetStudio/UnityPy 按类读
Card / HoverHelp / Relic / TMP 等 ScriptableObject 里的 string。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Set

from app.core.unity_raw_text import HAS_CJK, HAS_KANA, _want_jp, find_unity_asset_files

LogFn = Optional[Callable[[str], None]]

# Prefer game master / UI text classes (still allow broader TSKT.*)
_PRIORITY_HINTS = (
    "Card",
    "Enemy",
    "Hover",
    "Relic",
    "State",
    "Localization",
    "TextMeshPro",
    "Talk",
    "Novel",
    "Master",
    "Credit",
    "OnlyDemo",
)

_JP_ANY = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")


def _detect_unity_version(game_dir: Path) -> str:
    """Best-effort Unity version for TypeTreeGenerator."""
    data = next(
        iter(
            list(game_dir.glob("*_Data"))
            + ([game_dir / "Data"] if (game_dir / "Data").is_dir() else [])
        ),
        None,
    )
    if data and data.is_dir():
        for name in ("globalgamemanagers", "data.unity3d", "resources.assets"):
            fp = data / name
            if not fp.is_file():
                continue
            try:
                blob = fp.read_bytes()[:4096]
            except OSError:
                continue
            m = re.search(rb"(20\d{2}\.\d+\.\d+[a-z]\d+)", blob)
            if m:
                return m.group(1).decode("ascii", errors="ignore")
    return "2023.1.15f1"


def _is_jp_field(s: str) -> bool:
    if not isinstance(s, str):
        return False
    s = s.strip("\x00").strip()
    if len(s) < 2 or len(s) > 2500:
        return False
    if s.startswith(("Assets/", "Character/", "Background/", "Audio/", "SE/", "BGM/")):
        return False
    if re.fullmatch(r"[A-Za-z0-9_./\\:-{}]+", s):
        return False
    # Templates like {attack}ダメージ — keep even if light on kana
    if "{" in s and HAS_CJK.search(s):
        return True
    if _want_jp(s, loose=True) or HAS_KANA.search(s):
        return True
    if HAS_CJK.search(s) and len(s) <= 80:
        return True
    return bool(_JP_ANY.search(s)) and len(s) <= 120


def _walk_strings(obj: Any, path: str = "") -> Iterable[str]:
    if obj is None:
        return
    if isinstance(obj, str):
        if _is_jp_field(obj):
            yield obj.strip("\x00")
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) in ("rid", "fileID", "guid", "m_Script"):
                continue
            yield from _walk_strings(v, f"{path}.{k}" if path else str(k))
        return
    if isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v, path)
        return
    if hasattr(obj, "__dict__"):
        d = {k: v for k, v in vars(obj).items() if not str(k).startswith("_")}
        if d:
            yield from _walk_strings(d, path)


def _script_full_name(obj) -> str:
    try:
        head = obj.parse_monobehaviour_head()
        script = head.m_Script.deref_parse_as_object()
        cls = getattr(script, "m_ClassName", "") or ""
        ns = getattr(script, "m_Namespace", "") or ""
        return f"{ns}.{cls}" if ns else cls
    except Exception:
        return ""


def _want_class(full: str) -> bool:
    if not full:
        return False
    low = full.lower()
    if any(h.lower() in low for h in _PRIORITY_HINTS):
        return True
    if low.startswith("tskt.") and "unity" not in low:
        return True
    return False


def _make_generator(game_dir: Path, log: LogFn = None):
    from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator

    ver = _detect_unity_version(game_dir)
    # AssetStudio backend works on this Unity 2023 IL2CPP title; AssetsTools may assert
    for backend in ("AssetStudio", "AssetsTools"):
        try:
            gen = TypeTreeGenerator(ver, generator=backend)
            gen.load_local_game(str(game_dir))
            if log:
                n = 0
                try:
                    n = len(gen.get_loaded_dll_names())
                except Exception:
                    # 只是统计展示用的 DLL 数，失败显示 0 即可
                    pass
                log(f"TypeTreeGenerator OK（{backend} / Unity {ver}，DLL≈{n}）")
            return gen
        except Exception as e:
            if log:
                log(f"TypeTreeGenerator {backend} 失败: {e}")
            continue
    dummy = None
    for cand in game_dir.rglob("DummyDll"):
        if cand.is_dir() and any(cand.glob("*.dll")):
            dummy = cand
            break
    if dummy:
        try:
            gen = TypeTreeGenerator(ver, generator="AssetStudio")
            gen.load_dll_folder(str(dummy))
            if log:
                log(f"TypeTreeGenerator 改用 DummyDll: {dummy}")
            return gen
        except Exception as e:
            if log:
                log(f"DummyDll TypeTree 失败: {e}")
    return None


def collect_typetree_jp_strings(game_dir: Path, log: LogFn = None) -> List[str]:
    """Return unique JP-looking strings from typed MonoBehaviour fields."""
    try:
        import UnityPy
    except ImportError:
        if log:
            log("未安装 UnityPy，跳过 TypeTree 结构化抽取")
        return []

    try:
        import TypeTreeGeneratorAPI  # noqa: F401
    except ImportError:
        from app.core.ensure_deps import ensure_package

        if not ensure_package("TypeTreeGeneratorAPI", "TypeTreeGeneratorAPI", log):
            if log:
                log("缺少 TypeTreeGeneratorAPI：跳过结构化字段抽取（汉化组标准）")
            return []

    generator = _make_generator(game_dir, log)
    if generator is None:
        if log:
            log("无法构建 TypeTreeGenerator，跳过结构化抽取")
        return []

    assets = find_unity_asset_files(game_dir)
    if not assets:
        return []

    try:
        from app.core.unity_bundle_crypto import apply_unity_cn_keys, discover_unity_cn_keys
        from app.core.unity_raw_text import _unity_load

        keys = discover_unity_cn_keys(game_dir)
        if keys:
            apply_unity_cn_keys(keys, log)
    except Exception:
        _unity_load = None  # type: ignore

    seen: Set[str] = set()
    out: List[str] = []
    ok = fail = skip = 0

    for fp in assets:
        if log:
            log(f"  TypeTree 扫描: {fp.name}")
        try:
            if _unity_load:
                env, _how = _unity_load(fp, game_dir, log)
            else:
                from app.core.unity_bundle_crypto import configure_unitypy_fallback

                configure_unitypy_fallback(game_dir, log=None)
                env = UnityPy.load(str(fp))
        except Exception as e:
            if log:
                log(f"  加载失败: {e}")
            continue
        env.typetree_generator = generator
        for obj in env.objects:
            if obj.type.name != "MonoBehaviour":
                continue
            full = _script_full_name(obj)
            if not _want_class(full):
                skip += 1
                continue
            tree = None
            for method in ("parse_as_dict", "read_typetree"):
                try:
                    tree = getattr(obj, method)()
                    if tree is not None:
                        break
                except Exception:
                    continue
            if tree is None:
                fail += 1
                continue
            ok += 1
            for s in _walk_strings(tree):
                s = (s or "").strip()
                if not s or s in seen:
                    continue
                seen.add(s)
                out.append(s)

    if log:
        log(
            f"TypeTree 结构化抽取: {len(out)} 条唯一日文/模板字段"
            f"（解析成功 {ok}，失败 {fail}，跳过类 {skip}）"
        )
    return out
