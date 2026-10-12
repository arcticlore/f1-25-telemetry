"""Лента графиков реального времени (rolling window, pyqtgraph)."""

from __future__ import annotations

from typing import Sequence

import pyqtgraph as pg
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from ...state import TraceSample
from ..theme import COLORS, SERIES_COLORS

__all__ = ["LiveTrace"]

pg.setConfigOptions(antialias=True, background=COLORS["bg_alt"], foreground=COLORS["muted"])


class _TracePlot(QWidget):
    """Один график с произвольным числом рядов."""

    def __init__(
        self,
        title: str,
        ylabel: str,
        y_range: tuple[float, float] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setLabel("bottom", "время", units="с")
        self.plot.setLabel("left", ylabel)
        self.plot.setTitle(title, color=COLORS["text"], size="10pt")
        self.plot.getPlotItem().setMenuEnabled(False)
        if y_range is not None:
            self.plot.setYRange(*y_range, padding=0)
        lay.addWidget(self.plot)
        self.curves: list[pg.PlotDataItem] = []

    def add_curve(self, colour: str, width: float = 1.5, step: bool = False) -> pg.PlotDataItem:
        """Добавляет ряд данных."""
        kwargs: dict = {"pen": pg.mkPen(colour, width=width)}
        if step:
            kwargs["stepMode"] = "left"
        curve = self.plot.plot(**kwargs)
        self.curves.append(curve)
        return curve

    def set_data(self, series: int, x: Sequence[float], y: Sequence[float]) -> None:
        """Обновляет один ряд по индексу."""
        if series >= len(self.curves) or len(x) < 2:
            if series < len(self.curves):
                self.curves[series].setData([], [])
            return
        self.curves[series].setData(list(x), list(y))


class LiveTrace(QWidget):
    """Ленточные графики игрока: скорость, педали, передача, RPM, DRS."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        self.splitter = QSplitter()
        self.splitter.setChildrenCollapsible(False)

        # --- колонка 1 ---
        col1 = QWidget()
        l1 = QVBoxLayout(col1)
        l1.setContentsMargins(0, 0, 0, 0)
        l1.setSpacing(4)

        self.speed = _TracePlot("Скорость", "км/ч", (0, 360))
        self._c_speed = self.speed.add_curve(SERIES_COLORS[0])

        self.pedals = _TracePlot("Газ / Тормоз", "0..1", (0.0, 1.05))
        self._c_throttle = self.pedals.add_curve(COLORS["green"])
        self._c_brake = self.pedals.add_curve(COLORS["red"])

        self.gear = _TracePlot("Передача", "передача", (-1, 9))
        self._c_gear = self.gear.add_curve(SERIES_COLORS[3], step=True)

        l1.addWidget(self.speed, 3)
        l1.addWidget(self.pedals, 2)
        l1.addWidget(self.gear, 1)

        # --- колонка 2 ---
        col2 = QWidget()
        l2 = QVBoxLayout(col2)
        l2.setContentsMargins(0, 0, 0, 0)
        l2.setSpacing(4)

        self.rpm = _TracePlot("Обороты", "об/мин")
        self._c_rpm = self.rpm.add_curve(SERIES_COLORS[5])

        self.drs = _TracePlot("DRS", "0/1", (-0.05, 1.15))
        self._c_drs = self.drs.add_curve(SERIES_COLORS[2], step=True)

        l2.addWidget(self.rpm, 2)
        l2.addWidget(self.drs, 1)

        self.splitter.addWidget(col1)
        self.splitter.addWidget(col2)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        outer.addWidget(self.splitter)

    def update_samples(self, samples: Sequence[TraceSample]) -> None:
        """Перерисовывает все ряды по кольцевому буферу."""
        if len(samples) < 2:
            return
        t0 = samples[0].session_time
        x = [s.session_time - t0 for s in samples]
        self._c_speed.setData(x, [s.speed_kmh for s in samples])
        self._c_throttle.setData(x, [s.throttle for s in samples])
        self._c_brake.setData(x, [s.brake for s in samples])
        self._c_gear.setData(x, [float(s.gear) for s in samples])
        self._c_rpm.setData(x, [float(s.rpm) for s in samples])
        self._c_drs.setData(x, [float(s.drs) for s in samples])
