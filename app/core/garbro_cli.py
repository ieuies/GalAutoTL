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
    "GARbro.GUI.exe",  # 官方便携版只有 GUI；认它避免重复下载
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
        # GUI 版无法静默解包——不要尝试启动（会弹窗且解不出来）。
        # 需要 garbro-cli / GARbro.Console 才能命令行解包。
        if log:
            log(
                f"GARbro 为 GUI 版（{garbro.name}），无法静默解包；"
                "已跳过。需要命令行版（garbro-cli / GARbro.Console）才能自动解包。"
            )
        return False

    for cmd in attempts:
        try:
            if log:
                log("调用: " + " ".join(cmd))
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
                cwd=str(garbro.parent),
                stdin=subprocess.DEVNULL,  # console never prompts for a crypt scheme interactively
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
            encoding="utf-8",
            errors="replace",
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


_CONSOLE_EXE_NAMES = {
    "garbro-cli.exe",
    "garbro.console.exe",
    "gameres.console.exe",
}


def _is_console_name(p: Path) -> bool:
    return p is not None and p.name.lower() in _CONSOLE_EXE_NAMES


def _valid_garbro_console(p: Path) -> bool:
    """A GARbro.Console.exe is a thin host (few KB); validity needs the sibling
    official-release DLLs (ArcFormats.dll / GameRes.dll) that hold the formats.
    A GUI exe is never a usable console, even when the DLLs sit beside it."""
    if not _is_console_name(p):
        return False
    if not p.is_file() or p.stat().st_size < 5000:
        return False
    return (p.parent / "ArcFormats.dll").is_file() and (p.parent / "GameRes.dll").is_file()


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
    # 1) 先看本机是否已有可命令行驱动的 Console/cli（GUI 版不能静默解包）
    existing = find_garbro()
    if existing and _is_console_name(existing) and (
        _valid_garbro_exe(existing) or _valid_garbro_console(existing)
    ):
        return existing

    # 2) 尝试自动下载官方版
    cache = _garbro_cache_dir()
    console = cache / "GARbro.Console.exe"
    # 缓存里若有残缺假 exe，先清掉再重下
    if console.is_file() and not _valid_garbro_exe(console) and not _valid_garbro_console(console):
        try:
            console.unlink()
        except OSError:
            pass
    # 缓存里已有 GARbro（GUI 或任何 exe）→ 不再重复下载 rar
    already = any(cache.glob("*.exe")) if cache.is_dir() else False
    if not _valid_garbro_exe(console) and not already:
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

    def _accept(p: Path) -> bool:
        return _valid_garbro_exe(p) or _valid_garbro_console(p)

    if _accept(console):
        if log:
            log(f"GARbro 就绪: {console}")
        return console
    # 解压后可能是 garbro-cli.exe 或 GARbro.Console.exe
    for cand in ("GARbro.Console.exe", "GameRes.Console.exe", "garbro-cli.exe"):
        p = cache / cand
        if _accept(p):
            if log:
                log(f"GARbro 就绪: {p}")
            return p
    # 便携版没有 Console（官方 release 只发 GUI）→ 尝试从源码编译
    if not _accept(console):
        built = _build_garbro_console(cache, log)
        if built:
            return built
    return None


def _garbro_src_dir() -> Path:
    """Local copy of the official GARbro source (for building GARbro.Console)."""
    return Path(__file__).resolve().parents[2] / "tools" / "cache" / "garbro-src"


def _find_msbuild() -> Optional[str]:
    """Locate MSBuild.exe (VS installs) for building the .NET Framework targets."""
    import shutil

    hit = shutil.which("msbuild") or shutil.which("MSBuild.exe")
    if hit:
        return hit
    for root in (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft Visual Studio",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio",
    ):
        if not root.is_dir():
            continue
        for p in root.glob("*/*/MSBuild/Current/Bin/MSBuild.exe"):
            return str(p)
        for p in root.glob("*/*/MSBuild/*/Bin/MSBuild.exe"):
            return str(p)
    return None


