# -*- coding: utf-8 -*-
"""GalAutoTL UI — ink / amber workstation (product tool, not system dialog)."""

# Visual direction: deep ink console + warm paper controls + brass accent.
# Avoid purple gradients, cream+serif terracotta, and flat single-tone chrome.

APP_STYLE = """
* {
    font-family: "Bahnschrift", "Yu Gothic UI", "Microsoft YaHei UI", sans-serif;
    font-size: 13px;
}

QMainWindow {
    background: #0e141c;
}
QWidget#centralRoot {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #121a24, stop:0.45 #161f2b, stop:1 #0c1219);
}

/* —— Left control rail —— */
QWidget#leftPane {
    background: transparent;
}
QScrollArea#leftScroll {
    background: transparent;
    border: none;
}
QScrollArea#leftScroll > QWidget > QWidget {
    background: transparent;
}

/* —— Sidebar navigation —— */
QFrame#navSide {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #1a2433, stop:1 #121a26);
    border: 1px solid #2a3a4f;
    border-radius: 18px;
}
QLabel#navMark {
    color: #d4a24c;
    font-family: "Bahnschrift", "Cascadia Mono", monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2.8px;
}
QLabel#navBrand {
    color: #f3eee4;
    font-family: "Bahnschrift SemiBold", "Bahnschrift", "Yu Gothic UI", sans-serif;
    font-size: 20px;
    font-weight: 700;
    letter-spacing: -0.4px;
    padding-bottom: 4px;
}
QLabel#navHint {
    color: #6d7f96;
    font-size: 11px;
    padding-top: 4px;
}
QPushButton#navBtn {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 11px 12px;
    color: #9aabbf;
    font-weight: 600;
    font-size: 13.5px;
    text-align: left;
}
QPushButton#navBtn:hover {
    background: rgba(212, 162, 76, 0.08);
    border-color: #3a4d66;
    color: #e8dcc8;
}
QPushButton#navBtn:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #2a2418, stop:1 #1e2a3c);
    border: 1px solid #6a5428;
    color: #e0b35c;
}
QStackedWidget#pageStack {
    background: transparent;
}

QFrame#brandRail {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #1a2433, stop:1 #152032);
    border: 1px solid #2a3a4f;
    border-radius: 18px;
}
QLabel#brandMark {
    color: #d4a24c;
    font-family: "Bahnschrift", "Cascadia Mono", monospace;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 3.5px;
}
QLabel#brandTitle {
    color: #f3eee4;
    font-family: "Bahnschrift SemiBold", "Bahnschrift", "Yu Gothic UI", sans-serif;
    font-size: 28px;
    font-weight: 700;
    letter-spacing: -0.6px;
}
QLabel#brandSub {
    color: #8b9bb0;
    font-size: 12.5px;
    padding-top: 2px;
}
QLabel#stepLine {
    color: #6d7f96;
    font-size: 11.5px;
    letter-spacing: 0.2px;
    padding-top: 8px;
}

QFrame#card {
    background: rgba(255, 248, 238, 0.96);
    border: 1px solid #c9b89a;
    border-radius: 16px;
}
QLabel#sectionTitle {
    color: #1a2030;
    font-family: "Bahnschrift SemiBold", "Bahnschrift", "Microsoft YaHei UI", sans-serif;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.2px;
    text-transform: uppercase;
}
QLabel#hint {
    color: #6a7385;
    font-size: 12px;
}

QLabel#engineBadge {
    background: #f0e6d4;
    color: #5c4a2e;
    border: 1px solid #d4c3a4;
    border-radius: 12px;
    padding: 11px 13px;
    font-weight: 600;
    font-size: 12.5px;
}
QLabel#engineBadge[state="idle"] {
    background: #ebe4d8;
    color: #7a7060;
    border-color: #d0c6b4;
    font-weight: 500;
}
QLabel#engineBadge[state="ok"] {
    background: #e5f0e4;
    color: #2f5d3a;
    border-color: #b5d0b8;
}
QLabel#engineBadge[state="warn"] {
    background: #f7e8d0;
    color: #8a5a12;
    border-color: #e0c48a;
}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #fffaf2;
    border: 1px solid #cbb99a;
    border-radius: 10px;
    padding: 9px 12px;
    min-height: 18px;
    color: #1a2030;
    selection-background-color: #c4782e;
    selection-color: #fff8ef;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1.5px solid #c4782e;
    background: #ffffff;
}
QComboBox::drop-down {
    border: none;
    width: 26px;
}
QComboBox::down-arrow {
    width: 0;
    height: 0;
}

QPushButton {
    background: #efe6d6;
    border: 1px solid #cbb99a;
    border-radius: 10px;
    padding: 8px 12px;
    color: #243044;
    font-weight: 600;
}
QPushButton:hover {
    background: #e7dcc8;
    border-color: #b89a6a;
}
QPushButton:pressed {
    background: #ddd0b8;
}
QPushButton:disabled {
    color: #9aa3b2;
    background: #ebe6dc;
    border-color: #d5cec0;
}

QPushButton#primaryBtn {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #d4893a, stop:1 #b86a22);
    border: none;
    color: #fff8ef;
    font-family: "Bahnschrift SemiBold", "Bahnschrift", "Microsoft YaHei UI", sans-serif;
    font-weight: 700;
    font-size: 15px;
    letter-spacing: 0.6px;
    padding: 15px 22px;
    border-radius: 14px;
}
QPushButton#primaryBtn:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #e09848, stop:1 #c4782e);
}
QPushButton#primaryBtn:pressed {
    background: #a35f1c;
}
QPushButton#primaryBtn:disabled {
    background: #c4a882;
    color: #f5ead8;
}

QPushButton#ghostBtn {
    background: #fff8ef;
    border: 1px solid #cbb99a;
    color: #2a3344;
    font-weight: 600;
}
QPushButton#ghostBtn:hover {
    background: #fff3e0;
    border-color: #c4782e;
    color: #8a4e14;
}

QPushButton#dangerBtn {
    background: #f7e6e2;
    border: 1px solid #e0b4ac;
    color: #a33b2e;
    font-weight: 700;
    border-radius: 14px;
}
QPushButton#dangerBtn:hover {
    background: #f0d4ce;
}
QPushButton#dangerBtn:disabled {
    background: #efe8e4;
    color: #b8a9a4;
    border-color: #ddd4d0;
}

QCheckBox {
    color: #2a3344;
    spacing: 8px;
    font-weight: 500;
}
QCheckBox::indicator {
    width: 17px;
    height: 17px;
    border-radius: 5px;
    border: 1.5px solid #b9a888;
    background: #fffaf2;
}
QCheckBox::indicator:checked {
    background: #c4782e;
    border-color: #c4782e;
}

QGroupBox {
    background: rgba(255, 248, 238, 0.92);
    border: 1px solid #c9b89a;
    border-radius: 16px;
    margin-top: 14px;
    padding: 18px 14px 14px 14px;
    font-weight: 700;
    color: #1a2030;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 16px;
    padding: 0 8px;
    color: #5c4a2e;
}

QLabel#toolsCaption {
    color: #8b9bb0;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1px;
}

/* —— Right console —— */
QFrame#rightPane {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #151d2a, stop:1 #0b1018);
    border: 1px solid #2a3a4f;
    border-radius: 20px;
}
QLabel#paneTitle {
    color: #f0ebe3;
    font-family: "Bahnschrift SemiBold", "Bahnschrift", sans-serif;
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 0.2px;
}
QLabel#paneHint {
    color: #7d8fa6;
    font-size: 12px;
}
QLabel#statusChip {
    background: #1e2a3c;
    color: #c4a574;
    border: 1px solid #3a4d66;
    border-radius: 10px;
    padding: 6px 12px;
    font-weight: 600;
    font-size: 12px;
}
QLabel#statusChip[state="run"] {
    background: #2a2418;
    color: #e0b35c;
    border-color: #6a5428;
}
QLabel#statusChip[state="ok"] {
    background: #1a2a20;
    color: #8fd0a0;
    border-color: #3a6a48;
}
QLabel#statusChip[state="fail"] {
    background: #2a1818;
    color: #e09088;
    border-color: #6a3838;
}
QLabel#statusChip[state="warn"] {
    background: #2a2418;
    color: #e0b35c;
    border-color: #6a5428;
}

QProgressBar {
    border: 1px solid #2a3a4f;
    border-radius: 8px;
    background: #0a0f16;
    text-align: center;
    color: #c9d4e4;
    min-height: 14px;
    max-height: 14px;
    font-size: 10px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #c4782e, stop:1 #e0a85c);
    border-radius: 7px;
}

QTextEdit#logView {
    background: #070b11;
    color: #b7c6d8;
    border: 1px solid #1c2a3c;
    border-radius: 14px;
    padding: 12px 14px;
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI", monospace;
    font-size: 12px;
    selection-background-color: #c4782e;
    selection-color: #fff8ef;
}

QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 6px 2px;
}
QScrollBar::handle:vertical {
    background: #3a4d66;
    border-radius: 4px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover {
    background: #c4782e;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QTextEdit#logView QScrollBar::handle:vertical {
    background: #2a3a4f;
}

QMessageBox {
    background: #f7f0e6;
}
QMessageBox QLabel {
    color: #1a2030;
}
"""
