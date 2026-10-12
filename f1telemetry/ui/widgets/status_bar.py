"""Строка состояния: соединение, пакеты/с, потери, запись в БД."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QWidget

from ..theme import COLORS, font, mono_font

__all__ = ["StatusBarWidget"]


class StatusBarWidget(QWidget):
    """Нижняя панель диагностики приёма и записи."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 3, 8, 3)
        self._layout.setSpacing(14)

        self.conn = self._label("● Ожидание", COLORS["muted"])
        self.addr = self._label("", COLORS["muted"])
        self.rate = self._label("0.0 пак/с", COLORS["text"], mono=True)
        self.loss = self._label("потеря —", COLORS["muted"], mono=True)
        self.errors = self._label("ошибки 0", COLORS["muted"], mono=True)

        self.queue = QProgressBar()
        self.queue.setRange(0, 100)
        self.queue.setValue(0)
        self.queue.setFixedWidth(90)
        self.queue.setFixedHeight(10)
        self.queue.setTextVisible(False)
        self.queue.setToolTip("Очередь записи в SQLite")

        self.db = self._label("БД —", COLORS["muted"], mono=True)
        self.session = self._label("сессия —", COLORS["muted"], mono=True)

        for w in (self.conn, self.addr, self.session, self.rate, self.loss,
                  self.errors, self.queue, self.db):
            self._layout.addWidget(w)
        self._layout.addStretch(1)

    def _label(self, text: str, color: str, *, mono: bool = False) -> QLabel:
        lab = QLabel(text)
        lab.setFont(mono_font(11) if mono else font(11))
        lab.setStyleSheet(f"color: {color};")
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return lab

    # --- обновление --------------------------------------------------------

    def update_health(self, h: dict[str, Any]) -> None:
        """Обновляет панель по результату ``Recorder.health()``."""
        listener = h.get("listener")
        age = None
        snap_age = h.get("last_packet_age_s")
        if snap_age is not None:
            age = snap_age

        if listener is not None:
            self.addr.setText(f"UDP {listener.get('local_addr', '')}")
            alive = (listener.get("packets", 0) > 0) or (h.get("datagrams", 0) > 0)
        else:
            self.addr.setText("реплей фикстуры")
            alive = h.get("datagrams", 0) > 0

        if not alive:
            self._set_conn("○ Нет данных", COLORS["muted"])
        elif age is not None and age > 3.0:
            self._set_conn(f"⚠ Пауза {age:.1f} с", COLORS["yellow"])
        else:
            self._set_conn("● Запись", COLORS["green"])

        self.rate.setText(f"{self._pps(h):.1f} пак/с")
        lost = h.get("lost_frames", 0)
        total = max(1, h.get("datagrams", 0) + lost)
        pct = 100.0 * lost / total
        self.loss.setText(f"потеря {lost} ({pct:.1f}%)")
        self.loss.setStyleSheet(
            f"color: {COLORS['red'] if pct > 5 else COLORS['muted']};"
        )

        errs = h.get("parse_errors", 0) + h.get("format_errors", 0)
        self.errors.setText(f"ошибки {errs}")
        self.errors.setStyleSheet(
            f"color: {COLORS['yellow'] if errs else COLORS['muted']};"
        )

        queued = int(h.get("queued", 0))
        self.queue.setValue(min(100, int(queued / 20)))
        self.queue.setToolTip(f"Очередь записи: {queued} операций")
        self.db.setText(f"БД {h.get('ops', 0)} оп / {h.get('rows', 0)} стр")

    def _pps(self, h: dict[str, Any]) -> float:
        v = h.get("packets_per_sec")
        if isinstance(v, (int, float)) and v:
            return float(v)
        listener = h.get("listener") or {}
        return float(listener.get("packets", 0))

    def _set_conn(self, text: str, color: str) -> None:
        self.conn.setText(text)
        self.conn.setStyleSheet(f"color: {color}; font-weight: bold;")

    def set_session(self, uid: int | None, name: str = "") -> None:
        """Показывает UID текущей сессии."""
        if uid is None:
            self.session.setText("сессия —")
            return
        suffix = f" · {name}" if name else ""
        self.session.setText(f"сессия {uid}{suffix}")
