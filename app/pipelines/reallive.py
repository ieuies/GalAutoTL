# -*- coding: utf-8 -*-
"""RealLive pipeline: translate export_utf8 / cn_utf8, optional external patch tools."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, List, Optional

from app.config import AppConfig, cache_db_path
from app.core.api_client import OpenAICompatClient
from app.core.cp932_safe import to_cp932_safe
from app.core.pipeline_harden import remain_filter_set, write_remainder_report
from app.core.translate import TranslateCache, translate_batch
from app.pipelines.generic_text import backup_file

LogFn = Optional[Callable[[str], None]]
ProgressFn = Optional[Callable[[int, int], None]]
CancelFn = Optional[Callable[[], bool]]

LINE_RE = re.compile(r"^(<\d+>\s*)(.*)$")
SKIP_PREFIXES = ("//", "#", "@")
JP_HINT = re.compile(r"[\u3040-\u30ff]")
_SPEAKER_RE = re.compile(r"^\\\{[^}]*\}")
_ELLIPSIS_ONLY = re.compile(
    r"^[\s\u3000-\u303f\uff00-\uffef.・…‥─—～~!！?？、。,，「」『』（）()\"'|]+$"
)


def _is_ellipsis_only(body: str) -> bool:
    """Speaker + …… / punctuation — keep as-is, not a missed translation."""
    inner = _SPEAKER_RE.sub("", body or "").strip()
    inner = inner.strip("「」\"'|")
    return (not inner) or bool(_ELLIPSIS_ONLY.match(inner))


def find_utf_dirs(game_dir: Path, tools_dir: str) -> tuple[Optional[Path], Optional[Path]]:
    """Return (jp_export_dir, cn_out_dir)."""
    candidates = []
    if tools_dir.strip():
        t = Path(tools_dir)
        candidates.append(t / "export_utf8")
        candidates.append(t / "patch_work" / "cn_utf8")
    candidates.append(game_dir / "_tools" / "export_utf8")
    candidates.append(game_dir / "export_utf8")
    jp = None
    for c in candidates:
        if c.is_dir() and any(c.glob("*.utf")):
            # prefer export_utf8 named dirs as JP source
            if "cn_utf8" in str(c).lower():
                continue
            jp = c
            break
    if jp is None:
        for c in candidates:
            if c.is_dir() and any(c.glob("*.utf")):
                jp = c
                break
    cn = None
    if tools_dir.strip():
        cn = Path(tools_dir) / "patch_work" / "cn_utf8"
    if cn is None:
        cn = game_dir / "_tools" / "patch_work" / "cn_utf8"
    return jp, cn


def _collect_utf_lines(path: Path) -> tuple[list[str], list[tuple[int, str, str]]]:
    """Return (all_lines, pending list of (line_index, prefix, body))."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="cp932", errors="replace")
    lines = text.splitlines()
    pending = []
    for i, line in enumerate(lines):
        if any(line.startswith(p) for p in SKIP_PREFIXES):
            continue
        m = LINE_RE.match(line)
        if not m:
            continue
        prefix, body = m.group(1), m.group(2)
        if not body.strip():
            continue
        # skip pure voice tags
        if re.match(r"^\\[a-zA-Z]", body) and not body.startswith("\\{"):
            continue
        # only lines that still contain kana (remain Japanese)
        if JP_HINT.search(body):
            pending.append((i, prefix, body))
    return lines, pending


