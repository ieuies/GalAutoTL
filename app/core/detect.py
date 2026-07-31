# -*- coding: utf-8 -*-
"""Detect visual-novel engine from a game folder."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class DetectResult:
    engine: str = "unknown"
    confidence: str = "low"  # low | medium | high
    pipeline: str = "generic"  # reallive|generic|packed|lcse|kirikiri|yuris|unity|artemis|bgi
    notes: List[str] = field(default_factory=list)
    hints: List[str] = field(default_factory=list)
    workflow: str = ""  # human-readable steps

    def summary(self) -> str:
        lines = [
            f"引擎: {self.engine}（置信度 {self.confidence}）",
            f"建议管线: {self.pipeline}",
        ]
        if self.notes:
            lines.append("发现: " + "；".join(self.notes))
        if self.hints:
            lines.append("提示: " + "；".join(self.hints))
        if self.workflow:
            lines.append("流程:\n" + self.workflow)
        return "\n".join(lines)


def _exists_any(root: Path, names: list[str]) -> Optional[str]:
    for n in names:
        if (root / n).exists():
            return n
    return None


def _rglob_limited(root: Path, pattern: str, limit: int = 5) -> List[Path]:
    """Like rglob, but stop after `limit` hits (list(rglob)[:n] still walks the whole tree)."""
    out: List[Path] = []
    try:
        for p in root.rglob(pattern):
            out.append(p)
            if len(out) >= limit:
                break
    except OSError:
        pass
    return out


def _has_lcse(root: Path) -> bool:
    if list(root.glob("lcsebody*")) and list(root.glob("lcsebody*.lst")):
        return True
    # Cap exe scans — large Steam folders can have dozens of launchers
    for i, exe in enumerate(root.glob("*.exe")):
        if i >= 24:
            break
        try:
            raw = exe.read_bytes()[:2_000_000]
        except Exception:
            continue
        if b"LC-ScriptEngine" in raw or b"LikeCScriptEngine" in raw or b"lcsebody" in raw.lower():
            return True
    return False


def detect_engine(game_dir: str | Path) -> DetectResult:
    root = Path(game_dir)
    if not root.is_dir():
        return DetectResult(notes=["目录无效"], hints=["请选择游戏根目录"])

    notes: list[str] = []
    hints: list[str] = []

    # SakanaGL — .sx / .sxstorage + sakanagl.dll (e.g. IsekaiHaremSaver)
    sx = list(root.glob("*.sx")) + list((root / "pkg").glob("*.sx")) if (root / "pkg").is_dir() else list(root.glob("*.sx"))
    sxstorage = list(root.glob("*.sxstorage")) + (
        list((root / "pkg").glob("*.sxstorage")) if (root / "pkg").is_dir() else []
    )
    has_sakana = (root / "sakanagl.dll").is_file() or bool(sx) or bool(sxstorage)
    if has_sakana and (sx or sxstorage or (root / "sakanagl.dll").is_file()):
        if sx:
            notes.append("SX: " + ", ".join(p.name for p in sx[:4]))
        if sxstorage:
            notes.append(f".sxstorage 约 {len(sxstorage)} 个")
        if (root / "sakanagl.dll").is_file():
            notes.append("sakanagl.dll")
        return DetectResult(
            engine="SakanaGL",
            confidence="high",
            pipeline="sakana",
            notes=notes,
            hints=[
                "已支持一键：解 .sx/.sxstorage → AI 译文本 → 槽位夹紧回封",
                "需依赖 zstandard（首次会自动安装）",
            ],
            workflow=(
                "① 游戏根目录选到含 sakanagl.dll / pkg/*.sx 的文件夹\n"
                "② 管线 SakanaGL（或自动探测）→ 开始汉化\n"
                "③ 备份封包 → 解包译文本 → 写回 .sxstorage\n"
                "④ 详见 汉化启动说明_SakanaGL.txt"
            ),
        )

    # LC-ScriptEngine (Liquid / NEXTON) — e.g. 大催眠乱交学園
    if _has_lcse(root):
        bodies = [p.name for p in root.glob("lcsebody*") if p.is_file() and not p.name.endswith(".lst")]
        notes.append("lcsebody: " + (", ".join(bodies[:4]) or "有"))
        return DetectResult(
            engine="LC-ScriptEngine（Liquid/NEXTON）",
            confidence="high",
            pipeline="lcse",
            notes=notes,
            hints=[
                "已支持一键：解包 SNX → AI 翻译 → 回封注入",
                "请把「游戏根目录」选到此文件夹，源语言选日文",
            ],
            workflow=(
                "① 游戏根目录指到含 lcsebody1 的文件夹\n"
                "② 填 API Key，管线选 LCSE（或自动探测）\n"
                "③ 开始汉化：备份 → 解包 → AI 精翻 → 槽位硬化回封（防中途卡死）\n"
                "④ 自动 GBK 显示补丁 +「点我启动_中文汉化版.bat」\n"
                "⑤ 用该 bat 启动；勿用日语 LE；尽量新游戏"
            ),
        )

    # RealLive
    rl_exe = _exists_any(root, ["REALLIVE.EXE", "RealLive.exe", "reallive.exe"])
    seen = _exists_any(root, ["SEEN.TXT", "Seen.txt", "seen.txt"])
    if rl_exe or seen:
        notes.append(f"{rl_exe or '无EXE'} / {seen or '无SEEN.TXT'}")
        utf = root / "_tools" / "export_utf8"
        if utf.is_dir() and any(utf.glob("*.utf")):
            notes.append(f"已有 UTF 导出: {utf}")
        else:
            hints.append("无 export_utf8 时会自动下载 RLDev/kprl 从 SEEN.TXT 导出")
        return DetectResult(
            engine="RealLive / AVG32",
            confidence="high" if (rl_exe and seen) else "medium",
            pipeline="reallive",
            notes=notes,
            hints=hints,
            workflow=(
                "① 选含 SEEN.TXT 的游戏根目录\n"
                "② 开始汉化：自动解包（kprl）→ AI 翻译 → 写出 cn_utf8\n"
                "③ 有 _tools/full_patch 时可自动写回；否则用导出目录验收"
            ),
        )

    # Kirikiri
    xp3 = list(root.glob("*.xp3"))[:8] + list(root.glob("*.XP3"))[:8]
    # de-dup
    _seen = set()
    xp3_uniq = []
    for p in xp3:
        k = str(p).lower()
        if k not in _seen:
            _seen.add(k)
            xp3_uniq.append(p)
    xp3 = xp3_uniq
    ks_all = [p for p in root.rglob("*.ks") if "_galautotl" not in str(p).lower()]
    ks = ks_all[:8]
    krkr = any(
        (root / n).exists()
        for n in ("krkr.exe", "Kirikiri.exe", "kirikiri.exe", "krkrz.exe", "Krkrz.exe")
    ) or any(p.name.lower().endswith(".exe") and "krkr" in p.name.lower() for p in root.glob("*.exe"))
    if xp3 or ks or krkr:
        if xp3:
            notes.append("XP3: " + ", ".join(p.name for p in xp3[:6]))
        if ks_all:
            notes.append(f"明文 .ks 约 {len(ks_all)} 个")
        return DetectResult(
            engine="Kirikiri / KAG",
            confidence="high",
            pipeline="kirikiri",
            notes=notes,
            hints=[
                "一键：解 XP3 → 仅译 scenario/或k_scenario → UTF-16 → patch2 + 免封包",
                "禁 CP932；不译 script/macro/.tjs；漏翻二扫 + UI 词表",
                "日文路径建议复制到 C:\\Games\\英文名",
            ],
            workflow=(
                "① 游戏根目录尽量用纯英文路径（含 data.xp3）\n"
                "② 源语言日文，管线 Kirikiri（或自动探测）\n"
                "③ 开始汉化：备份 → 解包 → 只译剧本 → patch2.xp3\n"
                "④ 用同一目录 + Locale Emulator/日语区域启动；勿开另一份拷贝"
            ),
        )

    # YU-RIS
    ybn = list(root.glob("*.ybn")) + list(root.glob("ysbin/*.ybn"))
    ypf = list(root.glob("*.ypf")) + list(root.glob("pac/*.ypf"))
    if ybn or ypf:
        if ypf:
            notes.append("YPF: " + ", ".join(p.name for p in ypf[:6]))
        if ybn:
            notes.append(f".ybn 约 {len(ybn)} 个")
        return DetectResult(
            engine="YU-RIS",
            confidence="high",
            pipeline="yuris",
            notes=notes,
            hints=[
                "已支持一键：YSTB 解密 → AI 译 → 松散 .ybn 注入",
                "若仅有 YPF：需 garbro-cli 或先解出 ysbin",
            ],
            workflow=(
                "① 游戏根目录选到含 .ypf / .ybn 的文件夹\n"
                "② 管线 YU-RIS，源语言日文 → 开始汉化\n"
                "③ 自动密钥还原对白并写回松散脚本\n"
                "④ 直接开游戏；详见 汉化启动说明_YURIS.txt"
            ),
        )

    # Unity
    data_dirs = list(root.glob("*_Data"))
    if (root / "UnityPlayer.dll").exists() or data_dirs or (
        (root / "Data").is_dir() and (root / "Data" / "Managed").exists()
    ):
        sa = any((d / "StreamingAssets").is_dir() for d in data_dirs)
        il2cpp = any(
            (d / "il2cpp_data" / "Metadata" / "global-metadata.dat").is_file() for d in data_dirs
        ) or (root / "GameAssembly.dll").exists()
        notes.append("UnityPlayer / *_Data" + (" + StreamingAssets" if sa else ""))
        if il2cpp:
            notes.append("IL2CPP（MonoBehaviour 原始字串 + metadata）")
        return DetectResult(
            engine="Unity",
            confidence="high",
            pipeline="unity",
            notes=notes,
            hints=[
                "StreamingAssets + TextAsset + MB + Il2Cpp + TypeTree 多源收字",
                "UTF-8 运行时注入（禁 CP932）；漏翻二扫 + UI 词表；默认不改 data.unity3d",
                "需 UnityPy；用「点我启动_中文汉化_Unity.bat」",
            ],
            workflow=(
                "① 选含 UnityPlayer.dll / xxx_Data 的游戏根目录\n"
                "② 探测后选管线 Unity（不要简单模式扫整包）\n"
                "③ 自动译并注入；备份为 *.galautotl.bak\n"
                "④ 详见 汉化启动说明_Unity.txt"
            ),
        )

    # Artemis
    pfs = list(root.glob("*.pfs")) + list(root.glob("root.pfs*"))
    ast = _rglob_limited(root, "*.ast", 5)
    if pfs or (root / "system.ini").exists() and ast:
        if pfs:
            notes.append("PFS: " + ", ".join(p.name for p in pfs[:6]))
        if ast:
            notes.append("明文 .ast 已有")
        return DetectResult(
            engine="Artemis",
            confidence="high",
            pipeline="artemis",
            notes=notes,
            hints=[
                "一键：解 PFS → 译 script/*.ast → 松散 script/ 覆盖",
                "name= 只留短角色名，整句只写正文（避免名字层+对话框叠字）",
                "勿译 system/*.lua；日文环境可用 Locale Emulator",
            ],
            workflow=(
                "① 游戏根目录选到含 root.pfs / 主程序 exe 的文件夹\n"
                "② 管线选 Artemis（或自动探测）\n"
                "③ 开始汉化：译正文 + 还原角色名框（防叠字）\n"
                "④ 详见 汉化启动说明_Artemis.txt"
            ),
        )

    # BGI / Ethornell
    arcs = list(root.glob("data*.arc")) + list(root.glob("*.arc"))
    buriko = False
    try:
        for p in list(root.glob("*"))[:30]:
            if p.is_file() and p.suffix == "" and p.stat().st_size > 64:
                if p.read_bytes()[:28].startswith(b"BurikoCompiledScriptVer1.00"):
                    buriko = True
                    break
    except OSError:
        pass
    if arcs and (
        buriko
        or any(p.name.lower().startswith("data") for p in arcs)
        or list(root.glob("*.exe"))
    ):
        # avoid misdetect generic .arc from other engines: prefer data*.arc or Buriko header
        if buriko or any(p.name.lower().startswith("data") for p in arcs):
            notes.append("ARC: " + ", ".join(p.name for p in arcs[:6]))
            if buriko:
                notes.append("BurikoCompiledScript")
            return DetectResult(
                engine="BGI / Ethornell",
                confidence="high" if buriko else "medium",
                pipeline="bgi",
                notes=notes,
                hints=[
                    "已支持一键：解 data*.arc → 译剧情脚本 → 写回/cn_bgi_scripts",
                    "需 garbro-cli 时请先安装；._bp 系统脚本不改",
                ],
                workflow=(
                    "① 游戏根目录选到含 data*.arc 的文件夹\n"
                    "② 管线 BGI → 开始汉化\n"
                    "③ 译场景脚本；完整副本在 cn_bgi_scripts\\\n"
                    "④ 详见 汉化启动说明_BGI.txt"
                ),
            )

    # Classic SoftPal ADV (data.pac + SCRIPT.SRC/TEXT.DAT) — before Kagura .pak
    softpal_hits: list[str] = []
    if (root / "dll" / "Pal.dll").is_file() or (root / "Pal.dll").is_file():
        softpal_hits.append("Pal.dll")
    try:
        from app.core.softpal_pac import find_data_pac, pac_has_script_pair

        pac = find_data_pac(root)
        if pac and pac_has_script_pair(pac):
            softpal_hits.append(pac.name)
    except Exception:
        pac = None
        for name in ("data.pac", "DATA.PAC"):
            p = root / name
            if p.is_file():
                softpal_hits.append(name)
                break
    for folder in (root, root / "data", root / "source"):
        if (folder / "SCRIPT.SRC").is_file() and (folder / "TEXT.DAT").is_file():
            softpal_hits.append(f"{folder.name}/SCRIPT.SRC+TEXT.DAT")
            break
        # case variants
        if folder.is_dir():
            names = {p.name.upper() for p in folder.iterdir() if p.is_file()}
            if "SCRIPT.SRC" in names and "TEXT.DAT" in names:
                softpal_hits.append(f"{folder.name}/SCRIPT+TEXT")
                break
    if softpal_hits:
        notes.append("SoftPal: " + ", ".join(softpal_hits[:6]))
        return DetectResult(
            engine="SoftPal ADV",
            confidence="high",
            pipeline="softpal",
            notes=notes,
            hints=[
                "已支持一键：data.pac → SCRIPT.SRC/TEXT.DAT → 机翻 → 写入 data\\ 松散覆盖",
                "与汉化组教程一致：引擎优先读 data\\，通常不必回封 pac",
            ],
            workflow=(
                "① 选到含 data.pac（或已解出 SCRIPT.SRC+TEXT.DAT）的游戏根目录\n"
                "② 一键汉化（管线 SoftPal）\n"
                "③ 用日语区域启动；字体按教程改 SYSTEM.INI 或游戏内切换\n"
                "④ 对照表 GalAutoTL_review.txt 可校对后重跑"
            ),
        )

    # Kagura / Debonosu Softpal-style PAK (Lua .scb in game.pak)
    kagura_exe = [
        p
        for p in root.glob("*.exe")
        if "kagura" in p.name.lower() or p.name.lower().startswith("kagura")
    ]
    pak_files = list(root.glob("*.pak"))
    if kagura_exe or (
        pak_files
        and any(
            p.name.lower() in ("game.pak", "script.pak", "bmp.pak", "voice.pak")
            for p in pak_files
        )
    ):
        notes.append(
            "Kagura/Debonosu: "
            + ", ".join(p.name for p in (kagura_exe + pak_files)[:8])
        )
        return DetectResult(
            engine="Kagura / Softpal (Debonosu)",
            confidence="high" if kagura_exe else "medium",
            pipeline="kagura",
            notes=notes,
            hints=[
                "一键：.scb 台词 + btText.dat UI/战斗 + kagura*.exe 系统字串",
                "强制 CP932（禁 UTF-8 回写，避免花屏）；漏翻自动二扫",
                "请用 Locale Emulator（日语）启动；拟声假名在 CP932 下可能残留",
            ],
            workflow=(
                "① 游戏根目录选到含 game.pak / kagura*.exe 的文件夹\n"
                "② 一键汉化（管线 Kagura：强制 CP932 + btText + EXE）\n"
                "③ Locale Emulator（日语）启动验收\n"
                "④ 对照表 GalAutoTL_review.txt 可改短后重跑"
            ),
        )

    # Ren'Py
    if (root / "renpy").is_dir() or _rglob_limited(root, "*.rpy", 3):
        return DetectResult(
            engine="Ren'Py",
            confidence="high",
            pipeline="generic",
            notes=["检测到 .rpy / renpy"],
            hints=["可直接译 .rpy（注意勿动缩进与指令）"],
            workflow="文本文件夹选游戏目录 → 通用管线译 .rpy/.txt",
        )

    # Generic text-heavy folder（忽略本工具自己写的对照表/术语表，避免误判）
    exts = (".txt", ".json", ".csv", ".ks", ".po", ".yml", ".yaml", ".tsv", ".utf", ".rpy")
    skip_names = {
        "galautotl_review.txt",
        "galautotl.txt",
        "galautotl_glossary.txt",
        "galautotl_glossary_auto.txt",
        "galautotl_glossary_candidates.txt",
        "startup_readme.txt",
    }

    def _count_ext(e: str) -> int:
        n = 0
        for p in root.rglob(f"*{e}"):
            if p.name.lower() in skip_names or p.name.lower().startswith("galautotl"):
                continue
            n += 1
            if n >= 50:
                break
        return n

    counts = {e: _count_ext(e) for e in exts}
    total = sum(counts.values())
    if total > 0:
        notes.append("明文文件: " + ", ".join(f"{e[1:]}={n}" for e, n in counts.items() if n))
        return DetectResult(
            engine="通用文本目录",
            confidence="medium",
            pipeline="generic",
            notes=notes,
            hints=["可直接用本工具翻译"],
            workflow="文本文件夹指向此目录 → 选源语言 → 开始汉化",
        )

    return DetectResult(
        engine="unknown / 未展开封包",
        confidence="low",
        pipeline="packed",
        notes=["目录内几乎没有明文脚本"],
        hints=["多数 Gal 文本在封包里，需先解包"],
        workflow="① 查清引擎 ② 用对应解包工具导出文本 ③ 再交给本工具 AI 翻译 ④ 回封",
    )
