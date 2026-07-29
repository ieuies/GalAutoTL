# -*- coding: utf-8 -*-
"""Inject ARIALUNI SDF glyph tables into PARANORMASIGHT MAIN TMP fonts."""
from __future__ import annotations

import re
import shutil
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import UnityPy

from app.config import appdata_dir

LogFn = Optional[Callable[[str], None]]

FONT_PACK_NAMES = ("a021", "a035", "a038")
A021_NAME = FONT_PACK_NAMES[0]
BACKUP_SUFFIX = ".galautotl_font.bak"
BUILTIN_BACKUP_SUFFIX = ".galautotl_tmpfont.bak"
BUILTIN_ASSET_NAMES = ("sharedassets0.assets", "resources.assets")
UNITY_VERSION = "2021.3.8f1"
FONT_NAME_HINTS = ("sdf", "font", "main", "telop", "meiryo", "liberation", "fot", "arial")
SMALL_MAIN_CHARS = 4553
ARIAL_FONT_NAMES = ("ARIALUNI SDF",)
MAIN_FONT_NAME = "MAIN"
MAIN_ATLAS_NAME = "MAIN Atlas"
ARIAL_ATLAS_NAME = "ARIALUNI SDF Atlas"
SKIP_IF_CHARS_GT = 20_000
VERIFY_CHARS_GE = 30_000
MAX_WRITE_BYTES = 400 * 1024 * 1024
UNITYFS = b"UnityFS"
PACK_NAME_RE = re.compile(r"^a\d{3}$", re.I)
FONT_BUNDLE_PREFER = (
    "arialuni_sdf_u2019",
    "arialuni_sdf_u2021",
    "arialuni_sdf_u2022",
    "arialuni_sdf_u2018plus",
    "arialuni_sdf_u2018",
    "arialuni_sdf",
)


def _log(log: LogFn, msg: str) -> None:
    if log:
        log(msg)


def _runtime_cache_dir() -> Path:
    p = appdata_dir() / "unity_runtime"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _game_data_dir(game_dir: Path) -> Optional[Path]:
    game_dir = Path(game_dir)
    for data in game_dir.glob("*_Data"):
        if data.is_dir():
            return data
    return None


def _streaming_assets_dir(game_dir: Path) -> Optional[Path]:
    data = _game_data_dir(game_dir)
    if not data:
        return None
    sa = data / "StreamingAssets"
    return sa if sa.is_dir() else None


def _is_streaming_asset_pack(path: Path) -> bool:
    if not path.is_file() or BACKUP_SUFFIX in path.name:
        return False
    name = path.name
    return bool(PACK_NAME_RE.match(name)) or name.lower() == "windows"


def find_font_pack(game_dir: Path, pack_name: str) -> Optional[Path]:
    """Return StreamingAssets pack by short name (e.g. a021, a035, a038)."""
    sa = _streaming_assets_dir(Path(game_dir))
    if not sa:
        return None
    want = pack_name.lower()
    cand = sa / want
    return cand if cand.is_file() else None


def find_a021_pack(game_dir: Path) -> Optional[Path]:
    return find_font_pack(game_dir, A021_NAME)


def strip_header(blob: bytes) -> Tuple[bytes, bytes, int]:
    off = blob.find(UNITYFS)
    if off < 0:
        raise ValueError("UnityFS not found in bundle")
    return blob[:off], blob[off:], off


def _setup_typetree_generator(game_dir: Path, log: LogFn = None) -> Optional[Any]:
    """Load TypeTreeGenerator so IL2CPP-stripped MonoBehaviours (TMP_FontAsset) parse."""
    UnityPy.config.FALLBACK_UNITY_VERSION = UNITY_VERSION
    try:
        from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator
    except ImportError:
        _log(log, "UnityPy TypeTreeGenerator 不可用")
        return None

    game_dir = Path(game_dir)
    for backend in ("AssetStudio", "AssetsTools"):
        try:
            gen = TypeTreeGenerator(UNITY_VERSION, generator=backend)
            gen.load_local_game(str(game_dir))
            _log(log, f"TypeTreeGenerator OK ({backend})")
            return gen
        except Exception as e:
            _log(log, f"TypeTreeGenerator {backend} 失败: {e}")

    dummy = game_dir / "_galautotl_unity" / "il2cpp_dump" / "DummyDll"
    if dummy.is_dir():
        _log(log, f"DummyDll 存在但 TypeTree 未加载: {dummy}")
    return None


