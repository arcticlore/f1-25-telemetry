"""Тёмная тема и стили Qt для F1 25 Telemetry Recorder."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

__all__ = ["COLORS", "apply_theme", "font", "mono_font", "accent_color"]


COLORS: dict[str, str] = {
    "bg": "#0d1117",
    "bg_alt": "#161b22",
    "panel": "#1c2128",
    "border": "#30363d",
    "text": "#e6edf3",
    "muted": "#8b949e",
    "accent": "#2f81f7",
    "green": "#3fb950",
    "yellow": "#d29922",
    "red": "#f85149",
    "orange": "#ff7b45",
    "cyan": "#39c5cf",
    "purple": "#a371f7",
}

# Цвета серий графиков (в порядке подключения)
SERIES_COLORS: tuple[str, ...] = (
    "#2f81f7", "#3fb950", "#ff7b45", "#d29922", "#a371f7", "#39c5cf", "#f85149",
)


def accent_color(name: str) -> str:
    """Цвет из палитры по имени."""
    return COLORS.get(name, COLORS["accent"])


def font(size: int = 12, *, bold: bool = False, mono: bool = False) -> QFont:
    """Шрифт интерфейса."""
    f = QFont("Sans Serif" if not mono else "Monospace", size)
    f.setBold(bold)
    return f


def mono_font(size: int = 12, *, bold: bool = False) -> QFont:
    """Моноширинный шрифт (числа, времена)."""
    return font(size, bold=bold, mono=True)


def _stylesheet() -> str:
    c = COLORS
    return f"""
    QWidget {{
        background-color: {c['bg']};
        color: {c['text']};
        font-size: 12px;
    }}
    QMainWindow, QDialog {{ background-color: {c['bg']}; }}
    QToolTip {{
        background-color: {c['panel']};
        color: {c['text']};
        border: 1px solid {c['border']};
        padding: 4px;
    }}
    QMenuBar {{ background-color: {c['bg_alt']}; border-bottom: 1px solid {c['border']}; }}
    QMenuBar::item:selected {{ background-color: {c['panel']}; }}
    QMenu {{ background-color: {c['panel']}; border: 1px solid {c['border']}; }}
    QMenu::item:selected {{ background-color: {c['accent']}; color: white; }}
    QTabWidget::pane {{
        border: 1px solid {c['border']};
        background-color: {c['bg']};
        top: -1px;
    }}
    QTabBar::tab {{
        background-color: {c['bg_alt']};
        color: {c['muted']};
        padding: 8px 18px;
        border: 1px solid {c['border']};
        border-bottom: none;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{ color: {c['text']}; background-color: {c['panel']}; }}
    QTabBar::tab:hover {{ color: {c['text']}; }}
    QTableWidget, QListWidget, QTreeView {{
        background-color: {c['bg_alt']};
        alternate-background-color: {c['panel']};
        border: 1px solid {c['border']};
        gridline-color: {c['border']};
        selection-background-color: {c['accent']};
        selection-color: white;
    }}
    QHeaderView::section {{
        background-color: {c['panel']};
        color: {c['muted']};
        border: none;
        border-right: 1px solid {c['border']};
        border-bottom: 1px solid {c['border']};
        padding: 4px 8px;
    }}
    QPushButton {{
        background-color: {c['panel']};
        border: 1px solid {c['border']};
        border-radius: 4px;
        padding: 6px 14px;
        color: {c['text']};
    }}
    QPushButton:hover {{ border-color: {c['accent']}; }}
    QPushButton:pressed {{ background-color: {c['accent']}; color: white; }}
    QPushButton:disabled {{ color: {c['muted']}; }}
    QPushButton[accent="true"] {{
        background-color: {c['accent']};
        color: white;
        border: none;
    }}
    QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
        background-color: {c['panel']};
        border: 1px solid {c['border']};
        border-radius: 4px;
        padding: 4px 8px;
        color: {c['text']};
    }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{
        background-color: {c['panel']};
        border: 1px solid {c['border']};
        selection-background-color: {c['accent']};
    }}
    QStatusBar {{ background-color: {c['bg_alt']}; border-top: 1px solid {c['border']}; }}
    QStatusBar::item {{ border: none; }}
    QGroupBox {{
        border: 1px solid {c['border']};
        border-radius: 6px;
        margin-top: 10px;
        padding-top: 12px;
        font-weight: bold;
        color: {c['muted']};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
    }}
    QScrollBar:vertical {{
        background: {c['bg']};
        width: 10px;
        margin: 0;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical {{
        background: {c['border']};
        border-radius: 5px;
        min-height: 24px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QSplitter::handle {{ background-color: {c['border']}; }}
    QLabel[role="banner"] {{
        background-color: {c['yellow']};
        color: #1a1a1a;
        font-weight: bold;
        padding: 8px;
        border-radius: 4px;
    }}
    QLabel[role="big"] {{ font-size: 44px; font-weight: bold; }}
    QLabel[role="mid"] {{ font-size: 20px; font-weight: bold; }}
    QLabel[role="muted"] {{ color: {c['muted']}; }}
    QFrame[role="panel"] {{
        background-color: {c['panel']};
        border: 1px solid {c['border']};
        border-radius: 6px;
    }}
    """


def apply_theme(app: QApplication) -> None:
    """Применяет тёмную палитру и QSS к приложению."""
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(COLORS["bg"]))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(COLORS["text"]))
    pal.setColor(QPalette.ColorRole.Base, QColor(COLORS["bg_alt"]))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(COLORS["panel"]))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(COLORS["panel"]))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(COLORS["text"]))
    pal.setColor(QPalette.ColorRole.Text, QColor(COLORS["text"]))
    pal.setColor(QPalette.ColorRole.Button, QColor(COLORS["panel"]))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(COLORS["text"]))
    pal.setColor(QPalette.ColorRole.BrightText, QColor(COLORS["red"]))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(COLORS["accent"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(COLORS["muted"]))
    app.setPalette(pal)
    app.setStyleSheet(_stylesheet())
