# -*- coding: utf-8 -*-
"""Patch Kagura / Debonosu Softpal EXE embedded system UI (CP932 C-strings)."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Exact JP → CN. Replacement CP932 bytes must be <= original length (NUL-padded).
# Avoid chars not in CP932 (嗎/內/铀/蘑…).
EXE_UI: Dict[str, str] = {
    "本当に終了してもよろしいですか？": "真的要結束？",
    "タイトルに戻ってもよろしいですか？": "要返回標題？",
    "終了してWindowsに戻ります。": "結束並返回 Windows。",
    "ゲームを開始します。": "開始遊戲。",
    "ゲームを開始します": "開始遊戲",
    "はい": "是",
    "いいえ": "否",
    "確認": "確認",
    "警告！": "警告！",
    "お知らせ": "通知",
    "ロード": "読取",
    "セーブ": "保存",
    "ゲーム終了": "結束遊戲",
    "タイトルに戻る": "返回標題",
    "終了しますか？": "要結束？",
    "ロード禁止です。": "禁止読取。",
    "セーブ禁止です。": "禁止保存。",
    "ゲームデータを保存しました。": "已保存遊戲數據。",
    "ゲームデータをロードしました。": "已読取遊戲數據。",
    "ゲームデータのロードに失敗しました。": "読取遊戲數據失敗。",
    "ウィンドウを閉じてゲームに戻ります。": "關閉視窗並返回遊戲。",
    "画面全体を使ってゲームをプレイします。": "使用全螢幕遊玩。",
    "ボイス": "語音",
    "メッセージ": "訊息",
    "サウンド": "聲音",
    "その他": "其他",
    "動作環境": "運行環境",
    "瞬時": "瞬間",
    "低速": "低速",
    "中速": "中速",
    "速度(%s)": "速度(%s)",
    "アップデートを中断しますか？": "要中断更新？",
    "この内容でアップロードしてもよろしいですか？": "確定按此内容上傳？",
    "タイトルを入力してください。": "請輸入標題。",
    "アップロードに失敗しました。": "上傳失敗。",
    "使用するゲームパッドを選択します。": "選択要使用的手柄。",
    "ゲームパッドで操作できるようにします。": "打開手柄操作。",
    "ロード項目 %s": "読取項目 %s",
    "セーブ項目 %s": "保存項目 %s",
    "『%s』を\r\nロードします。よろしいですか？": "要読取\r\n『%s』？",
    "『%s』のセーブデータが見つかりません。": "找不到『%s』的保存數據。",
    "フルスクリーンモードが解除されますがよろしいですか？\n動作が不安定になることがありますのでセーブをお奨めします。": "將解除全螢幕，確定？\n運行可能不穩定，建議先保存。",
    "最大の色数でゲームをプレイします。\r\nチェックを外すと画質は若干落ちますが、動作が軽くなることがあります。": "以最大色數遊玩。\r\n取消勾選可減輕負擔，畫質會略降。",
    "ゲーム画面の解像度を選びます。\r\n下の方ほど高品質ですが、速度が低下することがあります。": "選択遊戲解析度。\r\n越下方畫質越高，速度可能下降。",
    "ゲームの起動に必要なディスクが見つかりません。\r\nディスクをドライブにセットして『再試行』をクリックしてください。": "找不到起動所需光盤。\r\n請放入光盤後點『重試』。",
    "フルスクリーン時の偽装の種類です。\r\nゲーム中にF1を押すと偽装します。": "全螢幕時的偽裝類型。\r\n遊戲中按F1偽裝。",
    "ウィンドウモード時の偽装の種類です。\r\nゲーム中にF1を押すと偽装します。": "視窗模式的偽裝類型。\r\n遊戲中按F1偽裝。",
    "メッセージウィンドウの色を調整します。": "調整訊息視窗顏色。",
    "%03d『%s』を\r\n削除します。よろしいですか？": "要刪除\r\n%03d『%s』？",
    "『%s』のデータを\r\n引き継ぎます。よろしいですか？": "要引継\r\n『%s』的數據？",
    "   『%s』を\r\n%03d『%s』に\r\n上書き保存します。よろしいですか？": "   要將『%s』\r\n覆蓋保存到\r\n%03d『%s』？",
    "%03d『%s』を\r\n%03d『%s』に\r\n上書きコピーします。よろしいですか？": "要將%03d『%s』\r\n覆蓋複製到\r\n%03d『%s』？",
    "%03d『%s』を\r\n%03d『%s』に\r\n上書き移動します。よろしいですか？": "要將%03d『%s』\r\n覆蓋移動到\r\n%03d『%s』？",
    "『%s』の%sを\r\n%03d『%s』に\r\n上書き保存します。よろしいですか？": "要將『%s』的%s\r\n覆蓋保存到\r\n%03d『%s』？",
}


def find_kagura_exe(game_dir: Path) -> Optional[Path]:
    cands = [
        p
        for p in game_dir.glob("*.exe")
        if "kagura" in p.name.lower()
        and "unins" not in p.name.lower()
        and "unitycrash" not in p.name.lower()
    ]
    if not cands:
        return None
    cands.sort(key=lambda p: p.stat().st_size, reverse=True)
    return cands[0]


def patch_exe_bytes(data: bytearray, mapping: Dict[str, str]) -> Tuple[int, List[str]]:
    n = 0
    notes: List[str] = []
    items = sorted(mapping.items(), key=lambda kv: -len(kv[0].encode("cp932")))
    for src, dst in items:
        try:
            sb = src.encode("cp932")
            db = dst.encode("cp932")
        except UnicodeEncodeError as ex:
            notes.append(f"skip encode {src!r}: {ex}")
            continue
        if len(db) > len(sb):
            notes.append(f"too long {src!r} -> {dst!r}")
            continue
        padded = db + b"\x00" * (len(sb) - len(db))
        start = 0
        hits = 0
        while True:
            i = data.find(sb, start)
            if i < 0:
                break
            end = i + len(sb)
            if end < len(data) and data[end] != 0:
                start = i + 1
                continue
            data[i : i + len(sb)] = padded
            hits += 1
            start = i + len(sb)
        if hits:
            n += hits
            notes.append(f"ok x{hits}: {src!r}")
    return n, notes


def patch_kagura_exe(
    game_dir: Path,
    *,
    bak_dir: Optional[Path] = None,
    mapping: Optional[Dict[str, str]] = None,
) -> Tuple[int, Optional[Path]]:
    """Patch system UI in kagura*.exe. Always patch from backup original if present.

    Returns (hit_count, exe_path_or_None).
    """
    exe = find_kagura_exe(game_dir)
    if not exe:
        return 0, None
    mapping = mapping or EXE_UI
    if bak_dir is None:
        bak_dir = Path.home() / "Desktop" / "自动翻译备份" / f"kagura_{game_dir.name}"
    bak_dir.mkdir(parents=True, exist_ok=True)
    bak = bak_dir / exe.name
    if not bak.exists():
        shutil.copy2(exe, bak)
    # Always start from clean original to avoid double-patch / partial JP leftovers
    shutil.copy2(bak, exe)
    data = bytearray(exe.read_bytes())
    n, _notes = patch_exe_bytes(data, mapping)
    exe.write_bytes(data)
    return n, exe