def _load_env_from_path(path: Path, generator: Any = None):
    path = Path(path)
    raw = path.read_bytes()
    off = raw.find(UNITYFS)
    if off >= 0:
        body = raw[off:]
        env = UnityPy.load(body)
    else:
        # Path-based load lets TypeTreeGenerator resolve globalgamemanagers in *_Data
        env = UnityPy.load(str(path))
        body = raw
    if generator is not None:
        env.typetree_generator = generator
    return raw, body, env


def _find_font(env, name: str):
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            tree = obj.read_typetree()
        except Exception:
            continue
        if tree.get("m_Name") == name and "m_CharacterTable" in tree:
            return obj, tree
    return None, None


def _find_all_tmp_font_objects(env) -> List[Tuple[Any, dict]]:
    out: List[Tuple[Any, dict]] = []
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            tree = obj.read_typetree()
        except Exception:
            continue
        if "m_CharacterTable" not in tree:
            continue
        out.append((obj, tree))
    return out


def _find_all_tmp_fonts(env) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for _, tree in _find_all_tmp_font_objects(env):
        aw = tree.get("m_AtlasWidth")
        ah = tree.get("m_AtlasHeight")
        out.append(
            {
                "name": tree.get("m_Name", "?"),
                "chars": len(tree.get("m_CharacterTable") or []),
                "atlas_wh": (int(aw), int(ah)) if aw and ah else None,
            }
        )
    return out


def _atlas_path_id(ref: Any) -> Optional[int]:
    if ref is None:
        return None
    if isinstance(ref, dict):
        pid = ref.get("m_PathID") or ref.get("path_id")
        return int(pid) if pid else None
    pid = getattr(ref, "path_id", None)
    return int(pid) if pid else None


def _find_font_atlas_obj(env, font_tree: dict):
    for ref in font_tree.get("m_AtlasTextures") or []:
        pid = _atlas_path_id(ref)
        if not pid:
            continue
        for obj in env.objects:
            if obj.path_id == pid and obj.type.name == "Texture2D":
                return obj
    name = font_tree.get("m_Name") or ""
    for cand in (
        f"{name} Atlas",
        f"{name} SDF Atlas",
        name.replace("_SDF", " SDF Atlas"),
        MAIN_ATLAS_NAME if name == MAIN_FONT_NAME else None,
    ):
        if not cand:
            continue
        obj, _ = _find_texture(env, cand)
        if obj:
            return obj
    return None


def _inspect_font_tree(tree: dict) -> Dict[str, Any]:
    aw = tree.get("m_AtlasWidth")
    ah = tree.get("m_AtlasHeight")
    dims = (int(aw), int(ah)) if aw and ah else None
    chars = len(tree.get("m_CharacterTable") or [])
    return {
        "name": tree.get("m_Name", "?"),
        "chars": chars,
        "atlas_wh": dims,
        "ok": chars >= VERIFY_CHARS_GE and dims == (8192, 8192),
    }


def _find_texture(env, name: str):
    for obj in env.objects:
        if obj.type.name != "Texture2D":
            continue
        d = obj.read()
        if d.m_Name == name:
            return obj, d
    return None, None


def _find_atlas_textures(env) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for obj in env.objects:
        if obj.type.name != "Texture2D":
            continue
        try:
            d = obj.read()
            if "Atlas" in d.m_Name or "atlas" in d.m_Name:
                out.append({"name": d.m_Name, "wh": (int(d.m_Width), int(d.m_Height))})
        except Exception:
            pass
    return out


def _find_legacy_fonts(env) -> List[str]:
    names: List[str] = []
    for obj in env.objects:
        if obj.type.name != "Font":
            continue
        try:
            names.append(obj.read().m_Name)
        except Exception:
            pass
    return names


def _tex_dims(env) -> Optional[Tuple[int, int]]:
    obj, _ = _find_texture(env, MAIN_ATLAS_NAME)
    if not obj:
        return None
    d = obj.read()
    return int(d.m_Width), int(d.m_Height)


def _main_char_count(env) -> int:
    _, tree = _find_font(env, MAIN_FONT_NAME)
    if not tree:
        return 0
    return len(tree.get("m_CharacterTable") or [])


def _inspect_main(env) -> Dict[str, Any]:
    _, tree = _find_font(env, MAIN_FONT_NAME)
    dims = _tex_dims(env)
    return {
        "chars": _main_char_count(env),
        "atlas_wh": dims,
        "ok": bool(
            tree
            and tree.get("m_Name") == MAIN_FONT_NAME
            and _main_char_count(env) >= VERIFY_CHARS_GE
            and dims == (8192, 8192)
        ),
    }


