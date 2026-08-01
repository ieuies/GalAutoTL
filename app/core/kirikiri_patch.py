# -*- coding: utf-8 -*-
"""Kirikiri / XP3 patch helpers (paths, KirikiriTools, plaintext checks).

Lessons locked in from 洗脳航路 localization (do not regress):
1. Never stub root Config.tjs — shadows system/Config.tjs → black/broken boot.
2. Never rewrite data.xp3 / strip *.sig by default — breaks signed titles.
3. Never translate script/config/plugin_ks/system — [iscript] TJS becomes syntax errors.
4. Never deploy ciphertext/mojibake as loose scenario/ — shadows good data.xp3 → mid-game crash.
5. Use patch2.xp3 (not patch.xp3): Initialize useArchiveIfExists("patch.xp3") → patch.xp3.xp3.
6. All CN overrides must be UTF-16-LE; mixing with CP932 from data.xp3 → mojibake after UTF-16 scripts load.
7. xp3dec: XOR (adlr&0xFF), with key scan fallback when primary fails KAG sniff.
8. Reject AI poison like 「无法识别，疑似乱码」.
9. FREAKSTRIKE: only CN-override k_scenario/ — never k_others/k_bonus (first.ks/macros).
10. Skip XP3 \"protected archive\" stub names; prefer plain bodies when ENC bit is a lie.
11. Prefer ASCII game paths — CN deploy vs launch-dir mismatch looks like \"没翻译\".
"""
from __future__ import annotations

import re
import shutil
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

# Top-level folders inside data.xp3 (KAG asset roots)
KAG_ROOT_DIRS = frozenset(
    {
        "scenario",
        "system",
        "image",
        "fgimage",
        "bgimage",
        "bgm",
        "sound",
        "video",
        "others",
        "rule",
        "evimage",
        "data",
        "config",
        "script",
        "plugin_ks",
    }
)

KIRIKIRI_VERSION_DLL_URL = (
    "https://github.com/arcusmaximus/KirikiriTools/releases/download/1.7/version.dll"
)

# Engine dirs — never AI-translate / never CN-override as loose folders
ENGINE_KS_DIRS = frozenset(
    {"script", "system", "plugin_ks", "plugin", "config"}
)

# FREAKSTRIKE / Iris macro trees — look like content folders but break boot if CN-overridden
MACRO_KS_DIRS = frozenset(
    {
        "k_others",  # first.ks, macro01… — boot glue
        "k_bonus",  # gallery/UI scripts; safe to leave JP
        "k_system",
        "k_plugin",
        "others",  # some packs put macros under others/
    }
)

# Boot/macro .ks file names are rejected via is_macro_ks_file()


def is_macro_ks_file(path: Path) -> bool:
    """True for boot/macro scripts that must stay Japanese."""
    stem = path.stem.lower()
    name = path.name.lower()
    if name in {"first.ks", "cgmsk.ks", "dialog.ks"}:
        return True
    if stem.startswith("macro"):
        return True
    if stem == "first" or stem.startswith("first_"):
        return True
    return False


def is_dialogue_ks_relpath(rel: Path) -> bool:
    """True for story dialogue trees only; False for engine/macros.

    Supports classic `scenario/` and FREAKSTRIKE `k_scenario/`.
    Never treat `k_others` / `k_bonus` as dialogue — those hold first.ks / macros;
    translating them breaks boot (same class of bug as translating script/).
    """
    parts = Path(rel).parts
    parts_l = [p.lower() for p in parts]
    if not parts_l:
        return False
    if any(p in ENGINE_KS_DIRS or p in MACRO_KS_DIRS for p in parts_l):
        return False
    if is_macro_ks_file(Path(parts[-1])):
        return False
    if "scenario" in parts_l:
        return True
    if any(p == "k_scenario" for p in parts_l):
        return True
    if any(p.endswith("_scenario") for p in parts_l):
        return True
    return False


def is_scenario_safe_ui_ks(rel: Path) -> bool:
    """scenario/ dialog.ks / macro*.ks / first.ks / cgmsk.ks — safe UI only.

    Aligns deploy with harvest (`is_macro_ks_file`). Still blocked under
    k_others/k_bonus/engine — those overlays break boot.
    """
    parts_l = [p.lower() for p in Path(rel).parts]
    if any(p in ENGINE_KS_DIRS or p in MACRO_KS_DIRS for p in parts_l):
        return False
    if not is_macro_ks_file(Path(rel)):
        return False
    return (
        "scenario" in parts_l
        or any(p == "k_scenario" or p.endswith("_scenario") for p in parts_l)
    )


