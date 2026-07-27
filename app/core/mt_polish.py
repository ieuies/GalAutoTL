# -*- coding: utf-8 -*-
"""Post-MT polish learned from RealLive / Galgame CN localization.

Catches systematic machine-translation and soft-CP932 leftovers that prompts
alone cannot reliably prevent.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

LogFn = Optional[Callable[[str], None]]

# ---------------------------------------------------------------------------
# Phrase / scrap tables (longer first)
# ---------------------------------------------------------------------------

_PHRASE_FIXES: tuple[tuple[str, str], ...] = (
    # choice / skip-menu (common Gal / RealLive)
    ("不用在15号做葫芦", "跳过15日做抹布"),
    ("不要在15号跳过做葫芦", "不跳过15日做抹布"),
    ("请勿在16日跳过公司的清洁", "不跳过16日扫神社"),
    ("跳过17th three size", "跳过17日测三围"),
    ("17天请勿跳过三种尺寸", "不跳过17日测三围"),
    ("公司的清洁", "神社打扫"),
    ("社の扫除", "神社打扫"),
    ("社的扫除", "神社打扫"),
    ("社の掃除", "神社打扫"),
    ("做葫芦", "做抹布"),
    ("three size", "三围"),
    ("Three Size", "三围"),
    ("スリーサイズ", "三围"),
    ("ぞうきん", "抹布"),
    ("雑巾", "抹布"),
    ("幼驯染み谈义", "青梅竹马商谈"),
    ("幼馴染み談義", "青梅竹马商谈"),
    ("幼馴染み谈义", "青梅竹马商谈"),
    ("昼休み会议", "午休会议"),
    ("昼休み会議", "午休会议"),
    ("飞ばさない", "不跳过"),
    ("飞ばす", "跳过"),
    ("飛ばさない", "不跳过"),
    ("飛ばす", "跳过"),
    ("请勿跳过序言部分", "不跳过序章"),
    ("跳过序言", "跳过序章"),
    ("请勿跳过序言", "不跳过序章"),
    ("到委员长那边去", "去委员长那边"),
    ("不吐不快。", "忍不住要说一句"),
    ("不吐不快", "忍不住要说一句"),
    ("朝他的脸吹了一口气", "对着吹气"),
    ("突然握住她的手", "突然牵起她的手"),
    ("那种恶作剧可不行", "别搞那种恶作剧"),
    ("绫音姊", "绫姐"),
    ("绫音姐", "绫姐"),
    ("日向同学", "日向酱"),
    ("日向酱酱", "日向酱"),
    # soft-CP932 / MT demonstratives & interrogatives
    ("你、你此家辈", "你、你这家伙"),
    ("你此家辈", "你这家伙"),
    ("此、此家辈", "这、这家伙"),
    ("此家辈阿", "这家伙啊"),
    ("此家辈", "这家伙"),
    ("此家旅馆", "这家旅馆"),
    ("此家公司", "这家公司"),
    ("此家店", "这家店"),
    ("此间旅馆", "这家旅馆"),
    ("打何主意", "打什么主意"),
    ("打何算盘", "打什么算盘"),
    ("叫何名字", "叫什么名字"),
    ("在说何阿", "在说什么啊"),
    ("说出何话", "说出什么话"),
    ("何好像是这样", "好像是这样"),
    ("何精巧", "多么精巧"),
    ("何的", "什么的"),
    ("何阿", "什么啊"),
    ("何啊", "什么啊"),
    ("此件事", "这件事"),
    ("此段时间", "这段时间"),
    ("此段期间", "这段期间"),
    ("此片土地", "这片土地"),
    ("此到底是", "这到底是"),
    ("此可不是", "这可不是"),
    ("此可是我", "这可是我"),
    ("此分心情", "这份心情"),
    ("此分心意", "这份心意"),
    ("此分上", "这份上"),
    ("此句话", "这句话"),
    ("此孩子", "这孩子"),
    ("此些", "这些"),
    ("此就是", "这就是"),
    ("此回事", "这回事"),
    ("此时候", "这时候"),
    ("此几天", "这几天"),
    ("此副身体", "这副身体"),
    ("此副模样", "这副模样"),
    ("此架飞机", "这架飞机"),
    ("此点小事", "这点小事"),
    ("此男人", "这男人"),
    ("此大丈夫", "这没关系"),
    ("此招", "这招"),
    ("此回", "这回"),
    ("此种", "这种"),
    ("此么", "这么"),
    ("此个", "这个"),
    ("此样", "这样"),
    ("此是", "这是"),
    ("此辺", "这边"),
    ("此边", "这边"),
    ("此话", "这话"),
    ("此块", "这块"),
    ("此群", "这群"),
    ("此分数", "这分数"),
    ("此奖签", "这奖签"),
    ("此名字", "这名字"),
    ("此不是", "这不是"),
    ("我此诚实", "我这诚实"),
    ("在此鬼混", "在这鬼混"),
    ("跟此", "跟这"),
    # plural 达 / JP grammar leftovers
    ("朋友达", "朋友们"),
    ("同学达", "同学们"),
    ("学生达", "学生们"),
    ("孩子达", "孩子们"),
    ("小孩达", "小孩们"),
    ("女孩达", "女孩们"),
    ("男孩达", "男孩们"),
    ("女生达", "女生们"),
    ("男生达", "男生们"),
    ("老师达", "老师们"),
    ("客人达", "客人们"),
    ("大人达", "大人们"),
    ("人们达", "人们"),
    ("神官达", "神官们"),
    ("精灵达", "精灵们"),
    ("女性达", "女性们"),
    ("男性达", "男性们"),
    ("老爷爷达", "老爷爷们"),
    ("她达", "她们"),
    ("他达", "他们"),
    ("人达", "人们"),
    ("它达", "它们"),
    ("我达", "我们"),
    ("单身汉达", "单身汉吧"),
    # 呗 / 罢 (soft ね / 吧)
    ("对呗", "对吧"),
    ("是呗", "是吧"),
    ("了呗", "了吧"),
    ("吗呗", "吗"),
    ("呢呗", "呢"),
    ("啊呗", "啊"),
    ("不対罢", "不对吧"),
    ("不对罢", "不对吧"),
    ("対罢", "对吧"),
    ("对罢", "对吧"),
    ("上罢", "上吧"),
    ("回家罢", "回家吧"),
    ("那个罢", "那个吧"),
    ("八卦罢", "八卦吧"),
    ("罢。", "吧。"),
    ("罢！", "吧！"),
    ("罢？", "吧？"),
    ("罢”", "吧”"),
    ("罢」", "吧」"),
    # JP scraps commonly left mid-line
    ("まぁ、", "嘛，"),
    ("まぁ", "嘛"),
    ("どう可能", "怎么可能"),
    ("どうして", "为什么"),
    ("どうした", "怎么了"),
    ("ごめんね", "抱歉啊"),
    ("ごめんなさい", "对不起"),
    ("ごめん", "抱歉"),
    ("ははは", "哈哈哈"),
    ("へへへ", "嘿嘿嘿"),
    ("ふふふ", "呵呵呵"),
    ("干ま阿", "干嘛啊"),
    ("干ま", "干嘛"),
    ("干嘛阿", "干嘛啊"),
    ("不是阿", "不是啊"),
    ("助忙", "帮忙"),
    ("贵方", "你"),
    ("貴方", "你"),
    ("没弁法", "没办法"),
    ("弁法", "办法"),
    ("脚踏车", "自行车"),
    ("聴不解", "听不懂"),
    ("弄不解", "弄不懂"),
    ("聴到", "听到"),
    ("聴见", "听见"),
    ("聴说", "听说"),
    ("連聴", "连听"),
    ("连聴", "连听"),
    ("整斉", "整齐"),
    ("絶冷水", "泼冷水"),
    ("绝冷水", "泼冷水"),
    ("故弄玄态", "故弄玄虚"),
    ("三不五颜", "三不五时"),
    ("质上在", "夜里在"),
    ("羞颜", "丢脸"),
    ("打闭", "打烊"),
    ("阳曲的兴趣", "扭曲的兴趣"),
    ("见来", "原来"),
    ("見来", "原来"),
    ("满有", "很有"),
    ("満有", "很有"),
    ("ちゃん", "酱"),
    # soft-CP932 JP dumps expanded for full CN
    ("そんなら", "那么"),
    ("どうする", "怎么办"),
    ("どうやって", "怎么"),
)

# Choice-like / short UI only (exact line or very short) — avoid mid-dialogue false hits
_SHORT_LINE_FIXES: tuple[tuple[str, str], ...] = (
    ("一起睡", "挨着睡"),
    ("吹一口气", "对着吹气"),
    ("我来干吧", "那就干吧"),
    ("到那边去", "去那边"),
    ("三种尺寸", "三围"),
    ("三个尺寸", "三围"),
    ("添い寝する", "挨着睡"),
    ("息をふきかける", "对着吹气"),
    ("やってやるぜ", "那就干吧"),
)

# JP/TW glyph leftovers → 简体：仅无假名的中文行才改，避免改坏未译日文
_JP_GLYPH_TO_CN: tuple[tuple[str, str], ...] = (
    ("絶対", "绝对"),
    ("回帰", "回归"),
    ("関係", "关系"),
    ("関系", "关系"),
    ("体験", "体验"),
    ("游戯", "游戏"),
    ("遊戯", "游戏"),
    ("従", "从"),
    ("発", "发"),
    ("観", "观"),
    ("気", "气"),
    ("様", "样"),
    ("実", "实"),
    ("読", "读"),
    ("経", "经"),
    ("継", "继"),
    ("総", "总"),
    ("転", "转"),
    ("広", "广"),
    ("戦", "战"),
    ("撃", "击"),
    ("辺", "边"),
    ("対", "对"),
    ("応", "应"),
    ("覚", "觉"),
    ("変", "变"),
    ("図", "图"),
    ("録", "录"),
    ("処", "处"),
    ("黒", "黑"),
)

# zh_cn only: light TW→CN (avoid breaking proper names)
_TW_TO_CN: tuple[tuple[str, str], ...] = (
    ("甚么", "什么"),
    ("什麽", "什么"),
    ("什麼", "什么"),
    ("哪裏", "哪里"),
    ("哪裡", "哪里"),
    ("這裡", "这里"),
    ("那裏", "那里"),
    ("那裡", "那里"),
    ("這麼", "这么"),
    ("那麼", "那么"),
    ("怎麼", "怎么"),
    ("為什麼", "为什么"),
    ("爲什麼", "为什么"),
    ("謝謝", "谢谢"),
    ("關係", "关系"),
    ("發現", "发现"),
    ("實現", "实现"),
    ("實際", "实际"),
    ("絕對", "绝对"),
    ("對話", "对话"),
    ("應該", "应该"),
    ("感覺", "感觉"),
    ("時間", "时间"),
    ("問題", "问题"),
    ("開始", "开始"),
    ("結束", "结束"),
    ("看見", "看见"),
    ("聽說", "听说"),
    ("聽説", "听说"),
    ("覺得", "觉得"),
    ("認識", "认识"),
    ("擔心", "担心"),
    ("開心", "开心"),
    ("難過", "难过"),
    ("離開", "离开"),
    ("回來", "回来"),
    ("已經", "已经"),
    ("還是", "还是"),
    ("還有", "还有"),
    ("還沒", "还没"),
    ("這樣", "这样"),
    ("那樣", "那样"),
    ("雖然", "虽然"),
    ("不過", "不过"),
    ("之後", "之后"),
    ("之前", "之前"),
    ("裡面", "里面"),
    ("裏面", "里面"),
    ("外面", "外面"),
    ("們", "们"),
    ("個", "个"),
    ("這", "这"),
    ("麼", "么"),
    ("對", "对"),
    ("說", "说"),
    ("説", "说"),
    ("聽", "听"),
    ("見", "见"),
    ("過", "过"),
    ("來", "来"),
    ("東", "东"),
    ("門", "门"),
    ("開", "开"),
    ("關", "关"),
    ("長", "长"),
    ("間", "间"),
    ("體", "体"),
    ("會", "会"),
    ("點", "点"),
    ("種", "种"),
    ("無", "无"),
    ("與", "与"),
    ("為", "为"),
    ("爲", "为"),
    ("於", "于"),
    ("並", "并"),
    ("從", "从"),
    ("當", "当"),
    ("後", "后"),
    ("裏", "里"),
    ("裡", "里"),
)

_DIGIT_IDIOM_FIXES: tuple[tuple[str, str], ...] = (
    ("想出了0计", "想出了一计"),
    ("0计", "一计"),
    ("乱0八糟", "乱七八糟"),
    ("混为0谈", "混为一谈"),
    ("2话不说", "二话不说"),
    ("听0半", "听一半"),
    ("把0些", "把一些"),
    ("多把0些", "多把一些"),
    ("第1印象", "第一印象"),
    ("才1天", "才一天"),
    ("前0名", "前几名"),
    ("进前0名", "进前几名"),
    ("0副冷淡", "一副冷淡"),
    ("0脸", "一脸"),
    ("0惊", "一惊"),
    ("心头0惊", "心头一惊"),
    ("回头0看", "回头一看"),
    ("近距离0看", "近距离一看"),
    ("0看", "一看"),
    ("这样0来", "这样一来"),
    ("0来", "一来"),
    ("几乎0无所知", "几乎一无所知"),
    ("0无所知", "一无所知"),
    ("0转眼", "一转眼"),
    ("0不小心", "一不小心"),
    ("最后0刻", "最后一刻"),
    ("赖到最后0刻", "赖到最后一刻"),
    ("0把吧", "一把吧"),
    ("老师0把", "老师一把"),
    ("0致", "一致"),
    ("甚至0围", "甚至一围"),
    ("涌起2股", "涌起一股"),
    ("吓了0", "吓了一"),
    ("0瞬间", "一瞬间"),
    ("0开始", "一开始"),
    ("花了0", "花了一"),
    ("到唯1", "到唯一"),
    ("超过1", "超过一"),
    ("能早1", "能早一"),
    ("0句话", "一句话"),
    ("吵了0", "吵了一"),
    ("吓我0", "吓我一"),
    ("0分钟", "一分钟"),
    ("0公里", "一公里"),
    ("2年", "两年"),
    ("明明0", "明明一"),
    ("老师0", "老师一"),
    ("只是0", "只是一"),
    ("涌起0", "涌起一"),
    ("就像0", "就像一"),
    ("猛地0", "猛地一"),
    ("确认0", "确认一"),
    ("顺带0", "顺带一"),
    ("微微0", "微微一"),
    ("视了0", "视了一"),
    ("过了0", "过了一"),
    ("感到0", "感到一"),
    ("带着0", "带着一"),
    ("环顾0", "环顾一"),
    ("杀0了", "杀了"),
    ("第1", "第一"),
)

_SOFT_AFTER_FIXES: tuple[tuple[str, str], ...] = (
    ("此家辈", "こいつ"),
    ("此家店", "この店"),
    ("此家", "この"),
    ("朋友达", "朋友們"),
    ("同学达", "同学們"),
    ("学生达", "学生們"),
    ("我达", "我們"),
    ("她达", "彼女たち"),
    ("他达", "彼等"),
    ("打何主意", "打什麼主意"),
    ("何阿", "什麼呀"),
    ("対罢", "対唄"),
    ("对罢", "対唄"),
    ("不对罢", "不对唄"),
    ("粗糙，粗糙，粗糙", "うおおおお"),
    ("粗糙，粗糙", "うおお"),
    ("粗糙粗糙粗糙", "うおおおお"),
    ("粗糙粗糙", "うおお"),
)

_JP_SCRAP_RE = re.compile(
    r"(まぁ|どうして|どうした|どう|どちら|ごめんね|ごめんなさい|ごめん|"
    r"ははは|へへへ|ふふふ|おい|ふん)"
)

_PLURAL_DA = re.compile(
    r"(?<![萨○欧阿斯维里])"
    r"(朋友|同学|学生|孩子|小孩|女孩|男孩|女生|男生|老师|客人|大人|"
    r"人们|神官|精灵|女性|男性|老爷爷|爷爷|奶奶|大家|各位|"
    r"她们|他们|它们|我们|她|他|人|它)"
    r"达"
)
_FINAL_DA = re.compile(r"([\u4e00-\u9fff])达([？?。！!…]|$)")
_BEI_TAIL = re.compile(r"呗([”」』！!？?。…～〜]?)")
_BA_TAIL = re.compile(r"罢([！!？?」』…”\s]|$)")

# 「粗糙」连刷 = 拟声机翻误译（ウオオ/うわぁ 等被错译），不是原文拟声
_ROUGH_SPAM = re.compile(r"(?:粗糙[，,、\s]*){2,}")
_ROUGH_ONLY = re.compile(
    r"^[「『\"'\s]*(?:粗糙[，,、\s！!？?…\.。]*)+[」』\"'\s]*$"
)
_DONDON = re.compile(r"どんどん+")
_AAA_JP = re.compile(r"[アァ]{3,}|[あ]{4,}")
_UOO_JP = re.compile(r"[ウゥ][オオォー～〜]{2,}|う[おぉー～〜]{2,}")

# 整句几乎只有叫声 / 拟声（对照原文纠「粗糙」误译）
_SFX_LINE_SRC = re.compile(
    r"^[「『\"'\s]*"
    r"(?:"
    r"[ウゥう][ワわァぁアあォぉオおー～〜]{2,}[っッ]?"
    r"|[ウゥう][オオォー～〜ぁあ]{2,}[っッ]?"
    r"|[アァあ]{3,}[っッ]?"
    r"|[ガヵカ][タッ]?[ガヵカ][タダ]*"
    r"|どんどん+"
    r"|ドンドン+"
    r")"
    r"[！!？?…\.。ｌlＬ]*"
    r"[」』\"'\s]*$"
)

_CI_DEMON = re.compile(
    r"(?<![因就如至从为对])此(?=[件段片回分句些孩可就根啊架副几点间家到底为也块群话奖名])"
)

# Generic skip-menu: 「…を飛ばす」 leftovers / awkward MT
_SKIP_YES = re.compile(
    r"(?:请勿|不要|不用)?(?:在)?(\d{1,2})[日号天]?(?:跳过)?(.{0,12}?)(?:跳过|を飛ばす|飞ばす)"
)
_SKIP_NO = re.compile(
    r"(?:请勿|不要)(?:在)?(\d{1,2})[日号天]?.{0,16}?(?:跳过|を飛ばす)"
)

_PARTICLE_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"ま([……”」』！!？?。，、])"), r"嘛\1"),
    (re.compile(r"ま……"), "嘛……"),
    (re.compile(r"ま…"), "嘛…"),
    (re.compile(r"ま$"), "嘛"),
    (re.compile(r"ろ([！”」』？?。])"), r"吧\1"),
    (re.compile(r"([\u4e00-\u9fff…])っ+([”」』！!？?。]?)"), r"\1\2"),
    (re.compile(r"([\u4e00-\u9fff])ー([”」』]?)"), r"\1～\2"),
    (re.compile(r"ね[ー～]?([”」』！!？?])"), r"呢\1"),
    (re.compile(r"ちゅ+"), "啾"),
    (re.compile(r"ぱ+"), "啪"),
    (re.compile(r"阿([”」』！!？?…～])"), r"啊\1"),
    (re.compile(r"阿$"), "啊"),
    (re.compile(r"([～〜，、！!？?])阿([，、！!？?…”」』]|$)"), r"\1啊\2"),
    (re.compile(r"^阿，"), "啊，"),
)

# Protect before 「一起睡」→挨着睡
_PROTECT_SLEEP = ("一起睡觉", "一起睡着", "一起睡吧", "一起睡了")

DIGIT_IDIOM_SHIELDS: tuple[str, ...] = (
    "一计",
    "乱七八糟",
    "混为一谈",
    "二话不说",
    "一脸",
    "一惊",
    "一看",
    "一来",
    "一无所知",
    "一转眼",
    "一不小心",
    "一刻",
    "一把",
    "一致",
    "一瞬间",
    "一开始",
    "一句话",
    "一分钟",
    "一公里",
    "一些",
    "一半",
    "一副",
    "唯一",
    "第一印象",
    "第一",
    "两人",
    "两年",
    "两股",
    "一下",
    "一起",
    "一般",
    "一定",
    "一样",
    "一直",
    "一边",
    "一点",
)

# Issue scanners (for UI / logs)
_ISSUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("残留假名口癖", re.compile(r"まぁ|どうして|ごめん|ははは|へへ|ふふ")),
    ("复数达尾巴", re.compile(r"(朋友|同学|学生|孩子|们|她|他|人)达")),
    ("文言此/何", re.compile(r"此家辈|打何主意|何阿|此件事|此样|此个")),
    ("成语数字损坏", re.compile(r"0计|乱0八糟|2话不说|0无所知|第1印象")),
    ("拟声误译粗糙", re.compile(r"(?:粗糙[，,、\s]*){2,}|^粗糙[！!？?…\.。]*$")),
    ("选项机翻腔", re.compile(r"葫芦|three size|三种尺寸|公司的清洁|不吐不快")),
    ("软改字呗罢", re.compile(r"[对是了]呗|対罢|对罢|上罢")),
)


def _apply_pairs(text: str, pairs: tuple[tuple[str, str], ...]) -> str:
    # Longer keys first so 絶対 is not broken by 対→对
    for a, b in sorted(pairs, key=lambda kv: len(kv[0]), reverse=True):
        if a in text:
            text = text.replace(a, b)
    return text


def _sfx_cn_from_jp(src: str) -> Optional[str]:
    """Map a JP scream / SFX line to natural CN. None if not SFX-like."""
    s = (src or "").strip().strip("「『」』\"'")
    if not s or not _SFX_LINE_SRC.match(s):
        return None
    core = re.sub(r"[！!？?…\.。ｌlＬ\s]+$", "", s)
    punct = s[len(core) :] if len(s) > len(core) else ""
    if not punct and (src or "").rstrip().endswith(("！", "!", "？", "?")):
        punct = "！"

    if re.match(r"どんどん+|ドンドン+", core):
        return "咚咚" + (punct or "")
    if re.search(r"[ガヵカ][タッ]?[ガヵカ]", core):
        return "咔嗒咔嗒" + (punct or "！")
    if re.match(r"[ウゥう][オオォー～〜ぉお]", core) or re.search(
        r"[ウゥう][オオォ]{2,}", core
    ):
        n = max(3, min(8, len(re.findall(r"[オオォおーぉお～〜]", core)) + 1))
        return "呜" + ("噢" * (n - 1)) + (punct or "！")
    if re.match(r"[ウゥう][ワわァぁアあ]", core) or "わ" in core or "ワ" in core:
        n = max(2, min(8, len(re.findall(r"[ぁあァア]", core)) + 1))
        return "哇" + ("啊" * (n - 1)) + (punct or "！")
    if re.match(r"[アァあ]", core):
        n = max(3, min(8, len(re.findall(r"[アァあ]", core))))
        return ("啊" * n) + (punct or "！")
    return "呜噢噢噢" + (punct or "！")


def _build_builtin_sfx_pairs() -> tuple[tuple[str, str], ...]:
    """Hard glossary: scream/SFX never enter the model → no「粗糙」误译."""
    pairs: list[tuple[str, str]] = [
        ("ドンドン", "咚咚"),
        ("ガタガタ", "咔嗒咔嗒"),
        ("がたがた", "咔嗒咔嗒"),
    ]
    for n in range(2, 14):
        wa = "ぁ" * n
        wa_k = "ァ" * n
        pairs.append((f"うわ{wa}", "哇" + "啊" * n))
        pairs.append((f"うわ{wa}っ", "哇" + "啊" * n))
        pairs.append((f"うわ{wa}！", "哇" + "啊" * n + "！"))
        pairs.append((f"ウワ{wa_k}", "哇" + "啊" * n))
        pairs.append((f"うわぁ{wa}", "哇" + "啊" * (n + 1)))
        oo = "オ" * n
        oo_h = "お" * n
        pairs.append((f"ウ{oo}", "呜" + "噢" * n))
        pairs.append((f"ウ{oo}！", "呜" + "噢" * n + "！"))
        pairs.append((f"う{oo_h}", "呜" + "噢" * n))
        pairs.append((f"あぁ{'ぁ' * n}", "啊" * (n + 1)))
        pairs.append((f"ああ{'あ' * n}", "啊" * (n + 2)))
    pairs.extend(
        (
            ("うわぁ", "哇啊"),
            ("うわぁっ", "哇啊"),
            ("うわぁ！", "哇啊！"),
            ("うわあっ", "哇啊"),
            ("うわあ", "哇啊"),
            ("ウオオ", "呜噢噢"),
            ("ウオオオ", "呜噢噢噢"),
            ("ウオオオオ", "呜噢噢噢噢"),
            ("うおお", "呜噢噢"),
            ("うおおお", "呜噢噢噢"),
        )
    )
    seen: dict[str, str] = {}
    for a, b in pairs:
        seen[a] = b
    return tuple(sorted(seen.items(), key=lambda kv: (-len(kv[0]), kv[0])))


_BUILTIN_SFX_PAIRS = _build_builtin_sfx_pairs()


def builtin_sfx_glossary():
    """Built-in scream/SFX glossary — hard consistency, not prompt hope."""
    from app.core.glossary import Glossary

    return Glossary(pairs=_BUILTIN_SFX_PAIRS)


def _fix_sfx_mt_errors(body: str, src: str = "", *, soft_cp932: bool = False) -> str:
    """纠正拟声被机翻成「粗糙」等错误；对照原文优先。"""
    if not body:
        return body

    mapped = _sfx_cn_from_jp(src) if src else None
    if mapped and "粗糙" in body:
        if _SFX_LINE_SRC.match((src or "").strip()) or _ROUGH_ONLY.match(body.strip()):
            return mapped
        if _ROUGH_SPAM.search(body):
            return mapped

    if _ROUGH_ONLY.match(body.strip()) or _ROUGH_SPAM.search(body):
        repl = "うおおおお！" if soft_cp932 else "呜噢噢噢噢噢！"
        if _ROUGH_ONLY.match(body.strip()):
            return repl
        body = _ROUGH_SPAM.sub(repl, body)

    if mapped and body.strip().strip("「」\"'") in ("粗糙", "粗糙。", "粗糙！", "粗糙…"):
        return mapped

    return body


def _fix_dondon_leftover(body: str) -> str:
    """どんどん：短句拟声→咚咚；句中副词残留→越来越（勿一律咚咚）。"""
    if "どんどん" not in body and "ドンドン" not in body:
        return body
    stripped = body.strip()
    if re.fullmatch(r"[「『\"']?どんどん+[！!？?…\.。]*[」』\"']?", stripped):
        return re.sub(r"どんどん+", "咚咚", body)
    if re.fullmatch(r"[「『\"']?ドンドン+[！!？?…\.。]*[」』\"']?", stripped):
        return re.sub(r"ドンドン+", "咚咚", body)
    body = re.sub(r"^([「『\"']?)どんどん+[，,、]", r"\1咚咚，", body)
    body = re.sub(r"ドンドン+", "咚咚", body)
    body = re.sub(r"どんどん+", "越来越", body)
    return body


def scan_mt_issues(text: str) -> List[str]:
    """Return human-readable issue tags found in one line."""
    if not text:
        return []
    hits = [name for name, pat in _ISSUE_PATTERNS if pat.search(text)]
    kana = len(re.findall(r"[\u3040-\u30ff]", text))
    han = len(re.findall(r"[\u4e00-\u9fff]", text))
    if han >= 4 and 0 < kana <= max(2, han // 5):
        if "残留假名口癖" not in hits:
            hits.append("中文夹杂平假名")
    return hits


def _apply_short_line_fixes(body: str) -> str:
    """Only rewrite when the whole line (stripped) equals a choice phrase."""
    stripped = body.strip().strip("「」\"'")
    for a, b in sorted(_SHORT_LINE_FIXES, key=lambda kv: len(kv[0]), reverse=True):
        if stripped == a:
            # preserve surrounding whitespace/quotes from original if any
            return body.replace(a, b, 1)
    return body


def polish_mt_text(
    text: str,
    *,
    lang: str = "zh_cn",
    soft_cp932: bool = False,
    src: str = "",
) -> str:
    """Polish one translated line. Safe to run repeatedly."""
    if not text or text.startswith("#") or text.startswith("//"):
        return text
    body = text

    # 拟声机翻误译优先（对照原文）
    body = _fix_sfx_mt_errors(body, src, soft_cp932=soft_cp932)

    if soft_cp932:
        body = _apply_pairs(body, _SOFT_AFTER_FIXES)
        body = _apply_pairs(body, _DIGIT_IDIOM_FIXES)
        body = _fix_sfx_mt_errors(body, src, soft_cp932=True)
        # soft 路径保留日文どんどん 显示；不硬改成咚咚
        body = _PLURAL_DA.sub(r"\1們", body)
        body = _BEI_TAIL.sub(r"唄\1", body)
        body = _BA_TAIL.sub(r"唄\1", body)
        body = re.sub(
            r"(?<![因就如至從从為为對对])此(?=[件段片回分句些孩可就根架副幾几点間间家])",
            "這",
            body,
        )
        return body

    # Protect phrases that contain 「一起睡」 before short-line replace
    parks: dict[str, str] = {}
    for i, phrase in enumerate(_PROTECT_SLEEP):
        if phrase in body:
            key = f"\0S{i}\0"
            parks[key] = phrase
            body = body.replace(phrase, key)

    body = _apply_pairs(body, _PHRASE_FIXES)
    body = _apply_pairs(body, _DIGIT_IDIOM_FIXES)
    body = _apply_short_line_fixes(body)

    for key, val in parks.items():
        body = body.replace(key, val)

    # Glyph / TW conversion only on kana-free (already CN) lines
    if lang == "zh_cn" and not re.search(r"[\u3040-\u30ff]", body):
        body = _apply_pairs(body, _JP_GLYPH_TO_CN)
        body = _apply_pairs(body, _TW_TO_CN)

    body = _fix_sfx_mt_errors(body, src, soft_cp932=False)
    body = _fix_dondon_leftover(body)
    body = _AAA_JP.sub("啊啊啊", body)
    body = _UOO_JP.sub("呜噢噢噢", body)

    body = _PLURAL_DA.sub(r"\1们", body)

    def _final_da(m: re.Match[str]) -> str:
        ch, tail = m.group(1), m.group(2)
        if ch == "们":
            return m.group(0)
        return f"{ch}吧{tail}"

    body = _FINAL_DA.sub(_final_da, body)
    body = _BEI_TAIL.sub(r"吧\1", body)
    body = _BA_TAIL.sub(r"吧\1", body)
    body = _CI_DEMON.sub("这", body)
    body = re.sub(r"(?<![到])此为止", "到此为止", body)
    body = re.sub(r"此(种|么|个|样|是|边|辺|块|群|话|分数|奖|名|不是)", r"这\1", body)

    for pat, repl in _PARTICLE_FIXES:
        body = pat.sub(repl, body)

    body = re.sub(r" {2,}", " ", body)
    body = body.replace("酱酱", "酱")
    body = re.sub(r"([\u4e00-\u9fff])さん", r"\1", body)

    if lang.startswith("zh") and re.search(r"[\u4e00-\u9fff]", body):
        if _JP_SCRAP_RE.search(body) and not re.fullmatch(
            r"[\s\u3040-\u30ff\u3000-\u303f\uff00-\uffef]+", body
        ):
            body = _apply_pairs(
                body,
                (
                    ("どうして", "为什么"),
                    ("どうした", "怎么了"),
                    ("どう", "怎么"),
                    ("どちら", "哪里"),
                    ("まぁ、", "嘛，"),
                    ("まぁ", "嘛"),
                    ("ごめんね", "抱歉啊"),
                    ("ごめんなさい", "对不起"),
                    ("ごめん", "抱歉"),
                    ("ははは", "哈哈哈"),
                    ("へへへ", "嘿嘿嘿"),
                    ("ふふふ", "呵呵呵"),
                    ("おい", "喂"),
                    ("ふん", "哼"),
                    ("やつ", "这家伙"),
                ),
            )

    return body


def polish_prompt_rules(lang: str = "zh_cn") -> str:
    style = "简体" if lang == "zh_cn" else "繁体"
    return (
        "10. 日语复数 ～達／たち 译成「们」，禁止残留「达／達」当复数尾巴；\n"
        "11. 指示／疑问：これ・この→这／这个，なに→什么；禁止文言硬译「此家辈」「打何主意」；\n"
        "12. 语气词译成中文（嘛／吧／啊／呢），禁止在中文句子里夹杂 まぁ・ろ・ね・っ 等假名；\n"
        "13. 【严禁拟声误译】ウオオオ／うわぁ／あっ 等叫声必须译成「呜噢噢／哇啊／啊」类拟声，"
        "绝对禁止译成「粗糙」（那是把 粗い 错套到拟声上的典型机翻错误）。"
        "仅当原文确指质地 rough（粗い舌／表面等）时才可用「粗糙」。"
        "どんどん 作副词→越来越／不断；作纯拟声短句→咚咚；\n"
        "14. 短选项／菜单更要自然："
        "ぞうきん=抹布（不是葫芦），社の掃除=扫神社（不是公司清洁），"
        "スリーサイズ=三围，～を飛ばす=跳过～；添い寝=挨着睡；\n"
        "15. 成语里的「一／二」保持汉字（一计、乱七八糟、二话不说），"
        f"不要改成 0/1/2；输出用{style}书面语，避免翻译腔。\n"
        "16. 称呼统一：爱称按术语表；不要「绫音姊／日向同学」混用；\n"
        "17. 禁止输出英文夹杂选项（three size 等），专有名词用中文。\n"
    )


def shield_digit_idioms(text: str) -> tuple[str, dict[str, str]]:
    parks: dict[str, str] = {}
    out = text or ""
    for i, phrase in enumerate(sorted(DIGIT_IDIOM_SHIELDS, key=len, reverse=True)):
        if phrase not in out:
            continue
        key = f"\0D{i}\0"
        parks[key] = phrase
        out = out.replace(phrase, key)
    return out, parks


def unshield_digit_idioms(text: str, parks: dict[str, str]) -> str:
    out = text or ""
    for key, val in parks.items():
        out = out.replace(key, val)
    return out


# ---------------------------------------------------------------------------
# File / directory polish
# ---------------------------------------------------------------------------

_UTF_LINE = re.compile(r"^(<\d+>\s*)(.*)$")
_REVIEW_CN = re.compile(r"^(CN:\s*)(.*)$", re.I)
_XUA_LINE = re.compile(r"^((?:\[.*?\]=)?)(.+)$")  # soft


def polish_line_keep_prefix(line: str, lang: str = "zh_cn", soft_cp932: bool = False) -> str:
    """Polish body of common export formats, keep prefixes / tags."""
    stripped = line.lstrip()
    if stripped.startswith("#") or stripped.startswith(";"):
        return line
    m = _UTF_LINE.match(line)
    if m:
        return m.group(1) + polish_mt_text(
            m.group(2), lang=lang, soft_cp932=soft_cp932
        )
    m = _REVIEW_CN.match(line)
    if m:
        return m.group(1) + polish_mt_text(
            m.group(2), lang=lang, soft_cp932=soft_cp932
        )
    # Artemis / lua-like: text = "..." or text={"..."}
    m = re.match(
        r'^(\s*text\s*=\s*)([\"\'\{])(.*)([\"\'\}])(\s*,?\s*)$',
        line,
        re.I,
    )
    if m:
        body = polish_mt_text(m.group(3), lang=lang, soft_cp932=soft_cp932)
        return f"{m.group(1)}{m.group(2)}{body}{m.group(4)}{m.group(5)}"
    # XUnity / dict: src=dst — only polish dst
    if "=" in line and not stripped.startswith("["):
        left, _, right = line.partition("=")
        if right and re.search(r"[\u4e00-\u9fff]", right):
            return left + "=" + polish_mt_text(right, lang=lang, soft_cp932=soft_cp932)
    # Kirikiri: keep leading fullwidth space / name tags, polish rest if CJK
    if re.search(r"[\u4e00-\u9fff]", line):
        # Don't touch pure command lines like [jump ...]
        if re.match(r"^\s*\[[^\]]+\]\s*$", line):
            return line
        return polish_mt_text(line, lang=lang, soft_cp932=soft_cp932)
    return line


_JSON_STR = re.compile(r'("(?:\\.|[^"\\])*")')


def polish_json_content(
    text: str, lang: str = "zh_cn", soft_cp932: bool = False
) -> tuple[str, int]:
    """Polish only JSON string values; keep structure valid."""
    n = 0

    def _repl(m: re.Match[str]) -> str:
        nonlocal n
        raw = m.group(1)
        try:
            val = json.loads(raw)
        except Exception:
            return raw
        if not isinstance(val, str) or not val:
            return raw
        # skip pure JP leftovers
        kana = len(re.findall(r"[\u3040-\u30ff]", val))
        han = len(re.findall(r"[\u4e00-\u9fff]", val))
        if kana >= 3 and kana >= han:
            return raw
        neu = polish_mt_text(val, lang=lang, soft_cp932=soft_cp932)
        if neu != val:
            n += 1
            return json.dumps(neu, ensure_ascii=False)
        return raw

    return _JSON_STR.sub(_repl, text), n


def polish_text_content(
    text: str, lang: str = "zh_cn", soft_cp932: bool = False
) -> tuple[str, int]:
    """Polish whole file content; return (new_text, changed_lines)."""
    lines = text.splitlines()
    out: list[str] = []
    n = 0
    for line in lines:
        nl = polish_line_keep_prefix(line, lang=lang, soft_cp932=soft_cp932)
        if nl != line:
            n += 1
        out.append(nl)
    new = "\n".join(out)
    if text.endswith("\n"):
        new += "\n"
    return new, n


def _is_mostly_japanese(text: str) -> bool:
    kana = len(re.findall(r"[\u3040-\u30ff]", text))
    han = len(re.findall(r"[\u4e00-\u9fff]", text))
    if kana < 8:
        return False
    return kana >= max(8, han * 2)


_SKIP_DIR_PARTS = (
    "\\extract\\",
    "/extract/",
    "\\orig\\",
    "/orig/",
    "\\original\\",
    "/original/",
    "\\unencrypted\\",
    "/unencrypted/",
    "\\jp\\",
    "/jp/",
    "\\backup\\",
    "/backup/",
    "\\_proxy_backup\\",
    "/_proxy_backup/",
    # Deployed CN overrides — polish only work dirs
    "\\cn_scenario\\",
    "/cn_scenario/",
    "\\cn_bgi_scripts\\",
    "/cn_bgi_scripts/",
)


def _should_skip_path(path: Path) -> bool:
    s = str(path).lower().replace("/", "\\")
    # normalize for both separators in check
    low = str(path).lower()
    for part in _SKIP_DIR_PARTS:
        if part.lower() in low.replace("/", "\\") or part.lower() in low:
            return True
    name = path.name.lower()
    if name in _POLISH_SKIP_NAMES:
        return True
    if any(name.startswith(p) for p in _POLISH_SKIP_PREFIXES):
        return True
    if name in ("readme.txt", "readme.md", "汉化启动说明.txt"):
        return True
    if name.startswith("点我启动"):
        return True
    if path.suffix.lower() in (".exe", ".dll", ".bin", ".xp3", ".pfs", ".arc", ".ypf"):
        return True
    try:
        if path.stat().st_size > 12 * 1024 * 1024:
            return True
    except OSError:
        return True
    return False


def polish_file(path: Path, lang: str = "zh_cn", soft_cp932: bool = False) -> int:
    if _should_skip_path(path):
        return 0
    raw, enc = _read_text_auto(path)
    if _is_mostly_japanese(raw[:8000]):
        return 0
    if path.suffix.lower() == ".json":
        new, n = polish_json_content(raw, lang=lang, soft_cp932=soft_cp932)
    else:
        new, n = polish_text_content(raw, lang=lang, soft_cp932=soft_cp932)
    if n:
        _write_text_auto(path, new, enc)
    return n


def _read_text_auto(path: Path) -> tuple[str, str]:
    """Return (text, encoding_name_for_write). Preserves BOM presence for UTF-16."""
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", errors="replace"), "utf-16-le-sig"
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace"), "utf-16-be-sig"
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    suf = path.suffix.lower()
    # Kirikiri .ks/.tjs：常见 UTF-16-LE（无 BOM）
    if suf in (".ks", ".tjs"):
        try:
            t16 = raw.decode("utf-16-le")
            if t16 and "\x00" not in t16[: min(200, len(t16))]:
                return t16, "utf-16-le"
        except Exception:
            pass
    # utf-16 without BOM heuristic: many NULs on odd bytes (ASCII-heavy)
    if len(raw) >= 4 and raw[1::2] == b"\x00" * (len(raw) // 2):
        try:
            return raw.decode("utf-16-le"), "utf-16-le"
        except Exception:
            pass
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        if suf in (".ks", ".tjs"):
            return raw.decode("utf-16-le", errors="replace"), "utf-16-le"
        return raw.decode("utf-8", errors="replace"), "utf-8"


def _write_text_auto(path: Path, text: str, encoding: str) -> None:
    # Preserve original BOM policy: *-sig keeps BOM; bare utf-16-le/be does not.
    if encoding == "utf-16-le":
        path.write_bytes(text.encode("utf-16-le"))
    elif encoding == "utf-16-be":
        path.write_bytes(text.encode("utf-16-be"))
    elif encoding == "utf-16-le-sig":
        path.write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))
    elif encoding == "utf-16-be-sig":
        path.write_bytes(b"\xfe\xff" + text.encode("utf-16-be"))
    elif encoding == "utf-8-sig":
        path.write_text(text, encoding="utf-8-sig")
    else:
        path.write_text(text, encoding="utf-8")

_POLISH_GLOBS = (
    # RealLive
    "**/cn_utf8/*.utf",
    "**/export_utf8/*.utf",
    "**/patch_work/cn_utf8/*.utf",
    "**/scene_*.utf",
    # Unity runtime dict (NOT human review / remain / glossary — those are hand-edited)
    "**/GalAutoTL.txt",
    # Work dirs only — do NOT polish deployed game overrides (cn_scenario / script/)
    "**/_galautotl_kirikiri/**/*.ks",
    "**/_galautotl_kirikiri/**/*.tjs",
    "**/_galautotl_kirikiri/**/*.txt",
    "**/_galautotl_artemis/**/*.ast",
    "**/_galautotl_artemis/**/*.txt",
    # YU-RIS / BGI / Sakana / LCSE work exports (text side)
    "**/_galautotl_yuris/**/*.txt",
    "**/_galautotl_yuris/**/*.csv",
    "**/_galautotl_bgi/**/*.txt",
    "**/_galautotl_sakana/**/*.txt",
    "**/_galautotl_sakana/**/*.json",
    "**/_galautotl_lcse/**/*.txt",
    "**/_galautotl_lcse/**/*.csv",
    "**/_galautotl_lcse/**/*.json",
    "**/_galautotl_unity/**/*.txt",
    "**/_galautotl_unity/**/*.json",
    # generic catch-all under work dirs
    "**/_galautotl_*/**/*.txt",
    "**/_galautotl_*/**/*.ks",
    "**/_galautotl_*/**/*.ast",
    "**/_galautotl_*/**/*.json",
)

# Human / report artifacts — never auto-polish (would overwrite proofreading)
_POLISH_SKIP_NAMES = frozenset(
    {
        "galautotl_review.txt",
        "galautotl_remain.txt",
        "galautotl_image_ui.txt",
        "对照表.txt",
        "review.txt",
    }
)
_POLISH_SKIP_PREFIXES = ("galautotl_glossary",)


def discover_polish_targets(root: Path) -> List[Path]:
    """Find translation files under a game / tools / text directory (all engines)."""
    root = Path(root)
    if not root.is_dir():
        return []
    found: dict[str, Path] = {}
    for pattern in _POLISH_GLOBS:
        for p in root.glob(pattern):
            if p.is_file() and p.stat().st_size > 0 and not _should_skip_path(p):
                found[str(p.resolve())] = p
    for p in root.glob("*.utf"):
        if p.is_file() and not _should_skip_path(p):
            found[str(p.resolve())] = p
    # Unity XUA
    for p in root.glob("BepInEx/Translation/**/*.txt"):
        if p.is_file() and p.stat().st_size > 0 and not _should_skip_path(p):
            found[str(p.resolve())] = p
    # Do NOT polish deployed overrides (script/, cn_scenario/, cn_bgi_scripts/)
    return sorted(found.values(), key=lambda x: str(x).lower())


def polish_directory(
    root: Path | str,
    lang: str = "zh_cn",
    soft_cp932: bool = False,
    log: LogFn = None,
    limit: int = 0,
) -> tuple[int, int]:
    """Polish all discovered translation files. Returns (files_touched, lines_changed).

    Covers RealLive utf、Kirikiri ks、Artemis ast、Unity 词典、各 _galautotl_* 导出文本。
    Binary-only payloads (LCSE SNX / BGI 脚本池 / YBN / Sakana 槽) 在「翻译当时」已润色；
    仅润色无法改二进制内部，需重新跑对应管线。
    """
    paths = discover_polish_targets(Path(root))
    if limit > 0:
        paths = paths[:limit]
    if log:
        log(f"可润色文本文件 {len(paths)} 个（多引擎）")
    files = lines = 0
    for p in paths:
        n = polish_file(p, lang=lang, soft_cp932=soft_cp932)
        if n:
            files += 1
            lines += n
            if log:
                try:
                    rel = p.relative_to(Path(root))
                except Exception:
                    rel = p.name
                log(f"润色 {rel}: {n} 行")
    if log:
        log(f"润色完成：{files} 个文件，{lines} 行")
    return files, lines


def polish_after_pipeline(
    game_dir: Path | str | None,
    lang: str = "zh_cn",
    soft_cp932: bool = False,
    enabled: bool = True,
    log: LogFn = None,
    extra_roots: Sequence[Path | str] | None = None,
) -> tuple[int, int]:
    """Post-pass after any engine pipeline: polish written text artifacts."""
    if not enabled:
        return 0, 0
    roots: list[Path] = []
    if game_dir:
        roots.append(Path(game_dir))
    for r in extra_roots or ():
        p = Path(r)
        if p.is_dir() and p not in roots:
            roots.append(p)
    tf = tl = 0
    for root in roots:
        if not root.is_dir():
            continue
        if log:
            log(f"多引擎后处理润色: {root}")
        f, l = polish_directory(root, lang=lang, soft_cp932=soft_cp932, log=log)
        tf += f
        tl += l
    return tf, tl


def run_self_checks() -> list[str]:
    """Return list of failure messages (empty = all ok)."""
    fails: list[str] = []
    cases = {
        "朋友达来了": "朋友们来了",
        "你此家辈在打何主意": "你这家伙在打什么主意",
        "想出了0计": "想出了一计",
        "乱0八糟": "乱七八糟",
        "做葫芦": "做抹布",
        "跳过17th three size": "跳过17日测三围",
        "公司的清洁": "神社打扫",
        "對吧": "对吧",  # TW→CN
        "一起睡觉吧": "一起睡觉吧",  # protected
        "添い寝→一起睡": None,
    }
    for src, expect in cases.items():
        if expect is None:
            continue
        if src == "添い寝→一起睡":
            continue
        out = polish_mt_text(src, lang="zh_cn")
        if expect not in out and out != expect:
            fails.append(f"{src!r} => {out!r} (want {expect!r})")
    # 一起睡 alone → 挨着睡
    if polish_mt_text("一起睡") != "挨着睡":
        fails.append("一起睡 should become 挨着睡")
    if "一计" not in polish_mt_text("想出了0计"):
        fails.append("0计 fix")
    # src-aware SFX MT error
    if "哇" not in polish_mt_text("粗糙粗糙", src="うわぁっ！"):
        fails.append("sfx src fix failed")
    from app.core.mt_polish import builtin_sfx_glossary

    g = builtin_sfx_glossary()
    if not g or "うわぁ" not in {s for s, _ in g.pairs}:
        fails.append("builtin sfx glossary missing うわぁ")
    return fails
