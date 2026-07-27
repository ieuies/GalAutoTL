# -*- coding: utf-8 -*-
"""Self-check for mt_polish (run: py -3 -m app.core.mt_polish_check)."""
from __future__ import annotations

from app.core.mt_polish import polish_mt_text, run_self_checks, scan_mt_issues
from app.core.translate import _finalize
from app.core.xua_match_rules import preserve_arabic_digits


def main() -> int:
    fails = run_self_checks()

    def must_eq(name: str, got: str, want: str) -> None:
        if got != want:
            fails.append(f"{name}: {got!r} != {want!r}")

    def must_has(name: str, got: str, want: str) -> None:
        if want not in got:
            fails.append(f"{name}: {got!r} missing {want!r}")

    def must_same(name: str, text: str) -> None:
        out = polish_mt_text(text)
        if out != text:
            fails.append(f"{name}: mangled {text!r} => {out!r}")

    # core fixes
    must_has("friends", polish_mt_text("朋友达"), "朋友们")
    must_has("yatsu", polish_mt_text("此家辈"), "这家伙")
    must_has("nani", polish_mt_text("打何主意"), "什么主意")
    must_has("zero", polish_mt_text("0计"), "一计")
    must_has("zokin", polish_mt_text("做葫芦"), "抹布")
    must_eq("sleep_choice", polish_mt_text("一起睡"), "挨着睡")
    must_eq("sleep_protect", polish_mt_text("一起睡觉吧"), "一起睡觉吧")
    must_eq("gan_choice", polish_mt_text("我来干吧"), "那就干吧")
    must_has("rough2", polish_mt_text("粗糙粗糙"), "呜噢")
    must_same("rough1", "表面有点粗糙。")
    # 拟声误译：对照原文纠「粗糙」
    must_has(
        "sfx_uwa",
        polish_mt_text("粗糙，粗糙，粗糙", src="うわぁぁぁぁぁ！！"),
        "哇",
    )
    must_has(
        "sfx_uoo",
        polish_mt_text("粗糙！", src="ウオオオオ！"),
        "呜噢",
    )
    # どんどん：副词 vs 拟声
    must_has("dondon_adv", polish_mt_text("心がどんどん浮き立つ"), "越来越")
    must_eq("dondon_sfx", polish_mt_text("どんどん"), "咚咚")
    # 合法质感「粗糙」保留
    must_same("rough_tongue", "我用舌头感受着她那粗糙的舌面。")

    # false positives — must NOT change
    for name, text in [
        ("jp_zettai", "絶対に無理だ。"),
        ("jp_jitsu", "実は好きだった。"),
        ("jp_kuuki", "空気が変だ。"),
        ("jp_time", "陽盛りの時間。"),
        ("fp_gan", "我来干吧，这事我来。"),
        ("fp_blow", "吹一口气就好了。"),
        ("fp_go", "到那边去看看。"),
        ("fp_size", "三种尺寸都有货。"),
        ("ci", "因此我才来的。"),
        ("ruci", "如此一来就好了。"),
    ]:
        must_same(name, text)

    # kana-free CN glyph leftover OK
    must_has("glyph", polish_mt_text("絶対不行"), "绝对")

    must_eq(
        "finalize",
        _finalize("朋友达対罢", False, "俺達だろ", "zh_cn"),
        "朋友们对吧",
    )
    dig = preserve_arabic_digits("１４日。", "跳过十四日。想出了一计。")
    must_has("digits_idiom", dig, "一计")
    must_has("digits_date", dig, "14")
    must_has("scan", ",".join(scan_mt_issues("朋友达此家辈")), "达")

    if fails:
        print("FAIL", len(fails))
        for f in fails:
            print(" -", f)
        return 1
    print("OK mt_polish self-check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