def is_deployable_ks_relpath(rel: Path) -> bool:
    return is_dialogue_ks_relpath(rel) or is_scenario_safe_ui_ks(rel)


def scrub_stale_macro_overrides(game_dir: Path, log=None) -> int:
    """Remove leftover CN overlays of macro/engine dirs from older GalAutoTL runs."""
    removed = 0
    for sub in set(ENGINE_KS_DIRS) | set(MACRO_KS_DIRS):
        for base in (game_dir, game_dir / "unencrypted", game_dir / "cn_scenario"):
            stale = base / sub
            if not stale.is_dir():
                continue
            # only remove if it looks like a .ks dump (our prior CN overlay)
            ks = list(stale.rglob("*.ks"))
            if not ks:
                continue
            shutil.rmtree(stale)
            removed += 1
            if log:
                log(f"已清理危险覆盖目录 {stale.relative_to(game_dir)}/（宏/引擎脚本不可汉化）")
    return removed


def dialogue_top_folders(root: Path) -> List[str]:
    """Top-level folder names under root that hold deployable dialogue .ks."""
    found: set[str] = set()
    for p in root.rglob("*.ks"):
        rel = p.relative_to(root)
        if not is_dialogue_ks_relpath(rel):
            continue
        top = rel.parts[0]
        if top.lower() in ENGINE_KS_DIRS or top.lower() in MACRO_KS_DIRS:
            continue
        found.add(top)
    return sorted(found)


def warn_if_bad_game_path(game_dir: Path, log=None) -> None:
    """Warn when path is mojibake / non-ASCII — easy to deploy CN to one folder and launch another."""
    s = str(game_dir)
    try:
        s.encode("ascii")
        ascii_ok = True
    except UnicodeEncodeError:
        ascii_ok = False
    # classic GBK mojibake of Japanese folder names often contains these
    suspicious = any(c in s for c in "儂僞憊儕僀僞") or "�" in s
    if log and (not ascii_ok or suspicious):
        log(
            "警告: 游戏路径含非英文/疑似乱码文件夹名。"
            "建议复制到纯英文目录（如 C:\\Games\\GameName）再汉化，"
            "否则容易「汉化写在 A 文件夹、却从 B 文件夹启动」导致看起来没翻译。"
        )

POISON_TL_MARKERS = (
    "无法识别",
    "疑似乱码",
    "按原文输出",
    "［无法翻译］",
    "[无法翻译]",
)


def is_poison_translation(text: str) -> bool:
    """True if model returned a garbage placeholder instead of a real translation."""
    if not text or not text.strip():
        return True
    s = text.strip()
    return any(m in s for m in POISON_TL_MARKERS)


def normalize_kag_relpath(rel: Path) -> Path:
    """Map extracted paths like data/scenario/x.ks → scenario/x.ks for in-game lookup."""
    parts = rel.parts
    for i, part in enumerate(parts):
        if part.lower() in KAG_ROOT_DIRS:
            return Path(*parts[i:])
    if len(parts) >= 2 and parts[1].lower() in KAG_ROOT_DIRS:
        return Path(*parts[1:])
    return rel


def looks_like_ks_script(data: bytes) -> bool:
    """True only for readable KAG script bytes (reject cxdec / xp3dec ciphertext)."""
    from app.core.xp3_crypto import looks_like_kag_after_decode

    return looks_like_kag_after_decode(data)


def is_deployable_scenario_bytes(data: bytes) -> bool:
    """Extra gate before writing loose scenario/ (avoid mid-game crash/mojibake)."""
    if not data or not looks_like_ks_script(data):
        return False
    try:
        if data[:2] == b"\xff\xfe":
            sample = data[2:6000].decode("utf-16-le", errors="replace")
        elif data[:2] == b"\xfe\xff":
            sample = data[2:6000].decode("utf-16-be", errors="replace")
        else:
            sample = data[:6000].decode("cp932", errors="replace")
    except Exception:
        return False
    if sample.count("\ufffd") > 8:
        return False
    if is_poison_translation(sample[:500]):
        return False
    # classic mojibake / private-use clutter
    if "粢" in sample[:400]:
        return False
    pua = sum(1 for c in sample[:800] if "\ue000" <= c <= "\uf8ff")
    if pua > 12:
        return False
    nl = sample.count("\n")
    if len(data) > 500 and nl < 3:
        return False
    return True