def translate_utf_tree(
    jp_dir: Path,
    cn_dir: Path,
    cfg: AppConfig,
    log: LogFn = None,
    progress: ProgressFn = None,
    should_cancel: CancelFn = None,
) -> int:
    cn_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(jp_dir.glob("*.utf"))
    if not files:
        raise FileNotFoundError(f"未找到 *.utf: {jp_dir}")

    # Pre-scan for overall progress (line-based across all scenes)
    file_needs: list[int] = []
    for path in files:
        _lines, pending = _collect_utf_lines(path)
        file_needs.append(sum(1 for _i, _p, b in pending if JP_HINT.search(b)))
    grand_total = max(sum(file_needs), 1)
    if log:
        log(f"共 {len(files)} 个场景，约 {grand_total} 条待译台词")

    client = OpenAICompatClient(cfg.api_base, cfg.api_key, cfg.api_model, cfg.temperature)
    cache = TranslateCache(cache_db_path())
    file_count = 0
    done_lines = 0
    try:
        for fi, path in enumerate(files, 1):
            if should_cancel and should_cancel():
                break
            out = cn_dir / path.name
            need_n = file_needs[fi - 1]

            # Resume: already written CN for this scene
            if out.is_file() and out.stat().st_size > 0:
                if log:
                    log(f"[{fi}/{len(files)}] 跳过已有 {path.name}")
                done_lines += need_n
                if progress:
                    progress(done_lines, grand_total)
                file_count += 1
                continue

            if log:
                log(f"[{fi}/{len(files)}] {path.name}（本场景 {need_n} 条，总进度 {done_lines}/{grand_total}）")
            lines, pending = _collect_utf_lines(path)
            need = [(i, p, b) for i, p, b in pending if JP_HINT.search(b)]
            if not need:
                shutil.copy2(path, out)
                file_count += 1
                if progress:
                    progress(done_lines, grand_total)
                continue

            def _file_progress(d: int, _t: int, base: int = done_lines) -> None:
                if progress:
                    progress(min(base + d, grand_total), grand_total)

            bodies = [b for _, _, b in need]
            rfilt = remain_filter_set(cfg)
            if rfilt is not None:
                kept = [(i, p, b) for i, p, b in need if b in rfilt]
                if not kept:
                    shutil.copy2(path, out)
                    file_count += 1
                    if progress:
                        progress(done_lines, grand_total)
                    continue
                need = kept
                bodies = [b for _, _, b in need]
                if log:
                    log(f"仅译漏句: 本文件保留 {len(need)} 条")
            translated = translate_batch(
                bodies,
                client,
                cfg.lang,
                cp932=cfg.cp932_safe,
                cache=cache,
                chunk=cfg.batch_size,
                log=log,
                progress=_file_progress,
                should_cancel=should_cancel,
                source_lang=getattr(cfg, "source_lang", "ja") or "ja",
                game_dir=cfg.game_dir or cfg.text_dir,
                do_polish=getattr(cfg, "mt_polish", True),
            )
            for (idx, prefix, _body), dst in zip(need, translated):
                lines[idx] = prefix + dst
            text = "\n".join(lines)
            if path.read_bytes()[-1:] == b"\n":
                text += "\n"
            game = Path(cfg.game_dir)
            if out.exists():
                backup_file(out, game if game.is_dir() else cn_dir, cfg.do_backup, log)
            out.write_text(text, encoding="utf-8")
            done_lines += len(need)
            if progress:
                progress(done_lines, grand_total)
            file_count += 1
    finally:
        cache.close()
    return file_count


def _ensure_reallive_display(game_dir: Path, log: LogFn = None) -> None:
    """Coming×Humming display: prefer ONE VNTextProxy + sjis_ext + CJK font.

    RealLive stores text as Shift-JIS; a Chinese font alone cannot show 你/啊 etc.
    Full Simplified Chinese needs sjis_ext.bin tunneling + a single proxy DLL
    (multiple proxies often APPCRASH). Prefer keeping dinput8.dll only.
    """
    proxies = (
        "dinput8.dll",
        "winmm.dll",
        "version.dll",
        "d2d1.dll",
        "d3d9.dll",
        "ddraw.dll",
        "msacm32.dll",
        "xinput1_3.dll",
    )
    present = [n for n in proxies if (game_dir / n).is_file()]
    # Coming×Humming / RealLive imports winmm.dll only — dinput8 never loads.
    prefer = "winmm.dll" if "winmm.dll" in present else (
        "dinput8.dll" if "dinput8.dll" in present else (present[0] if present else "")
    )
    if len(present) > 1 and prefer:
        backup = game_dir / "_proxy_backup"
        backup.mkdir(exist_ok=True)
        for n in present:
            if n == prefer:
                continue
            src = game_dir / n
            try:
                dest = backup / n
                if dest.exists():
                    src.unlink()
                else:
                    src.replace(dest)
            except OSError:
                pass
        if log:
            log(f"本作品多代理易闪退：已只保留 {prefer}，其余挪到 _proxy_backup")
    try:
        from app.core.fonts import copy_cjk_font_to_game

        ok, tip = copy_cjk_font_to_game(game_dir)
        if log:
            log(tip if ok else f"字体: {tip}")
            has_proxy = (game_dir / "winmm.dll").is_file()
            has_ext = (game_dir / "sjis_ext.bin").is_file()
            if has_proxy and has_ext:
                log(
                    "已检测到 winmm 显示代理 + sjis_ext.bin。"
                    "本作品请用 _tools/mini_proxy（精简版），完整 VNTextProxy 易闪退。"
                )
            else:
                log(
                    "RealLive 完整简体：sjis_ext.bin + _tools/mini_proxy/winmm.dll"
                    "（本作品只加载 winmm；官方 VNTextProxy 常闪退）。"
                    "可运行 _tools/启用简体显示代理.bat。"
                )
    except Exception as e:
        if log:
            log(f"拷字体失败: {e}")


