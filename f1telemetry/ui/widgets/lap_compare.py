"""Сравнение двух кругов: оверлей телеметрии + дельта по дистанции."""

from __future__ import annotations

from typing import Any

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ...analytics import DistanceSample, delta_curve
from ...constants import name_of, TYRE_COMPOUND_VISUAL
from ...storage import Storage
from ..theme import COLORS, SERIES_COLORS, mono_font

__all__ = ["LapCompare"]


def _fmt_ms(ms: Any) -> str:
    if not ms:
        return "—"
    ms = int(ms)
    return f"{ms // 60000}:{(ms % 60000) / 1000:06.3f}"


class _LapPicker(QWidget):
    """Выпадающий список кругов одного сеанса."""

    def __init__(self, caption: str, colour: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lab = QLabel(caption)
        lab.setFont(mono_font(11))
        lab.setStyleSheet(f"color: {colour}; font-weight: bold;")
        self.combo = QComboBox()
        self.combo.setMinimumWidth(260)
        lay.addWidget(lab)
        lay.addWidget(self.combo)

    def set_laps(self, laps: list[Any]) -> None:
        """``laps`` — строки ``laps`` из Storage (sqlite3.Row)."""
        self.combo.blockSignals(True)
        self.combo.clear()
        for lap in laps:
            label = (
                f"круг {lap['lap_num']}  {_fmt_ms(lap['lap_time_ms'])}"
                f"  {name_of(TYRE_COMPOUND_VISUAL, lap['compound_visual'])}"
            )
            self.combo.addItem(label, int(lap["id"]))
        self.combo.blockSignals(False)

    def lap_id(self) -> int | None:
        idx = self.combo.currentIndex()
        if idx < 0:
            return None
        return int(self.combo.itemData(idx))


class LapCompare(QWidget):
    """Вкладка сравнения: скорости по дистанции и кривая дельты."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db_path = None
        self._session_uid: int | None = None
        self._car_index: int | None = None
        self._laps: list[Any] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        head = QHBoxLayout()
        self.title = QLabel("Сравнение кругов")
        self.title.setFont(mono_font(12, bold=True))
        self.picker_a = _LapPicker("A", SERIES_COLORS[0])
        self.picker_b = _LapPicker("B", COLORS["orange"])
        self.summary = QLabel("")
        self.summary.setFont(mono_font(11))
        self.summary.setStyleSheet(f"color: {COLORS['text']};")
        head.addWidget(self.title)
        head.addWidget(self.picker_a)
        head.addWidget(self.picker_b)
        head.addStretch(1)
        head.addWidget(self.summary)
        outer.addLayout(head)

        self.splitter = QSplitter()
        self.splitter.setChildrenCollapsible(False)

        self.plot_speed = pg.PlotWidget()
        self.plot_speed.showGrid(x=True, y=True, alpha=0.15)
        self.plot_speed.setLabel("bottom", "дистанция", units="м")
        self.plot_speed.setLabel("left", "скорость", units="км/ч")
        self.plot_speed.setTitle("Скорость по дистанции", color=COLORS["text"], size="10pt")
        self.plot_speed.getPlotItem().setMenuEnabled(False)
        self._c_a = self.plot_speed.plot(pen=pg.mkPen(SERIES_COLORS[0], width=1.5))
        self._c_b = self.plot_speed.plot(pen=pg.mkPen(COLORS["orange"], width=1.5))

        self.plot_delta = pg.PlotWidget()
        self.plot_delta.showGrid(x=True, y=True, alpha=0.15)
        self.plot_delta.setLabel("bottom", "дистанция", units="м")
        self.plot_delta.setLabel("left", "дельта", units="с")
        self.plot_delta.setTitle(
            "Δ (A относительно B; + = A медленнее)", color=COLORS["text"], size="10pt"
        )
        self.plot_delta.getPlotItem().setMenuEnabled(False)
        self.plot_delta.setXLink(self.plot_speed)
        self._zero = pg.InfiniteLine(angle=0, pen=pg.mkPen(COLORS["border"], width=1))
        self.plot_delta.addItem(self._zero)
        self._c_delta = self.plot_delta.plot(pen=pg.mkPen(SERIES_COLORS[1], width=1.5))

        self.splitter.addWidget(self.plot_speed)
        self.splitter.addWidget(self.plot_delta)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        outer.addWidget(self.splitter)

        self.picker_a.combo.currentIndexChanged.connect(self._redraw)
        self.picker_b.combo.currentIndexChanged.connect(self._redraw)

    # --- API ---------------------------------------------------------------

    def configure(self, db_path: Any, session_uid: int | None,
                  car_index: int | None = None) -> None:
        """Задаёт БД и сессию, перечитывает списки кругов."""
        self._db_path = db_path
        self._session_uid = session_uid
        self._car_index = car_index
        self.refresh()

    def refresh(self) -> None:
        if self._db_path is None or self._session_uid is None:
            return
        try:
            st = Storage(self._db_path, read_only=True)
            try:
                car = self._car_index
                if car is None:
                    rows_all = st.laps(self._session_uid)
                    car = int(rows_all[0]["car_index"]) if rows_all else 0
                self._laps = [r for r in st.laps(self._session_uid)
                              if int(r["car_index"]) == car]
            finally:
                st.close()
        except Exception:  # noqa: BLE001
            return
        self.picker_a.set_laps(self._laps)
        self.picker_b.set_laps(self._laps)
        # по умолчанию: последние два круга
        if len(self._laps) >= 2:
            self.picker_b.combo.setCurrentIndex(0)
            self.picker_a.combo.setCurrentIndex(1)
        self._redraw()

    # --- внутренности ------------------------------------------------------

    def _find(self, lap_id: int | None) -> Any | None:
        for r in self._laps:
            if int(r["id"]) == lap_id:
                return r
        return None

    def _redraw(self, *_: Any) -> None:
        a = self._find(self.picker_a.lap_id())
        b = self._find(self.picker_b.lap_id())
        if a is None or b is None or self._db_path is None or self._session_uid is None:
            return
        try:
            st = Storage(self._db_path, read_only=True)
            try:
                rows_a = st.telemetry_for_lap(
                    self._session_uid, int(a["car_index"]), int(a["lap_num"]))
                rows_b = st.telemetry_for_lap(
                    self._session_uid, int(b["car_index"]), int(b["lap_num"]))
            finally:
                st.close()
        except Exception:  # noqa: BLE001
            return
        if not rows_a or not rows_b:
            self._c_a.setData([], [])
            self._c_b.setData([], [])
            self._c_delta.setData([], [])
            return

        d_a = [DistanceSample(distance_m=r["lap_distance_m"], speed_kmh=r["speed_kmh"])
               for r in rows_a]
        d_b = [DistanceSample(distance_m=r["lap_distance_m"], speed_kmh=r["speed_kmh"])
               for r in rows_b]
        self._c_a.setData([s.distance_m for s in d_a], [s.speed_kmh for s in d_a])
        self._c_b.setData([s.distance_m for s in d_b], [s.speed_kmh for s in d_b])

        curve = delta_curve(d_a, d_b, grid_m=5.0)
        if curve:
            self._c_delta.setData([c[0] for c in curve], [c[1] for c in curve])
            final = curve[-1][1]
            self.summary.setText(f"Δ на финише: {final:+.3f} с")
            self.summary.setStyleSheet(
                f"color: {COLORS['green'] if final < 0 else COLORS['red']}; font-weight: bold;"
            )
        else:
            self._c_delta.setData([], [])
            self.summary.setText("нет пересечения дистанций")
