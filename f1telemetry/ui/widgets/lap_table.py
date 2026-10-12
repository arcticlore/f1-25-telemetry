"""Таблица кругов/секторов в реальном времени."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...constants import name_of, TYRE_COMPOUND_VISUAL
from ..theme import COLORS, font, mono_font

__all__ = ["LapTable"]

_COLUMNS = ("Круг", "Время", "S1", "S2", "S3", "Поз.", "Шины", "Возр.", "Топл.", "К/ч")


def _fmt_ms(ms: Any) -> str:
    if not ms:
        return "—"
    ms = int(ms)
    return f"{ms // 60000}:{(ms % 60000) / 1000:06.3f}"


class LapTable(QWidget):
    """Список завершённых кругов игрока (последние сверху)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        head = QHBoxLayout()
        self.title = QLabel("Круги")
        self.title.setFont(font(11, bold=True))
        self.title.setStyleSheet(f"color: {COLORS['muted']};")
        self.best_lab = QLabel("")
        self.best_lab.setFont(mono_font(11, bold=True))
        self.best_lab.setStyleSheet(f"color: {COLORS['green']};")
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.best_lab)
        lay.addLayout(head)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(list(_COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setStretchLastSection(True)
        for col in (1, 2, 3, 4):
            self.table.setColumnWidth(col, 90)
        lay.addWidget(self.table)

        self._seen: set[int] = set()
        self._best_ms: int | None = None

    def add_lap(self, lap: dict[str, Any]) -> None:
        """Добавляет завершённый круг (словарь из SessionState/Storage)."""
        lap_num = int(lap.get("lap_num") or 0)
        if lap_num <= 0 or lap_num in self._seen:
            return
        self._seen.add(lap_num)

        time_ms = lap.get("lap_time_ms") or 0
        valid = bool(lap.get("valid", 1))
        row = 0
        self.table.insertRow(row)

        items = (
            str(lap_num),
            _fmt_ms(time_ms) if valid else f"{_fmt_ms(time_ms)} ✕",
            _fmt_ms(lap.get("sector1_ms")),
            _fmt_ms(lap.get("sector2_ms")),
            _fmt_ms(lap.get("sector3_ms")),
            str(lap.get("position") or "—"),
            name_of(TYRE_COMPOUND_VISUAL, lap.get("compound_visual")),
            f"+{int(lap.get('tyre_age_laps') or 0)}",
            f"{float(lap.get('fuel_at_start') or 0):.1f}",
            f"{int(lap.get('top_speed_kmh') or 0)}",
        )
        for col, text in enumerate(items):
            cell = QTableWidgetItem(text)
            cell.setFont(mono_font(10))
            cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if col == 1:
                if self._best_ms is None or (valid and time_ms and time_ms < self._best_ms):
                    if valid and time_ms:
                        self._best_ms = time_ms
                        cell.setForeground(QColor(COLORS["green"]))
                        self.best_lab.setText(f"лучший {_fmt_ms(time_ms)}")
                elif valid and time_ms and time_ms == self._best_ms:
                    cell.setForeground(QColor(COLORS["green"]))
                elif not valid:
                    cell.setForeground(QColor(COLORS["muted"]))
            if col in (2, 3, 4):
                # сектора — обычный цвет текста
                cell.setForeground(QColor(COLORS["text"]))
            self.table.setItem(row, col, cell)

        # держим не больше 200 строк
        while self.table.rowCount() > 200:
            self.table.removeRow(self.table.rowCount() - 1)

    def clear(self) -> None:
        self.table.setRowCount(0)
        self._seen.clear()
        self._best_ms = None
        self.best_lab.setText("")
