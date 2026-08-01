# -*- coding: utf-8 -*-
"""GalAutoTL UI — 浅色简洁专业风 (light, calm, professional).

Design intent: a quiet, focused translation workstation. Soft light-gray
canvas, white cards with delicate shadows, thin hairline dividers, and a
single restrained blue accent for the primary action. Three-step guided
flow on the left, active panel on the right, advanced options collapsed.

Tokens
  canvas      #f7f8fa  base
  card        #ffffff  cards / panels
  line        #e5e7eb  hairlines
  ink         #1a2233  primary text
  sub         #6b7280  secondary text
  faint       #9ca3af  placeholder / disabled
  accent      #2563eb  primary blue
  accent-lt   #eff4ff  active-tint background
  good        #16a34a  success
  warn        #d97706  warning
"""
from __future__ import annotations

APP_STYLE = """
* {
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
    color: #1a2233;
}

QMainWindow {
    background: #f7f8fa;
}
QWidget#centralRoot {
    background: #f7f8fa;
}

/* —— 通用卡片 —— */
QFrame#card {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 14px;
}
QFrame#brandRail {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 14px;
}
QLabel#sectionTitle {
    color: #1a2233;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
}
QStackedWidget#pageStack { background: transparent; }

/* —— 右侧日志台 —— */
QFrame#rightPane {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 16px;
}
QLabel#paneTitle {
    color: #1a2233;
    font-size: 16px;
    font-weight: 800;
}
QLabel#paneHint {
    color: #9ca3af;
    font-size: 12px;
}

/* —— 左侧 guide rail —— */
QWidget#leftPane { background: transparent; }
QScrollArea#leftScroll { background: transparent; border: none; }
QScrollArea#leftScroll > QWidget > QWidget { background: transparent; }

QFrame#guideSide {
    background: transparent;
    border: none;
}

/* 顶部品牌区 */
QLabel#brandTitle {
    color: #1a2233;
    font-size: 22px;
    font-weight: 800;
    letter-spacing: 0.3px;
}
QLabel#brandSub {
    color: #6b7280;
    font-size: 12px;
}
QLabel#versionChip {
    background: #e8edf7;
    color: #2563eb;
    border-radius: 11px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 600;
}

/* 步骤卡片（导航用，checked=激活） */
QPushButton#navStepBtn {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    text-align: left;
}
QPushButton#navStepBtn:hover {
    background: #f3f4f6;
    border-color: #d1d5db;
}
QPushButton#navStepBtn:checked {
    background: #eff4ff;
    border: 1.5px solid #2563eb;
}
QLabel#stepNum {
    background: #e5e7eb;
    color: #6b7280;
    border-radius: 11px;
    min-width: 22px;
    max-width: 22px;
    min-height: 22px;
    max-height: 22px;
    font-size: 12px;
    font-weight: 700;
}
QPushButton#navStepBtn:checked QLabel#stepNum {
    background: #2563eb;
    color: #ffffff;
}
QLabel#stepTitle {
    color: #1a2233;
    font-size: 14px;
    font-weight: 700;
}
QLabel#stepDesc {
    color: #9ca3af;
    font-size: 11px;
}
QPushButton#navStepBtn:checked QLabel#stepDesc {
    color: #6b7280;
}
QFrame#stepConnector {
    background: #d1d5db;
    min-width: 2px;
    max-width: 2px;
}

/* —— Center panel —— */
QFrame#panel {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 16px;
}
QLabel#panelTitle {
    color: #1a2233;
    font-size: 17px;
    font-weight: 800;
}
QLabel#panelDesc {
    color: #6b7280;
    font-size: 13px;
}
QLabel#sectionLabel {
    color: #1a2233;
    font-size: 13px;
    font-weight: 700;
}
QLabel#hint {
    color: #9ca3af;
    font-size: 12px;
}

/* —— 输入区 —— */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #f9fafb;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    padding: 9px 12px;
    min-height: 20px;
    color: #1a2233;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {
    border-color: #c7d2fe;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1.5px solid #2563eb;
    background: #ffffff;
}
QComboBox::drop-down { border: none; width: 26px; }
QComboBox::down-arrow {
    width: 0; height: 0;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid #6b7280;
}
QComboBox QAbstractItemView {
    background: #ffffff;
    color: #1a2233;
    border: 1px solid #e5e7eb;
    selection-background-color: #eff4ff;
    selection-color: #2563eb;
    outline: none;
    padding: 4px;
}

/* —— 按钮体系 —— */
QPushButton {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    padding: 8px 16px;
    color: #374151;
    font-weight: 600;
}
QPushButton:hover {
    background: #f3f4f6;
    border-color: #d1d5db;
}
QPushButton:pressed { background: #e5e7eb; }
QPushButton:disabled {
    color: #9ca3af;
    background: #f9fafb;
    border-color: #e5e7eb;
}

/* 主操作：蓝 */
QPushButton#primaryBtn {
    background: #2563eb;
    border: none;
    color: #ffffff;
    font-weight: 700;
    font-size: 14px;
    padding: 10px 24px;
    border-radius: 20px;
}
QPushButton#primaryBtn:hover { background: #3b82f6; }
QPushButton#primaryBtn:pressed { background: #1d4ed8; }
QPushButton#primaryBtn:disabled {
    background: #c7d2fe;
    color: #ffffff;
}

/* 深色按钮（浏览/校验） */
QPushButton#darkBtn {
    background: #1f2937;
    border: none;
    color: #ffffff;
    font-weight: 600;
    padding: 8px 18px;
    border-radius: 10px;
}
QPushButton#darkBtn:hover { background: #374151; }
QPushButton#darkBtn:pressed { background: #111827; }

QPushButton#ghostBtn {
    background: transparent;
    border: 1px solid #e5e7eb;
    color: #374151;
    font-weight: 600;
}
QPushButton#ghostBtn:hover {
    background: #f3f4f6;
    border-color: #d1d5db;
}

QPushButton#dangerBtn {
    background: #ffffff;
    border: 1px solid #fecaca;
    color: #dc2626;
    font-weight: 600;
    border-radius: 20px;
}
QPushButton#dangerBtn:hover { background: #fef2f2; }
QPushButton#dangerBtn:disabled {
    color: #9ca3af;
    border-color: #e5e7eb;
    background: #f9fafb;
}

/* 工具小按钮 */
QPushButton#toolBtn {
    background: #f3f4f6;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    color: #6b7280;
    font-size: 12px;
    font-weight: 600;
    padding: 8px 12px;
}
QPushButton#toolBtn:hover {
    background: #e8edf7;
    color: #2563eb;
    border-color: #d1e0f5;
}

/* —— 折叠行 —— */
QPushButton#foldBtn {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    color: #6b7280;
    font-weight: 600;
    text-align: left;
    padding: 10px 14px;
}
QPushButton#foldBtn:hover {
    background: #f9fafb;
    border-color: #d1d5db;
}

/* —— 复选 —— */
QCheckBox {
    color: #374151;
    spacing: 8px;
    font-weight: 500;
}
QCheckBox::indicator {
    width: 16px; height: 16px;
    border-radius: 4px;
    border: 1.5px solid #d1d5db;
    background: #ffffff;
}
QCheckBox::indicator:checked {
    background: #2563eb;
    border-color: #2563eb;
}

/* —— 引擎徽章 —— */
QLabel#engineBadge {
    background: #eff4ff;
    color: #2563eb;
    border: 1px solid #d1e0f5;
    border-radius: 10px;
    padding: 9px 13px;
    font-weight: 600;
    font-size: 12px;
}
QLabel#engineBadge[state="idle"] {
    background: #f9fafb;
    color: #9ca3af;
    border-color: #e5e7eb;
}
QLabel#engineBadge[state="ok"] {
    background: #ecfdf5;
    color: #16a34a;
    border-color: #bbf7d0;
}
QLabel#engineBadge[state="warn"] {
    background: #fffbeb;
    color: #d97706;
    border-color: #fde68a;
}

/* —— 状态标签 —— */
QLabel#statusChip {
    background: #ecfdf5;
    color: #16a34a;
    border: 1px solid #bbf7d0;
    border-radius: 15px;
    padding: 4px 12px;
    font-weight: 600;
    font-size: 12px;
}
QLabel#statusChip[state="run"] {
    background: #eff4ff;
    color: #2563eb;
    border-color: #d1e0f5;
}
QLabel#statusChip[state="fail"] {
    background: #fef2f2;
    color: #dc2626;
    border-color: #fecaca;
}
QLabel#statusChip[state="warn"] {
    background: #fffbeb;
    color: #d97706;
    border-color: #fde68a;
}

/* —— 进度 —— */
QProgressBar {
    border: 1px solid #e5e7eb;
    border-radius: 6px;
    background: #f3f4f6;
    text-align: center;
    color: #6b7280;
    min-height: 8px;
    max-height: 8px;
    font-size: 9px;
}
QProgressBar::chunk {
    background: #2563eb;
    border-radius: 6px;
}

/* —— 日志 —— */
QTextEdit#logView {
    background: #f9fafb;
    color: #374151;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    padding: 12px 14px;
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI", monospace;
    font-size: 12px;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
}

/* —— 滚动条 —— */
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 6px 2px;
}
QScrollBar::handle:vertical {
    background: #d1d5db;
    border-radius: 4px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: #9ca3af; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTextEdit#logView QScrollBar::handle:vertical { background: #d1d5db; }

QGroupBox {
    background: #f9fafb;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    margin-top: 12px;
    padding: 16px 14px 12px 14px;
    font-weight: 700;
    color: #1a2233;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
    color: #2563eb;
}

QMessageBox {
    background: #ffffff;
}
QMessageBox QLabel { color: #1a2233; }
"""
