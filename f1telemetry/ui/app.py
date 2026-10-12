"""Точка входа GUI: QApplication, тема, selftest."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .. import __version__
from .main_window import MainWindow
from .theme import apply_theme

log = logging.getLogger(__name__)

__all__ = ["build_parser", "main", "run_selftest"]


def build_parser() -> argparse.ArgumentParser:
    """Аргументы командной строки GUI."""
    p = argparse.ArgumentParser(
        prog="f1telemetry-gui",
        description="F1 25 Telemetry Recorder — GUI",
    )
    p.add_argument("--db", default="data/f1.sqlite", help="путь к SQLite")
    p.add_argument("--host", default="0.0.0.0", help="адрес привязки UDP")
    p.add_argument("--port", type=int, default=20777, help="UDP-порт")
    p.add_argument("--fixture", default=None,
                   help="воспроизвести фикстуру вместо UDP")
    p.add_argument("--selftest", action="store_true",
                   help="offscreen-проверка запуска и закрытия окна")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    """Запускает GUI (или selftest)."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("F1TELEMETRY_LOG", "INFO").upper(),
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    app = QApplication(sys.argv[:1])
    app.setApplicationName("F1 25 Telemetry Recorder")
    app.setApplicationVersion(__version__)
    apply_theme(app)

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    window = MainWindow(
        db_path,
        host=args.host,
        port=args.port,
        fixture=args.fixture,
        autostart=(not args.selftest) or args.fixture is not None,
    )
    window.show()

    if args.selftest:
        QTimer.singleShot(1200, window.close)
        QTimer.singleShot(1500, app.quit)
        rc = app.exec()
        print("GUI SELFTEST OK")
        return 0 if rc == 0 else rc

    return app.exec()


# --- selftest без окна -------------------------------------------------------

def run_selftest(db_path: str | Path = "data/selftest.sqlite") -> int:
    """Прогоняет GUI-компоненты offscreen без открытия окна."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication(sys.argv[:1])
    apply_theme(app)

    from .widgets.analytics_view import AnalyticsView
    from .widgets.lap_compare import LapCompare
    from .widgets.lap_table import LapTable
    from .widgets.live_dash import LiveDash
    from .widgets.live_trace import LiveTrace
    from .widgets.session_browser import SessionBrowser
    from .widgets.status_bar import StatusBarWidget

    db = Path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    widgets = [
        LiveDash(), LiveTrace(), LapTable(), SessionBrowser(),
        LapCompare(), AnalyticsView(), StatusBarWidget(),
    ]
    mw = MainWindow(db, autostart=False)
    mw.close()

    # короткий тик обновления
    t0 = time.monotonic()
    for w in widgets:
        w.show()
        app.processEvents()
        w.hide()
    elapsed = time.monotonic() - t0
    print(f"GUI COMPONENTS OK ({len(widgets)} widgets, {elapsed * 1000:.0f} мс)")
    return 0
