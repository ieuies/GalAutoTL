# -*- coding: utf-8 -*-
"""Main GUI — one-click: pick game root → auto-detect →汉化."""
from __future__ import annotations

import traceback
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    Qt,
    QThread,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.config import AppConfig, config_path
from app.assets import app_icon_path
from app.core.cp932_safe import explain_dots
from app.core.detect import detect_engine
from app.core.ensure_deps import ensure_runtime_deps
from app.core.fonts import copy_cjk_font_to_game
from app.pipelines.artemis import run_artemis
from app.pipelines.bgi import run_bgi
from app.pipelines.generic_text import run_generic
from app.pipelines.kagura import run_kagura
from app.pipelines.kirikiri import run_kirikiri
from app.pipelines.lcse import run_lcse
from app.pipelines.reallive import run_reallive
from app.pipelines.sakana import run_sakana
from app.pipelines.softpal import run_softpal
from app.pipelines.unity import run_unity
from app.pipelines.yuris import run_yuris
from app.ui.styles import APP_STYLE

ENGINE_PIPES = (
    "lcse",
    "kirikiri",
    "yuris",
    "unity",
    "artemis",
    "bgi",
    "reallive",
    "sakana",
    "kagura",
    "softpal",
)


class Worker(QObject):
    log = Signal(str)
    progress = Signal(int, int)
    finished = Signal(bool, str)

    def __init__(self, cfg: AppConfig, pipeline: str) -> None:
        super().__init__()
        self.cfg = cfg
        self.pipeline = pipeline
        self._cancel = False

    def request_cancel(self) -> None:
        self._cancel = True

    def _log(self, msg: str) -> None:
        self.log.emit(msg)

    def _progress(self, done: int, total: int) -> None:
        self.progress.emit(done, total)

    def _cancel_fn(self) -> bool:
        return self._cancel

    @Slot()
    def run(self) -> None:
        try:
            missing = ensure_runtime_deps(self._log)
            if missing:
                self._log("部分依赖未就绪: " + ", ".join(missing))

            pipe = self.pipeline
            root = self.cfg.game_dir or self.cfg.text_dir
            if pipe == "polish_only":
                force_auto = False
            elif pipe == "remain_only":
                force_auto = True
            else:
                force_auto = bool(getattr(self.cfg, "simple_mode", True)) or pipe in (
                    "auto",
                    "generic",
                    "",
                    None,
                )
            remain_mode = pipe == "remain_only"
            if root and force_auto:
                det = detect_engine(root)
                pipe = det.pipeline
                self._log(det.summary())
                self._log(f"自动选择管线: {pipe}")
                if not self.cfg.game_dir:
                    self.cfg.game_dir = root
                if pipe in ENGINE_PIPES and not self.cfg.text_dir:
                    self.cfg.text_dir = root

            if remain_mode:
                from app.core.pipeline_harden import load_remain_filter_from_game

                jp = load_remain_filter_from_game(self.cfg.game_dir or self.cfg.text_dir)
                if not jp:
                    raise RuntimeError(
                        "未找到可用的 GalAutoTL_remain.txt。请先完整汉化一次生成漏句报告。"
                    )
                self.cfg.extra = dict(getattr(self.cfg, "extra", None) or {})
                self.cfg.extra["remain_filter"] = jp
                self._log(f"仅译漏句模式: 载入 {len(jp)} 条 JP")
            else:
                # clear leftover filter from a previous remain_only save
                extra = dict(getattr(self.cfg, "extra", None) or {})
                if "remain_filter" in extra:
                    extra.pop("remain_filter", None)
                    self.cfg.extra = extra

            if getattr(self.cfg, "auto_copy_font", True):
                target = self.cfg.game_dir or self.cfg.text_dir
                if pipe == "unity" and target:
                    try:
                        from app.core.unity_runtime_inject import ensure_xua_cjk_font

                        fn = ensure_xua_cjk_font(Path(target), self._log)
                        self._log(f"Unity/XUA 中文字体已配置: {fn}")
                    except Exception as e:
                        self._log(f"Unity 字体配置失败: {e}")
                elif target:
                    ok, tip = copy_cjk_font_to_game(target)
                    self._log(tip if ok else f"字体: {tip}")

            if pipe == "reallive":
                run_reallive(self.cfg, self._log, self._progress, self._cancel_fn)
            elif pipe == "polish_only":
                from app.core.mt_polish import polish_directory

                roots = []
                g = self.cfg.game_dir or self.cfg.text_dir
                t = self.cfg.tools_dir
                if g:
                    roots.append(g)
                if t:
                    roots.append(t)
                if self.cfg.text_dir and self.cfg.text_dir not in roots:
                    roots.append(self.cfg.text_dir)
                total_f = total_l = 0
                for r in roots:
                    self._log(f"扫描润色: {r}")
                    f, l = polish_directory(
                        r,
                        lang=getattr(self.cfg, "lang", "zh_cn") or "zh_cn",
                        soft_cp932=bool(getattr(self.cfg, "cp932_safe", False)),
                        log=self._log,
                    )
                    total_f += f
                    total_l += l
                self._log(f"仅润色完成：合计 {total_f} 文件 / {total_l} 行")
            elif pipe == "lcse":
                run_lcse(self.cfg, self._log, self._progress, self._cancel_fn)
            elif pipe == "kirikiri":
                run_kirikiri(self.cfg, self._log, self._progress, self._cancel_fn)
            elif pipe == "yuris":
                run_yuris(self.cfg, self._log, self._progress, self._cancel_fn)
            elif pipe == "unity":
                run_unity(self.cfg, self._log, self._progress, self._cancel_fn)
            elif pipe == "artemis":
                run_artemis(self.cfg, self._log, self._progress, self._cancel_fn)
            elif pipe == "bgi":
                run_bgi(self.cfg, self._log, self._progress, self._cancel_fn)
            elif pipe == "sakana":
                run_sakana(self.cfg, self._log, self._progress, self._cancel_fn)
            elif pipe == "kagura":
                run_kagura(self.cfg, self._log, self._progress, self._cancel_fn)
            elif pipe == "softpal":
                run_softpal(self.cfg, self._log, self._progress, self._cancel_fn)
            elif pipe == "packed":
                raise RuntimeError(
                    "该引擎封包暂无通用一键。请换本工具已支持的引擎，或先手动解包出明文后再试。"
                )
            else:
                run_generic(self.cfg, self._log, self._progress, self._cancel_fn)

            # All engines: second-pass polish on any text artifacts written to disk
            # 仅译漏句：跳过全盘润色，避免把未在 remain 里的文件也改一遍
            from app.core.pipeline_harden import remain_filter_set

            if self._cancel:
                self._log("已取消：跳过收尾润色")
                self.finished.emit(True, "已取消")
                return

            if (
                pipe != "polish_only"
                and getattr(self.cfg, "mt_polish", True)
                and remain_filter_set(self.cfg) is None
            ):
                from app.core.mt_polish import polish_after_pipeline

                extras = []
                if self.cfg.tools_dir:
                    extras.append(self.cfg.tools_dir)
                if self.cfg.text_dir:
                    extras.append(self.cfg.text_dir)
                polish_after_pipeline(
                    self.cfg.game_dir or self.cfg.text_dir,
                    lang=getattr(self.cfg, "lang", "zh_cn") or "zh_cn",
                    soft_cp932=bool(getattr(self.cfg, "cp932_safe", False)),
                    enabled=True,
                    log=self._log,
                    extra_roots=extras,
                )
            elif remain_filter_set(self.cfg) is not None:
                self._log("仅译漏句：跳过全盘润色")
            self.finished.emit(True, "完成")
        except Exception as e:
            self._log(traceback.format_exc())
            self.finished.emit(False, str(e))


