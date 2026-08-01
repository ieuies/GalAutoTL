# -*- coding: utf-8 -*-
"""Auto-locate and invoke GARbro / garbro-cli for encrypted archives."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, List, Optional

LogFn = Optional[Callable[[str], None]]

CANDIDATE_NAMES = (
    "garbro-cli.exe",
    "GARbro.Console.exe",
    "GameRes.Console.exe",
    "garbro.exe",
    "GARbro.exe",
)


def find_garbro(extra_dirs: Optional[List[Path]] = None) -> Optional[Path]:
    paths: List[Path] = []
    if extra_dirs:
        paths.extend(extra_dirs)
    # 工具自动下载的缓存目录（GARbro.Console.exe 会放在这里）
    try:
        paths.append(_garbro_cache_dir())
    except Exception:
        pass
    # tools next to this app / common locations
    env = os.environ.get("GALAUTOTL_GARBRO") or os.environ.get("GARBRO")
    if env:
        paths.append(Path(env))
    desk = Path.home() / "Desktop"
    for base in (
        desk / "工具",
        desk / "tools",
        desk / "GARbro",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "GARbro",
        Path(os.environ.get("LOCALAPPDATA", "")) / "GARbro",
    ):
        if base:
            paths.append(base)
    # PATH
    for name in CANDIDATE_NAMES:
        hit = shutil.which(name)
        if hit:
            return Path(hit)
    for base in paths:
        p = Path(base)
        if p.is_file() and p.suffix.lower() == ".exe":
            return p
        if p.is_dir():
            for name in CANDIDATE_NAMES:
                cand = p / name
                if cand.is_file():
                    return cand
            # shallow search
            for name in CANDIDATE_NAMES:
                for hit in p.rglob(name):
                    return hit
    return None


def extract_with_garbro(
    archive: Path,
    out_dir: Path,
    garbro: Path,
    log: LogFn = None,
) -> bool:
    """Best-effort extract. Returns True if out_dir gained files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    before = sum(1 for _ in out_dir.rglob("*") if _.is_file())
    name = garbro.name.lower()
    attempts: List[List[str]] = []
    if "cli" in name:
        attempts.append([str(garbro), "extract", "--input", str(archive), "--output", str(out_dir)])
        attempts.append([str(garbro), "x", "-o", str(out_dir), "-y", str(archive)])
        attempts.append([str(garbro), "x", "-o", str(out_dir), str(archive)])
    elif "console" in name:
        # GARbro.Console / GameRes.Console: 无选项列出，-x 提取全部
        attempts.append([str(garbro), "-x", str(archive)])
        attempts.append([str(garbro), "-x", str(archive), str(out_dir)])
        attempts.append([str(garbro), str(archive), str(out_dir)])
    else:
        # GUI — 有些构建支持把目标路径作为参数打开；尝试提取类参数，
        # 失败则明确提示（不能静默解包）
        attempts.append([str(garbro), str(archive)])
        if log:
            log(f"找到 GARbro GUI（{garbro.name}），尝试命令行打开；若无法静默解包请改用 Console 版")

    for cmd in attempts:
        try:
            if log:
                log("调用: " + " ".join(cmd))
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
                cwd=str(garbro.parent),
            )
            after = sum(1 for _ in out_dir.rglob("*") if _.is_file())
            if after > before:
                if log:
                    log(f"GARbro 解出 {after - before} 个文件")
                return True
            if r.returncode == 0 and after > before:
                return True
        except Exception as e:
            if log:
                log(f"GARbro 调用失败: {e}")
            continue
    return False


# ---------------------------------------------------------------------------
# GARbro 官方版自动下载
# ---------------------------------------------------------------------------
GARBRO_REPO = "morkt/GARbro"
GARBRO_CACHE_NAME = "garbro"


def _garbro_cache_dir() -> Path:
    """Local cache under the app tools dir: <repo>/tools/cache/garbro/"""
    return Path(__file__).resolve().parents[2] / "tools" / "cache" / GARBRO_CACHE_NAME