def _patch_net20_csproj(src: Path) -> bool:
    """Make Net20.csproj buildable without the .NET 3.5 targeting pack.

    Adds the official Microsoft.NETFramework.ReferenceAssemblies.net20 NuGet
    package (provides v2.0 reference assemblies) and pins FrameworkPathOverride
    to it, bypassing the "needs .NET Framework 3.5 SP1" check on modern MSBuild.
    """
    p = src / "Net20" / "Net20.csproj"
    if not p.is_file():
        return False
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if "ReferenceAssemblies.net20" in text and "<FrameworkPathOverride>" in text:
        return True
    fpo = (
        Path.home()
        / ".nuget"
        / "packages"
        / "microsoft.netframework.referenceassemblies.net20"
        / "1.0.3"
        / "build"
        / ".NETFramework"
        / "v2.0"
    )
    pkg = (
        "  <ItemGroup>\n"
        '    <PackageReference Include="Microsoft.NETFramework.ReferenceAssemblies.net20" '
        'Version="1.0.3" PrivateAssets="all" />\n'
        "  </ItemGroup>\n"
    )
    if "ReferenceAssemblies.net20" not in text:
        idx = text.find('  <Import Project="$(MSBuildToolsPath)\\Microsoft.CSharp.targets"')
        if idx == -1:
            idx = text.find("  <Import Project=")
        if idx == -1:
            return False
        text = text[:idx] + pkg + text[idx:]
    if "<FrameworkPathOverride>" not in text:
        anchor = "    <TargetFrameworkProfile />\n  </PropertyGroup>"
        add = f"    <TargetFrameworkProfile />\n    <FrameworkPathOverride>{fpo}</FrameworkPathOverride>\n  </PropertyGroup>"
        if anchor not in text:
            return False
        text = text.replace(anchor, add, 1)
    try:
        p.write_text(text, encoding="utf-8", newline="\n")
        return True
    except OSError:
        return False


def _obtain_garbro_source(log: LogFn = None) -> Optional[Path]:
    """Return a writable copy of the official source (Console/GARbro.Console.csproj).

    Order: existing app-cache copy (garbro-src) -> copy of a local checkout
    (Desktop/GARbro-master, never modified in place) -> download the official
    morkt/GARbro source zip into the app cache (MIT license).
    """
    dest = _garbro_src_dir()
    if (dest / "Console" / "GARbro.Console.csproj").is_file():
        return dest

    local = [
        Path.home() / "Desktop" / "GARbro-master",
        Path.home() / "Desktop" / "GARbro",
    ]
    for cand in local:
        for s in (cand, cand / "GARbro-master"):
            if (s / "Console" / "GARbro.Console.csproj").is_file():
                if log:
                    log("复制本机 GARbro 源码到缓存（不在原目录打补丁）…")
                try:
                    import shutil

                    dest.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(s, dest, dirs_exist_ok=True)
                    return dest
                except OSError as e:
                    if log:
                        log(f"复制 GARbro 源码失败: {e}")
                    return None

    dest.mkdir(parents=True, exist_ok=True)
    url = "https://codeload.github.com/morkt/GARbro/zip/refs/heads/master"
    archive = dest / "GARbro-master.zip"
    if log:
        log("下载官方 GARbro 源码（用于编译命令行版，MIT 许可）…")
    if not _download_with_mirrors(url, archive, log):
        return None
    try:
        import zipfile

        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)
        for p in dest.rglob("Console/GARbro.Console.csproj"):
            return p.parent.parent
    except Exception as e:
        if log:
            log(f"GARbro 源码解压失败: {e}")
    return None