def _card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    return f


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("GalAutoTL · Galgame 自动汉化")
        self.resize(1180, 780)
        self.setMinimumSize(960, 660)
        icon_path = app_icon_path()
        if icon_path is not None:
            self.setWindowIcon(QIcon(str(icon_path)))
        self.cfg = AppConfig.load()
        self._thread: QThread | None = None
        self._worker: Worker | None = None
        self._cancel_requested = False
        self._fade_anim: QPropertyAnimation | None = None
        self._build_ui()
        self._load_cfg_to_ui()
        self._apply_simple_mode()
        self.append_log("就绪。选择游戏根目录即可自动识别引擎。")
        try:
            ensure_runtime_deps(self.append_log)
        except Exception:
            pass

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if getattr(self, "_did_fade", False):
            return
        self._did_fade = True
        effect = QGraphicsOpacityEffect(self.centralWidget())
        self.centralWidget().setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(420)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_anim = anim
        anim.start()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("centralRoot")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(20, 20, 20, 20)
        shell.setSpacing(18)

        # —— Left: controls ——
        left_wrap = QWidget()
        left_wrap.setObjectName("leftPane")
        left_wrap.setMinimumWidth(400)
        left_wrap.setMaximumWidth(460)
        left = QVBoxLayout(left_wrap)
        left.setContentsMargins(0, 2, 0, 2)
        left.setSpacing(14)

        hero = QFrame()
        hero.setObjectName("brandRail")
        hl = QVBoxLayout(hero)
        hl.setContentsMargins(20, 18, 20, 18)
        hl.setSpacing(4)
        mark = QLabel("GALAUTOTL")
        mark.setObjectName("brandMark")
        title = QLabel("GalAutoTL")
        title.setObjectName("brandTitle")
        sub = QLabel("Galgame 自动汉化工作台")
        sub.setObjectName("brandSub")
        steps = QLabel("选目录  →  识引擎  →  开始汉化")
        steps.setObjectName("stepLine")
        hl.addWidget(mark)
        hl.addWidget(title)
        hl.addWidget(sub)
        hl.addWidget(steps)
        left.addWidget(hero)

        game_card = _card()
        gl = QVBoxLayout(game_card)
        gl.setContentsMargins(16, 14, 16, 14)
        gl.setSpacing(10)
        gl.addWidget(self._section("游戏根目录"))
        self.game_edit = QLineEdit()
        self.game_edit.setPlaceholderText("含 exe / *_Data / .xp3 / .pfs / lcsebody…")
        gl.addWidget(self._browse_row(self.game_edit, self._pick_game, "浏览"))
        self.detect_label = QLabel("尚未探测 — 选择目录后自动识别")
        self.detect_label.setObjectName("engineBadge")
        self.detect_label.setProperty("state", "idle")
        self.detect_label.setWordWrap(True)
        gl.addWidget(self.detect_label)
        left.addWidget(game_card)

        api_card = _card()
        al = QVBoxLayout(api_card)
        al.setContentsMargins(16, 14, 16, 14)
        al.setSpacing(10)
        al.addWidget(self._section("API"))
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("DeepSeek / OpenAI 兼容 Key")
        al.addWidget(self.api_key)
        api_adv = QWidget()
        af = QFormLayout(api_adv)
        af.setContentsMargins(0, 0, 0, 0)
        af.setSpacing(8)
        self.api_base = QLineEdit()
        self.api_model = QLineEdit()
        self.temp = QDoubleSpinBox()
        self.temp.setRange(0.0, 1.5)
        self.temp.setSingleStep(0.1)
        self.batch = QSpinBox()
        self.batch.setRange(1, 60)
        self.api_base_label = QLabel("Base URL")
        self.api_model_label = QLabel("Model")
        self.temp_label = QLabel("温度")
        self.batch_label = QLabel("批大小")
        af.addRow(self.api_base_label, self.api_base)
        af.addRow(self.api_model_label, self.api_model)
        af.addRow(self.temp_label, self.temp)
        af.addRow(self.batch_label, self.batch)
        self._api_adv = api_adv
        al.addWidget(api_adv)
        left.addWidget(api_card)

        opt_card = _card()
        ol = QVBoxLayout(opt_card)
        ol.setContentsMargins(16, 14, 16, 14)
        ol.setSpacing(10)
        ol.addWidget(self._section("汉化选项"))
        top_opt = QHBoxLayout()
        self.simple_mode = QCheckBox("一键模式（自动探测）")
        self.simple_mode.setChecked(True)
        self.simple_mode.toggled.connect(self._apply_simple_mode)
        top_opt.addWidget(self.simple_mode)
        top_opt.addStretch(1)
        self.lang_simple = QComboBox()
        self.lang_simple.addItem("简体中文", "zh_cn")
        self.lang_simple.addItem("繁体中文", "zh_tw")
        self.lang_simple.setMinimumWidth(110)
        lang_lab = QLabel("译成")
        lang_lab.setObjectName("hint")
        top_opt.addWidget(lang_lab)
        top_opt.addWidget(self.lang_simple)
        ol.addLayout(top_opt)
        ess = QHBoxLayout()
        self.auto_font_simple = QCheckBox("自动拷字体")
        self.auto_font_simple.setChecked(True)
        self.backup_simple = QCheckBox("自动备份")
        self.backup_simple.setChecked(True)
        ess.addWidget(self.auto_font_simple)
        ess.addWidget(self.backup_simple)
        ess.addStretch(1)
        ol.addLayout(ess)
        self._ess_row = ess
        left.addWidget(opt_card)

        act = QHBoxLayout()
        act.setSpacing(10)
        self.start_btn = QPushButton("开始汉化")
        self.start_btn.setObjectName("primaryBtn")
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.setMinimumHeight(52)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("dangerBtn")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setMinimumHeight(52)
        self.start_btn.clicked.connect(self.on_start)
        self.cancel_btn.clicked.connect(self.on_cancel)
        act.addWidget(self.start_btn, 3)
        act.addWidget(self.cancel_btn, 1)
        left.addLayout(act)

        tools_card = _card()
        tl = QVBoxLayout(tools_card)
        tl.setContentsMargins(16, 14, 16, 14)
        tl.setSpacing(10)
        tools_cap = QLabel("快捷工具")
        tools_cap.setObjectName("sectionTitle")
        tl.addWidget(tools_cap)
        tools = QGridLayout()
        tools.setHorizontalSpacing(8)
        tools.setVerticalSpacing(8)
        self.save_btn = QPushButton("保存设置")
        self.open_backup_btn = QPushButton("打开备份")
        self.review_btn = QPushButton("对照表")
        self.polish_btn = QPushButton("仅润色")
        self.remain_btn = QPushButton("仅译漏句")
        self.image_ui_btn = QPushButton("图片UI清单")
        self.font_btn = QPushButton("装字体")
        self.dots_btn = QPushButton("缺字说明")
        tool_items = (
            (self.save_btn, "写入本地配置"),
            (self.open_backup_btn, "打开桌面备份目录"),
            (self.review_btn, "打开 GalAutoTL_review.txt；改 CN: 后重跑灌回"),
            (self.polish_btn, "不调用 API，规则润色已有译文"),
            (self.remain_btn, "读取 GalAutoTL_remain.txt，只翻译仍漏的句子"),
            (self.image_ui_btn, "扫描 graphic=/图片引用，写出 GalAutoTL_image_ui.txt"),
            (self.font_btn, "仅复制中文字体到游戏目录"),
            (self.dots_btn, "关于 CP932 缺字与「・」"),
        )
        for i, (b, tip) in enumerate(tool_items):
            b.setObjectName("ghostBtn")
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            tools.addWidget(b, i // 3, i % 3)
        tl.addLayout(tools)
        self.save_btn.clicked.connect(self.on_save)
        self.open_backup_btn.clicked.connect(self.on_open_backup)
        self.review_btn.clicked.connect(self.on_open_review)
        self.polish_btn.clicked.connect(self.on_polish_only)
        self.remain_btn.clicked.connect(self.on_remain_only)
        self.image_ui_btn.clicked.connect(self.on_scan_image_ui)
        self.font_btn.clicked.connect(self.on_install_font)
        self.dots_btn.clicked.connect(self.on_explain_dots)

        unity_row = QHBoxLayout()
        unity_row.setSpacing(6)
        self.unity_harvest_btn = QPushButton("Unity 游玩补采")
        self.unity_lock_btn = QPushButton("Unity 锁定离线")
        for b in (self.unity_harvest_btn, self.unity_lock_btn):
            b.setObjectName("ghostBtn")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.unity_harvest_btn.setToolTip(
            "开启后漏句经本地代理调用 API 写入词典。玩一会后再点「锁定离线」。"
        )
        self.unity_lock_btn.setToolTip("采集译文并入词典并关闭 API，之后只查表。")
        self.unity_harvest_btn.clicked.connect(self.on_unity_harvest)
        self.unity_lock_btn.clicked.connect(self.on_unity_lock_offline)
        unity_row.addWidget(self.unity_harvest_btn)
        unity_row.addWidget(self.unity_lock_btn)
        self._unity_tools = QWidget()
        self._unity_tools.setLayout(unity_row)
        self._unity_tools.setVisible(False)
        tl.addWidget(self._unity_tools)
        left.addWidget(tools_card)

        adv_box = QGroupBox("高级选项")
        self.adv_box = adv_box
        of = QFormLayout(adv_box)
        of.setSpacing(8)
        self.text_edit = QLineEdit()
        self.tools_edit = QLineEdit()
        of.addRow("文本文件夹", self._browse_row(self.text_edit, self._pick_text))
        of.addRow("外部工具", self._browse_row(self.tools_edit, self._pick_tools))
        self.source_lang = QComboBox()
        self.source_lang.addItem("自动识别", "auto")
        self.source_lang.addItem("日文", "ja")
        self.source_lang.addItem("英文", "en")
        self.source_lang.addItem("韩文", "ko")
        self.source_lang.addItem("俄文", "ru")
        self.source_lang.addItem("其它语言", "other")
        self.lang = QComboBox()
        self.lang.addItem("简体中文（推荐）", "zh_cn")
        self.lang.addItem("繁体中文", "zh_tw")
        self.pipeline = QComboBox()
        self.pipeline.addItem("自动探测（推荐）", "auto")
        self.pipeline.addItem("通用文本", "generic")
        self.pipeline.addItem("Kirikiri / XP3", "kirikiri")
        self.pipeline.addItem("YU-RIS / YBN", "yuris")
        self.pipeline.addItem("Artemis / PFS", "artemis")
        self.pipeline.addItem("BGI / Ethornell", "bgi")
        self.pipeline.addItem("SakanaGL / SX", "sakana")
        self.pipeline.addItem("Unity / IL2CPP", "unity")
        self.pipeline.addItem("LCSE / Liquid", "lcse")
        self.pipeline.addItem("RealLive", "reallive")
        self.pipeline.addItem("Kagura / Debonosu", "kagura")
        self.pipeline.addItem("SoftPal ADV", "softpal")
        self.cp932 = QCheckBox("CP932 改字（仅古早编码）")
        self.cp932.setChecked(False)
        self.mt_polish = QCheckBox("机翻后处理润色（推荐）")
        self.mt_polish.setChecked(True)
        self.mt_polish.setToolTip(
            "自动修正：朋友达、此家辈、0计、假名残留、选项机翻腔、拟声误译等"
        )
        self.auto_font = QCheckBox("自动复制中文字体到游戏目录")
        self.auto_font.setChecked(True)
        self.backup = QCheckBox("写回前备份到桌面「自动翻译备份」")
        self.backup.setChecked(True)
        of.addRow("源语言", self.source_lang)
        of.addRow("译成", self.lang)
        of.addRow("强制管线", self.pipeline)
        of.addRow(self.cp932)
        of.addRow(self.mt_polish)
        of.addRow(self.auto_font)
        of.addRow(self.backup)
        self.detect_btn = QPushButton("仅探测引擎")
        self.detect_btn.setObjectName("ghostBtn")
        self.detect_btn.clicked.connect(self.on_detect)
        of.addRow(self.detect_btn)
        left.addWidget(adv_box)
        left.addStretch(1)

        left_scroll = QScrollArea()
        left_scroll.setObjectName("leftScroll")
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setWidget(left_wrap)
        left_scroll.setMinimumWidth(420)
        left_scroll.setMaximumWidth(480)
        shell.addWidget(left_scroll, 0)

        # —— Right: console ——
        right = QFrame()
        right.setObjectName("rightPane")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(20, 18, 20, 18)
        rl.setSpacing(12)
        head = QHBoxLayout()
        pane_title = QLabel("运行日志")
        pane_title.setObjectName("paneTitle")
        pane_hint = QLabel("引擎探测 · 翻译进度 · 写回结果")
        pane_hint.setObjectName("paneHint")
        head_l = QVBoxLayout()
        head_l.setSpacing(3)
        head_l.addWidget(pane_title)
        head_l.addWidget(pane_hint)
        head.addLayout(head_l, 1)
        self.progress_label = QLabel("就绪")
        self.progress_label.setObjectName("statusChip")
        self.progress_label.setProperty("state", "idle")
        head.addWidget(
            self.progress_label, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        rl.addLayout(head)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        rl.addWidget(self.progress_bar)

        self.log_view = QTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Cascadia Mono", 10))
        self.log_view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.log_view.setPlaceholderText(
            "日志将显示在这里…\n选择游戏目录后可自动探测引擎。"
        )
        rl.addWidget(self.log_view, 1)
        shell.addWidget(right, 1)

    def _section(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("sectionTitle")
        return lab

    def _set_status_chip(self, text: str, state: str = "idle") -> None:
        self.progress_label.setText(text)
        self.progress_label.setProperty("state", state)
        self.progress_label.style().unpolish(self.progress_label)
        self.progress_label.style().polish(self.progress_label)

    def _set_engine_badge(self, text: str, state: str = "ok") -> None:
        self.detect_label.setText(text)
        self.detect_label.setProperty("state", state)
        self.detect_label.style().unpolish(self.detect_label)
        self.detect_label.style().polish(self.detect_label)
        # brief attention pulse on the badge when engine is found
        if state == "ok" and self.detect_label.isVisible():
            effect = QGraphicsOpacityEffect(self.detect_label)
            self.detect_label.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity", self)
            anim.setDuration(280)
            anim.setStartValue(0.35)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.finished.connect(lambda: self.detect_label.setGraphicsEffect(None))
            anim.start()
            self._badge_anim = anim

    def _apply_simple_mode(self) -> None:
        simple = self.simple_mode.isChecked()
        self.adv_box.setVisible(not simple)
        self._api_adv.setVisible(not simple)
        if simple:
            self.pipeline.setCurrentIndex(self.pipeline.findData("auto"))
            self.cp932.setChecked(False)
            if self.batch.value() < 20:
                self.batch.setValue(24)

    def _browse_row(self, edit: QLineEdit, slot, btn_text: str = "浏览…") -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        h.addWidget(edit, 1)
        b = QPushButton(btn_text)
        b.setObjectName("ghostBtn")
        b.clicked.connect(slot)
        h.addWidget(b)
        return w

    def _pick_game(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "选择游戏根目录", self.game_edit.text() or ""
        )
        if d:
            self.game_edit.setText(d)
            # 换游戏时强制对齐文本目录，并清掉上一作外部工具路径，避免串台
            self.text_edit.setText(d)
            old_tools = self.tools_edit.text().strip()
            if old_tools:
                try:
                    from pathlib import Path

                    gd = Path(d).resolve()
                    td = Path(old_tools).resolve()
                    if gd != td and gd not in td.parents and td not in gd.parents:
                        self.tools_edit.clear()
                except Exception:
                    self.tools_edit.clear()
            self.on_detect(silent=True)

    def _pick_text(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "选择文本文件夹", self.text_edit.text() or ""
        )
        if d:
            self.text_edit.setText(d)

    def _pick_tools(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "选择外部工具目录", self.tools_edit.text() or ""
        )
        if d:
            self.tools_edit.setText(d)

    def _load_cfg_to_ui(self) -> None:
        c = self.cfg
        self.simple_mode.setChecked(getattr(c, "simple_mode", True))
        self.game_edit.setText(c.game_dir or c.text_dir)
        self.text_edit.setText(c.text_dir or c.game_dir)
        self.tools_edit.setText(c.tools_dir)
        idx = self.pipeline.findData(c.pipeline if c.pipeline else "auto")
        if idx < 0:
            idx = self.pipeline.findData("auto")
        if idx >= 0:
            self.pipeline.setCurrentIndex(idx)
        idx = self.source_lang.findData(getattr(c, "source_lang", "auto"))
        if idx >= 0:
            self.source_lang.setCurrentIndex(idx)
        idx = self.lang.findData(c.lang)
        if idx >= 0:
            self.lang.setCurrentIndex(idx)
            self.lang_simple.setCurrentIndex(self.lang_simple.findData(c.lang))
        self.cp932.setChecked(c.cp932_safe)
        self.mt_polish.setChecked(getattr(c, "mt_polish", True))
        self.auto_font.setChecked(getattr(c, "auto_copy_font", True))
        self.auto_font_simple.setChecked(getattr(c, "auto_copy_font", True))
        self.backup.setChecked(c.do_backup)
        self.backup_simple.setChecked(c.do_backup)
        self.api_base.setText(c.api_base or "https://api.deepseek.com")
        self.api_key.setText(c.api_key)
        self.api_model.setText(c.api_model or "deepseek-v4-flash")
        self.temp.setValue(c.temperature)
        self.batch.setValue(c.batch_size if c.batch_size else 24)
        if c.game_dir or c.text_dir:
            self.on_detect(silent=True)

    def _ui_to_cfg(self) -> AppConfig:
        c = self.cfg
        # Never persist 仅译漏句 allow-list into config.json
        extra = dict(getattr(c, "extra", None) or {})
        extra.pop("remain_filter", None)
        c.extra = extra
        c.simple_mode = self.simple_mode.isChecked()
        c.game_dir = self.game_edit.text().strip()
        c.text_dir = self.text_edit.text().strip() or c.game_dir
        c.tools_dir = self.tools_edit.text().strip()
        if c.simple_mode:
            # 一键模式：文本目录必须跟游戏根目录走，禁止沿用上一作 text_dir/tools_dir
            if c.game_dir:
                c.text_dir = c.game_dir
                self.text_edit.setText(c.game_dir)
            if c.tools_dir and c.game_dir:
                try:
                    from pathlib import Path

                    gd = Path(c.game_dir).resolve()
                    td = Path(c.tools_dir).resolve()
                    if gd != td and gd not in td.parents and td not in gd.parents:
                        c.tools_dir = ""
                        self.tools_edit.clear()
                except Exception:
                    c.tools_dir = ""
                    self.tools_edit.clear()
            c.pipeline = "auto"
            c.cp932_safe = False
            c.mt_polish = True
            c.lang = self.lang_simple.currentData()
            c.auto_copy_font = self.auto_font_simple.isChecked()
            c.do_backup = self.backup_simple.isChecked()
            self.lang.setCurrentIndex(self.lang.findData(c.lang))
            self.auto_font.setChecked(c.auto_copy_font)
            self.backup.setChecked(c.do_backup)
        else:
            c.pipeline = self.pipeline.currentData() or "auto"
            c.cp932_safe = self.cp932.isChecked()
            c.mt_polish = self.mt_polish.isChecked()
            c.lang = self.lang.currentData()
            c.auto_copy_font = self.auto_font.isChecked()
            c.do_backup = self.backup.isChecked()
        c.source_lang = self.source_lang.currentData()
        c.api_base = self.api_base.text().strip() or "https://api.deepseek.com"
        c.api_key = self.api_key.text().strip()
        c.api_model = self.api_model.text().strip() or "deepseek-v4-flash"
        c.temperature = float(self.temp.value())
        c.batch_size = int(self.batch.value())
        return c

    def append_log(self, msg: str) -> None:
        self.log_view.append(msg)
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    def on_detect(self, silent: bool = False) -> None:
        if not isinstance(silent, bool):
            silent = False
        d = self.game_edit.text().strip() or self.text_edit.text().strip()
        if not d:
            self._set_engine_badge("请先选择游戏根目录", "warn")
            return
        det = detect_engine(d)
        conf = getattr(det, "confidence", "") or ""
        state = "ok" if conf in ("high", "medium") else "warn"
        self._set_engine_badge(f"{det.engine}  →  {det.pipeline}", state)
        is_unity = det.pipeline == "unity" or "unity" in (det.engine or "").lower()
        self._unity_tools.setVisible(is_unity)
        if not silent:
            self.append_log(det.summary())
        if not self.simple_mode.isChecked() and det.pipeline in ENGINE_PIPES + (
            "generic",
            "packed",
        ):
            idx = self.pipeline.findData(
                det.pipeline if det.pipeline != "packed" else "auto"
            )
            if idx >= 0:
                self.pipeline.setCurrentIndex(idx)
        if det.pipeline in (
            "lcse",
            "kirikiri",
            "yuris",
            "unity",
            "artemis",
            "bgi",
            "reallive",
            "sakana",
            "kagura",
            "softpal",
        ):
            self.source_lang.setCurrentIndex(self.source_lang.findData("ja"))
        if det.pipeline == "reallive":
            # 有 VNTextProxy 时不要默认改字；改字会把「啊」变成「阿」
            self.cp932.setChecked(False)
            self.append_log(
                "RealLive：默认保留自然中文并安装显示补丁；"
                "只有代理无效时才勾选「CP932 改字」。"
            )
        if det.pipeline == "kagura":
            self.append_log(
                "Kagura：将翻译 game.pak 内 Lua 脚本；长句受等长字节限制可能截断。"
            )
        if det.pipeline == "softpal":
            self.append_log(
                "SoftPal：按汉化组流程写 data\\SCRIPT.SRC + TEXT.DAT（松散覆盖）。"
            )
        if not silent and det.workflow:
            self.append_log(det.workflow)

    @Slot()
    def on_save(self) -> None:
        self.cfg = self._ui_to_cfg()
        self.cfg.save()
        self.append_log(f"设置已保存: {config_path()}")
        QMessageBox.information(self, "保存", f"设置已写入\n{config_path()}")

    @Slot()
    def on_open_backup(self) -> None:
        p = Path.home() / "Desktop" / "自动翻译备份"
        p.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    @Slot()
    def on_open_review(self) -> None:
        from app.core.review_table import REVIEW_NAME, export_review_table, find_review_file

        root = self.game_edit.text().strip() or self.text_edit.text().strip()
        if not root:
            QMessageBox.warning(self, "缺少目录", "请先选择游戏根目录")
            return
        g = Path(root)
        path = find_review_file(g)
        if not path:
            path = export_review_table(
                g,
                [],
                [],
                header_note="尚无译文：先「开始汉化」生成后再校对",
            )
            self.append_log(f"已创建空对照表: {path}")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        self.append_log(
            f"对照表: {path} — 改 CN: 后保存，再点「开始汉化」灌回"
        )

    @Slot()
    def on_polish_only(self) -> None:
        if self._thread and self._thread.isRunning():
            return
        cfg = self._ui_to_cfg()
        if not cfg.game_dir and not cfg.text_dir and not cfg.tools_dir:
            QMessageBox.warning(self, "缺少目录", "请先选择游戏根目录或文本/工具目录")
            return
        if not cfg.game_dir:
            cfg.game_dir = cfg.text_dir or cfg.tools_dir
        cfg.save()
        self.cfg = cfg
        self.start_btn.setEnabled(False)
        self.polish_btn.setEnabled(False)
        self.remain_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setRange(0, 0)
        self._set_status_chip("润色中…", "run")
        self.append_log("==== 仅润色已有译文（不调用 API）====")
        self._thread = QThread()
        self._worker = Worker(cfg, "polish_only")
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self.append_log)
        self._worker.progress.connect(self.on_progress)
        self._worker.finished.connect(self.on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

    @Slot()
    def on_remain_only(self) -> None:
        if self._thread and self._thread.isRunning():
            return
        cfg = self._ui_to_cfg()
        if not cfg.game_dir and not cfg.text_dir:
            QMessageBox.warning(self, "缺少目录", "请先选择游戏根目录")
            return
        if not cfg.game_dir:
            cfg.game_dir = cfg.text_dir
        if not cfg.api_key:
            QMessageBox.warning(self, "缺少 API Key", "请填写 API Key")
            return
        from app.core.pipeline_harden import REMAIN_REPORT_NAME, load_remain_filter_from_game

        jp = load_remain_filter_from_game(cfg.game_dir)
        if not jp:
            QMessageBox.warning(
                self,
                "无漏句报告",
                f"未找到 {REMAIN_REPORT_NAME}。\n请先完整汉化一次，再点「仅译漏句」。",
            )
            return
        cfg.save()
        self.cfg = cfg
        self.start_btn.setEnabled(False)
        self.polish_btn.setEnabled(False)
        self.remain_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setRange(0, 0)
        self._set_status_chip("仅译漏句…", "run")
        self.append_log(f"==== 仅译漏句（{len(jp)} 条）====")
        self._thread = QThread()
        self._worker = Worker(cfg, "remain_only")
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self.append_log)
        self._worker.progress.connect(self.on_progress)
        self._worker.finished.connect(self.on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

    @Slot()
    def on_scan_image_ui(self) -> None:
        root = self.game_edit.text().strip() or self.text_edit.text().strip()
        if not root:
            QMessageBox.warning(self, "缺少目录", "请先选择游戏根目录")
            return
        from app.core.image_ui_scan import (
            IMAGE_UI_REPORT,
            scan_image_ui_refs,
            write_image_ui_report,
        )

        g = Path(root)
        hits = scan_image_ui_refs(g)
        path = write_image_ui_report(g, hits, log=self.append_log)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        QMessageBox.information(
            self,
            "图片 UI 清单",
            f"找到 {len(hits)} 处图片引用。\n已写出并打开：\n{g / IMAGE_UI_REPORT}\n\n"
            "这些是脚本里的图资源路径；画在图上的字需手工改图。",
        )

    @Slot()
    def on_install_font(self) -> None:
        target = self.game_edit.text().strip() or self.text_edit.text().strip()
        if not target:
            QMessageBox.warning(self, "缺少目录", "请先选择游戏根目录")
            return
        ok, tip = copy_cjk_font_to_game(target)
        self.append_log(tip)
        if ok:
            QMessageBox.information(self, "字体", tip)
        else:
            QMessageBox.warning(self, "字体", tip)

    @Slot()
    def on_explain_dots(self) -> None:
        QMessageBox.information(self, "关于缺字点", explain_dots())

    @Slot()
    def on_start(self) -> None:
        if self._thread and self._thread.isRunning():
            return
        cfg = self._ui_to_cfg()
        if not cfg.game_dir and not cfg.text_dir:
            QMessageBox.warning(self, "缺少目录", "请先选择游戏根目录")
            return
        if not cfg.game_dir:
            cfg.game_dir = cfg.text_dir
        if not cfg.api_key:
            QMessageBox.warning(self, "缺少 API Key", "请填写 API Key")
            return

        det = detect_engine(cfg.game_dir)
        self._set_engine_badge(f"{det.engine}  →  {det.pipeline}", "ok")
        self.append_log(det.summary())
        if cfg.simple_mode or cfg.pipeline in ("auto", "generic", ""):
            cfg.pipeline = "auto"

        cfg.save()
        self.cfg = cfg
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self._cancel_requested = False
        self.progress_bar.setRange(0, 0)  # busy
        self._set_status_chip("汉化中…", "run")
        self.append_log("==== 开始一键汉化 ====")
        self.append_log(f"游戏: {cfg.game_dir}")

        self._thread = QThread()
        self._worker = Worker(cfg, cfg.pipeline)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self.append_log)
        self._worker.progress.connect(self.on_progress)
        self._worker.finished.connect(self.on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

    @Slot()
    def on_unity_harvest(self) -> None:
        cfg = self._ui_to_cfg()
        g = Path(cfg.game_dir or "")
        if not g.is_dir() or not (g / "BepInEx").is_dir():
            QMessageBox.warning(
                self,
                "提示",
                "请先选已做过 Unity 一键汉化的游戏目录（含 BepInEx）。",
            )
            return
        try:
            from app.core.unity_runtime_inject import set_xua_runtime_mode

            lang = "zh-CN" if cfg.lang != "zh_tw" else "zh-TW"
            set_xua_runtime_mode(
                g,
                "harvest",
                target_lang=lang,
                source_lang=cfg.source_lang or "ja",
                log=self.append_log,
            )
            QMessageBox.information(
                self,
                "补采模式",
                "已开启「游玩采集」：\n"
                "1. 用「点我启动_中文汉化_Unity.bat」进游戏\n"
                "2. 多走剧情/菜单\n"
                "3. 回来点「合并并锁定离线」",
            )
        except Exception as e:
            QMessageBox.critical(self, "失败", str(e))

    @Slot()
    def on_unity_lock_offline(self) -> None:
        cfg = self._ui_to_cfg()
        g = Path(cfg.game_dir or "")
        if not g.is_dir() or not (g / "BepInEx").is_dir():
            QMessageBox.warning(
                self,
                "提示",
                "请先选已做过 Unity 一键汉化的游戏目录（含 BepInEx）。",
            )
            return
        try:
            from app.core.unity_runtime_inject import set_xua_runtime_mode

            lang = "zh-CN" if cfg.lang != "zh_tw" else "zh-TW"
            set_xua_runtime_mode(
                g,
                "offline",
                target_lang=lang,
                source_lang=cfg.source_lang or "ja",
                log=self.append_log,
            )
            QMessageBox.information(
                self,
                "已离线",
                "已并入 GalAutoTL.txt 并关闭 API，之后只查静态词典。",
            )
        except Exception as e:
            QMessageBox.critical(self, "失败", str(e))

    @Slot()
    def on_cancel(self) -> None:
        if not self._worker:
            return
        if self._cancel_requested:
            return
        self._cancel_requested = True
        self._worker.request_cancel()
        self.cancel_btn.setEnabled(False)
        self.append_log(
            "正在取消…（当前这条 API 请求结束后停；已译部分在缓存里，可下次续跑）"
        )
        self._set_status_chip("正在取消…", "warn")

    @Slot(int, int)
    def on_progress(self, done: int, total: int) -> None:
        if self._cancel_requested:
            self._set_status_chip("正在取消…", "warn")
            if total > 0:
                self.progress_bar.setRange(0, total)
                self.progress_bar.setValue(min(done, total))
                self.progress_bar.setFormat(f"取消中  {done}/{total}")
            return
        if total > 0:
            pct = min(100, int(100 * done / total))
            self._set_status_chip(f"{done}/{total} · {pct}%", "run")
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(min(done, total))
            self.progress_bar.setFormat(f"%p%  {done}/{total}")
        else:
            self._set_status_chip("汉化中…", "run")
            self.progress_bar.setRange(0, 0)

    @Slot(bool, str)
    def on_finished(self, ok: bool, msg: str) -> None:
        was_cancel = self._cancel_requested
        self._cancel_requested = False
        self.start_btn.setEnabled(True)
        self.polish_btn.setEnabled(True)
        self.remain_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setRange(0, 100)
        if was_cancel:
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("已取消")
            self._set_status_chip("已取消", "warn")
            self.append_log(f"==== 已取消: {msg} ====")
            return
        self.progress_bar.setValue(100 if ok else 0)
        self.progress_bar.setFormat("完成" if ok else "失败")
        self._set_status_chip("完成" if ok else "失败", "ok" if ok else "fail")
        self.append_log(f"==== {'成功' if ok else '失败'}: {msg} ====")
        if ok:
            extra = ""
            pipe = self.cfg.pipeline or ""
            if pipe == "unity" or (
                self.cfg.game_dir
                and (Path(self.cfg.game_dir) / "点我启动_中文汉化_Unity.bat").exists()
            ):
                extra = (
                    "\n\nUnity：请用「点我启动_中文汉化_Unity.bat」。\n"
                    "漏句可「游玩补采」→「合并并锁定离线」。"
                )
            QMessageBox.information(
                self,
                "完成",
                "汉化结束。\n"
                "· 备份在桌面「自动翻译备份」\n"
                "· 对照表 GalAutoTL_review.txt 可改 CN 后重跑灌回\n"
                "· 见游戏目录「汉化启动说明_*.txt」\n"
                "· 直接启动游戏试玩"
                + extra,
            )
        else:
            QMessageBox.critical(self, "失败", msg)


def run_app() -> int:
    import sys

    from PySide6.QtGui import QIcon

    from app.assets import app_icon_path

    app = QApplication(sys.argv)
    app.setApplicationName("GalAutoTL")
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    icon_path = app_icon_path()
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))
    win = MainWindow()
    win.show()
    return app.exec()
