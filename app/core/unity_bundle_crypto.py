# -*- coding: utf-8 -*-
"""Unity AssetBundle / Addressables decrypt + load helpers.

Covers common galgame cases:
1) Plain UnityFS / UnityRaw / UnityWeb
2) Header XOR (single-byte or repeating keystream recovered vs UnityFS magic)
3) Unity CN builtin encryption via UnityPy.set_assetbundle_decrypt_key
4) Optional user key file: GalAutoTL_unity_ab_key.txt (hex / base64 / raw)

Does not claim to break every custom AES scheme — those still need a game-specific key.
"""
from __future__ import annotations

import base64
import hashlib
import re
import tempfile
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

LogFn = Optional[Callable[[str], None]]

UNITY_MAGICS = (
    b"UnityFS\x00",
    b"UnityRaw",
    b"UnityWeb",
    b"\xfa\xfa\xfa\xfa",  # legacy
)

_KEY_NAME_CANDIDATES = (
    "GalAutoTL_unity_ab_key.txt",
    "unity_ab_key.txt",
    "assetbundle_key.txt",
    "ab_key.txt",
)

_FALLBACK_CONFIGURED: Optional[str] = None


def configure_unitypy_fallback(
    game_dir: Optional[Path] = None,
    *,
    version: Optional[str] = None,
    log: LogFn = None,
) -> str:
    """Set UnityPy.config.FALLBACK_UNITY_VERSION so assets without version header load.

    PARANORMASIGHT / many IL2CPP builds fail with:
    'No valid Unity version found, and the fallback version is not correctly configured.'
    """
    global _FALLBACK_CONFIGURED
    import UnityPy
    from UnityPy import config as up_config

    ver = (version or "").strip()
    if not ver and game_dir is not None:
        try:
            from app.core.unity_runtime_inject import detect_unity_version

            ver = detect_unity_version(Path(game_dir)) or ""
        except Exception:
            ver = ""
    if not ver:
        ver = "2021.3.8f1"
    if _FALLBACK_CONFIGURED == ver:
        return ver
    up_config.FALLBACK_UNITY_VERSION = ver
    _FALLBACK_CONFIGURED = ver
    if log:
        log(f"UnityPy FALLBACK_UNITY_VERSION = {ver}")
    return ver


def is_unity_bundle_magic(data: bytes) -> bool:
    if not data or len(data) < 8:
        return False
    return any(data.startswith(m[: min(8, len(m))]) for m in UNITY_MAGICS if m)


def _xor_bytes(data: bytes, key: bytes, *, limit: Optional[int] = None) -> bytes:
    if not key:
        return data
    n = len(data) if limit is None else min(len(data), limit)
    out = bytearray(data)
    kl = len(key)
    for i in range(n):
        out[i] ^= key[i % kl]
    return bytes(out)


