"""Вкладка Engineer: крупные панели «от стенда» — только вывод, без управления.

Источник данных: live-срез :class:`~f1telemetry.state.LiveSnapshot`
(симулятор или игра) либо записанная сессия из БД
(:meth:`Storage.engineer_summary`) — live с приоритетом, пока идут пакеты.

Звуковые алерты (луч/TMPT, дождь по прогнозу, шины, топливо, DRS, SC) —
best-effort через :class:`~f1telemetry.ui.sounds.AlertSounds`; эмуляция
нажатий в игру не выполняется и не поддерживается.
"""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ...constants import TYRE_COMPOUND_SHORT, TYRE_COMPOUND_VISUAL, name_of
from ...state import LiveSnapshot
from ...storage import Storage
from ..sounds import AlertSounds
from ..theme import COLORS, font, mono_font

log = logging.getLogger(__name__)

# пороги алертов
TYRE_ALERT_WEAR_PCT = 80.0
TYRE_ALERT_WEAR_CLEAR_PCT = 70.0   # гистерезис
FUEL_ALERT_LAPS = 1.0
RAIN_ALERT_PCT = 40
RAIN_ALERT_HORIZON_MIN = 90        # смотрим прогноз на ближайшие N минут
ALERT_COOLDOWN_S = 30.0

WEATHER_RU: dict[int, str] = {
    0: "Ясно", 1: "Лёгкая облачность", 2: "Пасмурно",
    3: "Небольшой дождь", 4: "Сильный дождь", 5: "Гроза",
}
SC_RU: dict[int, str] = {0: "Нет", 1: "Полный SC", 2: "VSC", 3: "Формация"}
SC_COLOR: dict[int, str] = {
    0: COLORS["muted"], 1: COLORS["orange"], 2: COLORS["yellow"],
    3: COLORS["cyan"],
}
FLAG_RU: dict[int, str] = {
    -1: "?", 0: "—", 1: "зелёный", 2: "синий", 3: "жёлтый",
}
FLAG_COLOR: dict[int, str] = {
    -1: COLORS["muted"], 0: COLORS["muted"], 1: COLORS["green"],
    2: COLORS["accent"], 3: COLORS["yellow"],
}
ERS_MODE_RU: dict[int, str] = {0: "OFF", 1: "MEDIUM", 2: "HOTLAP", 3: "OVERTAKE"}
COMPOUND_COLOR: dict[int, str] = {
    16: COLORS["red"],      # Soft
    17: COLORS["yellow"],   # Medium
    18: "#d0d7de",          # Hard
    7: COLORS["green"],     # Inter
    8: COLORS["accent"],    # Wet
}

# индексы колёс в массивах игры: 0=RL, 1=RR, 2=FL, 3=FR
_CORNERS: tuple[tuple[str, int], ...] = (("FL", 2), ("FR", 3), ("RL", 0), ("RR", 1))


def _wear_color(pct: float) -> str:
    if pct >= TYRE_ALERT_WEAR_PCT:
        return COLORS["red"]
    if pct >= 50.0:
        return COLORS["yellow"]
    return COLORS["green"]


def _panel(title: str) -> tuple[QFrame, QVBoxLayout]:
    """Панель с заголовком в стиле тёмной темы."""
    fr = QFrame()
    fr.setStyleSheet(
        f"QFrame {{ background: {COLORS['panel']};"
        f" border: 1px solid {COLORS['border']}; border-radius: 8px; }}"
    )
    lay = QVBoxLayout(fr)
    lay.setContentsMargins(14, 10, 14, 12)
    lay.setSpacing(6)
    t = QLabel(title)
    t.setFont(font(11, bold=True))
    t.setStyleSheet(f"color: {COLORS['muted']}; letter-spacing: 2px;")
    lay.addWidget(t)
    return fr, lay


def _big(text: str = "—", size: int = 40, *, color: str | None = None,
         mono: bool = True) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(mono_font(size, bold=True) if mono else font(size, bold=True))
    lbl.setStyleSheet(f"color: {color or COLORS['text']};")
    return lbl


