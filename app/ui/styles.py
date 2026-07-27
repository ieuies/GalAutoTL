# -*- coding: utf-8 -*-
"""GalAutoTL UI — slate workstation + teal accent (desktop tool, not landing page)."""

APP_STYLE = """
* {
    font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 13px;
}
QMainWindow, QWidget#centralRoot {
    background: #dce3ec;
}
QWidget#leftPane {
    background: transparent;
}
QWidget#rightPane {
    background: #152233;
    border-radius: 16px;
}
QLabel#brandMark {
    color: #0d9488;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
}
QLabel#brandTitle {
    color: #0c1929;
    font-size: 26px;
    font-weight: 800;
    letter-spacing: -0.4px;
}
QLabel#brandSub {
    color: #5b6b7f;
    font-size: 12.5px;
}
QLabel#stepPill {
    background: #e8f5f3;
    color: #0f6b61;
    border: 1px solid #b9e2dc;
    border-radius: 14px;
    padding: 5px 11px;
    font-size: 11.5px;
    font-weight: 600;
}
QFrame#heroCard, QFrame#card {
    background: #ffffff;
    border: 1px solid #c8d3e1;
    border-radius: 14px;
}
QFrame#heroCard {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #ffffff, stop:1 #f3faf8);
    border-left: 4px solid #0d9488;
}
QLabel#sectionTitle {
    color: #0c1929;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.4px;
}
QLabel#hint {
    color: #5b6b7f;
    font-size: 12px;
}
QLabel#paneTitle {
    color: #e8eef6;
    font-size: 13px;
    font-weight: 700;
}
QLabel#paneHint {
    color: #8fa0b5;
    font-size: 11.5px;
}
QLabel#engineBadge {
    background: #e6f4f2;
    color: #0f6b61;
    border: 1px solid #b7e0da;
    border-radius: 10px;
    padding: 10px 12px;
    font-weight: 600;
}
QLabel#engineBadge[state="warn"] {
    background: #fff6e8;
    color: #9a6700;
    border-color: #f0d9a8;
}
QLabel#engineBadge[state="idle"] {
    background: #f0f3f7;
    color: #5a6a7e;
    border-color: #d5dde8;
    font-weight: 500;
}
QLabel#engineBadge[state="ok"] {
    background: #e6f4f2;
    color: #0f6b61;
    border-color: #b7e0da;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #f5f8fc;
    border: 1px solid #c5d0df;
    border-radius: 9px;
    padding: 8px 11px;
    min-height: 18px;
    selection-background-color: #0d9488;
    color: #152233;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1.5px solid #0d9488;
    background: #ffffff;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QPushButton {
    background: #eef2f7;
    border: 1px solid #c5d0df;
    border-radius: 9px;
    padding: 8px 12px;
    color: #1a2b3d;
}
QPushButton:hover {
    background: #e2eaf3;
    border-color: #b0becf;
}
QPushButton:pressed {
    background: #d5e0ec;
}
QPushButton:disabled {
    color: #9aa8b8;
    background: #f3f5f8;
}
QPushButton#primaryBtn {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #0d9488, stop:1 #0b7c73);
    border: none;
    color: #ffffff;
    font-weight: 750;
    font-size: 15px;
    padding: 14px 22px;
    border-radius: 12px;
}
QPushButton#primaryBtn:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #11a899, stop:1 #0d9488);
}
QPushButton#primaryBtn:pressed {
    background: #0b7f75;
}
QPushButton#primaryBtn:disabled {
    background: #9ecfc9;
    color: #f0fffc;
}
QPushButton#ghostBtn {
    background: #ffffff;
    border: 1px solid #c5d0df;
}
QPushButton#ghostBtn:hover {
    background: #f5f8fc;
    border-color: #0d9488;
    color: #0b7f75;
}
QPushButton#toolBtn {
    background: #243447;
    border: 1px solid #33485f;
    color: #d7e2ef;
    border-radius: 8px;
    padding: 7px 10px;
}
QPushButton#toolBtn:hover {
    background: #2c3f55;
    border-color: #0d9488;
}
QPushButton#dangerBtn {
    background: #fff1f0;
    border: 1px solid #f0c2c0;
    color: #b42318;
    font-weight: 600;
}
QPushButton#dangerBtn:hover {
    background: #ffe4e2;
}
QCheckBox {
    color: #2a3a4d;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #b7c4d6;
    background: #ffffff;
}
QCheckBox::indicator:checked {
    background: #0d9488;
    border-color: #0d9488;
}
QProgressBar {
    border: 1px solid #2a3f56;
    border-radius: 7px;
    background: #0f1c2e;
    text-align: center;
    color: #d7e2ef;
    min-height: 16px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #0d9488, stop:1 #14b8a6);
    border-radius: 6px;
}
QTextEdit#logView {
    background: #0b1522;
    color: #c9d7e8;
    border: 1px solid #1e3148;
    border-radius: 12px;
    padding: 10px;
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI", monospace;
    font-size: 12px;
    selection-background-color: #0d9488;
}
QGroupBox {
    background: #ffffff;
    border: 1px solid #c8d3e1;
    border-radius: 14px;
    margin-top: 12px;
    padding: 16px 12px 12px 12px;
    font-weight: 700;
    color: #0c1929;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
}
QScrollArea {
    border: none;
    background: transparent;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}
QScrollBar:vertical {
    background: transparent;
    width: 9px;
    margin: 4px;
}
QScrollBar::handle:vertical {
    background: #b4c1d1;
    border-radius: 4px;
    min-height: 28px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:vertical#logScroll, QTextEdit#logView QScrollBar:vertical {
    background: #0b1522;
}
QMessageBox {
    background: #ffffff;
}
"""
