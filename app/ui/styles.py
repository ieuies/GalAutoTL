# -*- coding: utf-8 -*-
"""GalAutoTL UI — 深墨 × 和纸 × 朱印 (ink × washi × vermilion accent).

Design intent: a cohesive dark workstation — deep ink chrome everywhere, warm
washi-paper inputs, and a single vermilion accent for the primary action.
Unified surface (no more light cards floating on dark), which reads as one
tool instead of three panels.

Tokens
  ink        #0d0d10  base
  ink-raise  #14141a  sidebar / panels
  ink-panel  #1b1b23  cards
  washi      #ede4d0  paper fields + primary text
  washi-dim  #a89e8b  secondary text
  vermilion  #c8432c  accent — primary CTA / active nav / focus
  gold       #c9a25a  secondary accent (group titles)
  line       #2b2b35  hairlines
  good / warn / bad  status colors
"""
from __future__ import annotations

APP_STYLE = """
* {
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}

QMainWindow {
    background: #0d0d10;
}
QWidget#centralRoot {
    background: qradialgradient(cx:0.82, cy:0.08, radius:1.35,
        fx:0.82, fy:0.08, stop:0 #171720, stop:0.55 #111116, stop:1 #0d0d10);
}

/* —— Left control rail —— */
QWidget#leftPane { background: transparent; }
QScrollArea#leftScroll { background: transparent; border: none; }
QScrollArea#leftScroll > QWidget > QWidget { background: transparent; }

/* —— Sidebar: 深墨列 + 朱印 —— */
QFrame#navSide {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #17171e, stop:1 #121216);
    border: 1px solid #2b2b35;
    border-radius: 18px;
}
QLabel#navMark {
    color: #d8d0bf;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 3px;
}
QLabel#navBrand {
    color: #f2ecdd;
    font-size: 20px;
    font-weight: 800;
    letter-spacing: 1px;
    padding-bottom: 4px;
}
QLabel#navHint {
    color: #a89e8b;
    font-size: 11px;
    padding-top: 4px;
}
/* 竖向「日→中」转译轴 */
QPushButton#navBtn {
    background: transparent;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 8px;
    padding: 11px 12px;
    color: #a89e8b;
    font-weight: 600;
    font-size: 13.5px;
    text-align: left;
}
QPushButton#navBtn:hover {
    background: rgba(201, 162, 90, 0.08);
    color: #e8dcc8;
}
QPushButton#navBtn:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(200, 67, 44, 0.22), stop:1 transparent);
    border-left: 3px solid #c8432c;
    color: #f2ecdd;
    font-weight: 700;
}
QStackedWidget#pageStack { background: transparent; }

/* —— 顶部品牌条 —— */
QFrame#brandRail {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #17171e, stop:1 #131318);
    border: 1px solid #2b2b35;
    border-radius: 18px;
}
QLabel#brandMark {
    color: #c8432c;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 4px;
}
QLabel#brandTitle {
    color: #f2ecdd;
    font-size: 28px;
    font-weight: 800;
    letter-spacing: 0.5px;
}
QLabel#brandSub {
    color: #a89e8b;
    font-size: 12.5px;
    padding-top: 2px;
}
QLabel#stepLine {
    color: #8a7f6c;
    font-size: 11.5px;
    letter-spacing: 0.5px;
    padding-top: 8px;
}

/* —— 卡片：深墨面板（不再白卡）—— */
QFrame#card {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #1d1d26, stop:1 #191920);
    border: 1px solid #2b2b35;
    border-radius: 16px;
}
QLabel#sectionTitle {
    color: #d8d0bf;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
    text-transform: uppercase;
}
QLabel#hint {
    color: #a89e8b;
    font-size: 12px;
}

/* —— 引擎徽章：盖章式 chip —— */
QLabel#engineBadge {
    background: #14141a;
    color: #d8d0bf;
    border: 1px solid #33333f;
    border-left: 3px solid #c8432c;
    border-radius: 10px;
    padding: 11px 13px;
    font-weight: 600;
    font-size: 12.5px;
}
QLabel#engineBadge[state="idle"] {
    background: #14141a;
    color: #a89e8b;
    border-left-color: #3a3a46;
}
QLabel#engineBadge[state="ok"] {
    background: #131a16;
    color: #9cc9a0;
    border: 1px solid #2c4436;
    border-left: 3px solid #7fbf7f;
}
QLabel#engineBadge[state="warn"] {
    background: #1a1712;
    color: #ddb56a;
    border: 1px solid #4a3a1e;
    border-left: 3px solid #d9a441;
}

/* —— 输入区：和纸落在墨台 —— */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #ede4d0;
    border: 1px solid #3a3a46;
    border-radius: 10px;
    padding: 9px 12px;
    min-height: 18px;
    color: #1a1a21;
    selection-background-color: #c8432c;
    selection-color: #fff8ef;
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {
    border-color: #5a5a68;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1.5px solid #c8432c;
    background: #f4ecdc;
}
QComboBox::drop-down { border: none; width: 26px; }
QComboBox::down-arrow {
    width: 0; height: 0;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid #4a4438;
}
/* 下拉列表项：浅和纸底 + 深字，避免弹出时看不清 */
QComboBox QAbstractItemView {
    background: #ede4d0;
    color: #1a1a21;
    border: 1px solid #3a3a46;
    selection-background-color: #c8432c;
    selection-color: #fff8ef;
    outline: none;
    padding: 4px;
}

/* —— 按钮体系 —— */
QPushButton {
    background: #1d1d26;
    border: 1px solid #33333f;
    border-radius: 10px;
    padding: 8px 12px;
    color: #d8d0bf;
    font-weight: 600;
}
QPushButton:hover {
    background: #242430;
    border-color: #4a4a58;
    color: #f2ecdd;
}
QPushButton:pressed { background: #101014; }
QPushButton:disabled {
    color: #6a655a;
    background: #16161c;
    border-color: #26262f;
}

/* 主操作：朱印红 */
QPushButton#primaryBtn {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #d4543c, stop:1 #a83220);
    border: none;
    color: #fff6ea;
    font-weight: 800;
    font-size: 15px;
    letter-spacing: 1px;
    padding: 15px 22px;
    border-radius: 14px;
}
QPushButton#primaryBtn:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #e06048, stop:1 #bd3a26);
}
QPushButton#primaryBtn:pressed { background: #8e2b1c; }
QPushButton#primaryBtn:disabled {
    background: #4a3a34;
    color: #b8a99c;
}

QPushButton#ghostBtn {
    background: transparent;
    border: 1px solid #33333f;
    color: #c9c2b2;
    font-weight: 600;
}
QPushButton#ghostBtn:hover {
    background: rgba(200, 67, 44, 0.08);
    border-color: #c8432c;
    color: #f2ecdd;
}

QPushButton#dangerBtn {
    background: #221516;
    border: 1px solid #5a2a24;
    color: #e08a7a;
    font-weight: 700;
    border-radius: 14px;
}
QPushButton#dangerBtn:hover { background: #2c1a1a; }
QPushButton#dangerBtn:disabled {
    background: #18181e;
    color: #6a5a56;
    border-color: #26262f;
}

/* —— 复选 —— */
QCheckBox {
    color: #c9c2b2;
    spacing: 8px;
    font-weight: 500;
}
QCheckBox::indicator {
    width: 17px; height: 17px;
    border-radius: 5px;
    border: 1.5px solid #4a4438;
    background: #ede4d0;
}
QCheckBox::indicator:checked {
    background: #c8432c;
    border-color: #c8432c;
}

/* —— 分组 —— */
QGroupBox {
    background: #1b1b23;
    border: 1px solid #2b2b35;
    border-radius: 16px;
    margin-top: 14px;
    padding: 18px 14px 14px 14px;
    font-weight: 700;
    color: #d8d0bf;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 16px;
    padding: 0 8px;
    color: #c9a25a;
}

QLabel#toolsCaption {
    color: #8a7f6c;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1px;
}

/* —— 右侧日志台 —— */
QFrame#rightPane {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #14141a, stop:1 #0e0e12);
    border: 1px solid #2b2b35;
    border-radius: 20px;
}
QLabel#paneTitle {
    color: #f2ecdd;
    font-size: 16px;
    font-weight: 800;
    letter-spacing: 0.5px;
}
QLabel#paneHint {
    color: #8a7f6c;
    font-size: 12px;
}
QLabel#statusChip {
    background: #1b1b23;
    color: #c9a25a;
    border: 1px solid #33333f;
    border-radius: 10px;
    padding: 6px 12px;
    font-weight: 600;
    font-size: 12px;
}
QLabel#statusChip[state="run"] {
    background: #22160f;
    color: #e06048;
    border-color: #5a2a1c;
}
QLabel#statusChip[state="ok"] {
    background: #141a16;
    color: #9cc9a0;
    border-color: #2c4436;
}
QLabel#statusChip[state="fail"] {
    background: #201414;
    color: #e08a7a;
    border-color: #5a2824;
}
QLabel#statusChip[state="warn"] {
    background: #1a1712;
    color: #ddb56a;
    border-color: #4a3a1e;
}

/* —— 进度 —— */
QProgressBar {
    border: 1px solid #2b2b35;
    border-radius: 8px;
    background: #0a0a0e;
    text-align: center;
    color: #c9c2b2;
    min-height: 14px;
    max-height: 14px;
    font-size: 10px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #c8432c, stop:1 #e0785c);
    border-radius: 7px;
}

/* —— 日志 —— */
QTextEdit#logView {
    background: #0b0b0f;
    color: #cfc8b8;
    border: 1px solid #22222b;
    border-radius: 14px;
    padding: 12px 14px;
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI", monospace;
    font-size: 12px;
    selection-background-color: #c8432c;
    selection-color: #fff8ef;
}

/* —— 滚动条 —— */
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 6px 2px;
}
QScrollBar::handle:vertical {
    background: #33333f;
    border-radius: 4px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: #c8432c; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTextEdit#logView QScrollBar::handle:vertical { background: #26262f; }

QMessageBox {
    background: #17171e;
}
QMessageBox QLabel { color: #e8e2d4; }
"""