def count_plain_ks(root: Path) -> Tuple[int, int]:
    """Return (plaintext_count, total_ks)."""
    total = 0
    plain = 0
    for p in root.rglob("*.ks"):
        if not p.is_file():
            continue
        total += 1
        try:
            if looks_like_ks_script(p.read_bytes()):
                plain += 1
        except OSError:
            pass
    return plain, total


def count_deployable_ks(root: Path) -> Tuple[int, int]:
    """Return (deployable_count, total_ks) using the STRICT gate.

    is_deployable_scenario_bytes rejects cxdec ciphertext that happens to
    decode to something kana-ish (looks_like_ks_script is too lenient there),
    so this is the right signal for "content layer still encrypted → needs
    GARbro".
    """
    total = 0
    good = 0
    for p in root.rglob("*.ks"):
        if not p.is_file():
            continue
        total += 1
        try:
            if is_deployable_scenario_bytes(p.read_bytes()):
                good += 1
        except OSError:
            pass
    return good, total


def ks_tree_looks_already_chinese(root: Path, *, sample_files: int = 24) -> bool:
    """True if sampled plaintext .ks dialogue looks mostly already Chinese.

    Used to avoid treating a previous GalAutoTL CN deploy as the JP source
    on a second full「开始汉化」.
    """
    from app.core.pipeline_harden import looks_already_chinese

    files = [p for p in root.rglob("*.ks") if p.is_file()]
    files = [p for p in files if looks_like_ks_script(p.read_bytes())]
    if not files:
        return False
    files = files[:sample_files]
    cn = 0
    jp = 0
    for p in files:
        try:
            raw = p.read_bytes()
            if raw[:2] == b"\xff\xfe":
                text = raw[2:12000].decode("utf-16-le", errors="replace")
            elif raw[:2] == b"\xfe\xff":
                text = raw[2:12000].decode("utf-16-be", errors="replace")
            else:
                text = raw[:12000].decode("utf-8", errors="replace")
                if text.count("\ufffd") > 20:
                    text = raw[:12000].decode("cp932", errors="replace")
        except Exception:
            continue
        for line in text.splitlines():
            s = line.strip()
            if len(s) < 2 or s.startswith((";", "@", "*", "#", "[")):
                continue
            # strip simple [tags] for sampling
            body = re.sub(r"\[[^\]]*\]", "", s).strip()
            if len(body) < 2:
                continue
            if looks_already_chinese(body):
                cn += 1
            elif re.search(r"[\u3040-\u30ff]", body):
                jp += 1
            if cn + jp >= 80:
                break
        if cn + jp >= 80:
            break
    if cn + jp < 6:
        return False
    return cn >= max(jp * 2, 1) and cn >= 5


def _scenario_ks_count_in_archives(game_dir: Path) -> int:
    """Total scenario/*.ks across original .xp3 archives (best-effort).

    Excludes GalAutoTL-generated packs (unencrypted.xp3, patch*.xp3) so the
    comparison against unencrypted/ reflects the true JP source size.
    """
    from app.core.xp3_io import find_xp3_archives, list_xp3
    try:
        archives = find_xp3_archives(game_dir)
    except Exception:
        return 0
    total = 0
    for arc in archives:
        name = arc.name.lower()
        if "unencrypted" in name or name.startswith("patch"):
            continue
        try:
            for e in list_xp3(arc):
                p = e.path.lower()
                if p.startswith("scenario/") and p.endswith(".ks"):
                    total += 1
        except Exception:
            continue
    return total


def _unencrypted_incomplete(game_dir: Path) -> bool:
    """True if unencrypted/ has far fewer scenario scripts than the archives.

    version.dll only exports scripts the game actually loaded; on a fresh run
    the later scenario files are missing, so translating from unencrypted/
    yields Chinese only for the first chapters. In that case prefer the full
    extract from the .xp3 archives instead.
    """
    unenc = game_dir / "unencrypted" / "scenario"
    if not unenc.is_dir():
        return False
    try:
        n_unenc = len(list(unenc.glob("*.ks")))
    except OSError:
        return False
    n_arch = _scenario_ks_count_in_archives(game_dir)
    # Archives empty / can't list → trust unencrypted as-is.
    if n_arch <= 0:
        return False
    # unencrypted/ is a partial export when it has fewer scenario scripts
    # than the archives (version.dll only exports what was loaded so far).
    return n_unenc < n_arch


