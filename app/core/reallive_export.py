# -*- coding: utf-8 -*-
"""Auto-provision RLDev (kprl) and export RealLive SEEN.TXT → export_utf8.

RealLive lesson: GalAutoTL used to require a pre-made `_tools/export_utf8`.
That step is now part of the RealLive pipeline (download kprl → disassemble slots).
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import urllib.request
from pathlib import Path
from typing import Callable, List, Optional

from app.config import appdata_dir

LogFn = Optional[Callable[[str], None]]

_API_ROOT = (
    "https://api.github.com/repos/000ylop/galgametools/contents/rldev-1.40-win32"
)
_UA = {"User-Agent": "GalAutoTL/1.0"}
_INDEX_SLOTS = 10000


def _log(log: LogFn, msg: str) -> None:
    if log:
        log(msg)


def rldev_cache_dir() -> Path:
    p = appdata_dir() / "rldev-1.40-win32"
    p.mkdir(parents=True, exist_ok=True)
    return p


def find_seen_txt(game_dir: Path) -> Optional[Path]:
    for name in ("SEEN.TXT", "Seen.txt", "seen.txt"):
        p = game_dir / name
        if p.is_file():
            return p
    return None


def _api_get(url: str):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def _download_file(url: str, dest: Path, log: LogFn = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    mirrors = [url]
    if "raw.githubusercontent.com" in url or "github.com" in url:
        mirrors = ["https://ghfast.top/" + url, url]
    last: Optional[Exception] = None
    for m in mirrors:
        try:
            req = urllib.request.Request(m, headers=_UA)
            with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
            if dest.stat().st_size < 64:
                raise RuntimeError("file too small")
            return
        except Exception as e:
            last = e
            if dest.is_file():
                dest.unlink(missing_ok=True)
    raise RuntimeError(f"下载失败 {dest.name}: {last}")


def _download_tree(api_url: str, dest_dir: Path, log: LogFn = None) -> None:
    items = _api_get(api_url)
    for it in items:
        name = it["name"]
        if it["type"] == "dir":
            _download_tree(it["url"], dest_dir / name, log)
            continue
        url = it.get("download_url")
        if not url:
            continue
        dest = dest_dir / name
        if dest.is_file() and dest.stat().st_size > 0:
            continue
        _log(log, f"下载 rldev: {dest.relative_to(dest_dir)}")
        _download_file(url, dest, log)


def ensure_rldev(log: LogFn = None, prefer: Optional[Path] = None) -> Path:
    """Return RLDev root containing bin/kprl.exe (cached under AppData)."""
    if prefer is not None:
        p = Path(prefer)
        if (p / "bin" / "kprl.exe").is_file() and (p / "lib" / "reallive.kfn").is_file():
            return p
    root = rldev_cache_dir()
    kprl = root / "bin" / "kprl.exe"
    kfn = root / "lib" / "reallive.kfn"
    if kprl.is_file() and kfn.is_file():
        return root
    _log(log, "首次准备 RealLive 解包工具 RLDev（自动下载，只需一次）…")
    _download_tree(_API_ROOT, root, log)
    if not kprl.is_file():
        raise FileNotFoundError(f"RLDev 下载不完整，缺少 {kprl}")
    if not kfn.is_file():
        raise FileNotFoundError(f"RLDev 下载不完整，缺少 {kfn}")
    _log(log, f"RLDev 就绪: {root}")
    return root


def _occupied_slots(seen_path: Path) -> List[int]:
    raw = seen_path.read_bytes()
    out: List[int] = []
    for i in range(_INDEX_SLOTS):
        off, ln = struct.unpack_from("<II", raw, i * 8)
        if ln > 0 and off + ln <= len(raw):
            out.append(i)
    return out


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def export_seen_to_utf8(
    game_dir: Path,
    tools_dir: Optional[Path] = None,
    log: LogFn = None,
    *,
    force: bool = False,
) -> Path:
    """Ensure game/_tools/export_utf8 has *.utf. Returns that directory."""
    game_dir = Path(game_dir)
    tools = Path(tools_dir) if tools_dir else (game_dir / "_tools")
    export = tools / "export_utf8"
    if not force and export.is_dir() and any(export.glob("*.utf")):
        n = sum(1 for _ in export.glob("*.utf"))
        _log(log, f"已有剧本导出 {n} 个 *.utf → {export}")
        return export

    seen = find_seen_txt(game_dir)
    if not seen:
        raise FileNotFoundError(f"未找到 SEEN.TXT: {game_dir}")

    rldev = ensure_rldev(log, prefer=tools / "rldev")
    kprl = rldev / "bin" / "kprl.exe"
    seens = tools / "seens"
    if export.exists():
        shutil.rmtree(export)
    if seens.exists():
        shutil.rmtree(seens)
    export.mkdir(parents=True)
    seens.mkdir(parents=True)

    # Mirror rldev into game _tools for optional rlc/full_patch later
    game_rldev = tools / "rldev"
    if not (game_rldev / "bin" / "kprl.exe").is_file():
        if game_rldev.exists():
            shutil.rmtree(game_rldev)
        shutil.copytree(rldev, game_rldev)

    env = os.environ.copy()
    env["RLDEV"] = str(rldev)
    slots = _occupied_slots(seen)
    _log(log, f"用 kprl 解包 SEEN.TXT（{len(slots)} 个场景）…")

    ok = 0
    fail = 0
    for i, slot in enumerate(slots, 1):
        out = seens / f"scene_{slot}"
        out.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(kprl),
            "-d",
            "-e",
            "utf-8",
            "-o",
            str(out),
            str(seen),
            str(slot),
        ]
        r = subprocess.run(
            cmd,
            env=env,
            cwd=str(game_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        utfs = list(out.rglob("*.utf"))
        if r.returncode == 0 and utfs:
            ok += 1
            for utf in utfs:
                dest = export / f"scene_{slot}_{utf.name}"
                dest.write_text(_read_text(utf), encoding="utf-8")
        else:
            fail += 1
        if i % 40 == 0:
            _log(log, f"  解包进度 {i}/{len(slots)}（成功 {ok}，跳过 {fail}）")

    n = sum(1 for _ in export.glob("*.utf"))
    if n <= 0:
        raise RuntimeError(
            "kprl 未能解出任何 *.utf。可检查 SEEN.TXT / RLDEV，或手动放置 export_utf8。"
        )
    _log(log, f"剧本导出完成：{n} 个 *.utf（成功 {ok}，失败/跳过 {fail}）→ {export}")
    return export


def ensure_reallive_utf_dirs(
    game_dir: Path,
    tools_dir: str = "",
    log: LogFn = None,
) -> tuple[Path, Path]:
    """Return (jp_export_dir, cn_out_dir), auto-exporting if needed."""
    tools = Path(tools_dir) if tools_dir.strip() else (game_dir / "_tools")
    jp = export_seen_to_utf8(game_dir, tools, log=log, force=False)
    cn = tools / "patch_work" / "cn_utf8"
    cn.mkdir(parents=True, exist_ok=True)
    return jp, cn