def scan_asset_file(path: Path, generator: Any = None) -> List[Dict[str, Any]]:
    """Scan one bundle/assets file for TMP fonts, atlas textures, and legacy fonts."""
    path = Path(path)
    rows: List[Dict[str, Any]] = []
    base = {
        "pack": path.name,
        "path": str(path),
        "file_size": path.stat().st_size if path.is_file() else 0,
    }
    try:
        _, _, env = _load_env_from_path(path, generator)
    except Exception as e:
        return [{**base, "error": str(e)}]

    for font in _find_all_tmp_fonts(env):
        rows.append(
            {
                **base,
                "kind": "TMP",
                "font": font["name"],
                "chars": font["chars"],
                "atlas_wh": font["atlas_wh"],
                "needs_patch": font["chars"] < SKIP_IF_CHARS_GT,
            }
        )
    for leg in _find_legacy_fonts(env):
        rows.append({**base, "kind": "legacy Font", "font": leg, "chars": None, "atlas_wh": None})
    if not rows:
        for atlas in _find_atlas_textures(env):
            rows.append(
                {
                    **base,
                    "kind": "Texture2D",
                    "font": atlas["name"],
                    "chars": None,
                    "atlas_wh": atlas["wh"],
                }
            )
    return rows


def scan_addressables_tmp_fonts(game_dir: Path, generator: Any = None) -> List[Dict[str, Any]]:
    """Scan StreamingAssets/aa/*.bundle for TMP FontAssets."""
    game_dir = Path(game_dir)
    data = _game_data_dir(game_dir)
    if not data:
        return []
    aa = data / "StreamingAssets" / "aa"
    if not aa.is_dir():
        return []
    rows: List[Dict[str, Any]] = []
    for bundle in sorted(aa.rglob("*.bundle")):
        rows.extend(scan_asset_file(bundle, generator))
    return rows


def scan_tmp_font_assets(game_dir: Path) -> List[Dict[str, Any]]:
    """Full scan: StreamingAssets packs, sharedassets*.assets, resources.assets, Addressables."""
    game_dir = Path(game_dir)
    data = _game_data_dir(game_dir)
    if not data:
        return []

    generator = _setup_typetree_generator(game_dir)

    targets: List[Path] = []
    sa = data / "StreamingAssets"
    if sa.is_dir():
        for p in sorted(sa.iterdir()):
            if _is_streaming_asset_pack(p):
                targets.append(p)
    for name in BUILTIN_ASSET_NAMES:
        p = data / name
        if p.is_file():
            targets.append(p)
    for p in sorted(data.glob("sharedassets*.assets")):
        if BACKUP_SUFFIX not in p.name and BUILTIN_BACKUP_SUFFIX not in p.name and p not in targets:
            targets.append(p)
    res = data / "resources.assets"
    if res.is_file() and res not in targets:
        targets.append(res)

    rows: List[Dict[str, Any]] = []
    for p in targets:
        rows.extend(scan_asset_file(p, generator))
    rows.extend(scan_addressables_tmp_fonts(game_dir, generator))
    return rows


def discover_font_packs(game_dir: Path) -> List[Path]:
    """Auto-discover StreamingAssets a### packs with MAIN or small TMP FontAssets."""
    sa = _streaming_assets_dir(Path(game_dir))
    if not sa:
        return []

    found: List[Path] = []
    for p in sorted(sa.iterdir()):
        if not _is_streaming_asset_pack(p):
            continue
        try:
            _, _, env = _load_env_from_path(p)
        except Exception:
            continue
        fonts = _find_all_tmp_fonts(env)
        if not fonts:
            continue
        has_main = any(f["name"] == MAIN_FONT_NAME for f in fonts)
        has_small = any(f["chars"] < SKIP_IF_CHARS_GT for f in fonts)
        if has_main or has_small:
            found.append(p)
    return found


def find_font_packs(game_dir: Path) -> List[Path]:
    """Return all StreamingAssets font packs that contain MAIN TMP (auto-discovered)."""
    discovered = discover_font_packs(game_dir)
    if discovered:
        return discovered
    out: List[Path] = []
    for name in FONT_PACK_NAMES:
        p = find_font_pack(game_dir, name)
        if p is not None:
            out.append(p)
    return out


def list_unpatched_fonts(game_dir: Path) -> List[Dict[str, Any]]:
    """TMP FontAssets with char count below SKIP_IF_CHARS_GT."""
    return [
        r
        for r in scan_tmp_font_assets(game_dir)
        if r.get("kind") == "TMP" and r.get("needs_patch")
    ]


