# -*- coding: utf-8 -*-
"""Forced UI glossary for Kagura — CP932-safe overlays on common UI terms."""
from __future__ import annotations

from typing import Dict

from app.core.pipeline_harden import COMMON_UI_GLOSSARY

# Wins over AI / COMMON. Must encode as cp932 (読取 not 读取; 黒 not 黑…).
KAGURA_UI: Dict[str, str] = {
    "セーブ": "保存",
    "ロード": "読取",
    "はい": "是",
    "いいえ": "否",
    "中断": "中断",
    "中断する": "中断",
    "もどる": "返回",
    "+もどる": "+返回",
    "買う": "購入",
    "売る": "売却",
    "預ける": "預入",
    "引き出す": "取出",
    "整頓": "整理",
    "強化する": "強化",
    "なかま": "同伴",
    "使う": "使用",
    "装備する": "装備",
    "はずす": "卸下",
    "置く": "放置",
    "拾う": "拾取",
    "交換する": "交換",
    "コマンドメニュー": "指令選単",
    "早送り": "快進",
    "ベリーイージー": "超簡単",
    "イージー": "簡単",
    "ノーマル": "普通",
    "ハード": "高難度",
    "ナイトメア": "悪夢",
    "コンフィグ": "設定",
    "ヘルプ": "説明",
    "キーボード": "鍵盤",
    "ゲームパッド": "手柄",
    "その他": "其他",
    "道具": "道具",
    "技": "技",
    "妖術": "妖術",
    "足もと": "脚下",
    "次の層へ行く": "前往下層",
    "拠点へ戻る": "返回據点",
    "助けに行く": "前去救助",
    "ログを見る": "查看日誌",
    "いつでも保存": "随时保存",
    "黒いヘアバンド": "黒色髪帯",
    "毒キノコ": "毒菇",
    "どくきのこ": "毒菇",
    "切れない刀": "切不断的刀",
    "切れない薙刀": "切不断的薙刀",
    "飛ばない弓": "射不出的弓",
    "呪いの罠": "呪詛の罠",
    "祟りの罠": "作祟の罠",
    "プラス修正があれば使えるかも？/ ": "有強化修正或許能用？/ ",
    "プラス修正があれば使える？/ ": "有強化修正就能用？/ ",
}

UI_GLOSSARY: Dict[str, str] = {**COMMON_UI_GLOSSARY, **KAGURA_UI}


def apply_ui_glossary(
    mapping: Dict[str, str], remain_filter=None
) -> Dict[str, str]:
    """Force Kagura UI overlays; respect 仅译漏句 allow-list when set."""
    out = dict(mapping)
    if remain_filter is None:
        out.update(UI_GLOSSARY)
    else:
        for k, v in UI_GLOSSARY.items():
            if k in remain_filter:
                out[k] = v
    return out