def find_plaintext_source(game_dir: Path, text_dir: Optional[Path]) -> Optional[Path]:
    """Pick best folder of plaintext .ks: explicit text_dir, then unencrypted/.

    Skip trees that already look Chinese when a JP archive / unencrypted JP
    source is still available — otherwise a second full run re-translates CN.
    If unencrypted/ is a partial export (fewer scenario scripts than the .xp3
    archives), prefer the full archive extract instead.
    """
    from app.core.xp3_io import find_xp3_archives

    unenc = game_dir / "unencrypted"
    candidates: List[Path] = []
    if text_dir and text_dir.is_dir():
        candidates.append(text_dir)
    # unencrypted 明显不完整时，不直接用它（改为从 XP3 完整解包）
    if unenc.is_dir() and not _unencrypted_incomplete(game_dir):
        if unenc not in candidates:
            candidates.append(unenc)

    has_archive = bool(find_xp3_archives(game_dir))
    for cand in candidates:
        n_plain, n_total = count_plain_ks(cand)
        if not ((n_total and n_plain >= max(1, n_total // 4)) or n_plain >= 3):
            continue
        # Prefer JP: if this dump is already CN and we can still get JP elsewhere
        if ks_tree_looks_already_chinese(cand):
            other_jp = False
            if cand.resolve() != unenc.resolve() and unenc.is_dir():
                if not ks_tree_looks_already_chinese(unenc) and count_plain_ks(unenc)[0] >= 3:
                    other_jp = True
            if has_archive or other_jp:
                continue
        return cand
    # Only fall back to unencrypted when it is still JP (or no XP3 to extract).
    # If unencrypted is already CN and XP3 exists, return None → force extract.
    # If unencrypted is a partial export, return None → prefer full archive extract.
    if unenc.is_dir() and count_plain_ks(unenc)[0] >= 3:
        if has_archive and (ks_tree_looks_already_chinese(unenc) or _unencrypted_incomplete(game_dir)):
            return None
        return unenc
    return None


def _bundled_version_dll() -> Path:
    return Path(__file__).resolve().parents[2] / "tools" / "kirikiri" / "version.dll"


def ensure_kirikiri_tools(
    game_dir: Path, log=None, *, create_extract_marker: bool = False
) -> bool:
    """Deploy version.dll. Do NOT create extract-unencrypted.txt after CN deploy
    (it would overwrite Chinese dumps with Japanese on next launch).
    """
    game_dir = Path(game_dir)
    bundled = _bundled_version_dll()
    if not bundled.is_file():
        bundled.parent.mkdir(parents=True, exist_ok=True)
        try:
            if log:
                log("下载 KirikiriTools version.dll …")
            urllib.request.urlretrieve(KIRIKIRI_VERSION_DLL_URL, bundled)
        except Exception as e:
            if log:
                log(f"下载 version.dll 失败: {e}")
            return False

    dst = game_dir / "version.dll"
    if not dst.is_file() or dst.stat().st_size != bundled.stat().st_size:
        shutil.copy2(bundled, dst)
        if log:
            log("已部署 version.dll（KirikiriTools 明文/补丁加载）")

    if create_extract_marker:
        marker = game_dir / "extract-unencrypted.txt"
        if not marker.is_file():
            marker.write_bytes(b"")
            if log:
                log("已创建 extract-unencrypted.txt（运行游戏可导出 unencrypted/）")
    return True


def force_ks_utf16_le(path: Path) -> bool:
    """Re-encode a .ks file as UTF-16-LE with BOM. Returns True if rewritten."""
    from app.core.ks_script import detect_ks_encoding, write_ks

    raw = path.read_bytes()
    if raw[:2] == b"\xff\xfe":
        return False
    enc = detect_ks_encoding(raw)
    if enc.startswith("utf-16"):
        text = raw.decode(enc, errors="replace")
    else:
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            text = raw.decode("cp932", errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]
    write_ks(path, text, "utf-16-le")
    return True


def force_tree_utf16_le(root: Path, only_scenario: bool = True) -> int:
    """UTF-16-LE all deployable dialogue .ks under root. Returns files rewritten."""
    n = 0
    for p in root.rglob("*.ks"):
        rel = p.relative_to(root)
        if only_scenario and not is_deployable_ks_relpath(rel):
            continue
        if not is_deployable_scenario_bytes(p.read_bytes()) and p.read_bytes()[:2] != b"\xff\xfe":
            if not looks_like_ks_script(p.read_bytes()):
                continue
        try:
            if force_ks_utf16_le(p):
                n += 1
        except Exception:
            continue
    return n


def stage_normalized_tree(src_root: Path, dest_root: Path) -> int:
    """Copy plaintext dialogue .ks (+ scenario sidecar txt/csv/tsv) into dest."""
    if dest_root.exists():
        shutil.rmtree(dest_root)
    dest_root.mkdir(parents=True)
    n = 0
    skipped = 0
    for p in src_root.rglob("*.ks"):
        try:
            raw = p.read_bytes()
        except OSError:
            skipped += 1
            continue
        if raw[:2] != b"\xff\xfe" and looks_like_ks_script(raw):
            try:
                force_ks_utf16_le(p)
                raw = p.read_bytes()
            except Exception:
                pass
        if not is_deployable_scenario_bytes(raw):
            skipped += 1
            continue
        rel = normalize_kag_relpath(p.relative_to(src_root))
        if not is_deployable_ks_relpath(rel):
            skipped += 1
            continue
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)
        n += 1
    # Sidecar tables next to dialogue (translated in-place under scripts/)
    for p in src_root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in {".txt", ".csv", ".tsv"}:
            continue
        rel = normalize_kag_relpath(p.relative_to(src_root))
        parts_l = [x.lower() for x in Path(rel).parts]
        if any(x in ENGINE_KS_DIRS or x in MACRO_KS_DIRS for x in parts_l):
            continue
        if not (
            "scenario" in parts_l
            or any(x == "k_scenario" or x.endswith("_scenario") for x in parts_l)
        ):
            continue
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)
        n += 1
    if skipped and n == 0:
        raise RuntimeError(
            "没有可部署的明文剧本 .ks（scenario/ 或 k_scenario/ 等）。"
            "密文/乱码不会写入免封包目录（否则会盖住 data.xp3 导致中途崩溃）。"
        )
    return n


def deploy_unencrypted_overrides(game_dir: Path, patch_root: Path, log=None) -> int:
    """Replace unencrypted/ with dialogue CN trees (version.dll priority)."""
    loose = game_dir / "unencrypted"
    if loose.exists():
        shutil.rmtree(loose)
    loose.mkdir(parents=True)
    n = 0
    for top in dialogue_top_folders(patch_root):
        src = patch_root / top
        if not src.is_dir():
            continue
        shutil.copytree(src, loose / top)
        n += sum(1 for _ in (loose / top).rglob("*.ks"))
    if log and n:
        log(f"已写入 unencrypted/（{n} 个 .ks，需保留 version.dll）")
    return n


KAG_ARCHIVE_SUBFOLDERS = (
    "bgm",
    "config",
    "cv",
    "effect",
    "evecg",
    "moive",
    "plugin_ks",
    "rule",
    "scenario",
    "script",
    "se",
    "syscg",
    "system",
    "clickable",
    "25cdata",
    "others",
)


def build_after_init2_loader(folders: Optional[List[str]] = None) -> str:
    """Raise priority for dialogue folders only — never override script/macros."""
    folders = folders or ["scenario"]
    lines = [
        "// GalAutoTL — dialogue CN only (never script/config/macro folders)",
        "try {",
    ]
    for folder in folders:
        folder = folder.replace("\\", "/").strip("/")
        low = folder.lower()
        if not folder or low in ENGINE_KS_DIRS or low in MACRO_KS_DIRS:
            continue
        lines.append(f'    Storages.addAutoPath(System.exePath + "{folder}/");')
        lines.append(
            f'    Storages.addAutoPath(System.exePath + "unencrypted/{folder}/");'
        )
        lines.append(
            f'    Storages.addAutoPath(System.exePath + "patch2.xp3>{folder}/");'
        )
        lines.append(
            f'    Storages.addAutoPath(System.exePath + "unencrypted.xp3>{folder}/");'
        )
    lines.append("} catch (e) {}")
    lines.append("")
    return "\n".join(lines)


def deploy_loose_kag_folders(game_dir: Path, patch_root: Path, log=None) -> int:
    """Copy dialogue folders to game root; scrub macro overlays from older runs."""
    scrub_stale_macro_overrides(game_dir, log)
    tops = dialogue_top_folders(patch_root)
    if not tops:
        return 0
    n = 0
    for top in tops:
        if top.lower() in ENGINE_KS_DIRS or top.lower() in MACRO_KS_DIRS:
            continue
        src = patch_root / top
        if not src.is_dir():
            continue
        dst = game_dir / top
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        n += sum(1 for _ in dst.rglob("*.ks"))
    if log:
        log(f"已写入免封包目录 {', '.join(tops)}/（{n} 个）")
    return n


AFTER_INIT2 = build_after_init2_loader()