def verify_main_fonts(game_dir: Path) -> Dict[str, Any]:
    """Verify all discovered MAIN fonts meet VERIFY_CHARS_GE."""
    packs = find_font_packs(game_dir)
    results: List[Dict[str, Any]] = []
    all_ok = True
    for pack in packs:
        try:
            _, _, env = _load_env_from_path(pack)
            info = _inspect_main(env)
        except Exception as e:
            info = {"chars": 0, "atlas_wh": None, "ok": False, "error": str(e)}
        entry = {"pack": pack.name, **info}
        results.append(entry)
        if not info.get("ok"):
            all_ok = False
    return {"ok": all_ok, "packs": results}


def _merge_arial_into_font(target_tree: dict, arial_tree: dict, *, keep_name: Optional[str] = None) -> List[str]:
    copied: List[str] = []
    replace_keys = [
        "m_CharacterTable",
        "m_GlyphTable",
        "m_UsedGlyphRects",
        "m_FreeGlyphRects",
        "m_CreationSettings",
        "m_fontInfo",
        "m_glyphInfoList",
        "m_KerningTable",
        "m_FontFeatureTable",
        "m_FontWeightTable",
        "m_FallbackFontAssetTable",
        "m_AtlasPadding",
        "m_AtlasRenderMode",
        "m_AtlasPopulationMode",
        "m_AtlasTextureIndex",
        "m_Version",
        "hashCode",
        "materialHashCode",
    ]
    orig_name = target_tree.get("m_Name")
    for key in replace_keys:
        if key in arial_tree:
            target_tree[key] = arial_tree[key]
            copied.append(key)

    if "m_FaceInfo" in arial_tree:
        dst = dict(target_tree.get("m_FaceInfo") or {})
        src = arial_tree["m_FaceInfo"]
        dst.update(src)
        if "m_FaceIndex" in (target_tree.get("m_FaceInfo") or {}):
            dst["m_FaceIndex"] = target_tree["m_FaceInfo"]["m_FaceIndex"]
        target_tree["m_FaceInfo"] = dst
        copied.append("m_FaceInfo(merged)")

    target_tree["m_AtlasWidth"] = arial_tree.get("m_AtlasWidth", 8192)
    target_tree["m_AtlasHeight"] = arial_tree.get("m_AtlasHeight", 8192)
    copied += ["m_AtlasWidth", "m_AtlasHeight"]

    if target_tree.get("m_AtlasTextures"):
        target_tree["m_IsMultiAtlasTexturesEnabled"] = 0
        first_pid = target_tree["m_AtlasTextures"][0]
        target_tree["m_AtlasTextures"] = [first_pid]
        copied.append("m_IsMultiAtlasTexturesEnabled=0")
        copied.append("m_AtlasTextures=[first only]")

    if keep_name is not None:
        target_tree["m_Name"] = keep_name
    elif orig_name is not None:
        target_tree["m_Name"] = orig_name
    return copied


def _merge_arial_into_main(main_tree: dict, arial_tree: dict) -> List[str]:
    return _merge_arial_into_font(main_tree, arial_tree, keep_name=MAIN_FONT_NAME)


def _patch_texture(dst_obj, src_obj) -> None:
    src = src_obj.read()
    dst = dst_obj.read()
    img = src.image
    if img is None:
        raise RuntimeError("source texture image is None")
    dst.m_Width = src.m_Width
    dst.m_Height = src.m_Height
    dst.m_TextureFormat = src.m_TextureFormat
    dst.image = img
    dst.save()


def _save_assets(env) -> bytes:
    return env.file.save()


def _save_bundle(env, *, packer: str = "lz4") -> bytes:
    try:
        return env.file.save(packer=packer)
    except TypeError:
        return env.file.save()


def _find_arialuni_bundle(game_dir: Path) -> Optional[Path]:
    game_dir = Path(game_dir)
    search_dirs = [
        game_dir,
        game_dir / "BepInEx" / "Translation" / "Fonts",
        _runtime_cache_dir() / "tmp_font_bundles",
    ]
    for folder in search_dirs:
        if not folder.is_dir():
            continue
        for name in FONT_BUNDLE_PREFER:
            path = folder / name
            if path.is_file() and path.stat().st_size > 100_000:
                return path
        for path in sorted(folder.glob("arialuni_sdf*")):
            if path.is_file() and path.stat().st_size > 100_000:
                return path
    return None


def _should_patch_font(chars_before: int) -> bool:
    if chars_before >= VERIFY_CHARS_GE:
        return False
    return chars_before <= SKIP_IF_CHARS_GT


def _should_patch_main(_pack: Path, chars_before: int) -> bool:
    return _should_patch_font(chars_before)