class EngineerTab(QWidget):
    """Крупная «инженерная» панель: шины, топливо, ERS, статус, дельты, погода."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        super().__init__()
        self._db_path = Path(db_path) if db_path else None
        self._sounds = AlertSounds()
        self._sound_on = True
        self._alert_ts: dict[str, float] = {}
        self._tyres_alerted = False
        self._historical_uid: int | None = None
        self._live_until = 0.0
        self._rendered_uid: int | None = None
        self._build()

    # --- сборка UI ----------------------------------------------------------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("ENGINEER")
        title.setFont(font(16, bold=True))
        title.setStyleSheet(f"color: {COLORS['accent']}; letter-spacing: 3px;")
        head.addWidget(title)
        self._source_lbl = QLabel("нет данных")
        self._source_lbl.setFont(font(11))
        self._source_lbl.setStyleSheet(f"color: {COLORS['muted']};")
        head.addWidget(self._source_lbl)
        head.addStretch(1)
        self._mute = QCheckBox("Звук")
        self._mute.setChecked(True)
        self._mute.toggled.connect(self._on_mute)
        head.addWidget(self._mute)
        root.addLayout(head)

        self._alert_lbl = QLabel("")
        self._alert_lbl.setFont(font(15, bold=True))
        self._alert_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._alert_lbl.setStyleSheet(
            f"background: {COLORS['red']}; color: #0d1117;"
            " border-radius: 6px; padding: 6px;"
        )
        self._alert_lbl.hide()
        root.addWidget(self._alert_lbl)

        grid = QGridLayout()
        grid.setSpacing(8)
        panels = (
            (self._build_tyres(), 0, 0),
            (self._build_fuel(), 0, 1),
            (self._build_status(), 0, 2),
            (self._build_deltas(), 1, 0),
            (self._build_ers(), 1, 1),
            (self._build_weather(), 1, 2),
        )
        for (fr, _lay), row, col in panels:
            grid.addWidget(fr, row, col)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        grid.setRowStretch(0, 3)
        grid.setRowStretch(1, 2)
        root.addLayout(grid, 1)

    def _build_tyres(self) -> tuple[QFrame, QVBoxLayout]:
        fr, lay = _panel("ШИНЫ")
        top = QHBoxLayout()
        self._compound_badge = QLabel("—")
        self._compound_badge.setFont(font(18, bold=True))
        self._compound_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._compound_badge.setMinimumWidth(110)
        self._compound_badge.setStyleSheet(
            f"background: {COLORS['bg_alt']}; color: {COLORS['text']};"
            " border-radius: 6px; padding: 6px 10px;"
        )
        top.addWidget(self._compound_badge)
        age_box = QVBoxLayout()
        age_box.addWidget(self._small("возраст комплекта"))
        self._age_lbl = _big("—", 32)
        age_box.addWidget(self._age_lbl)
        top.addLayout(age_box, 1)
        lay.addLayout(top)

        corners = QGridLayout()
        corners.setSpacing(6)
        self._wear_lbls: dict[str, QLabel] = {}
        self._temp_lbls: dict[str, QLabel] = {}
        for n, (name, _idx) in enumerate(_CORNERS):
            cfr = QFrame()
            cfr.setStyleSheet(
                f"QFrame {{ background: {COLORS['bg_alt']}; border-radius: 6px; }}"
            )
            cl = QVBoxLayout(cfr)
            cl.setContentsMargins(8, 6, 8, 6)
            cl.setSpacing(2)
            nm = QLabel(name)
            nm.setFont(font(11, bold=True))
            nm.setStyleSheet(f"color: {COLORS['muted']};")
            cl.addWidget(nm)
            wear = _big("—", 28)
            self._wear_lbls[name] = wear
            cl.addWidget(wear)
            temp = QLabel("—")
            temp.setFont(mono_font(12))
            temp.setStyleSheet(f"color: {COLORS['muted']};")
            self._temp_lbls[name] = temp
            cl.addWidget(temp)
            corners.addWidget(cfr, n // 2, n % 2)
        lay.addLayout(corners)
        lay.addStretch(1)
        return fr, lay

    def _build_fuel(self) -> tuple[QFrame, QVBoxLayout]:
        fr, lay = _panel("ТОПЛИВО")
        self._fuel_laps_lbl = _big("—", 44)
        lay.addWidget(self._fuel_laps_lbl)
        lay.addWidget(self._small("кругов хватит"))
        self._fuel_bar = QProgressBar()
        self._fuel_bar.setRange(0, 100)
        self._fuel_bar.setTextVisible(False)
        self._fuel_bar.setFixedHeight(14)
        lay.addWidget(self._fuel_bar)
        self._fuel_kg_lbl = QLabel("—")
        self._fuel_kg_lbl.setFont(mono_font(14))
        self._fuel_kg_lbl.setStyleSheet(f"color: {COLORS['muted']};")
        lay.addWidget(self._fuel_kg_lbl)

        pit_fr = QFrame()
        pit_fr.setStyleSheet(
            f"QFrame {{ background: {COLORS['bg_alt']}; border-radius: 6px; }}"
        )
        pit_lay = QVBoxLayout(pit_fr)
        pit_lay.setContentsMargins(10, 8, 10, 8)
        pit_lay.setSpacing(2)
        pit_lay.addWidget(self._small("ПИТ-ОКНО"))
        self._pit_lbl = _big("— / —", 26)
        pit_lay.addWidget(self._pit_lbl)
        pit_lay.addWidget(self._small("идеальный / крайний круг"))
        lay.addWidget(pit_fr)
        lay.addStretch(1)
        return fr, lay

    def _build_status(self) -> tuple[QFrame, QVBoxLayout]:
        fr, lay = _panel("СТАТУС")
        self._sc_badge = QLabel("SC: —")
        self._sc_badge.setFont(font(20, bold=True))
        self._sc_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sc_badge.setStyleSheet(
            f"background: {COLORS['bg_alt']}; color: {COLORS['muted']};"
            " border-radius: 6px; padding: 8px;"
        )
        lay.addWidget(self._sc_badge)
        self._flag_badge = QLabel("флаг: —")
        self._flag_badge.setFont(font(14, bold=True))
        self._flag_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._flag_badge.setStyleSheet(
            f"background: {COLORS['bg_alt']}; color: {COLORS['muted']};"
            " border-radius: 6px; padding: 4px;"
        )
        lay.addWidget(self._flag_badge)
        pos_row = QHBoxLayout()
        self._pos_lbl = _big("—", 34)
        pos_box = QVBoxLayout()
        pos_box.addWidget(self._small("позиция"))
        pos_box.addWidget(self._pos_lbl)
        pos_row.addLayout(pos_box)
        self._lap_lbl = _big("—", 34)
        lap_box = QVBoxLayout()
        lap_box.addWidget(self._small("круг"))
        lap_box.addWidget(self._lap_lbl)
        pos_row.addLayout(lap_box)
        lay.addLayout(pos_row)
        self._time_lbl = _big("--:--", 26)
        lay.addWidget(self._time_lbl)
        lay.addWidget(self._small("остаток сессии"))
        lay.addStretch(1)
        return fr, lay

    def _build_deltas(self) -> tuple[QFrame, QVBoxLayout]:
        fr, lay = _panel("ДЕЛЬТЫ")
        lay.addWidget(self._small("до впереди"))
        self._delta_front_lbl = _big("—", 44)
        lay.addWidget(self._delta_front_lbl)
        lay.addWidget(self._small("до лидера"))
        self._delta_leader_lbl = _big("—", 34)
        lay.addWidget(self._delta_leader_lbl)
        lay.addStretch(1)
        return fr, lay

    def _build_ers(self) -> tuple[QFrame, QVBoxLayout]:
        fr, lay = _panel("ERS")
        self._ers_bar = QProgressBar()
        self._ers_bar.setRange(0, 100)
        self._ers_bar.setTextVisible(False)
        self._ers_bar.setFixedHeight(18)
        lay.addWidget(self._ers_bar)
        self._ers_pct_lbl = _big("—", 40)
        lay.addWidget(self._ers_pct_lbl)
        lay.addWidget(self._small("запас энергии"))
        self._ers_mode_lbl = _big("—", 22, mono=False)
        lay.addWidget(self._ers_mode_lbl)
        lay.addStretch(1)
        return fr, lay

    def _build_weather(self) -> tuple[QFrame, QVBoxLayout]:
        fr, lay = _panel("ПОГОДА")
        now_row = QHBoxLayout()
        self._weather_lbl = QLabel("—")
        self._weather_lbl.setFont(font(18, bold=True))
        now_row.addWidget(self._weather_lbl, 1)
        self._temps_lbl = _big("—", 22)
        now_row.addWidget(self._temps_lbl)
        lay.addLayout(now_row)
        lay.addWidget(self._small("трасса / воздух °C"))
        lay.addWidget(self._small("ПРОГНОЗ"))
        self._forecast_lbls: list[QLabel] = []
        for _i in range(6):
            row = QLabel("—")
            row.setFont(mono_font(13))
            row.setStyleSheet(f"color: {COLORS['text']};")
            self._forecast_lbls.append(row)
            lay.addWidget(row)
        lay.addStretch(1)
        return fr, lay

    @staticmethod
    def _small(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(font(11))
        lbl.setStyleSheet(f"color: {COLORS['muted']};")
        return lbl

    # --- публичный API ------------------------------------------------------

    def set_db_path(self, db_path: str | Path) -> None:
        """Смена пути к БД (после настроек)."""
        self._db_path = Path(db_path)

    def set_historical_session(self, session_uid: int) -> None:
        """Рисует записанную сессию из БД (когда нет живых пакетов)."""
        if self._db_path is None:
            return
        st = Storage(self._db_path, read_only=True)
        try:
            summary = st.engineer_summary(session_uid)
        except Exception:  # noqa: BLE001
            log.warning("engineer: не удалось прочитать сессию %s",
                        session_uid, exc_info=True)
            return
        finally:
            st.close()
        if not summary:
            return
        self._historical_uid = session_uid
        snap = self._snapshot_from_summary(session_uid, summary)
        self._rendered_uid = None
        self._source_lbl.setText(f"сессия {session_uid}")
        self._render(snap)

    def update_snapshot(self, snap: LiveSnapshot) -> None:
        """Вызывается GUI-таймером; live имеет приоритет над историей."""
        fresh = (
            snap.session_uid is not None
            and snap.last_packet_age_s < 3.0
        )
        if fresh:
            self._live_until = time.monotonic() + 3.0
            if snap.session_uid != self._rendered_uid:
                self._alert_ts.clear()
                self._tyres_alerted = False
                self._alert_lbl.hide()
            self._source_lbl.setText(
                f"LIVE · {snap.track_name or ''} {snap.session_type_name or ''}".strip()
                if snap.track_name else "LIVE"
            )
            self._render(snap)
            self._check_alerts(snap)
        elif (
            time.monotonic() >= self._live_until
            and self._historical_uid is not None
            and self._rendered_uid != self._historical_uid
        ):
            # live закончился — вернуться к выбранной записи
            self.set_historical_session(self._historical_uid)

    def on_event(self, code: str, data: dict[str, Any]) -> None:
        """Звуковые/визуальные алерты по событиям (вызов из MainWindow)."""
        if code == "TMPT":
            name = str(data.get("name") or f"#{data.get('vehicle_idx', '?')}")
            self._fire("tmpt", f"Команда в питe: {name}")
        elif code == "DRSE":
            self._fire("drs_on", "DRS разрешён", cooldown=2.0)
        elif code == "DRSD":
            self._fire("drs_off", "DRS отключён", cooldown=2.0)
        elif code == "SCAR":
            st = int(data.get("safety_car_type") or 0)
            self._fire("sc", f"Safety Car: {SC_RU.get(st, st)}", cooldown=2.0)

    # --- отрисовка ----------------------------------------------------------

    def _render(self, snap: LiveSnapshot) -> None:
        self._rendered_uid = snap.session_uid

        # шины
        short = TYRE_COMPOUND_SHORT.get(
            snap.tyre_compound_visual or snap.tyre_compound_actual, "?")
        full = snap.tyre_visual_name or snap.tyre_compound_name or "—"
        color = COMPOUND_COLOR.get(
            snap.tyre_compound_visual or snap.tyre_compound_actual,
            COLORS["muted"])
        self._compound_badge.setText(f"{short} · {full}")
        self._compound_badge.setStyleSheet(
            f"background: {color}; color: #0d1117;"
            " border-radius: 6px; padding: 6px 10px; font-weight: bold;"
        )
        self._age_lbl.setText(str(snap.tyre_age_laps))
        for name, idx in _CORNERS:
            wear = float(snap.tyre_wear_pct[idx])
            self._wear_lbls[name].setText(f"{wear:.0f}%")
            self._wear_lbls[name].setStyleSheet(
                f"color: {_wear_color(wear)};")
            inner = int(snap.tyre_inner_c[idx])
            surf = int(snap.tyre_surf_c[idx])
            self._temp_lbls[name].setText(f"{surf} / {inner}°")

        # топливо
        self._fuel_laps_lbl.setText(f"{snap.fuel_laps:.1f}")
        color = (COLORS["red"] if snap.fuel_laps <= FUEL_ALERT_LAPS
                 else COLORS["text"])
        self._fuel_laps_lbl.setStyleSheet(f"color: {color};")
        cap = snap.fuel_capacity_kg or 0.0
        pct = max(0.0, min(100.0, snap.fuel_kg / cap * 100.0)) if cap else 0.0
        self._fuel_bar.setValue(int(pct))
        bar_c = COLORS["red"] if pct < 15.0 else COLORS["accent"]
        self._fuel_bar.setStyleSheet(
            f"QProgressBar::chunk {{ background: {bar_c}; }}"
            f" QProgressBar {{ background: {COLORS['bg_alt']}; border-radius: 7px; }}"
        )
        self._fuel_kg_lbl.setText(f"{snap.fuel_kg:.1f} / {cap:.0f} кг")
        if snap.pit_ideal_lap or snap.pit_latest_lap:
            self._pit_lbl.setText(f"{snap.pit_ideal_lap} / {snap.pit_latest_lap}")
        else:
            self._pit_lbl.setText("—")

        # статус
        sc = int(snap.safety_car_status or 0)
        self._sc_badge.setText(f"SC: {SC_RU.get(sc, sc)}")
        self._sc_badge.setStyleSheet(
            f"background: {COLORS['bg_alt']}; color: {SC_COLOR.get(sc, COLORS['muted'])};"
            " border-radius: 6px; padding: 8px; font-weight: bold;"
        )
        flag = int(snap.fia_flag or 0)
        self._flag_badge.setText(f"флаг: {FLAG_RU.get(flag, flag)}")
        self._flag_badge.setStyleSheet(
            f"background: {COLORS['bg_alt']}; color: {FLAG_COLOR.get(flag, COLORS['muted'])};"
            " border-radius: 6px; padding: 4px; font-weight: bold;"
        )
        self._pos_lbl.setText(str(snap.position) if snap.position else "—")
        if snap.total_laps:
            self._lap_lbl.setText(f"{snap.lap_num}/{snap.total_laps}")
        else:
            self._lap_lbl.setText(str(snap.lap_num or "—"))
        left = max(0, int(snap.session_time_left_s or 0))
        self._time_lbl.setText(f"{left // 60:02d}:{left % 60:02d}")

        # дельты
        self._delta_front_lbl.setText(_fmt_delta(snap.delta_front_ms))
        self._delta_front_lbl.setStyleSheet(
            f"color: {_delta_color(snap.delta_front_ms)};")
        self._delta_leader_lbl.setText(_fmt_delta(snap.delta_leader_ms))
        self._delta_leader_lbl.setStyleSheet(
            f"color: {_delta_color(snap.delta_leader_ms)};")

        # ERS
        cap_j = snap.ers_capacity_j or 1.0
        ers_pct = max(0.0, min(100.0, snap.ers_store_j / cap_j * 100.0))
        self._ers_bar.setValue(int(ers_pct))
        self._ers_bar.setStyleSheet(
            f"QProgressBar::chunk {{ background: {COLORS['purple']}; }}"
            f" QProgressBar {{ background: {COLORS['bg_alt']}; border-radius: 9px; }}"
        )
        self._ers_pct_lbl.setText(f"{ers_pct:.0f}%")
        self._ers_mode_lbl.setText(ERS_MODE_RU.get(snap.ers_mode, str(snap.ers_mode)))

        # погода
        self._weather_lbl.setText(WEATHER_RU.get(int(snap.weather or -1), "—"))
        self._temps_lbl.setText(
            f"{snap.track_temp_c if snap.track_temp_c is not None else '—'} / "
            f"{snap.air_temp_c if snap.air_temp_c is not None else '—'}"
        )
        rows = [f for f in snap.forecast if f[0] <= RAIN_ALERT_HORIZON_MIN]
        for i, lbl in enumerate(self._forecast_lbls):
            if i < len(rows):
                off, w, rain, _tt, _at = rows[i]
                rain_c = COLORS["accent"] if rain >= RAIN_ALERT_PCT else COLORS["muted"]
                lbl.setText(
                    f"+{off} мин  {WEATHER_RU.get(w, w)}  "
                    f"дождь {rain}%"
                )
                lbl.setStyleSheet(f"color: {rain_c};")
            else:
                lbl.setText("")
                lbl.setStyleSheet(f"color: {COLORS['muted']};")

    # --- алерты -------------------------------------------------------------

    def _check_alerts(self, snap: LiveSnapshot) -> None:
        worst = max(snap.tyre_wear_pct, default=0.0)
        if worst >= TYRE_ALERT_WEAR_PCT:
            if not self._tyres_alerted:
                self._tyres_alerted = True
                self._fire("tyres", f"ШИНЫ НА ПРЕДЕЛЕ: износ {worst:.0f}%")
        elif worst < TYRE_ALERT_WEAR_CLEAR_PCT:
            self._tyres_alerted = False

        if snap.fuel_capacity_kg and snap.fuel_laps <= FUEL_ALERT_LAPS:
            self._fire("fuel", f"ТОПЛИВО НА ИСХОДЕ: {snap.fuel_laps:.1f} круга")

        rain = any(
            f[2] >= RAIN_ALERT_PCT and f[0] <= RAIN_ALERT_HORIZON_MIN
            for f in snap.forecast
        )
        if rain:
            self._fire("rain", "ОЖИДАЕТСЯ ДОЖДЬ по прогнозу")

    def _fire(self, key: str, text: str, *, cooldown: float = ALERT_COOLDOWN_S) -> None:
        now = time.monotonic()
        if now - self._alert_ts.get(key, -math.inf) < cooldown:
            return
        self._alert_ts[key] = now
        self._alert_lbl.setText(text)
        self._alert_lbl.show()
        if self._sound_on:
            self._sounds.play(key)

    def _on_mute(self, checked: bool) -> None:
        self._sound_on = checked

    # --- исторический режим -------------------------------------------------

    def _snapshot_from_summary(
        self, session_uid: int, summary: dict[str, Any]
    ) -> LiveSnapshot:
        """Собирает срез из строк БД для отображения записанной сессии."""
        snap = LiveSnapshot(session_uid=session_uid)
        sess = summary.get("session") or {}
        snap.track_name = sess.get("track_name")
        snap.session_type_name = sess.get("session_type_name")
        snap.weather = sess.get("weather")
        snap.air_temp_c = sess.get("air_temp_c")
        snap.track_temp_c = sess.get("track_temp_c")
        snap.total_laps = sess.get("total_laps")
        snap.pit_ideal_lap = int(sess.get("pit_ideal_lap") or 0)
        snap.pit_latest_lap = int(sess.get("pit_latest_lap") or 0)
        snap.safety_car_status = 0
        try:
            raw = sess.get("forecast_json")
            snap.forecast = tuple(
                (int(t), int(w), int(r), int(tt), int(at))
                for t, w, r, tt, at in (json.loads(raw) if raw else ())
            )
        except (TypeError, ValueError):
            snap.forecast = ()

        lap = summary.get("last_lap") or {}
        snap.lap_num = int(lap.get("lap_num") or 0)
        snap.tyre_age_laps = int(lap.get("tyre_age_laps") or 0)
        snap.tyre_compound_actual = int(lap.get("compound_actual") or 0)
        snap.tyre_compound_visual = int(lap.get("compound_visual") or 0)
        snap.tyre_compound_name = name_of(
            TYRE_COMPOUND_VISUAL, snap.tyre_compound_visual)
        snap.tyre_visual_name = snap.tyre_compound_name

        status = summary.get("last_status") or {}
        snap.fuel_kg = float(status.get("fuel_in_tank") or 0.0)
        snap.fuel_laps = float(status.get("fuel_remaining_laps") or 0.0)
        snap.ers_store_j = float(status.get("ers_store_j") or 0.0)

        det = summary.get("last_detail") or {}
        if det:
            snap.tyre_surf_c = tuple(
                int(det.get(k) or 0)
                for k in ("tyre_surf_rl", "tyre_surf_rr", "tyre_surf_fl", "tyre_surf_fr")
            )  # type: ignore[assignment]
            snap.tyre_inner_c = tuple(
                int(det.get(k) or 0)
                for k in ("tyre_inner_rl", "tyre_inner_rr", "tyre_inner_fl", "tyre_inner_fr")
            )  # type: ignore[assignment]
            snap.tyre_wear_pct = tuple(
                float(det.get(k) or 0.0)
                for k in ("tyre_wear_rl", "tyre_wear_rr", "tyre_wear_fl", "tyre_wear_fr")
            )  # type: ignore[assignment]

        fc = summary.get("classification") or {}
        snap.position = int(fc.get("position") or 0)

        events = summary.get("events") or {}
        # SCAR-событие → статус SC на последнем известном кадре
        if events.get("SCAR"):
            try:
                payload = json.loads(summary.get("last_scar_json") or "{}")
                snap.safety_car_status = int(payload.get("safety_car_type") or 1)
            except (TypeError, ValueError):
                snap.safety_car_status = 1
        return snap


def _fmt_delta(ms: int) -> str:
    """Дельта в секундах со знаком; 0 → «—»."""
    if not ms:
        return "—"
    return f"{ms / 1000.0:+.3f}"


def _delta_color(ms: int) -> str:
    if not ms:
        return COLORS["muted"]
    return COLORS["green"] if ms < 0 else COLORS["red"]
