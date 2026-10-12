"""Вкладка «Настройки»: UDP, путь к БД, автостарт, LAN-адреса для игры."""

from __future__ import annotations

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...paths import AppConfig, lan_addresses
from ..theme import COLORS, font, mono_font

__all__ = ["SettingsView"]


class SettingsView(QWidget):
    """Настройки с мгновенным применением: каждый ввод шлёт ``settings_changed``.

    ``MainWindow`` при сигнале сохраняет конфиг и перезапускает запись,
    если изменились адрес/порт/БД.
    """

    settings_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._block = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        # --- UDP ---
        udp = QGroupBox("UDP — откуда слушать игру")
        form = QFormLayout(udp)
        self.host_edit = QLineEdit()
        self.host_edit.setToolTip(
            "0.0.0.0 — все интерфейсы; либо конкретный IP этой машины")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setToolTip("В игре: Настройки → UDP Telemetry → Target port")
        form.addRow("Адрес:", self.host_edit)
        form.addRow("Порт:", self.port_spin)
        lay.addWidget(udp)

        # --- БД ---
        db_box = QGroupBox("База данных")
        db_lay = QVBoxLayout(db_box)
        row = QHBoxLayout()
        self.db_edit = QLineEdit()
        self.db_edit.setReadOnly(True)
        self.db_edit.setFont(mono_font(10))
        btn_choose = QPushButton("Выбрать…")
        btn_open_folder = QPushButton("Открыть папку")
        row.addWidget(self.db_edit, 1)
        row.addWidget(btn_choose)
        row.addWidget(btn_open_folder)
        db_lay.addLayout(row)
        db_note = QLabel("После смены файла запись перезапускается автоматически.")
        db_note.setFont(font(9))
        db_note.setStyleSheet(f"color: {COLORS['muted']};")
        db_lay.addWidget(db_note)
        lay.addWidget(db_box)

        # --- Автостарт ---
        self.autostart_check = QCheckBox("Начинать запись при запуске приложения")
        lay.addWidget(self.autostart_check)

        # --- LAN ---
        lan = QGroupBox("Адрес для игры (когда игра на другой машине, например ПК)")
        lan_lay = QVBoxLayout(lan)
        self.lan_list = QListWidget()
        self.lan_list.setMaximumHeight(72)
        self.lan_list.setFont(mono_font(10))
        lan_lay.addWidget(self.lan_list)
        lan_row = QHBoxLayout()
        btn_copy = QPushButton("Копировать IP")
        lan_row.addWidget(btn_copy)
        lan_row.addStretch(1)
        lan_lay.addLayout(lan_row)
        hint = QLabel(
            "В игре: Настройки → UDP Telemetry → «UDP IP Address / Target IP» = "
            "один из адресов выше; Target port = 20777. Если пакеты не идут — "
            "откройте порт в防火илле (Linux): "
            "sudo firewall-cmd --permanent --add-port=20777/udp && "
            "sudo firewall-cmd --reload"
        )
        hint.setWordWrap(True)
        hint.setFont(font(9))
        hint.setStyleSheet(f"color: {COLORS['muted']};")
        lan_lay.addWidget(hint)
        lay.addWidget(lan)
        lay.addStretch(1)

        # --- сигналы ---
        self.host_edit.editingFinished.connect(self._emit_changed)
        self.port_spin.valueChanged.connect(self._emit_changed)
        self.autostart_check.toggled.connect(self._emit_changed)
        btn_choose.clicked.connect(self._choose_db)
        btn_open_folder.clicked.connect(self._open_db_folder)
        btn_copy.clicked.connect(self._copy_ip)

        self._refresh_lan()

    # --- API ---------------------------------------------------------------

    def set_config(self, cfg: AppConfig) -> None:
        """Заполняет поля из конфига (без эмиссии settings_changed)."""
        self._block = True
        try:
            self.host_edit.setText(cfg.host)
            self.port_spin.setValue(cfg.port)
            self.db_edit.setText(str(cfg.resolved_db()))
            self.autostart_check.setChecked(cfg.autostart)
        finally:
            self._block = False

    def collect(self) -> AppConfig:
        """Текущие значения полей как :class:`AppConfig`."""
        return AppConfig(
            host=self.host_edit.text().strip() or "0.0.0.0",
            port=int(self.port_spin.value()),
            db_path=self.db_edit.text().strip(),
            autostart=self.autostart_check.isChecked(),
        )

    # --- внутренности ------------------------------------------------------

    def _emit_changed(self, *_: object) -> None:
        if not self._block:
            self.settings_changed.emit()

    def _choose_db(self) -> None:
        from ...paths import default_db_path

        current = self.db_edit.text() or str(default_db_path())
        path, _ = QFileDialog.getSaveFileName(
            self, "Выбрать файл базы данных", current,
            "SQLite (*.sqlite *.sqlite3 *.db);;Все файлы (*)")
        if path:
            self.db_edit.setText(path)
            self._emit_changed()

    def _open_db_folder(self) -> None:
        from pathlib import Path

        text = self.db_edit.text().strip()
        if text:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(text).parent)))

    def _copy_ip(self) -> None:
        item = self.lan_list.currentItem() or (
            self.lan_list.item(0) if self.lan_list.count() else None)
        if item is not None:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(item.text())

    def _refresh_lan(self) -> None:
        self.lan_list.clear()
        for ip in lan_addresses():
            self.lan_list.addItem(ip)
        if self.lan_list.count() == 0:
            self.lan_list.addItem("(нет сети — адреса не найдены)")