def recompress_font_pack_if_needed(pack: Path, log: LogFn = None) -> bool:
    """Re-LZ4 a font pack when UnityPy left it uncompressed; keep patch if valid."""
    pack = Path(pack)
    label = pack.name
    if not pack.is_file():
        return False
    try:
        raw = pack.read_bytes()
        prefix, body, _ = strip_header(raw)
        env = UnityPy.load(body)
        info = _inspect_main(env)
        if not info["ok"]:
            _log(log, f"{label} 重压缩跳过: MAIN 校验未通过 (chars={info['chars']})")
            return False

        from app.core.unity_fs_patch import parse_unityfs

        layout = parse_unityfs(body)
        if len(layout.blocks) > 1 and layout.blocks[0].compressed_size < layout.blocks[0].uncompressed_size:
            _log(log, f"{label} 已压缩 ({len(raw) // 1024 // 1024} MB)，无需重打包")
            return True

        saved = _save_bundle(env, packer="lz4")
        if len(saved) >= MAX_WRITE_BYTES:
            _log(log, f"{label} LZ4 后仍过大 ({len(saved) // 1024 // 1024} MB)，保留原文件")
            return False

        re_env = UnityPy.load(saved)
        if not _inspect_main(re_env)["ok"]:
            _log(log, f"{label} 重压缩后 MAIN 校验失败，保留原文件")
            return False

        shelled = prefix + saved
        pack.write_bytes(shelled)
        _log(
            log,
            f"{label} 已 LZ4 重压缩: {len(raw) // 1024 // 1024} MB → {len(shelled) // 1024 // 1024} MB",
        )
        return True
    except Exception as e:
        _log(log, f"{label} 重压缩失败: {e}")
        return False


def recompress_a021_if_needed(pack: Path, log: LogFn = None) -> bool:
    """Re-LZ4 a021 when UnityPy left it uncompressed; keep patch if valid."""
    return recompress_font_pack_if_needed(pack, log)


def patch_main_font_in_pack(pack: Path, font_path: Path, log: LogFn = None) -> bool:
    """Merge ARIALUNI SDF into MAIN inside one StreamingAssets font pack."""
    pack = Path(pack)
    font_path = Path(font_path)
    label = pack.name
    if not pack.is_file() or not font_path.is_file():
        return False

    try:
        raw = pack.read_bytes()
        prefix, body, ufs_off = strip_header(raw)
        game_env = UnityPy.load(body)
        chars_before = _main_char_count(game_env)
        if not _should_patch_main(pack, chars_before):
            _log(log, f"{label} MAIN 已有 {chars_before} 字符，跳过字体注入")
            return recompress_font_pack_if_needed(pack, log)

        font_env = UnityPy.load(font_path.read_bytes())
        main_obj, main_tree = _find_font(game_env, MAIN_FONT_NAME)
        arial_obj, arial_tree = _find_font(font_env, ARIAL_FONT_NAMES[0])
        if not main_obj or not arial_obj:
            _log(log, f"{label} 或 ARIALUNI 字体资产未找到，跳过注入")
            return False

        main_atlas_obj, _ = _find_texture(game_env, MAIN_ATLAS_NAME)
        arial_atlas_obj, _ = _find_texture(font_env, ARIAL_ATLAS_NAME)
        if not main_atlas_obj or not arial_atlas_obj:
            _log(log, f"{label} MAIN Atlas / ARIALUNI SDF Atlas 纹理未找到，跳过注入")
            return False

        keep_script = main_tree.get("m_Script")
        keep_material = main_tree.get("material")
        keep_fallback = main_tree.get("fallbackFontAssets")
        keep_clear = main_tree.get("m_ClearDynamicDataOnBuild")

        _merge_arial_into_main(main_tree, arial_tree)
        main_tree["m_Script"] = keep_script
        main_tree["material"] = keep_material
        if keep_fallback is not None:
            main_tree["fallbackFontAssets"] = keep_fallback
        if keep_clear is not None:
            main_tree["m_ClearDynamicDataOnBuild"] = keep_clear

        _patch_texture(main_atlas_obj, arial_atlas_obj)
        main_obj.save_typetree(main_tree)

        saved = _save_bundle(game_env, packer="lz4")
        if len(saved) >= MAX_WRITE_BYTES:
            _log(log, f"{label} 注入后体积过大 ({len(saved) // 1024 // 1024} MB)，未写入")
            return False

        re_env = UnityPy.load(saved)
        info = _inspect_main(re_env)
        if not info["ok"]:
            _log(log, f"{label} 注入后校验失败 (chars={info['chars']}, atlas={info['atlas_wh']})")
            return False

        shelled = prefix + saved
        rp, _, ro = strip_header(shelled)
        if rp != prefix or ro != ufs_off:
            _log(log, f"{label} 专有头校验失败，未写入")
            return False

        bak = Path(str(pack) + BACKUP_SUFFIX)
        if not bak.is_file():
            shutil.copy2(pack, bak)
            _log(log, f"已备份 {label} → {bak.name}")

        pack.write_bytes(shelled)
        _log(
            log,
            f"{label} MAIN 字体已注入 ARIALUNI ({chars_before} → {info['chars']} chars, 8192 atlas, "
            f"{len(raw) // 1024 // 1024} → {len(shelled) // 1024 // 1024} MB)",
        )
        return True
    except Exception as e:
        _log(log, f"{label} MAIN 字体注入失败: {e}")
        if log:
            log(traceback.format_exc()[-400:])
        return False


