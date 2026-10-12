"""Главное окно: вкладки Live / Сессии / Сравнение / Аналитика."""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..recorder import Recorder
from ..ui.theme import COLORS
from ..ui.widgets.analytics_view import AnalyticsView
from ..ui.widgets.lap_compare import LapCompare
from ..ui.widgets.lap_table import LapTable
from ..ui.widgets.live_dash import LiveDash
from ..ui.widgets.live_trace import LiveTrace
from ..ui.widgets.session_browser import SessionBrowser
from ..ui.widgets.status_bar import StatusBarWidget

__all__ = ["RecorderBridge", "MainWindow"]

log = logging.getLogger(__name__)


class RecorderBridge(QObject):
    """Переводит колбэки Recorder (другие потоки) в Qt-сигналы главного потока."""

    session_changed = Signal(object)   # uid: int (u64 не влезает в Signal(int))
    lap_completed = Signal(object)
    event_received = Signal(str, object)
    flashback_detected = Signal()
    format_warning = Signal(int)
    stats_updated = Signal(object)


class MainWindow(QMainWindow):
    """Основное окно приложения."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        host: str = "0.0.0.0",
        port: int = 20777,
        fixture: str | Path | None = None,
        replay_speed: float = 0.0,
        autostart: bool = True,
    ) -> None:
        super().__init__()
        self.setWindowTitle("F1 25 Telemetry Recorder")
        self.resize(1280, 800)
        self.db_path = Path(db_path)
        self._fixture = Path(fixture) if fixture else None

        # --- статус/баннер ---
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.banner = QLabel("")
        self.banner.setProperty("role", "banner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        root.addWidget(self.banner)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        # Live
        self.live = QWidget()
        live_lay = QVBoxLayout(self.live)
        live_lay.setContentsMargins(4, 4, 4, 4)
        live_lay.setSpacing(4)

        live_head = QHBoxLayout()
        live_head.addWidget(QLabel("Игрок:"))
        self.player_combo = QComboBox()
        self.player_combo.setMinimumWidth(240)
        self.player_combo.currentIndexChanged.connect(self._on_player_changed)
        live_head.addWidget(self.player_combo)
        live_head.addStretch(1)
        live_lay.addLayout(live_head)

        self.dash = LiveDash()
        self.trace = LiveTrace()
        self.lap_table = LapTable()
        live_lay.addWidget(self.dash, 3)
        live_lay.addWidget(self.trace, 3)
        live_lay.addWidget(self.lap_table, 2)

        # Sessions
        self.browser = SessionBrowser()
        self.browser.set_db_path(self.db_path)

        # Compare
        self.compare = LapCompare()

        # Analytics
        self.analytics = AnalyticsView()

        self.tabs.addTab(self.live, "Live")
        self.tabs.addTab(self.browser, "Сессии")
        self.tabs.addTab(self.compare, "Сравнение")
        self.tabs.addTab(self.analytics, "Аналитика")
        self.setCentralWidget(central)

        self.status = StatusBarWidget()
        self.statusBar().addWidget(self.status, 1)

        self._build_menu()

        # --- мост и запись ---
        self.bridge = RecorderBridge()
        self.bridge.session_changed.connect(self._on_session_changed)
        self.bridge.lap_completed.connect(self._on_lap)
        self.bridge.event_received.connect(self._on_event)
        self.bridge.flashback_detected.connect(self._on_flashback)
        self.bridge.format_warning.connect(self._on_format_warning)

        self.recorder: Recorder | None = None
        # splitscreen: что показываем (None = игрок 1 / авто)
        self._view_car: int | None = None
        self._combo_key: tuple[int, int] | None = None
        self._lap_buffers: dict[int, list[dict[str, Any]]] = {}
        if autostart:
            self._start_recorder(host, port)

        # --- опрос GUI ~30 Гц ---
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self.browser.session_opened.connect(self._on_session_opened)

    # --- запись ------------------------------------------------------------

    def _start_recorder(self, host: str, port: int) -> None:
        rec = Recorder(
            self.db_path,
            host=host,
            port=port,
            fixture=self._fixture,
            replay_speed=0.0 if self._fixture else 0.0,
        )
        rec.on_session_change = lambda uid: self.bridge.session_changed.emit(int(uid))
        rec.on_laps = lambda laps: self.bridge.lap_completed.emit(tuple(laps))
        rec.on_event = lambda code, data: self.bridge.event_received.emit(code, data)
        rec.on_flashback = lambda: self.bridge.flashback_detected.emit()
        rec.on_format_warning = lambda fmt: self.bridge.format_warning.emit(int(fmt))
        rec.on_stats = lambda s: self.bridge.stats_updated.emit(s)
        rec.start()
        self.recorder = rec

    def _stop_recorder(self) -> None:
        if self.recorder is not None:
            self._timer.stop()
            self.recorder.stop()
            self.recorder = None

    # --- меню --------------------------------------------------------------

    def _build_menu(self) -> None:
        m_file = QMenu("Файл", self)
        act_open = m_file.addAction("Открыть БД…")
        act_open.triggered.connect(self._open_db_dialog)
        act_refresh = m_file.addAction("Обновить сессии")
        act_refresh.triggered.connect(self.browser.refresh)
        m_file.addSeparator()
        act_quit = m_file.addAction("Выход")
        act_quit.triggered.connect(self.close)
        self.menuBar().addMenu(m_file)

        m_help = QMenu("Справка", self)
        act_about = m_help.addAction("О программе")
        act_about.triggered.connect(self._about)
        self.menuBar().addMenu(m_help)

    def _open_db_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть базу данных", str(self.db_path.parent),
            "SQLite (*.sqlite *.sqlite3 *.db);;Все файлы (*)")
        if not path:
            return
        self._stop_recorder()
        self.db_path = Path(path)
        self.browser.set_db_path(self.db_path)
        self.compare.configure(self.db_path, None)
        self.analytics.configure(self.db_path, None)
        self._start_recorder("0.0.0.0", 20777)
        self._timer.start()

    def _about(self) -> None:
        QMessageBox.about(
            self, "О программе",
            "<b>F1 25 Telemetry Recorder</b><br>"
            "Запись UDP-телеметрии EA SPORTS F1 25 в SQLite.<br>"
            "Порт 20777 · формат 2025 · офлайн.",
        )

    # --- слоты моста -------------------------------------------------------

    def _on_session_changed(self, uid: int) -> None:
        name = ""
        snap = self.recorder.snapshot() if self.recorder else None
        if snap and snap.session_uid == uid:
            name = snap.track_name or ""
        self.status.set_session(uid, name)
        self.lap_table.clear()
        self._lap_buffers.clear()
        self._view_car = None
        self._combo_key = None
        self.player_combo.clear()
        self.browser.refresh()
        self.compare.configure(self.db_path, uid, snap.car_index if snap else None)
        self.analytics.configure(self.db_path, uid)
        if snap:
            self.analytics.set_car(snap.car_index)

    def _on_lap(self, laps: tuple[int, ...]) -> None:
        if self.recorder is None:
            return
        for lap in self.recorder.state.take_recent_laps():
            buf = self._lap_buffers.setdefault(lap.car_index, [])
            buf.append(asdict(lap))
            if len(buf) > 300:
                del buf[:-300]
        self._refresh_lap_table()

    def _on_player_changed(self, *_: Any) -> None:
        data = self.player_combo.currentData()
        self._view_car = int(data) if data is not None else None
        self._refresh_lap_table()

    def _on_event(self, code: str, data: dict[str, Any]) -> None:
        if code in ("SSTA", "SEND", "CHQF", "RCWN", "SCAR", "RDFL"):
            log.info("event %s %s", code, data)

    def _on_flashback(self) -> None:
        self.status.conn.setText("↻ flashback")
        self.status.conn.setStyleSheet(f"color: {COLORS['yellow']}; font-weight: bold;")

    def _on_format_warning(self, fmt: int) -> None:
        self.banner.setText(
            f"Игра шлёт формат {fmt}, а приложение поддерживает 2025. "
            "В игре: Настройки → UDP Telemetry → UDP Format = 2025."
        )
        self.banner.show()

    def _on_session_opened(self, uid: int) -> None:
        self.compare.configure(self.db_path, uid)
        self.analytics.configure(self.db_path, uid)
        self.tabs.setCurrentWidget(self.compare)

    # --- опрос -------------------------------------------------------------

    def _sync_player_combo(self, rec: Recorder) -> None:
        """Перестраивает список игроков при смене splitscreen-состава."""
        player = rec.state.player_car_index
        secondary = rec.state.secondary_player_car_index
        key = (player, secondary)
        if key != self._combo_key:
            self._combo_key = key
            self.player_combo.blockSignals(True)
            self.player_combo.clear()
            name1 = rec.snapshot(player).driver_name
            self.player_combo.addItem(
                f"Игрок 1 — {name1}" if name1 else "Игрок 1", player)
            if secondary != 255:
                name2 = rec.snapshot(secondary).driver_name
                self.player_combo.addItem(
                    f"Игрок 2 — {name2}" if name2 else "Игрок 2", secondary)
            self.player_combo.blockSignals(False)
            if self._view_car is not None and self._view_car not in (player, secondary):
                self._view_car = None
            idx = self.player_combo.findData(
                self._view_car if self._view_car is not None else player)
            self.player_combo.setCurrentIndex(max(0, idx))
        self.player_combo.setVisible(secondary != 255)

    def _current_view_car(self) -> int | None:
        """Машина, которую сейчас показывает Live (None = игрок 1)."""
        if self.recorder is None:
            return None
        if self.recorder.state.secondary_player_car_index == 255:
            return None
        return self._view_car

    def _refresh_lap_table(self) -> None:
        """Перерисовывает таблицу кругов для выбранного игрока."""
        car = self._current_view_car()
        if car is None and self.recorder is not None:
            car = self.recorder.state.player_car_index
        self.lap_table.clear()
        for lap in self._lap_buffers.get(car, ()):
            self.lap_table.add_lap(lap)

    def _tick(self) -> None:
        rec = self.recorder
        if rec is None:
            return
        self._sync_player_combo(rec)
        view = self._current_view_car()
        snap = rec.snapshot(view)
        self.dash.update_snapshot(snap)
        self.trace.update_samples(rec.trace(view))
        self.status.update_health(rec.health())

    # --- закрытие ----------------------------------------------------------

    def closeEvent(self, e) -> None:  # noqa: N802
        self._stop_recorder()
        super().closeEvent(e)