def recover_xor_candidates(head: bytes) -> List[Tuple[bytes, Optional[int]]]:
    """Return (key, xor_limit) guesses that make head look like UnityFS."""
    out: List[Tuple[bytes, Optional[int]]] = []
    if len(head) < 8:
        return out
    target = UNITY_MAGICS[0]  # UnityFS\0
    # single-byte
    for k in range(256):
        if bytes(b ^ k for b in head[:8]) == target:
            out.append((bytes([k]), None))
            out.append((bytes([k]), 0x100))
            out.append((bytes([k]), 0x400))
            out.append((bytes([k]), 0x1000))
            break
    # repeating keystream from magic XOR
    ks = bytes(a ^ b for a, b in zip(head[:8], target))
    if ks != b"\x00" * 8:
        for L in (4, 8, 16):
            key = (ks * ((L // len(ks)) + 1))[:L]
            out.append((key, None))
            out.append((key, 0x100))
            out.append((key, 0x400))
            out.append((key, 0x1000))
            out.append((key, 102400))  # some CN titles XOR first ~100KB
    # dedupe
    seen = set()
    uniq = []
    for k, lim in out:
        sig = (k, lim)
        if sig in seen:
            continue
        seen.add(sig)
        uniq.append((k, lim))
    return uniq


def try_plain_or_xor_decrypt(data: bytes) -> Tuple[Optional[bytes], str]:
    """Return (decrypted_or_same, method_label)."""
    if is_unity_bundle_magic(data):
        return data, "plain"
    # Raw SerializedFile (.assets / level*) often starts with 0x00 — XOR vs UnityFS
    # magic yields a false-positive "UnityFS" header and corrupts the file.
    if len(data) >= 8 and data[:4] == b"\x00\x00\x00\x00":
        return None, ""
    for key, lim in recover_xor_candidates(data[:64]):
        # null plaintext ⇒ keystream == magic; never a real encryptor
        if key == b"UnityFS\x00" or key.startswith(b"UnityFS"):
            continue
        if set(key) <= {0}:
            continue
        dec = _xor_bytes(data, key, limit=lim)
        if is_unity_bundle_magic(dec):
            label = f"xor key={key.hex()} limit={lim if lim is not None else 'all'}"
            return dec, label
    return None, ""


def parse_key_blob(text: str) -> Optional[bytes]:
    t = (text or "").strip()
    if not t:
        return None
    # hex
    hx = re.sub(r"[\s,;]", "", t)
    if re.fullmatch(r"[0-9a-fA-F]+", hx) and len(hx) % 2 == 0 and 8 <= len(hx) <= 128:
        try:
            return bytes.fromhex(hx)
        except ValueError:
            pass
    # base64
    try:
        raw = base64.b64decode(t, validate=False)
        if 4 <= len(raw) <= 64:
            return raw
    except Exception:
        pass
    # utf-8 raw key string (Unity CN often 16 chars)
    b = t.encode("utf-8")
    if 4 <= len(b) <= 64:
        return b
    return None


def discover_unity_cn_keys(game_dir: Path) -> List[bytes]:
    keys: List[bytes] = []
    seen = set()

    def add(k: Optional[bytes]) -> None:
        if not k or k in seen:
            return
        seen.add(k)
        keys.append(k)

    import os

    env = os.environ.get("GALAUTOTL_UNITY_AB_KEY") or os.environ.get("UNITY_AB_KEY")
    if env:
        add(parse_key_blob(env))

    for name in _KEY_NAME_CANDIDATES:
        for base in (game_dir, game_dir / "StreamingAssets", *[p for p in game_dir.glob("*_Data")]):
            p = base / name if base.is_dir() else None
            if p and p.is_file():
                try:
                    add(parse_key_blob(p.read_text(encoding="utf-8", errors="ignore")))
                except Exception:
                    pass

    # light scan of GameAssembly / large dll for printable 16-char keys near decrypt hints
    bins: List[Path] = []
    for p in game_dir.glob("GameAssembly.dll"):
        bins.append(p)
    for p in game_dir.glob("*_Data/../GameAssembly.dll"):
        bins.append(p)
    ga = game_dir / "GameAssembly.dll"
    if ga.is_file():
        bins.append(ga)
    for dll in bins[:2]:
        try:
            raw = dll.read_bytes()
        except Exception:
            continue
        # keep scan bounded
        if len(raw) > 80_000_000:
            raw = raw[:80_000_000]
        for m in re.finditer(rb"AssetBundleDecryptKey|SetAssetBundleDecryptKey|UnityCN", raw):
            window = raw[max(0, m.start() - 64) : m.end() + 256]
            for sm in re.finditer(rb"[\x20-\x7e]{12,32}", window):
                add(sm.group(0))
        # also some games embed 16-byte hex ascii
        for sm in re.finditer(rb"[0-9a-fA-F]{32}", raw[:5_000_000]):
            add(parse_key_blob(sm.group(0).decode("ascii")))
            if len(keys) > 32:
                break
    return keys[:40]


def apply_unity_cn_keys(keys: Sequence[bytes], log: LogFn = None) -> bool:
    if not keys:
        return False
    try:
        import UnityPy
    except ImportError:
        return False
    for k in keys:
        try:
            UnityPy.set_assetbundle_decrypt_key(k)
            if log:
                preview = k[:16]
                log(f"已设置 UnityCN AssetBundle 密钥 ({len(k)} bytes, {preview!r}…)")
            return True
        except Exception:
            continue
    return False


def materialize_decrypted(
    path: Path,
    cache_dir: Path,
    *,
    log: LogFn = None,
) -> Tuple[Path, str]:
    """Return path to a loadable bundle (maybe temp decrypted copy) + method."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except Exception as e:
        raise RuntimeError(f"无法读取 {path.name}: {e}") from e

    # PARANORMASIGHT etc.: proprietary header then UnityFS
    ufs = data.find(b"UnityFS")
    if ufs > 0 and ufs < 4096:
        cache_dir.mkdir(parents=True, exist_ok=True)
        out = cache_dir / f"{path.stem}_ufs{ufs}{path.suffix or '.bundle'}"
        hdr = cache_dir / f"{path.stem}_ufs{ufs}.hdr"
        body = data[ufs:]
        if not hdr.exists() or hdr.stat().st_size != ufs:
            hdr.write_bytes(data[:ufs])
        if not out.exists() or out.stat().st_size != len(body):
            out.write_bytes(body)
            if log:
                log(f"  剥离壳头 {path.name} → UnityFS@{ufs}")
        return out, f"unityfs-prefix@{ufs}"

    if is_unity_bundle_magic(data):
        return path, "plain"

    dec, method = try_plain_or_xor_decrypt(data)
    if dec is None:
        # leave as-is; UnityCN path may still open via UnityPy key
        return path, "undecrypted"
    cache_dir.mkdir(parents=True, exist_ok=True)
    # stable name
    digest = hashlib.md5(path.read_bytes()[:4096] + str(path.stat().st_size).encode()).hexdigest()[:12]
    out = cache_dir / f"{path.stem}_{digest}{path.suffix or '.bundle'}"
    if not out.exists() or out.stat().st_size != len(dec):
        out.write_bytes(dec)
        if log:
            log(f"  解密 {path.name} → {out.name} ({method})")
    return out, method


def shell_prefix_bytes(path: Path, cache_dir: Optional[Path] = None) -> bytes:
    """Return proprietary bytes before UnityFS (empty if none)."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError:
        return b""
    ufs = data.find(b"UnityFS")
    if ufs > 0 and ufs < 4096:
        return data[:ufs]
    if cache_dir:
        for hdr in Path(cache_dir).glob(f"{path.stem}_ufs*.hdr"):
            try:
                return hdr.read_bytes()
            except OSError:
                continue
    return b""


def write_shelled_unityfs(
    dest: Path,
    unityfs_blob: bytes,
    *,
    prefix: bytes = b"",
    backup: bool = True,
) -> None:
    """Write UnityFS payload, re-attaching optional StreamingAssets shell header."""
    import shutil

    dest = Path(dest)
    if backup and dest.is_file():
        bak = Path(str(dest) + ".galautotl.bak")
        if not bak.exists():
            shutil.copy2(dest, bak)
    dest.write_bytes((prefix or b"") + unityfs_blob)


def load_unity_env(
    path: Path,
    *,
    cache_dir: Optional[Path] = None,
    log: LogFn = None,
    game_dir: Optional[Path] = None,
):
    """UnityPy.load with XOR preprocess + UnityCN key already set by caller."""
    import UnityPy

    path = Path(path)
    # Infer game root from …/Foo_Data/xxx.assets when not provided
    root = game_dir
    if root is None:
        parent = path.parent
        if parent.name.endswith("_Data") or parent.name.lower() == "data":
            root = parent.parent
        elif parent.parent.name.endswith("_Data"):
            root = parent.parent.parent
    try:
        configure_unitypy_fallback(root, log=log)
    except Exception:
        pass

    cache = cache_dir or (path.parent / "_galautotl_ab_dec")
    load_path, method = materialize_decrypted(path, cache, log=log)
    try:
        env = UnityPy.load(str(load_path))
        return env, method
    except Exception as e1:
        # Bad XOR cache (historical false-positive) → retry original file
        if load_path.resolve() != path.resolve():
            try:
                env = UnityPy.load(str(path))
                if log:
                    log(f"  解密副本不可用，改用原文件: {path.name}")
                return env, "plain-fallback"
            except Exception:
                pass
        if method != "undecrypted":
            raise
        # last resort: try all XOR candidates even if magic check was strict
        data = path.read_bytes()
        for key, lim in recover_xor_candidates(data[:64])[:12]:
            if key == b"UnityFS\x00" or key.startswith(b"UnityFS"):
                continue
            if len(data) >= 4 and data[:4] == b"\x00\x00\x00\x00":
                break
            dec = _xor_bytes(data, key, limit=lim)
            if not is_unity_bundle_magic(dec):
                continue
            tmp = cache / f"_try_{path.stem}_{key.hex()[:8]}.bundle"
            tmp.write_bytes(dec)
            try:
                env = UnityPy.load(str(tmp))
                if log:
                    log(f"  暴力 XOR 成功: {path.name} key={key.hex()} lim={lim}")
                return env, f"xor-brute {key.hex()}"
            except Exception:
                continue
        raise e1


def expand_asset_globs(game_dir: Path) -> List[Path]:
    """Include classic assets + .bundle / Addressables / StreamingAssets packs."""
    files: List[Path] = []
    data_dirs = list(game_dir.glob("*_Data"))
    if (game_dir / "Data").is_dir():
        data_dirs.append(game_dir / "Data")

    pats_top = (
        "*.unity3d",
        "*.assets",
        "*.bundle",
        "*.ab",
        "*.unity3d.bundle",
    )
    for data in data_dirs:
        for pat in (
            "*.unity3d",
            "*.assets",
            "sharedassets*.assets",
            "level*",
            "resources.assets",
            "*.bundle",
            "*.ab",
        ):
            files.extend(data.glob(pat))
        # Addressables / aa
        for sub in (
            data / "StreamingAssets",
            data / "StreamingAssets" / "aa",
            data / "StreamingAssets" / "AssetBundles",
            data / "StreamingAssets" / "Bundles",
        ):
            if not sub.is_dir():
                continue
            for pat in ("**/*.bundle", "**/*.ab", "**/*.unity3d", "**/*.assets"):
                try:
                    files.extend(sub.glob(pat))
                except Exception:
                    continue
            # PARANORMASIGHT: extensionless a000…a099 (UnityFS after short header)
            try:
                for p in sub.iterdir():
                    if p.is_file() and re.fullmatch(r"a\d{2,4}", p.name, re.I):
                        files.append(p)
            except Exception:
                pass

    for pat in pats_top:
        files.extend(game_dir.glob(pat))
    for sub in (
        game_dir / "StreamingAssets",
        game_dir / "AssetBundles",
        game_dir / "Bundles",
        game_dir / "aa",
    ):
        if sub.is_dir():
            for pat in ("**/*.bundle", "**/*.ab", "**/*.unity3d", "**/*.assets"):
                try:
                    files.extend(sub.glob(pat))
                except Exception:
                    continue

    # StandaloneWindows* player data next to exe sometimes
    for p in game_dir.glob("Standalone*/**/*.bundle"):
        files.append(p)

    return files
