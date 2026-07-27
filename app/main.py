# -*- coding: utf-8 -*-
"""GalAutoTL entrypoint."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from app.core.ensure_deps import ensure_runtime_deps
    from app.core.fonts import preferred_ui_font_family
    from app.ui.main_window import MainWindow

    # Quiet best-effort; UI will retry with log if needed
    try:
        ensure_runtime_deps()
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("GalAutoTL")
    family = preferred_ui_font_family()
    font = QFont(family, 10)
    app.setFont(font)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