def _patch_single_font_in_env(
    env,
    font_obj,
    font_tree: dict,
    arial_tree: dict,
    arial_atlas_obj,
    log: LogFn,
    label: str,
) -> Tuple[bool, int, int, bool]:
    """Patch one TMP font. Returns (patched, chars_before, chars_after, full_merge)."""
    name = font_tree.get("m_Name", "?")
    chars_before = len(font_tree.get("m_CharacterTable") or [])
    if not _should_patch_font(chars_before):
        _log(log, f"{label} {name} 已有 {chars_before} 字符，跳过")
        return False, chars_before, chars_before, False

    atlas_obj = _find_font_atlas_obj(env, font_tree)
    if not atlas_obj:
        _log(log, f"{label} {name} 图集纹理未找到，跳过")
        return False, chars_before, chars_before, False

    keep_script = font_tree.get("m_Script")
    keep_material = font_tree.get("material")
    keep_fallback = font_tree.get("fallbackFontAssets")
    keep_clear = font_tree.get("m_ClearDynamicDataOnBuild")

    try:
        _merge_arial_into_font(font_tree, arial_tree)
        font_tree["m_Script"] = keep_script
        font_tree["material"] = keep_material
        if keep_fallback is not None:
            font_tree["fallbackFontAssets"] = keep_fallback
        if keep_clear is not None:
            font_tree["m_ClearDynamicDataOnBuild"] = keep_clear
        _patch_texture(atlas_obj, arial_atlas_obj)
        font_obj.save_typetree(font_tree)
        chars_after = len(font_tree.get("m_CharacterTable") or [])
        full_merge = chars_after >= VERIFY_CHARS_GE
        _log(log, f"{label} {name} 已合并 ARIALUNI ({chars_before} → {chars_after})")
        return True, chars_before, chars_after, full_merge
    except Exception as e:
        _log(log, f"{label} {name} 完整合并失败: {e}，尝试仅图集")
        try:
            _patch_texture(atlas_obj, arial_atlas_obj)
            font_tree["m_AtlasWidth"] = 8192
            font_tree["m_AtlasHeight"] = 8192
            font_obj.save_typetree(font_tree)
            _log(log, f"{label} {name} 仅图集已替换（字符表未更新，仍可能 □）")
            return True, chars_before, chars_before, False
        except Exception as e2:
            _log(log, f"{label} {name} 图集替换也失败: {e2}")
            return False, chars_before, chars_before, False