def apply_cp932_tree(cn_dir: Path, log: LogFn = None) -> int:
    """Replace non-CP932 CJK (啊/你/嗎…) so RealLive won't show middle-dots."""
    if not cn_dir.is_dir():
        return 0
    from app.core.mt_polish import polish_mt_text

    n = 0
    for path in sorted(cn_dir.glob("*.utf")):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = path.read_text(encoding="cp932", errors="replace")
        out_lines = []
        changed = False
        for line in text.splitlines():
            m = LINE_RE.match(line)
            if not m:
                out_lines.append(line)
                continue
            prefix, body = m.group(1), m.group(2)
            safe = to_cp932_safe(body)
            safe = polish_mt_text(safe, soft_cp932=True)
            if safe != body:
                changed = True
                n += 1
            out_lines.append(prefix + safe)
        if changed:
            nl = text.endswith("\n")
            path.write_text("\n".join(out_lines) + ("\n" if nl else ""), encoding="utf-8")
    if log:
        log(f"CP932 改字 + 软改字后处理：{n} 行")
    return n


def apply_mt_polish_tree(cn_dir: Path, lang: str = "zh_cn", log: LogFn = None) -> int:
    """Post-MT polish on an already-translated cn_utf8 tree (no soft CP932)."""
    if not cn_dir.is_dir():
        return 0
    from app.core.mt_polish import polish_mt_text

    n = 0
    for path in sorted(cn_dir.glob("*.utf")):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = path.read_text(encoding="cp932", errors="replace")
        out_lines = []
        changed = False
        for line in text.splitlines():
            m = LINE_RE.match(line)
            if not m:
                out_lines.append(line)
                continue
            prefix, body = m.group(1), m.group(2)
            nb = polish_mt_text(body, lang=lang, soft_cp932=False)
            if nb != body:
                changed = True
                n += 1
            out_lines.append(prefix + nb)
        if changed:
            nl = text.endswith("\n")
            path.write_text("\n".join(out_lines) + ("\n" if nl else ""), encoding="utf-8")
    if log:
        log(f"机翻后处理润色：{n} 行")
    return n