def _refresh_release_dlls(cache: Path, log: LogFn = None) -> bool:
    """Ensure the cache holds a consistent official-release DLL set (ArcFormats etc.).

    The console host is a thin shell; formats come from the official release
    DLLs (GameRes/ArcFormats/Net20/...). If a *.rar is present, re-extract it to
    refresh the DLLs (e.g. after a failed experiment polluted the cache).
    """
    if (cache / "ArcFormats.dll").is_file() and (cache / "GameRes.dll").is_file():
        return True
    rar = next(cache.glob("*.rar"), None)
    if rar is None:
        return False
    return _extract_archive(rar, cache, log)


def _build_garbro_console(cache: Path, log: LogFn = None) -> Optional[Path]:
    """Build GARbro.Console.exe from the OFFICIAL source (morkt/GARbro, MIT).

    Official releases only ship the GUI; the console extractor lives in the
    source tree (Console/GARbro.Console.csproj).  We compile it locally with
    MSBuild/dotnet and drop the exe next to the official release DLLs, so no
    third-party binaries are ever downloaded.

    Limitations: the console can only open archives for games present in
    GARbro's game database (Formats.dat) — that is exactly the set of games
    GARbro can decrypt, so it matches the "needs GARbro" use case.
    """
    import shutil
    import subprocess as sp

    out_console = cache / "GARbro.Console.exe"
    if _valid_garbro_exe(out_console):
        # quick self-check: host + official DLLs present
        if (cache / "ArcFormats.dll").is_file() and (cache / "GameRes.dll").is_file():
            return out_console
    if not _refresh_release_dlls(cache, log):
        if log:
            log("缺少官方 GARbro 发行版 DLL（ArcFormats/GameRes），无法组装命令行版")
        return None

    src = _obtain_garbro_source(log)
    if src is None:
        if log:
            log("未找到/无法下载 GARbro 源码（需要 Desktop/GARbro-master 或联网），跳过编译")
        return None

    msbuild = _find_msbuild()
    dotnet = shutil.which("dotnet")
    csproj = src / "Console" / "GARbro.Console.csproj"
    if msbuild is None and dotnet is None:
        if log:
            log("本机无 msbuild/dotnet，无法编译 GARbro.Console（可手动放 garbro-cli/GARbro.Console.exe 到工具目录）")
        return None

    if msbuild:
        if not _patch_net20_csproj(src):
            if log:
                log("Net20.csproj 补丁失败，无法编译")
            return None
        bld = cache / "garbro-build"
        if bld.exists():
            shutil.rmtree(bld, ignore_errors=True)
        bld.mkdir(parents=True, exist_ok=True)
        cmd = [
            msbuild,
            str(csproj),
            "/p:Configuration=Release",
            "/p:OutputPath=" + str(bld),
            "/p:RuntimeIdentifiers=win",
            "/v:minimal",
            "/nologo",
            "/restore",
        ]
    else:
        bld = cache
        cmd = [dotnet, "build", str(csproj), "-c", "Release", "-o", str(cache)]
    if log:
        log(f"从官方源码编译 GARbro.Console（{src.name}）… 约 10-60 秒")
    try:
        r = sp.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            cwd=str(src),
        )
    except Exception as e:
        if log:
            log(f"GARbro.Console 编译异常: {e}")
        return None

    def _built_ok(p: Path) -> bool:
        # the console host is a few-KB shell; the DLLs beside it matter
        return p.is_file() and p.stat().st_size >= 5000

    exe = bld / "GARbro.Console.exe"
    if _built_ok(exe):
        shutil.copy2(exe, out_console)
    elif _built_ok(out_console):
        pass
    else:
        # fallback: search bin output
        for p in (src / "Console" / "bin" / "Release").rglob("GARbro.Console.exe"):
            if _built_ok(p):
                shutil.copy2(p, out_console)
                break
    if not _valid_garbro_console(out_console):
        if log:
            log(f"GARbro.Console 编译失败: {r.returncode}")
        return None
    if log:
        log(f"GARbro.Console 编译成功: {out_console} ({out_console.stat().st_size // 1024} KB)")
    return out_console