def patch_tmp_fonts_in_assets_file(
    assets_path: Path,
    font_path: Path,
    generator: Any = None,
    log: LogFn = None,
) -> bool:
    """Merge ARIALUNI SDF into all small TMP fonts inside one .assets file."""
    assets_path = Path(assets_path)
    label = assets_path.name
    if not assets_path.is_file() or not font_path.is_file():
        return False

    bak = Path(str(assets_path) + BUILTIN_BACKUP_SUFFIX)
    try:
        raw = assets_path.read_bytes()
        _, _, env = _load_env_from_path(assets_path, generator)

        font_env = UnityPy.load(font_path.read_bytes())
        if generator is not None:
            font_env.typetree_generator = generator
        _, arial_tree = _find_font(font_env, ARIAL_FONT_NAMES[0])
        arial_atlas_obj, _ = _find_texture(font_env, ARIAL_ATLAS_NAME)
        if not arial_tree or not arial_atlas_obj:
            _log(log, f"{label} ARIALUNI 源字体/图集未找到")
            return False

        fonts = _find_all_tmp_font_objects(env)
        if not fonts:
            _log(log, f"{label} 未发现 TMP FontAsset（typetree 可能失败）")
            return False

        patched_names: List[str] = []
        verify_names: List[str] = []
        atlas_only_names: List[str] = []
        skipped = 0

        for font_obj, font_tree in fonts:
            ok, before, after, full_merge = _patch_single_font_in_env(
                env, font_obj, font_tree, arial_tree, arial_atlas_obj, log, label
            )
            if not ok:
                if before >= VERIFY_CHARS_GE:
                    skipped += 1
                continue
            name = font_tree.get("m_Name", "?")
            patched_names.append(f"{name}({before}→{after})")
            if full_merge:
                verify_names.append(name)
            else:
                atlas_only_names.append(name)

        if not patched_names:
            if skipped:
                _log(log, f"{label} 全部 TMP 字体已是最新，跳过")
            return False

        saved = _save_assets(env)
        if len(saved) >= MAX_WRITE_BYTES:
            _log(log, f"{label} 注入后体积过大 ({len(saved) // 1024 // 1024} MB)，未写入")
            return False

        validation_ok = True
        for font_obj, font_tree in fonts:
            info = _inspect_font_tree(font_tree)
            if info["name"] in verify_names and not info["ok"]:
                _log(
                    log,
                    f"{label} 校验失败 {info['name']}: chars={info['chars']} atlas={info['atlas_wh']}",
                )
                validation_ok = False

        if not validation_ok:
            if bak.is_file():
                shutil.copy2(bak, assets_path)
                _log(log, f"{label} 校验失败，已从 {bak.name} 还原")
            return False

        if not bak.is_file():
            shutil.copy2(assets_path, bak)
            _log(log, f"已备份 {label} → {bak.name}")

        assets_path.write_bytes(saved)
        _log(
            log,
            f"{label} 已写入 TMP 字体: {', '.join(patched_names)} "
            f"({len(raw) // 1024 // 1024} → {len(saved) // 1024 // 1024} MB)",
        )
        if atlas_only_names:
            _log(log, f"{label} 仅图集（字符表未更新）: {', '.join(atlas_only_names)}")
        return True
    except Exception as e:
        _log(log, f"{label} 内置 TMP 字体注入失败: {e}")
        if log:
            log(traceback.format_exc()[-400:])
        if bak.is_file() and assets_path.is_file():
            try:
                shutil.copy2(bak, assets_path)
                _log(log, f"{label} 已从 {bak.name} 还原")
            except OSError:
                pass
        return False


def patch_paranormasight_builtin_tmp_fonts(game_dir: Path, log: LogFn = None) -> bool:
    """Patch TMP SDF fonts in sharedassets0.assets, resources.assets, and Addressables."""
    game_dir = Path(game_dir)
    data = _game_data_dir(game_dir)
    if not data:
        _log(log, "未找到 *_Data 目录")
        return False

    font_path = _find_arialuni_bundle(game_dir)
    if not font_path:
        _log(log, "未找到 arialuni_sdf 源包，跳过内置 TMP 字体注入")
        return False

    generator = _setup_typetree_generator(game_dir, log)
    if not generator:
        _log(log, "TypeTreeGenerator 不可用，内置 TMP 字体可能无法完整合并")

    _log(log, f"内置 TMP 注入源: {font_path}")

    ok = False
    for name in BUILTIN_ASSET_NAMES:
        assets_path = data / name
        if assets_path.is_file():
            if patch_tmp_fonts_in_assets_file(assets_path, font_path, generator, log):
                ok = True

    aa_rows = scan_addressables_tmp_fonts(game_dir, generator)
    aa_fonts = [r for r in aa_rows if r.get("kind") == "TMP" and r.get("needs_patch")]
    if aa_fonts:
        _log(log, f"Addressables 发现 {len(aa_fonts)} 个待修补 TMP 字体")
        for row in aa_fonts:
            bundle = Path(row["path"])
            if patch_tmp_fonts_in_assets_file(bundle, font_path, generator, log):
                ok = True
    elif aa_rows:
        _log(log, "Addressables 包内无 TMP FontAsset")
    else:
        _log(log, "Addressables 无待修补 TMP 字体")

    return ok


def patch_paranormasight_tmp_font(game_dir: Path, log: LogFn = None) -> bool:
    """Merge ARIALUNI SDF into StreamingAssets MAIN packs and builtin TMP fonts."""
    game_dir = Path(game_dir)
    ok = False
    font_path = _find_arialuni_bundle(game_dir)
    packs = find_font_packs(game_dir)

    if font_path and packs:
        _log(log, f"发现 {len(packs)} 个字体包: {', '.join(p.name for p in packs)}")
        _log(log, f"使用 ARIALUNI 源: {font_path}")
        for pack in packs:
            if patch_main_font_in_pack(pack, font_path, log):
                ok = True
    elif not packs:
        _log(log, "未发现含 MAIN TMP 的 StreamingAssets 包")
    else:
        _log(log, "未找到 arialuni_sdf，跳过 StreamingAssets MAIN 注入")

    if patch_paranormasight_builtin_tmp_fonts(game_dir, log):
        ok = True
    return ok