def repair_remaining_jp(
    jp_dir: Path,
    cn_dir: Path,
    cfg: AppConfig,
    log: LogFn = None,
    progress: ProgressFn = None,
    should_cancel: CancelFn = None,
) -> int:
    """Re-translate CN lines that are still identical Japanese (API miss / bad cache)."""
    if not jp_dir.is_dir() or not cn_dir.is_dir():
        return 0

    jobs: list[tuple[Path, list[str], list[tuple[int, str, str]]]] = []
    for jp_path in sorted(jp_dir.glob("*.utf")):
        cn_path = cn_dir / jp_path.name
        if not cn_path.is_file():
            continue
        try:
            jp_lines = jp_path.read_text(encoding="utf-8-sig").splitlines()
        except UnicodeDecodeError:
            jp_lines = jp_path.read_text(encoding="cp932", errors="replace").splitlines()
        try:
            cn_lines = cn_path.read_text(encoding="utf-8-sig").splitlines()
        except UnicodeDecodeError:
            cn_lines = cn_path.read_text(encoding="cp932", errors="replace").splitlines()

        pending: list[tuple[int, str, str]] = []
        # align by index; pad if lengths differ
        n = min(len(jp_lines), len(cn_lines))
        for i in range(n):
            jm = LINE_RE.match(jp_lines[i])
            cm = LINE_RE.match(cn_lines[i])
            if not jm or not cm:
                continue
            jbody, cbody = jm.group(2), cm.group(2)
            if jbody != cbody:
                continue
            if not JP_HINT.search(jbody):
                continue
            if _is_ellipsis_only(jbody):
                continue
            pending.append((i, jm.group(1), jbody))
        if pending:
            jobs.append((cn_path, cn_lines, pending))

    total = sum(len(p) for _, _, p in jobs)
    if not total:
        if log:
            log("无残留日文台词需要补翻")
        return 0
    if log:
        log(f"补翻残留日文：{len(jobs)} 个场景，{total} 条")

    client = OpenAICompatClient(cfg.api_base, cfg.api_key, cfg.api_model, cfg.temperature)
    # Fresh cache namespace so JP→JP poison won't stick; still allow good hits via empty new db? 
    # Better: no cache for repair of these lines — pass cache but they'll miss if we use different tag.
    cache = TranslateCache(cache_db_path())
    done = 0
    try:
        for cn_path, cn_lines, pending in jobs:
            if should_cancel and should_cancel():
                break
            bodies = [b for _, _, b in pending]
            rfilt = remain_filter_set(cfg)
            if rfilt is not None:
                pending = [(i, p, b) for i, p, b in pending if b in rfilt]
                if not pending:
                    continue
                bodies = [b for _, _, b in pending]
            # Bypass poisoned cache: translate with a repair tag via temporary no-cache
            translated = translate_batch(
                bodies,
                client,
                cfg.lang,
                cp932=True if cfg.cp932_safe else False,
                cache=None,
                chunk=min(cfg.batch_size or 24, 16),
                log=log,
                progress=None,
                should_cancel=should_cancel,
                source_lang=getattr(cfg, "source_lang", "ja") or "ja",
                game_dir=None,  # skip glossary rebuild / review overwrite per tiny batch
                do_polish=getattr(cfg, "mt_polish", True),
            )
            for (idx, prefix, src), dst in zip(pending, translated):
                if dst and str(dst).strip() and dst != src:
                    body = to_cp932_safe(dst) if cfg.cp932_safe else dst
                    if not cfg.cp932_safe:
                        from app.core.mt_polish import polish_mt_text

                        body = polish_mt_text(
                            body, lang=getattr(cfg, "lang", "zh_cn") or "zh_cn"
                        )
                    else:
                        from app.core.mt_polish import polish_mt_text

                        body = polish_mt_text(body, soft_cp932=True)
                    cn_lines[idx] = prefix + body
                done += 1
                if progress:
                    progress(done, total)
            out = "\n".join(cn_lines)
            if cn_path.read_bytes()[-1:] == b"\n":
                out += "\n"
            cn_path.write_text(out, encoding="utf-8")
            if log:
                log(f"补翻写出 {cn_path.name}（{len(pending)} 条）")
    finally:
        cache.close()
    return done