def _latest_garbro_asset_url(log: LogFn = None) -> str:
    """Query GitHub API for the latest GARbro release asset.

    GARbro officially ships a portable .rar (GARbro-v*.rar) plus a setup .exe.
    Prefer the portable .rar (contains GARbro.Console.exe), fall back to .zip.
    """
    import json
    import urllib.request

    api = f"https://api.github.com/repos/{GARBRO_REPO}/releases/latest"
    req = urllib.request.Request(
        api, headers={"User-Agent": "GalAutoTL/1.0", "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    assets = data.get("assets") or []
    # 优先便携 .rar（含 Console 版）
    for a in assets:
        n = a["name"].lower()
        if n.endswith(".rar") and "console" not in n:
            return a["browser_download_url"]
    # 其次 .zip
    for a in assets:
        n = a["name"].lower()
        if n.endswith(".zip"):
            return a["browser_download_url"]
    raise RuntimeError(
        f"GARbro latest release 未找到 .rar/.zip 资产（{len(assets)} 个资产）"
    )


def _extract_archive(archive: Path, dest: Path, log: LogFn = None) -> bool:
    """Extract zip (pure-python) or 7z/rar (system 7z). Returns True on success."""
    dest.mkdir(parents=True, exist_ok=True)
    try:
        if archive.suffix.lower() == ".zip":
            import zipfile

            with zipfile.ZipFile(archive) as zf:
                zf.extractall(dest)
            return True
        # .7z / .rar → 用系统 7z（7-Zip 可解 rar；Windows 上很常见）
        import subprocess as sp

        r = sp.run(
            ["7z", "x", str(archive), f"-o{dest}", "-y"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        return r.returncode == 0
    except Exception as e:
        if log:
            log(f"解压 GARbro 失败: {e}")
        return False


# GARbro 真实 exe 至少几百 KB；过小的文件是损坏/残缺下载（会报"不兼容"）
_GARBRO_EXE_MIN_SIZE = 200 * 1024


def _valid_garbro_exe(p: Path) -> bool:
    return p.is_file() and p.stat().st_size >= _GARBRO_EXE_MIN_SIZE


def _candidate_urls(url: str) -> List[str]:
    """GitHub may be slow/blocked in some regions → try common mirrors."""
    urls = [url]
    if "github.com" in url or "objects.githubusercontent.com" in url:
        mirrors = (
            "https://ghfast.top/",
            "https://ghproxy.net/",
            "https://mirror.ghproxy.com/",
        )
        for m in mirrors:
            urls.append(m + url)
    return urls


def _download_with_mirrors(url: str, dest: Path, log: LogFn = None) -> bool:
    """Download url to dest, trying mirrors on failure. True on success."""
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err: Optional[Exception] = None
    for cand in _candidate_urls(url):
        try:
            if log:
                log(f"下载: {cand}")
            urllib.request.urlretrieve(cand, dest)
            if dest.stat().st_size < 100_000:
                raise RuntimeError(f"下载文件过小: {dest.stat().st_size}")
            return True
        except Exception as e:
            last_err = e
            if dest.exists():
                try:
                    dest.unlink()
                except OSError:
                    pass
            continue
    if log:
        log(f"所有源下载失败: {last_err}")
    return False


def ensure_garbro(log: LogFn = None) -> Optional[Path]:
    """Locate garbro-cli/GARbro.Console; if absent, download official GARbro.

    Returns the Console/garbro-cli exe path, or None on failure.
    """
    # 1) 先看本机是否已有（排除残缺的假 exe）
    existing = find_garbro()
    if existing and _valid_garbro_exe(existing):
        return existing

    # 2) 尝试自动下载官方版
    cache = _garbro_cache_dir()
    console = cache / "GARbro.Console.exe"
    # 缓存里若有残缺假 exe，先清掉再重下
    if console.is_file() and not _valid_garbro_exe(console):
        try:
            console.unlink()
        except OSError:
            pass
    if not _valid_garbro_exe(console):
        try:
            if log:
                log("未找到 GARbro，尝试自动下载官方版 …")
            url = _latest_garbro_asset_url(log)
            fname = Path(url).name
            archive = cache / fname
            archive.parent.mkdir(parents=True, exist_ok=True)
            if not _download_with_mirrors(url, archive, log):
                if log:
                    log("GARbro 下载失败（含镜像重试）")
                return None
            if log:
                log(f"已下载 GARbro: {fname} ({archive.stat().st_size // (1024*1024)} MB)")
            if not _extract_archive(archive, cache, log):
                if log:
                    log("GARbro 解压失败")
                return None
        except Exception as e:
            if log:
                log(f"GARbro 自动下载失败: {e}")
            return None

    if _valid_garbro_exe(console):
        if log:
            log(f"GARbro 就绪: {console} ({console.stat().st_size // 1024} KB)")
        return console
    # 解压后可能是 garbro-cli.exe 或 GARbro.Console.exe
    for cand in ("GARbro.Console.exe", "GameRes.Console.exe", "garbro-cli.exe"):
        p = cache / cand
        if _valid_garbro_exe(p):
            if log:
                log(f"GARbro 就绪: {p}")
            return p
    # 便携版没有 Console（官方 release 只发 GUI）→ 尝试从源码编译
    if not _valid_garbro_exe(console):
        built = _build_garbro_console(cache, log)
        if built:
            return built
    return None


def _build_garbro_console(cache: Path, log: LogFn = None) -> Optional[Path]:
    """Compile GARbro.Console from official source when dotnet/msbuild is available.

    Official releases only ship the GUI; the console extractor exists in the
    source tree (Console/GARbro.Console.csproj, MIT). If the user has the source
    (e.g. Desktop/GARbro-master) and a .NET build tool, build it once so the
    pipeline can drive `-x` extraction fully automatically.
    """
    import shutil

    # Locate source tree
    candidates = [
        Path.home() / "Desktop" / "GARbro-master",
        Path.home() / "Desktop" / "GARbro",
        cache.parent / "GARbro-master",
        cache / "GARbro-master",
    ]
    src = None
    for cand in candidates:
        if (cand / "Console" / "GARbro.Console.csproj").is_file():
            src = cand
            break
    if src is None:
        if log:
            log("未找到 GARbro 源码（Desktop/GARbro-master），无法编译 Console 版")
        return None

    # Prefer msbuild (best for .NET Framework 4.6), else dotnet build
    msbuild = shutil.which("msbuild") or shutil.which("MSBuild.exe")
    dotnet = shutil.which("dotnet")
    csproj = src / "Console" / "GARbro.Console.csproj"
    out_console = cache / "GARbro.Console.exe"

    cmd = None
    if msbuild:
        cmd = [msbuild, str(csproj), "/p:Configuration=Release", "/p:OutputPath=" + str(cache)]
    elif dotnet:
        # .NET Core SDK may not build net46 without targeting pack; try anyway
        cmd = [dotnet, "build", str(csproj), "-c", "Release", "-o", str(cache)]
    if cmd is None:
        if log:
            log("本机无 msbuild/dotnet，无法编译 GARbro.Console")
        return None

    if log:
        log(f"尝试从源码编译 GARbro.Console: {src.name} …")
    try:
        import subprocess as sp
        r = sp.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(src))
        if _valid_garbro_exe(out_console):
            if log:
                log(f"GARbro.Console 编译成功: {out_console} ({out_console.stat().st_size // 1024} KB)")
            return out_console
        # fallback: search bin output
        for p in (src / "Console" / "bin" / "Release").rglob("GARbro.Console.exe"):
            if _valid_garbro_exe(p):
                shutil.copy2(p, out_console)
                if log:
                    log(f"GARbro.Console 就绪: {out_console}")
                return out_console
        if log:
            log(f"GARbro.Console 编译失败: {r.returncode}")
    except Exception as e:
        if log:
            log(f"GARbro.Console 编译异常: {e}")
    return None