def restore_paranormasight_tmp_font(game_dir: Path, log: LogFn = None) -> bool:
    """Restore font packs and builtin .assets from GalAutoTL backups."""
    game_dir = Path(game_dir)
    restored = False
    for pack in find_font_packs(game_dir):
        bak = Path(str(pack) + BACKUP_SUFFIX)
        if not bak.is_file():
            continue
        try:
            shutil.copy2(bak, pack)
            _log(log, f"已从 {bak.name} 还原 {pack.name}")
            restored = True
        except OSError as e:
            _log(log, f"还原 {pack.name} 失败: {e}")

    data = _game_data_dir(game_dir)
    if data:
        for name in BUILTIN_ASSET_NAMES:
            assets_path = data / name
            bak = Path(str(assets_path) + BUILTIN_BACKUP_SUFFIX)
            if not bak.is_file():
                continue
            try:
                shutil.copy2(bak, assets_path)
                _log(log, f"已从 {bak.name} 还原 {name}")
                restored = True
            except OSError as e:
                _log(log, f"还原 {name} 失败: {e}")

    if not restored:
        _log(log, "未找到任何 .galautotl_font.bak / .galautotl_tmpfont.bak 备份")
    return restored


def _format_scan_report(rows: List[Dict[str, Any]]) -> str:
    lines = [
        f"{'pack':<28} {'font':<38} {'chars':>8} {'atlas':>12} {'file_mb':>8}",
        "-" * 100,
    ]
    for r in sorted(rows, key=lambda x: (x.get("pack", ""), str(x.get("font", "")))):
        if "error" in r:
            lines.append(f"{r['pack']:<28} ERROR: {r['error']}")
            continue
        if not r.get("font"):
            continue
        atlas = r.get("atlas_wh")
        atlas_s = f"{atlas[0]}x{atlas[1]}" if atlas and atlas[0] else "-"
        chars = r.get("chars")
        chars_s = str(chars) if chars is not None else "N/A"
        mb = r.get("file_size", 0) / 1024 / 1024
        lines.append(f"{r['pack']:<28} {str(r['font']):<38} {chars_s:>8} {atlas_s:>12} {mb:>8.1f}")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Scan/patch PARANORMASIGHT TMP MAIN fonts")
    ap.add_argument("game_dir", nargs="?", default=r"C:\PARANORMASIGHT")
    ap.add_argument("--scan", action="store_true", help="Scan only, no patch")
    ap.add_argument("--patch", action="store_true", help="Patch StreamingAssets + builtin TMP fonts")
    ap.add_argument("--builtin-only", action="store_true", help="Patch sharedassets/resources only")
    ap.add_argument("--verify", action="store_true", help="Verify MAIN fonts after patch")
    args = ap.parse_args()

    game = Path(args.game_dir)
    logs: List[str] = []

    def _cli_log(msg: str) -> None:
        logs.append(msg)
        print(msg)

    print("=== BEFORE ===")
    before = scan_tmp_font_assets(game)
    print(_format_scan_report(before))
    unpatched = list_unpatched_fonts(game)
    if unpatched:
        print("\nUNPATCHED (< 20000 chars):")
        for r in unpatched:
            print(f"  {r['pack']}: {r['font']} = {r['chars']}")
    else:
        print("\nNo unpatched TMP fonts found.")

    if args.patch or args.builtin_only or (not args.scan and not args.verify):
        print("\n=== PATCHING ===")
        if args.builtin_only:
            patch_paranormasight_builtin_tmp_fonts(game, _cli_log)
        else:
            patch_paranormasight_tmp_font(game, _cli_log)

    if args.verify or args.patch or (not args.scan):
        v = verify_main_fonts(game)
        print("\n=== VERIFY ===")
        for p in v["packs"]:
            status = "OK" if p.get("ok") else "FAIL"
            print(f"  {p['pack']}: MAIN {p.get('chars', 0)} chars, atlas={p.get('atlas_wh')} [{status}]")
        print(f"All OK: {v['ok']}")

    if args.scan and not args.patch:
        sys.exit(0)

    print("\n=== AFTER ===")
    after = scan_tmp_font_assets(game)
    print(_format_scan_report([r for r in after if r.get("kind") == "TMP"]))
