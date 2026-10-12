"""Приборная панель: скорость, передача, RPM/рев-лайты, шины, топливо, ERS."""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ...state import LiveSnapshot
from ...constants import TYRE_COMPOUND_SHORT
from ..theme import COLORS, font, mono_font

__all__ = ["LiveDash"]

WHEEL_ORDER = (0, 1, 2, 3)   # RL, RR, FL, FR
WHEEL_LABELS = ("RL", "RR", "FL", "FR")


def _temp_colour(t: float) -> str:
    """Цвет шины по температуре °C."""
    if t <= 0:
        return COLORS["muted"]
    if t < 70:
        return COLORS["accent"]
    if t < 95:
        return COLORS["green"]
    if t < 110:
        return COLORS["yellow"]
    return COLORS["red"]


class SpeedDial(QWidget):
    """Круговой индикатор скорости."""

    def __init__(self, max_speed: float = 360.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.max_speed = max_speed
        self.speed = 0.0
        self._value = QLabel("0", self)
        self._value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._value.setFont(font(34, bold=True, mono=True))
        self._value.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
        self._unit = QLabel("км/ч", self)
        self._unit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._unit.setFont(font(10))
        self._unit.setStyleSheet(f"color: {COLORS['muted']}; background: transparent;")
        self.setMinimumSize(180, 180)

    def set_speed(self, value: float) -> None:
        self.speed = max(0.0, value)
        self._value.setText(f"{int(round(self.speed))}")
        self.update()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        side = min(self.width(), self.height())
        cx, cy = self.width() / 2, self.height() / 2
        self._value.setGeometry(int(cx - side * 0.35), int(cy - side * 0.22),
                                int(side * 0.7), int(side * 0.3))
        self._unit.setGeometry(int(cx - side * 0.2), int(cy + side * 0.1),
                               int(side * 0.4), 18)

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        rect = QRectF((self.width() - side) / 2 + 8, (self.height() - side) / 2 + 8,
                      side - 16, side - 16)
        start, span = 225, -270   # дуга снизу-слева по часовой

        pen_bg = QPen(QColor(COLORS["border"]), 10, c=Qt.PenCapStyle.RoundCap)
        p.setPen(pen_bg)
        p.drawArc(rect, int(start * 16), int(span * 16))

        frac = max(0.0, min(1.0, self.speed / self.max_speed))
        if frac > 0:
            pen_fg = QPen(QColor(COLORS["accent"]), 10, c=Qt.PenCapStyle.RoundCap)
            p.setPen(pen_fg)
            p.drawArc(rect, int(start * 16), int(span * frac * 16))

        # красная зона
        if self.max_speed > 0:
            red_from = 0.92
            if frac > red_from:
                pen_red = QPen(QColor(COLORS["red"]), 10, c=Qt.PenCapStyle.RoundCap)
                p.setPen(pen_red)
                p.drawArc(rect, int(start * 16), int(span * frac * 16))
        p.end()


class RevLights(QWidget):
    """Полоса рев-лайтов по ``m_revLightsPercent``."""

    SEGMENTS = 15

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.percent = 0
        self.setMinimumHeight(22)

    def set_percent(self, value: int) -> None:
        self.percent = max(0, min(100, int(value)))
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        lit = int(round(self.percent / 100.0 * self.SEGMENTS))
        w = (self.width() - (self.SEGMENTS - 1) * 3) / self.SEGMENTS
        for i in range(self.SEGMENTS):
            x = i * (w + 3)
            if i < lit:
                if i < 8:
                    col = COLORS["green"]
                elif i < 12:
                    col = COLORS["yellow"]
                else:
                    col = COLORS["red"]
            else:
                col = COLORS["border"]
            p.setBrush(QColor(col))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(x, 4, w, self.height() - 8), 2, 2)
        p.end()


