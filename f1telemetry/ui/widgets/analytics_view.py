"""Аналитика сессии: стинты/деградация, расход топлива, позиции."""

from __future__ import annotations

from typing import Any

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...analytics import fuel_per_lap, positions_by_lap, stint_degradation, stint_summary
from ...constants import name_of, TYRE_COMPOUND_VISUAL
from ...storage import Storage
from ..theme import COLORS, SERIES_COLORS, font, mono_font

__all__ = ["AnalyticsView"]

_STINT_COLS = ("Стинт", "Круги", "Шины", "Ср. круг", "Деградация", "R²")


def _fmt_ms(ms: Any) -> str:
    if not ms:
        return "—"
    ms = int(ms)
    return f"{ms // 60000}:{(ms % 60000) / 1000:06.3f}"


class AnalyticsView(QWidget):
    """Вкладка аналитики выбранной сессии."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db_path = None
        self._session_uid: int | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        head = QHBoxLayout()
        self.title = QLabel("Аналитика")
        self.title.setFont(font(13, bold=True))
        self.car_combo = QComboBox()
        self.car_combo.setMinimumWidth(220)
        self.car_combo.currentIndexChanged.connect(self.refresh)
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(QLabel("Машина:"))
        head.addWidget(self.car_combo)
        outer.addLayout(head)

        # --- таблица стинтов ---
        self.table = QTableWidget(0, len(_STINT_COLS))
        self.table.setHorizontalHeaderLabels(list(_STINT_COLS))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        outer.addWidget(self.table, 1)

        # --- графики ---
        self.splitter = QWidget()
        self.plot_deg = pg.PlotWidget()
        self.plot_deg.showGrid(x=True, y=True, alpha=0.15)
        self.plot_deg.setLabel("bottom", "возраст шин", units="кр")
        self.plot_deg.setLabel("left", "время круга", units="с")
        self.plot_deg.setTitle("Деградация по стинтам", color=COLORS["text"], size="10pt")
        self.plot_deg.getPlotItem().setMenuEnabled(False)
        self._deg_scatters: list[pg.ScatterPlotItem] = []

        self.plot_pos = pg.PlotWidget()
        self.plot_pos.showGrid(x=True, y=True, alpha=0.15)
        self.plot_pos.setLabel("bottom", "круг")
        self.plot_pos.setLabel("left", "позиция")
        self.plot_pos.invertY(True)
        self.plot_pos.setTitle("Позиции по кругам", color=COLORS["text"], size="10pt")
        self.plot_pos.getPlotItem().setMenuEnabled(False)
        self._pos_curves: list[pg.PlotDataItem] = []

        lay = QHBoxLayout(self.splitter)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self.plot_deg, 1)
        lay.addWidget(self.plot_pos, 1)
        outer.addWidget(self.splitter, 1)

        self.summary = QLabel("")
        self.summary.setFont(mono_font(11))
        self.summary.setStyleSheet(f"color: {COLORS['muted']};")
        outer.addWidget(self.summary)

    # --- API ---------------------------------------------------------------

    def configure(self, db_path: Any, session_uid: int | None) -> None:
        """Задаёт БД/сессию; машина — игрок по умолчанию."""
        self._db_path = db_path
        self._session_uid = session_uid
        self._reload_cars()

    def set_car(self, car_index: int) -> None:
        """Выбирает машину в выпадающем списке (без цикла сигналов)."""
        idx = self.car_combo.findData(int(car_index))
        if idx >= 0 and idx != self.car_combo.currentIndex():
            self.car_combo.blockSignals(True)
            self.car_combo.setCurrentIndex(idx)
            self.car_combo.blockSignals(False)
            self.refresh()

    def refresh(self) -> None:
        if self._db_path is None or self._session_uid is None:
            return
        car = self.car_combo.currentData()
        if car is None:
            return
        car = int(car)
        try:
            st = Storage(self._db_path, read_only=True)
            try:
                laps = [dict(r) for r in st.laps(self._session_uid, car)]
                pos_rows = [(int(r[0]), int(r[1]), int(r[2]))
                            for r in st.lap_positions(self._session_uid)]
            finally:
                st.close()
        except Exception:  # noqa: BLE001
            return

        # стинты
        deg = stint_degradation(laps)
        summary = stint_summary(laps, deg)
        self.table.setRowCount(0)
        for s in summary:
            row = self.table.rowCount()
            self.table.insertRow(row)
            vals = (
                str(s["stint_index"]),
                f"{s['start_lap']}–{s['end_lap']}",
                name_of(TYRE_COMPOUND_VISUAL, s["compound_visual"]),
                _fmt_ms(s["avg_lap_ms"]),
                f"{s['deg_ms_per_lap']:+.1f} мс/кр",
                f"{s['r2']:.2f}",
            )
            for col, text in enumerate(vals):
                cell = QTableWidgetItem(text)
                cell.setFont(mono_font(10))
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 4:
                    v = s["deg_ms_per_lap"]
                    colour = (COLORS["red"] if v > 100 else
                              COLORS["yellow"] if v > 40 else COLORS["green"])
                    cell.setForeground(QColor(colour))
                self.table.setItem(row, col, cell)

        self._draw_degradation(laps, deg)
        self._draw_positions(pos_rows, car)
        fuel_rows = [(int(r["lap_num"]), float(r["fuel_at_start"]))
                     for r in laps if r.get("fuel_at_start") is not None]
        burn = fuel_per_lap(fuel_rows) if fuel_rows else []
        if burn:
            avg = sum(x[1] for x in burn) / len(burn)
            self.summary.setText(
                f"кругов {len(laps)} · стинтов {len(summary)} · "
                f"средний расход {avg:.2f} кг/кр"
            )
        else:
            self.summary.setText(f"кругов {len(laps)} · стинтов {len(summary)}")

    # --- внутренности ------------------------------------------------------

    def _reload_cars(self) -> None:
        self.car_combo.blockSignals(True)
        self.car_combo.clear()
        if self._db_path is not None and self._session_uid is not None:
            try:
                st = Storage(self._db_path, read_only=True)
                try:
                    laps = st.laps(self._session_uid)
                finally:
                    st.close()
            except Exception:  # noqa: BLE001
                laps = []
            cars = sorted({int(r["car_index"]) for r in laps})
            for car in cars:
                name = ""
                self.car_combo.addItem(f"car {car}{name}", car)
        self.car_combo.blockSignals(False)
        self.refresh()

    def _draw_degradation(self, laps: list[dict], deg: list[Any]) -> None:
        for s in self._deg_scatters:
            self.plot_deg.removeItem(s)
        self._deg_scatters.clear()
        by_stint: dict[int, list[dict]] = {}
        for lap in laps:
            if not lap.get("valid") or not lap.get("lap_time_ms"):
                continue
            by_stint.setdefault(int(lap.get("stint_index") or 0), []).append(lap)
        for i, (stint, rows) in enumerate(sorted(by_stint.items())):
            colour = SERIES_COLORS[i % len(SERIES_COLORS)]
            xs = [float(r["tyre_age_laps"]) for r in rows]
            ys = [float(r["lap_time_ms"]) / 1000.0 for r in rows]
            sc = pg.ScatterPlotItem(
                xs, ys, brush=pg.mkBrush(colour, alpha=160),
                pen=pg.mkPen(None), size=6)
            self.plot_deg.addItem(sc)
            self._deg_scatters.append(sc)

    def _draw_positions(self, rows: list[tuple[int, int, int]], focus_car: int) -> None:
        for c in self._pos_curves:
            self.plot_pos.removeItem(c)
        self._pos_curves.clear()
        if not rows:
            return
        by_car = positions_by_lap(rows)
        # фокусная машина + до 5 ближайших по последней позиции
        last = {car: max(m.values()) for car, m in by_car.items() if m}
        focus_pos = last.get(focus_car, 99)
        others = sorted(
            (c for c in last if c != focus_car),
            key=lambda c: abs(last[c] - focus_pos),
        )[:5]
        for car in [focus_car, *others]:
            m = by_car.get(car)
            if not m:
                continue
            xs = sorted(m)
            ys = [m[x] for x in xs]
            colour = COLORS["accent"] if car == focus_car else COLORS["muted"]
            width = 2.0 if car == focus_car else 1.0
            curve = self.plot_pos.plot(
                xs, ys, pen=pg.mkPen(colour, width=width), symbol="o",
                symbolSize=4, symbolBrush=pg.mkBrush(colour))
            self._pos_curves.append(curve)