def try_run_external_patch(tools_dir: Path, log: LogFn = None) -> bool:
    """If full_patch.py / full_localize.ps1 exist, run patch+repack."""
    patch_py = tools_dir / "full_patch.py"
    localize = tools_dir / "full_localize.ps1"
    if not patch_py.exists():
        if log:
            log("未找到 full_patch.py，跳过自动注入（仅完成 cn_utf8 翻译）")
        return False
    if log:
        log("调用外部工具: full_patch.py --no-resume")
    r1 = subprocess.run(
        ["py", "-3", str(patch_py), "--no-resume"],
        cwd=str(tools_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if log:
        log(r1.stdout[-2000:] if r1.stdout else "")
        if r1.returncode != 0:
            log(f"patch 退出码 {r1.returncode}（可能仍有部分成功）")
    if localize.exists():
        if log:
            log("调用 full_localize.ps1 -Phase repack")
        r2 = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(localize),
                "-Phase",
                "repack",
            ],
            cwd=str(tools_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if log:
            log(r2.stdout[-1500:] if r2.stdout else f"repack code={r2.returncode}")
    return True


def _dir_has_utf(p: Path) -> bool:
    return p.is_dir() and any(p.glob("*.utf"))


def run_reallive(cfg: AppConfig, log: LogFn = None, progress: ProgressFn = None, should_cancel: CancelFn = None) -> None:
    game_dir = Path(cfg.game_dir)
    if not game_dir.is_dir():
        raise FileNotFoundError("请先选择游戏目录")

    jp_dir: Optional[Path] = None
    cn_dir: Optional[Path] = None

    # text_dir override = jp export（必须真有 *.utf，否则忽略并自动搜索）
    if cfg.text_dir.strip():
        cand = Path(cfg.text_dir)
        if _dir_has_utf(cand):
            jp_dir = cand
            cn_dir = jp_dir.parent / "cn_utf8"
            if "export_utf8" in jp_dir.name:
                pw = jp_dir.parent / "patch_work" / "cn_utf8"
                cn_dir = pw if (jp_dir.parent / "patch_work").exists() else (jp_dir.parent / "cn_utf8")
        elif log:
            log(f"「文本/UTF目录」无 *.utf，已忽略: {cand}")

    if jp_dir is None:
        jp_dir, cn_dir = find_utf_dirs(game_dir, cfg.tools_dir)
        if cn_dir is None:
            cn_dir = game_dir / "_tools" / "patch_work" / "cn_utf8"

    if jp_dir is None or not jp_dir.is_dir():
        hint = game_dir / "_tools" / "export_utf8"
        raise FileNotFoundError(
            "未找到 RealLive 的 *.utf 导出目录。\n"
            f"游戏目录: {game_dir}\n"
            f"预期路径示例: {hint}\n"
            "请把「文本/UTF目录」设为 export_utf8（含 *.utf），"
            "或清空该栏让程序在 游戏/_tools 下自动查找，"
            "也可填写「外部工具目录」。"
        )

    if log:
        log(f"日文 UTF: {jp_dir}")
        log(f"中文输出: {cn_dir}")

    # Prefer VNTextProxy + 完整汉字；仅当用户显式勾选时才做 啊→阿 改字
    if cfg.cp932_safe:
        if log:
            log("已启用 CP932 改字（会把「啊/啦」等换成别字，观感差；有 VNTextProxy 时请关闭）")
    else:
        if log:
            log("未改字：保留自然中文。请确保游戏目录有 VNTextProxy（dinput8.dll）+ 中文字体，否则缺字会显示成点")
        _ensure_reallive_display(game_dir, log)

    n = translate_utf_tree(jp_dir, cn_dir, cfg, log, progress, should_cancel)
    if log:
        log(f"已翻译/写出 {n} 个场景 UTF")

    repaired = repair_remaining_jp(jp_dir, cn_dir, cfg, log, progress, should_cancel)
    if log and repaired:
        log(f"补翻残留日文 {repaired} 条")

    # Remainder report from CN tree (still-JP lines)
    try:
        left: List[str] = []
        for p in sorted(cn_dir.rglob("*.utf")):
            try:
                for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    body = line.strip()
                    if body and JP_HINT.search(body):
                        left.append(body)
            except OSError:
                continue
        write_remainder_report(
            game_dir,
            "reallive",
            left,
            {s: s for s in left},
            log=log,
            allow=remain_filter_set(cfg),
            max_n=2000,
        )
    except Exception as e:
        if log:
            log(f"漏句报告跳过: {e}")

    # Coming×Humming lessons: strip JP scraps / 达 / 此何 / digit idioms / bad SFX
    if getattr(cfg, "mt_polish", True):
        polished = apply_mt_polish_tree(cn_dir, getattr(cfg, "lang", "zh_cn") or "zh_cn", log)
        if log and polished:
            log(f"机翻后处理已润色 {polished} 行")

    if cfg.cp932_safe:
        fixed = apply_cp932_tree(cn_dir, log)
        if log:
            log(f"CP932 改字处理 {fixed} 行")

    tools = Path(cfg.tools_dir) if cfg.tools_dir.strip() else game_dir / "_tools"
    if tools.is_dir():
        try_run_external_patch(tools, log)
    else:
        if log:
            log("提示: 设置外部工具目录后可自动 full_patch + repack 写回 SEEN.TXT")
            log(f"中文 UTF 已输出到: {cn_dir}")