class PedalBar(QWidget):
    """Вертикальная шкала газа/тормоза."""

    def __init__(self, label: str, colour: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.value = 0.0
        self.colour = colour
        self._label = QLabel(label, self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)
        self._label.setFont(font(9))
        self._label.setStyleSheet(f"color: {COLORS['muted']}; background: transparent;")
        self.setMinimumWidth(26)

    def set_value(self, value: float) -> None:
        self.value = max(0.0, min(1.0, value))
        self.update()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._label.setGeometry(0, self.height() - 16, self.width(), 16)

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        margin = 4
        body = QRectF(margin, margin, self.width() - 2 * margin, self.height() - 22)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(COLORS["border"]))
        p.drawRoundedRect(body, 3, 3)
        h = body.height() * self.value
        if h > 0:
            p.setBrush(QColor(self.colour))
            p.drawRoundedRect(QRectF(body.x(), body.bottom() - h, body.width(), h), 3, 3)
        p.end()


class _Metric(QWidget):
    """Строка «значение + подпись»."""

    def __init__(self, caption: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.value = QLabel("—")
        self.value.setFont(mono_font(16, bold=True))
        self.value.setStyleSheet(f"color: {COLORS['text']};")
        self.caption = QLabel(caption)
        self.caption.setFont(font(9))
        self.caption.setStyleSheet(f"color: {COLORS['muted']};")
        lay.addWidget(self.value)
        lay.addWidget(self.caption)

    def set(self, text: str, colour: str | None = None) -> None:
        self.value.setText(text)
        self.value.setStyleSheet(
            f"color: {colour or COLORS['text']}; font-weight: bold;"
        )


class _Bar(QWidget):
    """Горизонтальная шкала с подписью (топливо/ERS)."""

    def __init__(self, caption: str, colour: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.caption = QLabel(caption)
        self.caption.setFont(font(9))
        self.caption.setStyleSheet(f"color: {COLORS['muted']};")
        self.caption.setFixedWidth(38)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(12)
        self.bar.setStyleSheet(f"""
            QProgressBar {{ background-color: {COLORS['border']}; border: none; border-radius: 3px; }}
            QProgressBar::chunk {{ background-color: {colour}; border-radius: 3px; }}
        """)
        self.text = QLabel("")
        self.text.setFont(mono_font(10))
        self.text.setStyleSheet(f"color: {COLORS['text']};")
        self.text.setFixedWidth(88)
        lay.addWidget(self.caption)
        lay.addWidget(self.bar, 1)
        lay.addWidget(self.text)

    def set(self, fraction: float, text: str) -> None:
        self.bar.setValue(int(max(0.0, min(1.0, fraction)) * 1000))
        self.text.setText(text)


class _TyreCell(QWidget):
    """Одна шина: температура, износ, давление."""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(1)
        self.header = QLabel(name)
        self.header.setFont(font(9, bold=True))
        self.header.setStyleSheet(f"color: {COLORS['muted']};")
        self.temp = QLabel("—°")
        self.temp.setFont(mono_font(15, bold=True))
        self.temp.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.extra = QLabel("")
        self.extra.setFont(mono_font(9))
        self.extra.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.extra.setStyleSheet(f"color: {COLORS['muted']};")
        lay.addWidget(self.header)
        lay.addWidget(self.temp)
        lay.addWidget(self.extra)
        self.setStyleSheet("""
            _TyreCell { background-color: %s; border: 1px solid %s; border-radius: 6px; }
        """ % (COLORS["bg_alt"], COLORS["border"]))

    def set(self, temp_c: int, wear_pct: float, press_psi: float) -> None:
        self.temp.setText(f"{int(temp_c)}°")
        self.temp.setStyleSheet(
            f"color: {_temp_colour(temp_c)}; font-weight: bold;"
        )
        self.extra.setText(f"{wear_pct:.0f}%  {press_psi:.1f}")


class LiveDash(QWidget):
    """Живая приборная панель игрока."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(12)

        # --- левая колонка: скорость + педали ---
        left = QVBoxLayout()
        left.setSpacing(6)
        self.dial = SpeedDial()
        left.addWidget(self.dial, 1)

        pedals = QHBoxLayout()
        pedals.setSpacing(8)
        self.throttle_bar = PedalBar("газ", COLORS["green"])
        self.brake_bar = PedalBar("тормоз", COLORS["red"])
        pedals.addWidget(self.throttle_bar)
        pedals.addWidget(self.brake_bar)
        pedals.addSpacing(6)

        self.gear = QLabel("N")
        self.gear.setFont(font(40, bold=True, mono=True))
        self.gear.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.gear.setStyleSheet(f"color: {COLORS['text']};")
        self.gear.setMinimumWidth(64)
        pedals.addWidget(self.gear)

        self.rpm_lab = QLabel("0 об/мин")
        self.rpm_lab.setFont(mono_font(12))
        self.rpm_lab.setStyleSheet(f"color: {COLORS['muted']};")
        pedals.addWidget(self.rpm_lab, 1)
        left.addLayout(pedals)

        self.rev = RevLights()
        left.addWidget(self.rev)
        root.addLayout(left, 3)

        # --- центр: круг/позиция/дельты ---
        center = QVBoxLayout()
        center.setSpacing(8)
        self.header = QLabel("Ожидание данных…")
        self.header.setFont(font(13, bold=True))
        self.header.setStyleSheet(f"color: {COLORS['muted']};")
        center.addWidget(self.header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)
        self.m_lap = _Metric("КРУГ")
        self.m_pos = _Metric("ПОЗИЦИЯ")
        self.m_last = _Metric("ПОСЛЕДНИЙ")
        self.m_best = _Metric("ЛУЧШИЙ")
        self.m_theo = _Metric("ТЕОРЕТИЧ.")
        self.m_sector = _Metric("СЕКТОР")
        grid.addWidget(self.m_lap, 0, 0)
        grid.addWidget(self.m_pos, 0, 1)
        grid.addWidget(self.m_sector, 0, 2)
        grid.addWidget(self.m_last, 1, 0)
        grid.addWidget(self.m_best, 1, 1)
        grid.addWidget(self.m_theo, 1, 2)
        center.addLayout(grid)

        deltas = QHBoxLayout()
        deltas.setSpacing(18)
        self.d_front = _Metric("дельта впереди")
        self.d_leader = _Metric("дельта лидера")
        self.d_prog = _Metric("прогресс круга")
        deltas.addWidget(self.d_front)
        deltas.addWidget(self.d_leader)
        deltas.addWidget(self.d_prog)
        center.addLayout(deltas)

        bars = QVBoxLayout()
        bars.setSpacing(4)
        self.fuel_bar = _Bar("топл.", COLORS["cyan"])
        self.ers_bar = _Bar("ERS", COLORS["purple"])
        bars.addWidget(self.fuel_bar)
        bars.addWidget(self.ers_bar)
        center.addLayout(bars)
        center.addStretch(1)
        root.addLayout(center, 4)

        # --- правая колонка: шины ---
        right = QVBoxLayout()
        right.setSpacing(6)
        cap = QLabel("ШИНЫ")
        cap.setFont(font(10, bold=True))
        cap.setStyleSheet(f"color: {COLORS['muted']};")
        right.addWidget(cap)

        tyres = QGridLayout()
        tyres.setSpacing(6)
        self.tyres = [_TyreCell(name) for name in WHEEL_LABELS]
        # порядок: RL RR / FL FR
        tyres.addWidget(self.tyres[0], 0, 0)
        tyres.addWidget(self.tyres[1], 0, 1)
        tyres.addWidget(self.tyres[2], 1, 0)
        tyres.addWidget(self.tyres[3], 1, 1)
        right.addLayout(tyres)

        self.compound = QLabel("—")
        self.compound.setFont(font(12, bold=True))
        self.compound.setStyleSheet(f"color: {COLORS['text']};")
        right.addWidget(self.compound)

        self.info = QLabel("")
        self.info.setFont(font(10))
        self.info.setStyleSheet(f"color: {COLORS['muted']};")
        right.addWidget(self.info)
        right.addStretch(1)
        root.addLayout(right, 2)

    # --- обновление --------------------------------------------------------

    def update_snapshot(self, snap: LiveSnapshot) -> None:
        """Перерисовывает панель по свежему срезу."""
        self.dial.set_speed(snap.speed_kmh)
        self.throttle_bar.set_value(snap.throttle)
        self.brake_bar.set_value(snap.brake)
        self.gear.setText(_gear_text(snap.gear))
        self.rpm_lab.setText(f"{snap.rpm:,} об/мин".replace(",", " "))
        self.rev.set_percent(snap.rev_lights_pct)

        name = snap.driver_name or "игрок"
        track = snap.track_name or f"трасса #{snap.track_id}"
        stype = snap.session_type_name or ""
        self.header.setText(f"{name} · {track} · {stype}".replace(" ·  ", " · "))
        self.header.setStyleSheet(f"color: {COLORS['text']};")

        self.m_lap.set(str(snap.lap_num or "—"))
        self.m_pos.set(str(snap.position or "—"))
        self.m_sector.set(str(snap.sector or "—"))
        self.m_last.set(_fmt_ms(snap.last_lap_ms), _delta_colour(snap.last_lap_ms, snap.best_lap_ms))
        self.m_best.set(_fmt_ms(snap.best_lap_ms), COLORS["green"] if snap.best_lap_ms else None)
        self.m_theo.set(_fmt_ms(snap.theoretical_ms),
                        COLORS["cyan"] if snap.theoretical_ms else None)

        self.d_front.set(_fmt_delta(snap.delta_front_ms), _signed_colour(snap.delta_front_ms))
        self.d_leader.set(_fmt_delta(snap.delta_leader_ms), _signed_colour(snap.delta_leader_ms))
        self.d_prog.set(f"{snap.lap_progress * 100:.1f}%")

        cap = snap.fuel_capacity_kg or 1.0
        self.fuel_bar.set(snap.fuel_kg / cap, f"{snap.fuel_kg:.1f} кг")
        self.ers_bar.set(snap.ers_store_j / max(1.0, snap.ers_capacity_j),
                         f"{snap.ers_store_j / 1e6:.2f} МДж")

        for i in WHEEL_ORDER:
            self.tyres[i].set(
                snap.tyre_surf_c[i], snap.tyre_wear_pct[i], snap.tyre_press_psi[i]
            )
        short = TYRE_COMPOUND_SHORT.get(snap.tyre_compound_visual, "")
        self.compound.setText(
            f"{short or snap.tyre_visual_name} · {snap.tyre_age_laps} кр"
        )
        pit = "ПИТ" if snap.pit_status else ""
        drs = "DRS" if snap.drs else ""
        limiter = "ЛИМИТЕР" if snap.pit_limiter else ""
        flags = " · ".join(x for x in (pit, drs, limiter) if x)
        self.info.setText(flags)


def _gear_text(gear: int) -> str:
    if gear > 0:
        return str(gear)
    if gear < 0:
        return "R"
    return "N"


def _fmt_ms(ms: int | None) -> str:
    if not ms:
        return "—"
    ms = int(ms)
    return f"{ms // 60000}:{(ms % 60000) / 1000:06.3f}"


def _fmt_delta(ms: int | None) -> str:
    if not ms:
        return "—"
    return f"{ms / 1000.0:+.3f}"


def _signed_colour(ms: int | None) -> str:
    if not ms:
        return COLORS["muted"]
    if ms > 0:
        return COLORS["red"] if ms > 300 else COLORS["yellow"]
    return COLORS["green"]


def _delta_colour(value: int | None, best: int | None) -> str | None:
    if not value or not best:
        return None
    diff = value - best
    if diff <= 0:
        return COLORS["green"]
    if diff < 300:
        return COLORS["yellow"]
    return COLORS["red"]


__all__.append("SpeedDial")
