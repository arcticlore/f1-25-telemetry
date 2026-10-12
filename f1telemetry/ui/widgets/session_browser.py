"""Браузер сохранённых сессий."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...storage import Storage
from ..theme import COLORS, font, mono_font

__all__ = ["SessionBrowser"]

_COLS = ("Сессия", "Трасса", "Тип", "Погода", "Старт (UTC)", "Кругов", "Машин")


class SessionBrowser(QWidget):
    """Список сессий в БД + кнопки «Открыть»/«Удалить»."""

    session_opened = Signal(int)   # session_uid
    session_deleted = Signal(int)  # session_uid

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        head = QHBoxLayout()
        self.title = QLabel("Сессии")
        self.title.setFont(font(13, bold=True))
        self.hint = QLabel("выберите строку и нажмите «Открыть»")
        self.hint.setFont(font(10))
        self.hint.setStyleSheet(f"color: {COLORS['muted']};")
        self.btn_open = QPushButton("Открыть")
        self.btn_open.setProperty("accent", "true")
        self.btn_open.setEnabled(False)
        self.btn_delete = QPushButton("Удалить")
        self.btn_delete.setEnabled(False)
        self.btn_refresh = QPushButton("Обновить")
        head.addWidget(self.title)
        head.addWidget(self.hint)
        head.addStretch(1)
        head.addWidget(self.btn_refresh)
        head.addWidget(self.btn_delete)
        head.addWidget(self.btn_open)
        lay.addLayout(head)

        self.table = QTableWidget(0, len(_COLS))
        self.table.setHorizontalHeaderLabels(list(_COLS))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setStretchLastSection(True)
        lay.addWidget(self.table)

        self._uids: list[int] = []
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.btn_open.clicked.connect(self._on_open)
        self.btn_delete.clicked.connect(self._on_delete)
        self.btn_refresh.clicked.connect(self.refresh)

    # --- API ---------------------------------------------------------------

    def set_db_path(self, path: str | Path | None) -> None:
        """Запоминает путь к БД для перечитывания."""
        self._db_path = Path(path) if path else None
        self.refresh()

    def refresh(self) -> None:
        """Перечитывает список сессий из БД."""
        self.table.setRowCount(0)
        self._uids = []
        if getattr(self, "_db_path", None) is None or not self._db_path.exists():
            return
        try:
            st = Storage(self._db_path, read_only=True)
            try:
                sessions = st.list_sessions()
            finally:
                st.close()
        except Exception:  # noqa: BLE001 — БД может быть закрыта/заблокирована
            return
        for s in sessions:
            self._add_row(s)

    # --- внутренности ------------------------------------------------------

    def _add_row(self, s: Any) -> None:
        get = s.get if isinstance(s, dict) else lambda k, d=None: getattr(s, k, d)
        uid = int(get("session_uid") or 0)
        laps = get("total_laps")
        laps_txt = str(laps) if laps else "—"
        vals = (
            str(uid),
            get("track_name") or f"#{get('track_id')}",
            get("session_type_name") or f"type {get('session_type')}",
            str(get("weather") if get("weather") is not None else "—"),
            (get("started_at_utc") or "")[:19],
            laps_txt,
            str(get("num_cars") or "—"),
        )
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, text in enumerate(vals):
            cell = QTableWidgetItem(text)
            cell.setFont(mono_font(10) if col in (0, 4) else font(10))
            cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, col, cell)
        self._uids.append(uid)

    def _selected_uid(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        idx = rows[0].row()
        if 0 <= idx < len(self._uids):
            return self._uids[idx]
        return None

    def _on_selection(self) -> None:
        has = self._selected_uid() is not None
        self.btn_open.setEnabled(has)
        self.btn_delete.setEnabled(has)

    def _on_open(self) -> None:
        uid = self._selected_uid()
        if uid is not None:
            self.session_opened.emit(uid)

    def _on_delete(self) -> None:
        uid = self._selected_uid()
        db_path = getattr(self, "_db_path", None)
        if uid is None or db_path is None:
            return
        answer = QMessageBox.question(
            self, "Удалить сессию",
            f"Удалить сессию {uid} и все её данные (круги, телеметрия, "
            "события) из базы?\nДействие необратимо.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            st = Storage(db_path)
            try:
                st.delete_session(uid)
            finally:
                st.close()
        except OSError:
            return
        self.refresh()
        self.session_deleted.emit(uid)
