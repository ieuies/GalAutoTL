# -*- coding: utf-8 -*-
"""Il2CppDumper integration — online汉化 standard for IL2CPP string literals.

Tutorials: Il2CppDumper(GameAssembly + global-metadata) → stringliteral.json
then filter game JP strings into the AutoTranslator dictionary.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Callable, List, Optional, Set
from urllib.request import Request, urlopen

from app.config import appdata_dir
from app.core.il2cpp_meta_text import find_metadata
from app.core.unity_raw_text import HAS_CJK, HAS_KANA

LogFn = Optional[Callable[[str], None]]

DUMPER_ASSET = "Il2CppDumper-win-v6.7.46.zip"
DUMPER_URL = (
    "https://github.com/Perfare/Il2CppDumper/releases/download/v6.7.46/" + DUMPER_ASSET
)


def _dumper_cache_dir() -> Path:
    p = appdata_dir() / "il2cpp_dumper"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _download_dumper(log: LogFn = None) -> Path:
    cache = _dumper_cache_dir()
    zip_path = cache / DUMPER_ASSET
    extract = cache / "win"
    exe = extract / "Il2CppDumper.exe"
    if exe.is_file():
        return exe
    local = Path(__file__).resolve().parents[2] / "tools" / "il2cpp_dumper" / DUMPER_ASSET
    if local.is_file() and local.stat().st_size > 1000:
        shutil.copy2(local, zip_path)
    elif not zip_path.is_file() or zip_path.stat().st_size < 1000:
        if log:
            log(f"下载 Il2CppDumper: {DUMPER_URL}")
        urls = [DUMPER_URL, "https://ghfast.top/" + DUMPER_URL]
        last = None
        for u in urls:
            try:
                req = Request(u, headers={"User-Agent": "GalAutoTL/1.0"})
                with urlopen(req, timeout=180) as resp, open(zip_path, "wb") as f:
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                if zip_path.stat().st_size > 100_000:
                    break
            except Exception as e:
                last = e
                if zip_path.is_file():
                    zip_path.unlink(missing_ok=True)
        if not zip_path.is_file() or zip_path.stat().st_size < 100_000:
            raise RuntimeError(f"无法下载 Il2CppDumper: {last}")
    if extract.exists():
        shutil.rmtree(extract, ignore_errors=True)
    extract.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract)
    # zip may nest one folder
    if not exe.is_file():
        for p in extract.rglob("Il2CppDumper.exe"):
            return p
    if not exe.is_file():
        raise RuntimeError("Il2CppDumper.exe 解压后未找到")
    return exe


def find_game_assembly(game_dir: Path) -> Optional[Path]:
    for name in ("GameAssembly.dll", "GameAssembly.so", "libil2cpp.so"):
        p = game_dir / name
        if p.is_file():
            return p
    hits = list(game_dir.rglob("GameAssembly.dll"))
    return hits[0] if hits else None


def _want_literal(s: str) -> bool:
    s = (s or "").strip()
    if len(s) < 2 or len(s) > 2500:
        return False
    if s.startswith(("get_", "set_", "System.", "UnityEngine.", "Assets/")):
        return False
    if re.fullmatch(r"[A-Za-z0-9_./\\:+\-<>\[\]\{\}\(\),;=\s]+", s):
        return False
    # Prefer player-facing JP (and short CN already present)
    if HAS_KANA.search(s) or ("「" in s) or (HAS_CJK.search(s) and len(s) <= 80):
        # drop obvious code dumps
        if s.count(".") >= 6 and sum(c.isascii() for c in s) > len(s) * 0.6:
            return False
        if any(x in s for x in ("Exception", "StackTrace", "m_FileID", "Assembly-")):
            return False
        return True
    return False


def parse_stringliteral_json(path: Path) -> List[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: List[str] = []
    seen: Set[str] = set()
    # formats: list of {"value": "..."} or list of strings or {"StringLiteral":[...]}
    rows = data
    if isinstance(data, dict):
        for k in ("StringLiteral", "stringLiteral", "strings", "Values"):
            if k in data and isinstance(data[k], list):
                rows = data[k]
                break
    if not isinstance(rows, list):
        return out
    for row in rows:
        if isinstance(row, str):
            val = row
        elif isinstance(row, dict):
            val = row.get("value") or row.get("Value") or row.get("string") or ""
        else:
            continue
        val = str(val).strip("\x00")
        if not _want_literal(val) or val in seen:
            continue
        seen.add(val)
        out.append(val)
    return out


def collect_il2cpp_string_literals(game_dir: Path, log: LogFn = None) -> List[str]:
    """Run Il2CppDumper (or reuse cache) and return JP-looking string literals."""
    game_dir = Path(game_dir)
    work = game_dir / "_galautotl_unity" / "il2cpp_dump"
    lit = work / "stringliteral.json"
    if lit.is_file() and lit.stat().st_size > 100:
        rows = parse_stringliteral_json(lit)
        if log:
            log(f"复用 Il2CppDumper stringliteral.json：{len(rows)} 条可译")
        return rows

    ga = find_game_assembly(game_dir)
    meta = find_metadata(game_dir)
    if not ga or not meta:
        if log:
            log("未找到 GameAssembly.dll / global-metadata.dat，跳过 Il2CppDumper")
        return []

    try:
        exe = _download_dumper(log)
    except Exception as e:
        if log:
            log(f"Il2CppDumper 不可用（将继续用资源深扫）: {e}")
        return []

    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    if log:
        log(f"运行 Il2CppDumper → {work.name} …")
    try:
        # Non-interactive CLI: exe assembly metadata outdir
        proc = subprocess.run(
            [str(exe), str(ga), str(meta), str(work)],
            cwd=str(exe.parent),
            capture_output=True,
            text=True,
            timeout=300,
            encoding="utf-8",
            errors="replace",
            input="\n\n\n",  # skip Console.ReadKey pause
        )
        if log and proc.returncode != 0:
            log(f"Il2CppDumper 退出码 {proc.returncode}: {(proc.stderr or proc.stdout or '')[-400:]}")
    except Exception as e:
        if log:
            log(f"Il2CppDumper 执行失败: {e}")
        return []

    # output may be in work or work subdir
    if not lit.is_file():
        hits = list(work.rglob("stringliteral.json"))
        if hits:
            lit = hits[0]
    if not lit.is_file():
        if log:
            log("未生成 stringliteral.json（Unity 版本或 metadata 可能不支持）")
        return []

    rows = parse_stringliteral_json(lit)
    if log:
        log(f"Il2CppDumper 字面量可译: {len(rows)}")
    # Optional: place DummyDll as Managed hint for external tools
    dummy = None
    for cand in work.rglob("DummyDll"):
        if cand.is_dir():
            dummy = cand
            break
    if dummy:
        managed = game_dir / "DeadEndCity_Data" / "Managed"
        # only create if no real Managed
        data_dirs = list(game_dir.glob("*_Data"))
        for d in data_dirs:
            m = d / "Managed"
            if not m.exists():
                try:
                    shutil.copytree(dummy, m)
                    if log:
                        log(f"已放置 DummyDll → {m.relative_to(game_dir)}（供 AssetStudio/UABEA）")
                except Exception:
                    pass
            break
    return rows


def harvest_pua_icons(texts: List[str]) -> List[str]:
    """Most common leading TMP Material icon (PUA) codepoints for dict variants."""
    from collections import Counter

    c: Counter[str] = Counter()
    for t in texts:
        if not t:
            continue
        ch = t[0]
        if "\ue000" <= ch <= "\uf8ff":
            c[ch] += 1
        # also escaped form already unescaped by callers ideally
    return [ch for ch, _n in c.most_common(16)]
