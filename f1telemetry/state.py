"""Склейка UDP-пакетов в модель сессии (ядро, без Qt).

``SessionState`` потребляет результаты :func:`f1telemetry.parser.parse_packet`,
обновляет кольцевые буферы для живого UI и готовит операции записи для
:class:`f1telemetry.storage.Storage` / :class:`f1telemetry.storage.StorageWriter`.

Правила (AGENTS.md, раздел 4):

* граница круга — по росту ``m_currentLapNum`` при ``m_lapDistance`` у старта;
* откат (flashback / уменьшение номера круга) не финализирует круг, а сбрасывает
  накопленное и помечает уже записанные сэмплы флагом ``flashback``;
* в высокочастотные таблицы (``telemetry``, ``car_telemetry_detail``,
  ``car_status_samples``) пишется только игрок;
* в ``laps`` / ``participants`` / ``lap_positions`` / ``final_classification`` —
  все машины;
* все поля считаются «последним известным» значением; пропуски UDP — не ошибка.

Единицы измерения: км/ч, мс, метры, секунды, Джоули, кг, °C.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterator

from .analytics import DistanceSample, best_sectors, lap_aggregates, stint_degradation
from .constants import (
    MAX_CARS,
    SESSION_TYPES,
    TEAMS,
    TRACKS,
    TYRE_COMPOUND_ACTUAL,
    TYRE_COMPOUND_VISUAL,
    name_of,
    packet_name,
)
from .packets import (
    CarDamageData,
    CarStatusData,
    CarTelemetryData,
    PacketCarDamage,
    PacketCarSetup,
    PacketCarStatus,
    PacketCarTelemetry,
    PacketFinalClassification,
    PacketLapData,
    PacketLapPositions,
    PacketMotion,
    PacketMotionEx,
    PacketParticipants,
    PacketSession,
    PacketSessionHistory,
    PacketTyreSets,
)
from .parser import ParsedPacket, sector_ms
from .storage import LapRecord

log = logging.getLogger(__name__)

__all__ = [
    "ERS_CAPACITY_J",
    "LiveSnapshot",
    "PacketResult",
    "SessionState",
    "TraceSample",
]

ERS_CAPACITY_J = 4_000_000.0
"""Ёмкость ERS в F1 25, Дж (константа игры, в пакетах не передаётся)."""

VALID_LAP = 0x01
VALID_S1 = 0x02
VALID_S2 = 0x04
VALID_S3 = 0x08


# --- Видимые срезы -------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TraceSample:
    """Одна точка ленты графиков реального времени (только игрок)."""

    session_time: float
    lap_distance_m: float
    speed_kmh: float
    throttle: float
    brake: float
    gear: int
    rpm: int
    drs: int
    lap_num: int
    flashback: int = 0


@dataclass(slots=True)
class LiveSnapshot:
    """Мгновенный срез для виджетов. Читается GUI-таймером, не блокирует ядро."""

    # --- сессия ---
    session_uid: int | None = None
    session_time: float = 0.0
    track_id: int | None = None
    track_name: str | None = None
    session_type: int | None = None
    session_type_name: str | None = None
    weather: int | None = None
    air_temp_c: int | None = None
    track_temp_c: int | None = None
    total_laps: int | None = None
    track_length_m: int | None = None
    session_time_left_s: int = 0
    safety_car_status: int = 0
    num_cars: int = 0

    # --- игрок ---
    car_index: int = 255
    driver_name: str = ""
    team_name: str = ""
    position: int = 0
    grid_position: int = 0
    lap_num: int = 0
    lap_distance_m: float = 0.0
    lap_progress: float = 0.0
    sector: int = 1
    current_lap_ms: int = 0
    last_lap_ms: int = 0
    best_lap_ms: int = 0
    theoretical_ms: int = 0
    s1_ms: int = 0
    s2_ms: int = 0
    s3_ms: int = 0
    delta_front_ms: int = 0
    delta_leader_ms: int = 0
    pit_status: int = 0
    pit_limiter: int = 0
    num_pit_stops: int = 0
    driver_status: int = 0
    result_status: int = 0

    # --- телеметрия ---
    speed_kmh: float = 0.0
    throttle: float = 0.0
    brake: float = 0.0
    steer: float = 0.0
    gear: int = 0
    rpm: int = 0
    rev_lights_pct: int = 0
    drs: int = 0
    drs_allowed: int = 0
    suggested_gear: int = 0
    engine_temp_c: float = 0.0

    # --- положение / перегрузки ---
    pos_x: float = 0.0
    pos_y: float = 0.0
    pos_z: float = 0.0
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    g_lat: float = 0.0
    g_lon: float = 0.0
    g_vert: float = 0.0
    front_wheels_angle: float = 0.0

    # --- топливо / ERS ---
    fuel_kg: float = 0.0
    fuel_capacity_kg: float = 0.0
    fuel_laps: float = 0.0
    ers_store_j: float = 0.0
    ers_capacity_j: float = ERS_CAPACITY_J
    ers_mode: int = 0
    ers_deployed_lap_j: float = 0.0
    ers_harvested_lap_j: float = 0.0

    # --- шины / тормоза (порядок 0=RL, 1=RR, 2=FL, 3=FR) ---
    tyre_compound_actual: int = 0
    tyre_compound_visual: int = 0
    tyre_compound_name: str = ""
    tyre_visual_name: str = ""
    tyre_age_laps: int = 0
    tyre_surf_c: tuple[int, int, int, int] = (0, 0, 0, 0)
    tyre_inner_c: tuple[int, int, int, int] = (0, 0, 0, 0)
    tyre_press_psi: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    tyre_wear_pct: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    tyre_damage_pct: tuple[int, int, int, int] = (0, 0, 0, 0)
    brake_temp_c: tuple[int, int, int, int] = (0, 0, 0, 0)
    surface: tuple[int, int, int, int] = (0, 0, 0, 0)
    fia_flag: int = 0

    # --- повреждения кузова, % ---
    front_wing_damage: int = 0
    rear_wing_damage: int = 0
    floor_damage: int = 0
    engine_damage: int = 0
    gearbox_damage: int = 0
    engine_ice_wear: int = 0

    # --- диагностика ---
    packets_per_sec: float = 0.0
    packets_total: int = 0
    parse_errors: int = 0
    lost_frames: int = 0
    last_packet_age_s: float = math.inf


@dataclass(slots=True)
class PacketResult:
    """Что изменилось после обработки одного пакета."""

    packet_id: int = -1
    session_changed: bool = False
    new_session: bool = False
    laps_completed: tuple[int, ...] = ()
    flashback: bool = False
    event_code: str | None = None


# --- Состояние одной машины ---------------------------------------------------

@dataclass(slots=True)
class _CarLap:
    """Накопленное состояние круга одной машины."""

    lap_num: int = 0
    lap_distance: float = 0.0
    sector: int = 1
    s1_ms: int = 0
    s2_ms: int = 0
    invalid: int = 0
    position: int = 0
    grid_position: int = 0
    pit_stops: int = 0
    pit_status: int = 0
    driver_status: int = 0
    result_status: int = 0
    delta_front_ms: int = 0
    delta_leader_ms: int = 0
    last_lap_ms: int = 0
    current_lap_ms: int = 0
    stint_index: int = 0
    compound_actual: int = 0
    compound_visual: int = 0
    tyre_age: int = 0
    fuel_at_start: float | None = None
    ers_deployed_max: float = 0.0
    # счётчики агрегатов (игрок)
    top_speed: float = 0.0
    thr_sum: float = 0.0
    thr_n: int = 0
    brake_s: float = 0.0
    full_thr_s: float = 0.0
    last_telem_t: float | None = None
    sample_idx: int = 0
    detail_idx: int = 0
    status_idx: int = 0
    distance_samples: list[DistanceSample] = field(default_factory=list)
    telemetry_samples: list[dict[str, float]] = field(default_factory=list)
    known: bool = False
    """Уже выставлен стартовый номер круга (границы отслеживаем)."""


# --- Основной класс -----------------------------------------------------------

class SessionState:
    """Модель текущей сессии: склейка пакетов + подготовка записей в БД.

    Args:
        sample_hz: частота прореживания телеметрии игрока в БД (по умолчанию 20).
        detail_hz: частота записи деталей шин/тормозов (10).
        status_hz: частота записи сэмплов топлива/ERS (5).
        buffer_seconds: глубина кольцевого буфера живого графика.
        max_history_laps: сколько последних кругов держать в памяти игрока.
    """

    def __init__(
        self,
        *,
        sample_hz: float = 20.0,
        detail_hz: float = 10.0,
        status_hz: float = 5.0,
        buffer_seconds: float = 120.0,
        max_history_laps: int = 400,
        max_pending_ops: int = 20_000,
    ) -> None:
        self.sample_hz = float(sample_hz)
        self.detail_hz = float(detail_hz)
        self.status_hz = float(status_hz)
        self._sample_dt = 1.0 / self.sample_hz if self.sample_hz > 0 else 0.0
        self._detail_dt = 1.0 / self.detail_hz if self.detail_hz > 0 else 0.0
        self._status_dt = 1.0 / self.status_hz if self.status_hz > 0 else 0.0
        self.max_pending_ops = max_pending_ops

        ring_hz = max(10.0, self.sample_hz)
        self._ring: deque[TraceSample] = deque(
            maxlen=int(ring_hz * buffer_seconds)
        )
        self._max_history_laps = max_history_laps

        # --- сессия ---
        self.session_uid: int | None = None
        self.player_car_index: int = 255
        self.secondary_player_car_index: int = 255
        self.session_meta: dict[str, Any] = {}
        self.session_link_identifier: int = 0
        self.track_length_m: float = 0.0

        # --- машины ---
        self.cars: dict[int, _CarLap] = {}
        self.participants: dict[int, dict[str, Any]] = {}
        self.finals: dict[int, dict[str, Any]] = {}
        self.best_lap_ms: dict[int, int] = {}
        self._car_history: dict[int, list[dict[str, Any]]] = {}
        self._laps_written: dict[int, set[int]] = {}

        # --- последний известный «сырой» срез игрока ---
        self._telem: CarTelemetryData | None = None
        self._status: CarStatusData | None = None
        self._damage: CarDamageData | None = None
        self._motion: dict[str, float] = {}
        self._motion_ex: dict[str, float] = {}
        self._suggested_gear: int = 0
        self._drs_allowed: int = 0
        self._next_sample_t: float = 0.0
        self._next_detail_t: float = 0.0
        self._next_status_t: float = 0.0

        # --- счётчики / диагностика ---
        self.packets_total = 0
        self.parse_errors = 0
        self.lost_frames = 0
        self._last_frame: int | None = None
        self._rate_window: deque[float] = deque(maxlen=400)
        self._last_packet_monotonic: float | None = None

        # --- флешбэки / время ---
        self.session_time: float = 0.0
        self._flashback_until: float = -1.0
        self._flashback_seen = False
        self._stint_start_lap: dict[int, int] = {}
        self._stint_lap_times: dict[int, list[int]] = {}

        # --- очередь записи ---
        self._ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._pending_telemetry: list[tuple[Any, ...]] = []
        self._pending_detail: list[tuple[Any, ...]] = []
        self._pending_status: list[tuple[Any, ...]] = []
        self._pending_lap_pos: list[tuple[int, int, int, int]] = []

    # ------------------------------------------------------------------ API

    @property
    def is_active(self) -> bool:
        """True, если известна сессия (пришёл хотя бы один пакет с UID)."""
        return self.session_uid is not None

    def trace(self, max_points: int | None = None) -> list[TraceSample]:
        """Кольцевой буфер живого графика (старые -> новые)."""
        if max_points is None or max_points >= len(self._ring):
            return list(self._ring)
        return list(self._ring)[-max_points:]

    def snapshot(self) -> LiveSnapshot:
        """Мгновенный срез для GUI (потокобезопасно на уровне GIL)."""
        snap = LiveSnapshot()
        snap.session_uid = self.session_uid
        snap.packets_total = self.packets_total
        snap.parse_errors = self.parse_errors
        snap.lost_frames = self.lost_frames
        now = time.monotonic()
        if self._last_packet_monotonic is not None:
            snap.last_packet_age_s = now - self._last_packet_monotonic
        if self._rate_window:
            span = self._rate_window[-1] - self._rate_window[0]
            snap.packets_per_sec = (
                (len(self._rate_window) - 1) / span if span > 1e-6 else 0.0
            )

        meta = self.session_meta
        snap.track_id = meta.get("track_id")
        snap.track_name = meta.get("track_name")
        snap.session_type = meta.get("session_type")
        snap.session_type_name = meta.get("session_type_name")
        snap.weather = meta.get("weather")
        snap.air_temp_c = meta.get("air_temp_c")
        snap.track_temp_c = meta.get("track_temp_c")
        snap.total_laps = meta.get("total_laps")
        snap.session_time_left_s = int(meta.get("session_time_left_s") or 0)
        snap.safety_car_status = int(meta.get("safety_car_status") or 0)
        snap.num_cars = len(self.participants) or int(meta.get("num_cars") or 0)

        car = self.cars.get(self.player_car_index)
        part = self.participants.get(self.player_car_index, {})
        snap.car_index = self.player_car_index
        snap.driver_name = str(part.get("name") or "")
        snap.team_name = str(part.get("team_name") or "")

        snap.session_time = self.session_time
        if car is not None:
            snap.position = car.position
            snap.grid_position = car.grid_position
            snap.lap_num = car.lap_num
            snap.lap_distance_m = car.lap_distance
            if self.track_length_m > 0:
                snap.lap_progress = max(0.0, min(1.0, car.lap_distance / self.track_length_m))
            snap.sector = car.sector
            snap.current_lap_ms = car.current_lap_ms
            snap.last_lap_ms = car.last_lap_ms
            snap.best_lap_ms = self.best_lap_ms.get(self.player_car_index, 0)
            snap.s1_ms = car.s1_ms
            snap.s2_ms = car.s2_ms
            snap.s3_ms = (
                max(0, car.last_lap_ms - car.s1_ms - car.s2_ms) if car.last_lap_ms else 0
            )
            hist = self._car_history.get(self.player_car_index)
            if hist:
                best = best_sectors(hist)
                snap.theoretical_ms = best["theoretical"]
                if best["lap"]:
                    snap.best_lap_ms = min(snap.best_lap_ms or best["lap"], best["lap"])
            snap.delta_front_ms = car.delta_front_ms
            snap.delta_leader_ms = car.delta_leader_ms
            snap.pit_status = car.pit_status
            snap.num_pit_stops = car.pit_stops
            snap.driver_status = car.driver_status
            snap.result_status = car.result_status
            snap.tyre_compound_actual = car.compound_actual
            snap.tyre_compound_visual = car.compound_visual
            snap.tyre_compound_name = name_of(TYRE_COMPOUND_ACTUAL, car.compound_actual)
            snap.tyre_visual_name = name_of(TYRE_COMPOUND_VISUAL, car.compound_visual)
            snap.tyre_age_laps = car.tyre_age

        t = self._telem
        if t is not None:
            snap.speed_kmh = float(t.m_speed)
            snap.throttle = float(t.m_throttle)
            snap.brake = float(t.m_brake)
            snap.steer = float(t.m_steer)
            snap.gear = int(t.m_gear)
            snap.rpm = int(t.m_engineRPM)
            snap.rev_lights_pct = int(t.m_revLightsPercent)
            snap.drs = int(t.m_drs)
            snap.suggested_gear = self._suggested_gear
            snap.engine_temp_c = float(t.m_engineTemperature)
            snap.tyre_surf_c = tuple(int(x) for x in t.m_tyresSurfaceTemperature)  # type: ignore[assignment]
            snap.tyre_inner_c = tuple(int(x) for x in t.m_tyresInnerTemperature)  # type: ignore[assignment]
            snap.tyre_press_psi = tuple(float(x) for x in t.m_tyresPressure)  # type: ignore[assignment]
            snap.brake_temp_c = tuple(int(x) for x in t.m_brakesTemperature)  # type: ignore[assignment]
            snap.surface = tuple(int(x) for x in t.m_surfaceType)  # type: ignore[assignment]

        s = self._status
        if s is not None:
            snap.fuel_kg = float(s.m_fuelInTank)
            snap.fuel_capacity_kg = float(s.m_fuelCapacity)
            snap.fuel_laps = float(s.m_fuelRemainingLaps)
            snap.ers_store_j = float(s.m_ersStoreEnergy)
            snap.ers_mode = int(s.m_ersDeployMode)
            snap.ers_deployed_lap_j = float(s.m_ersDeployedThisLap)
            snap.ers_harvested_lap_j = float(s.m_ersHarvestedThisLapMGUK) + float(
                s.m_ersHarvestedThisLapMGUH
            )
            snap.pit_limiter = int(s.m_pitLimiterStatus)
            snap.drs_allowed = int(s.m_drsAllowed)
            snap.fia_flag = int(s.m_vehicleFiaFlags)

        m = self._motion
        if m:
            snap.pos_x = m.get("x", 0.0)
            snap.pos_y = m.get("y", 0.0)
            snap.pos_z = m.get("z", 0.0)
            snap.yaw = m.get("yaw", 0.0)
            snap.pitch = m.get("pitch", 0.0)
            snap.roll = m.get("roll", 0.0)
            snap.g_lat = m.get("g_lat", 0.0)
            snap.g_lon = m.get("g_lon", 0.0)
            snap.g_vert = m.get("g_vert", 0.0)

        ex = self._motion_ex
        if ex:
            snap.front_wheels_angle = ex.get("front_wheels_angle", 0.0)

        d = self._damage
        if d is not None:
            snap.tyre_wear_pct = tuple(float(x) for x in d.m_tyresWear)  # type: ignore[assignment]
            snap.tyre_damage_pct = tuple(int(x) for x in d.m_tyresDamage)  # type: ignore[assignment]
            snap.front_wing_damage = int(d.m_frontLeftWingDamage)
            snap.rear_wing_damage = int(d.m_rearWingDamage)
            snap.floor_damage = int(d.m_floorDamage)
            snap.engine_damage = int(d.m_engineDamage)
            snap.gearbox_damage = int(d.m_gearBoxDamage)
            snap.engine_ice_wear = int(d.m_engineICEWear)

        return snap

    def drain_writes(self) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
        """Забирает накопленные операции записи ``(op, args, kwargs)``.

        Пакетные вставки сырых сэмплов идут первыми, затем — ``_ops``
        (в частности ``mark_flashback`` должен выполняться уже после вставки
        отмечаемых строк).
        """
        ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

        if self._pending_telemetry:
            ops.append(("insert_telemetry", (self._pending_telemetry,), {}))
            self._pending_telemetry = []
        if self._pending_detail:
            ops.append(("insert_detail", (self._pending_detail,), {}))
            self._pending_detail = []
        if self._pending_status:
            ops.append(("insert_status_samples", (self._pending_status,), {}))
            self._pending_status = []
        if self._pending_lap_pos:
            ops.append(("upsert_lap_positions", (self._pending_lap_pos,), {}))
            self._pending_lap_pos = []

        ops.extend(self._ops)
        self._ops = []

        if len(ops) > self.max_pending_ops:
            log.warning("write queue too big (%d), dropping %d", len(ops),
                        len(ops) - self.max_pending_ops)
            ops = ops[-self.max_pending_ops:]
        return ops

    # ------------------------------------------------------------ обработка

    def on_packet(self, parsed: ParsedPacket) -> PacketResult:
        """Обрабатывает один разобранный пакет.

        Args:
            parsed: результат :func:`f1telemetry.parser.parse_packet`.

        Returns:
            :class:`PacketResult` — что изменилось.
        """
        self.packets_total += 1
        now = time.monotonic()
        self._last_packet_monotonic = now
        self._rate_window.append(now)

        h = parsed.header
        if self._last_frame is not None and h.overall_frame_identifier > self._last_frame:
            gap = h.overall_frame_identifier - self._last_frame
            if gap > 1 and self._last_frame > 0:
                self.lost_frames += gap - 1
        self._last_frame = h.overall_frame_identifier

        result = PacketResult(packet_id=parsed.packet_id)

        # смена сессии
        if h.session_uid != self.session_uid:
            result.session_changed = True
            result.new_session = self.session_uid is not None
            self._switch_session(h.session_uid)
            self._queue_session_row()

        # защита: игрок может меняться (в частности secondary player = 255)
        if h.player_car_index != self.player_car_index:
            self.player_car_index = h.player_car_index
        if h.secondary_player_car_index != self.secondary_player_car_index:
            self.secondary_player_car_index = h.secondary_player_car_index

        self.session_time = h.session_time

        match parsed.packet_id:
            case 1:
                self._on_session(parsed.packet)
            case 2:
                self._on_lap_data(parsed.packet, result)
            case 3:
                self._on_event(parsed, result)
            case 4:
                self._on_participants(parsed.packet)
            case 5:
                self._on_setup(parsed.packet)
            case 6:
                self._on_telemetry(parsed.packet)
            case 7:
                self._on_status(parsed.packet)
            case 0:
                self._on_motion(parsed.packet)
            case 13:
                self._on_motion_ex(parsed.packet)
            case 10:
                self._on_damage(parsed.packet)
            case 8:
                self._on_final(parsed.packet)
            case 11:
                self._on_session_history(parsed.packet)
            case 12:
                self._on_tyre_sets(parsed.packet)
            case 15:
                self._on_lap_positions(parsed.packet)
            case 9:
                pass  # Lobby Info: в БД не пишем
            case _:
                log.debug("unhandled packet %s", packet_name(parsed.packet_id))

        # флешбэк истёк по времени
        if self._flashback_until >= 0 and h.session_time > self._flashback_until:
            self._flashback_until = -1.0

        return result

    # ------------------------------------------------------------ внутреннее

    def _switch_session(self, uid: int) -> None:
        """Новая сессия: сброс состояния, запись завершения старой."""
        old = self.session_uid
        if old is not None:
            self._flush_stints(force=True)
            self._ops.append(("close_session", (old,), {}))
            log.info("session change: %s -> %s", old, uid)
        self.session_uid = uid
        self.cars.clear()
        self.participants.clear()
        self.finals.clear()
        self.best_lap_ms.clear()
        self._car_history.clear()
        self._laps_written.clear()
        self._telem = None
        self._status = None
        self._damage = None
        self._motion = {}
        self._motion_ex = {}
        self._ring.clear()
        self._next_sample_t = 0.0
        self._next_detail_t = 0.0
        self._next_status_t = 0.0
        self._flashback_until = -1.0
        self._flashback_seen = False
        self._last_frame = None
        self.lost_frames = 0
        self.packets_total = 0
        self._stint_start_lap: dict[int, int] = {}
        self._stint_lap_times: dict[int, list[int]] = {}

    def _car(self, idx: int) -> _CarLap:
        st = self.cars.get(idx)
        if st is None:
            st = _CarLap()
            self.cars[idx] = st
        return st

    def _queue(self, op: str, *args: Any, **kwargs: Any) -> None:
        self._ops.append((op, args, kwargs))

    def _queue_session_row(self) -> None:
        meta = self.session_meta
        self._queue(
            "upsert_session",
            int(self.session_uid or 0),
            packet_format=2025,
            game_year=25,
            game_version="",
            track_id=meta.get("track_id"),
            track_name=meta.get("track_name"),
            session_type=meta.get("session_type"),
            session_type_name=meta.get("session_type_name"),
            weather=meta.get("weather"),
            total_laps=meta.get("total_laps"),
            track_length_m=meta.get("track_length_m"),
            season_link_id=meta.get("season_link_id"),
            weekend_link_id=meta.get("weekend_link_id"),
            session_link_id=meta.get("session_link_id"),
            identical_session=meta.get("identical_session"),
            player_car_index=self.player_car_index,
            num_cars=len(self.participants) or None,
        )

    def _reset_lap_buffers(self, st: _CarLap) -> None:
        """Сброс накопленного по кругу (новый круг или откат)."""
        st.sample_idx = 0
        st.detail_idx = 0
        st.status_idx = 0
        st.top_speed = 0.0
        st.thr_sum = 0.0
        st.thr_n = 0
        st.brake_s = 0.0
        st.full_thr_s = 0.0
        st.last_telem_t = None
        st.ers_deployed_max = 0.0
        st.distance_samples.clear()
        st.telemetry_samples.clear()

    def _mark_flashback_lap(self, car_idx: int, st: _CarLap, sample_idx: int) -> None:
        """Помечает уже записанные сэмплы круга как флешбэк."""
        if self.session_uid is None:
            return
        self._queue(
            "mark_flashback", int(self.session_uid), car_idx, st.lap_num, sample_idx
        )

    # --- пакеты ---------------------------------------------------------------

    def _on_session(self, pkt: PacketSession) -> None:
        meta = self.session_meta
        track_id = int(pkt.m_trackId)
        meta.update({
            "track_id": track_id,
            "track_name": name_of(TRACKS, track_id),
            "session_type": int(pkt.m_sessionType),
            "session_type_name": name_of(SESSION_TYPES, int(pkt.m_sessionType)),
            "weather": int(pkt.m_weather),
            "air_temp_c": int(pkt.m_airTemperature),
            "track_temp_c": int(pkt.m_trackTemperature),
            "total_laps": int(pkt.m_totalLaps),
            "track_length_m": int(pkt.m_trackLength),
            "session_time_left_s": int(pkt.m_sessionTimeLeft),
            "session_duration_s": int(pkt.m_sessionDuration),
            "safety_car_status": int(pkt.m_safetyCarStatus),
            "season_link_id": int(pkt.m_seasonLinkIdentifier),
            "weekend_link_id": int(pkt.m_weekendLinkIdentifier),
            "session_link_id": int(pkt.m_sessionLinkIdentifier),
            "identical_session": 0,
            "formula": int(pkt.m_formula),
            "pit_speed_limit": int(pkt.m_pitSpeedLimit),
        })
        self.track_length_m = float(pkt.m_trackLength)
        self.session_link_identifier = int(pkt.m_sessionLinkIdentifier)
        self._queue_session_row()

    def _on_participants(self, pkt: PacketParticipants) -> None:
        uid = int(self.session_uid or 0)
        for i, p in enumerate(pkt.m_participants):
            raw_name = bytes(p.m_name).split(b"\x00", 1)[0].decode("utf-8", "replace")
            if not raw_name:
                continue
            team_id = int(p.m_teamId)
            row = {
                "name": raw_name,
                "driver_id": int(p.m_driverId),
                "team_id": team_id,
                "team_name": name_of(TEAMS, team_id),
                "race_number": int(p.m_raceNumber),
                "nationality": int(p.m_nationality),
                "ai_controlled": int(p.m_aiControlled),
                "platform": int(p.m_platform),
                "telemetry_public": int(p.m_yourTelemetry),
            }
            self.participants[i] = row
            self._queue("upsert_participant", uid, i, **row)
        if self.session_meta.get("num_cars") != len(self.participants):
            self.session_meta["num_cars"] = len(self.participants)
            self._queue_session_row()

    def _on_setup(self, pkt: PacketCarSetup) -> None:
        uid = int(self.session_uid or 0)
        s = pkt.m_carSetups[self.player_car_index]
        setup = {
            "frontWing": int(s.m_frontWing),
            "rearWing": int(s.m_rearWing),
            "onThrottle": int(s.m_onThrottle),
            "offThrottle": int(s.m_offThrottle),
            "frontCamber": float(s.m_frontCamber),
            "rearCamber": float(s.m_rearCamber),
            "frontToe": float(s.m_frontToe),
            "rearToe": float(s.m_rearToe),
            "frontSuspension": int(s.m_frontSuspension),
            "rearSuspension": int(s.m_rearSuspension),
            "frontAntiRollBar": int(s.m_frontAntiRollBar),
            "rearAntiRollBar": int(s.m_rearAntiRollBar),
            "frontSuspensionHeight": int(s.m_frontSuspensionHeight),
            "rearSuspensionHeight": int(s.m_rearSuspensionHeight),
            "brakePressure": int(s.m_brakePressure),
            "brakeBias": int(s.m_brakeBias),
            "engineBraking": int(s.m_engineBraking),
            "rearLeftTyrePressure": float(s.m_rearLeftTyrePressure),
            "rearRightTyrePressure": float(s.m_rearRightTyrePressure),
            "frontLeftTyrePressure": float(s.m_frontLeftTyrePressure),
            "frontRightTyrePressure": float(s.m_frontRightTyrePressure),
            "ballast": int(s.m_ballast),
            "fuelLoad": float(s.m_fuelLoad),
        }
        self._queue("upsert_setup", uid, self.player_car_index, setup)

    def _on_lap_data(self, pkt: PacketLapData, result: PacketResult) -> None:
        uid = int(self.session_uid or 0)
        completed: list[int] = []
        position_rows: list[tuple[int, int, int, int]] = []

        for i, ld in enumerate(pkt.m_lapData):
            if i >= MAX_CARS:
                break
            st = self._car(i)
            lap_num = int(ld.m_currentLapNum)
            dist = float(ld.m_lapDistance)
            cur_ms = int(ld.m_currentLapTimeInMS)

            # откат времени (flashback) — номер круга уменьшился
            if st.known and lap_num < st.lap_num:
                self._mark_flashback_lap(i, st, st.sample_idx)
                self._reset_lap_buffers(st)
                st.lap_num = lap_num
                st.lap_distance = dist
                st.s1_ms = 0
                st.s2_ms = 0
                st.invalid = 0
                result.flashback = True
                self._flashback_until = max(self._flashback_until, cur_ms / 1000.0 + 1.0)
                continue

            # подозрение на откат внутри круга: время пошло назад
            if (
                st.known
                and cur_ms > 0
                and st.current_lap_ms > 0
                and cur_ms + 2000 < st.current_lap_ms
            ):
                self._mark_flashback_lap(i, st, st.sample_idx)
                self._reset_lap_buffers(st)
                st.s1_ms = 0
                st.s2_ms = 0
                st.invalid = 0
                result.flashback = True

            if not st.known:
                st.known = True
                st.lap_num = lap_num
                st.lap_distance = dist
                st.sector = int(ld.m_sector)
                st.fuel_at_start = None
            elif lap_num > st.lap_num:
                self._finalize_lap(i, st, int(ld.m_lastLapTimeInMS), completed)
                st.lap_num = lap_num
                self._reset_lap_buffers(st)
                st.s1_ms = 0
                st.s2_ms = 0
                st.invalid = 0
                st.fuel_at_start = (
                    float(self._status.m_fuelInTank) if self._status is not None else None
                )
            else:
                # секторные границы внутри круга
                sector = int(ld.m_sector)
                if sector > st.sector:
                    if sector == 2:
                        st.s1_ms = cur_ms
                    elif sector == 3:
                        st.s2_ms = cur_ms - st.s1_ms
                    st.sector = sector

            if int(ld.m_currentLapInvalid):
                st.invalid = 1

            st.lap_distance = dist
            st.current_lap_ms = cur_ms
            st.position = int(ld.m_carPosition)
            st.grid_position = int(ld.m_gridPosition)
            st.pit_stops = int(ld.m_numPitStops)
            st.pit_status = int(ld.m_pitStatus)
            st.driver_status = int(ld.m_driverStatus)
            st.result_status = int(ld.m_resultStatus)
            st.delta_front_ms = sector_ms(
                ld.m_deltaToCarInFrontMinutesPart, ld.m_deltaToCarInFrontMSPart
            )
            st.delta_leader_ms = sector_ms(
                ld.m_deltaToRaceLeaderMinutesPart, ld.m_deltaToRaceLeaderMSPart
            )
            position_rows.append((uid, lap_num, i, st.position))

        if position_rows:
            self._pending_lap_pos.extend(position_rows)
        result.laps_completed = tuple(completed)

    def _finalize_lap(
        self, car_idx: int, st: _CarLap, last_lap_ms: int, completed: list[int]
    ) -> None:
        """Собирает :class:`LapRecord` и метрики по завершённому кругу."""
        uid = int(self.session_uid or 0)
        lap_time = int(last_lap_ms)
        if lap_time <= 0:
            lap_time = st.current_lap_ms

        s1 = st.s1_ms
        s2 = st.s2_ms
        s3 = max(0, lap_time - s1 - s2) if lap_time else 0
        valid = 0 if st.invalid else 1

        samples = (
            st.telemetry_samples if car_idx == self.player_car_index else []
        )
        agg = lap_aggregates(samples) if samples else {
            "top_speed_kmh": st.top_speed,
            "avg_throttle": (st.thr_sum / st.thr_n) if st.thr_n else 0.0,
            "full_throttle_pct": 100.0 * st.full_thr_s / max(1e-9, st.full_thr_s + st.brake_s),
            "brake_time_pct": 100.0 * st.brake_s / max(1e-9, st.full_thr_s + st.brake_s),
        }

        rec = LapRecord(
            session_uid=uid,
            car_index=car_idx,
            lap_num=st.lap_num,
            lap_time_ms=lap_time,
            sector1_ms=s1,
            sector2_ms=s2,
            sector3_ms=s3,
            valid=valid,
            s1_valid=0 if (st.invalid or (s1 <= 0 and lap_time)) else 1,
            s2_valid=0 if (st.invalid or (s2 <= 0 and lap_time)) else 1,
            s3_valid=0 if (st.invalid or (s3 <= 0 and lap_time)) else 1,
            position=st.position,
            stint_index=st.stint_index,
            compound_actual=st.compound_actual,
            compound_visual=st.compound_visual,
            tyre_age_laps=st.tyre_age,
            fuel_at_start=st.fuel_at_start,
            ers_deployed_j=st.ers_deployed_max or None,
            top_speed_kmh=agg["top_speed_kmh"],
            agg_throttle=agg["avg_throttle"],
            brake_time_pct=agg["brake_time_pct"],
            full_throttle_pct=agg["full_throttle_pct"],
        )
        self._queue("upsert_lap", rec)
        completed.append(car_idx)

        # лучший круг
        if valid and lap_time > 0:
            prev = self.best_lap_ms.get(car_idx)
            if prev is None or lap_time < prev:
                self.best_lap_ms[car_idx] = lap_time
                name = self.participants.get(car_idx, {}).get("name", f"#{car_idx}")
                self._queue(
                    "insert_event", uid, self.session_time, "FTLP", car_idx, None,
                    {"lap_time_s": lap_time / 1000.0, "lap": st.lap_num, "name": name},
                )

        # история круга для теоретического лучшего и дельт
        hist = self._car_history.setdefault(car_idx, [])
        hist.append({
            "lap_num": st.lap_num,
            "lap_time_ms": lap_time,
            "s1_ms": s1, "s2_ms": s2, "s3_ms": s3,
            "valid": valid,
            "stint_index": st.stint_index,
            "tyre_age_laps": st.tyre_age,
            "compound_actual": st.compound_actual,
            "compound_visual": st.compound_visual,
            "is_pit_lap": st.pit_status != 0,
        })
        if len(hist) > self._max_history_laps:
            del hist[: len(hist) - self._max_history_laps]

        written = self._laps_written.setdefault(car_idx, set())
        written.add(st.lap_num)
        if car_idx == self.player_car_index:
            self._maybe_flush_stint()

    def _maybe_flush_stint(self) -> None:
        """Пересчитывает агрегаты стинта игрока (при смене шины или периодически)."""
        hist = self._car_history.get(self.player_car_index, [])
        if not hist:
            return
        stints = stint_degradation(hist)
        uid = int(self.session_uid or 0)
        for d in stints:
            self._queue(
                "upsert_stint", uid, self.player_car_index, d.stint_index,
                start_lap=d.start_lap, end_lap=d.end_lap,
                compound_actual=d.compound_actual, compound_visual=d.compound_visual,
                laps_count=d.laps_count, avg_lap_ms=d.avg_lap_ms,
                deg_ms_per_lap=d.deg_ms_per_lap,
            )

    def _flush_stints(self, force: bool = False) -> None:
        if force:
            self._maybe_flush_stint()

    def _on_telemetry(self, pkt: PacketCarTelemetry) -> None:
        i = self.player_car_index
        if i >= MAX_CARS:
            return
        t = pkt.m_carTelemetryData[i]
        self._telem = t
        self._suggested_gear = int(pkt.m_suggestedGear)

        st = self.cars.get(i)
        if st is None:
            return

        t_now = self.session_time
        dt = 0.0
        if st.last_telem_t is not None:
            dt = max(0.0, t_now - st.last_telem_t)
        st.last_telem_t = t_now

        speed = float(t.m_speed)
        st.top_speed = max(st.top_speed, speed)
        st.thr_sum += float(t.m_throttle)
        st.thr_n += 1
        st.brake_s += dt if float(t.m_brake) > 0.02 else 0.0
        st.full_thr_s += dt if float(t.m_throttle) > 0.95 else 0.0
        st.distance_samples.append(DistanceSample(st.lap_distance, speed))
        st.telemetry_samples.append({
            "speed_kmh": speed,
            "throttle": float(t.m_throttle),
            "brake": float(t.m_brake),
        })
        if len(st.distance_samples) > 4000:
            del st.distance_samples[:2000]
            del st.telemetry_samples[:2000]

        uid = int(self.session_uid or 0)
        if t_now >= self._next_sample_t:
            self._next_sample_t = t_now + self._sample_dt
            flash = 1 if self._flashback_until >= 0 else 0
            self._pending_telemetry.append((
                uid, i, st.lap_num, st.sample_idx,
                float(t_now), float(st.lap_distance),
                speed, float(t.m_throttle), float(t.m_brake), float(t.m_steer),
                int(t.m_gear), int(t.m_engineRPM), int(t.m_drs), self._status.m_ersDeployMode if self._status else 0,
                float(self._motion.get("x", 0.0)),
                float(self._motion.get("y", 0.0)),
                float(self._motion.get("z", 0.0)),
                float(self._motion.get("vx", 0.0)),
                float(self._motion.get("vy", 0.0)),
                float(self._motion.get("vz", 0.0)),
                float(self._motion.get("g_lat", 0.0)),
                float(self._motion.get("g_lon", 0.0)),
                float(self._motion.get("g_vert", 0.0)),
                float(self._motion.get("yaw", 0.0)),
                float(self._motion.get("pitch", 0.0)),
                float(self._motion.get("roll", 0.0)),
                flash,
            ))
            st.sample_idx += 1

            self._ring.append(TraceSample(
                session_time=float(t_now),
                lap_distance_m=float(st.lap_distance),
                speed_kmh=speed,
                throttle=float(t.m_throttle),
                brake=float(t.m_brake),
                gear=int(t.m_gear),
                rpm=int(t.m_engineRPM),
                drs=int(t.m_drs),
                lap_num=st.lap_num,
                flashback=flash,
            ))

        if t_now >= self._next_detail_t:
            self._next_detail_t = t_now + self._detail_dt
            d = self._damage
            wear = d.m_tyresWear if d is not None else (0.0, 0.0, 0.0, 0.0)
            dmg = d.m_tyresDamage if d is not None else (0, 0, 0, 0)
            self._pending_detail.append((
                uid, i, st.lap_num, st.detail_idx,
                float(t.m_brakesTemperature[0]), float(t.m_brakesTemperature[1]),
                float(t.m_brakesTemperature[2]), float(t.m_brakesTemperature[3]),
                float(t.m_tyresSurfaceTemperature[0]), float(t.m_tyresSurfaceTemperature[1]),
                float(t.m_tyresSurfaceTemperature[2]), float(t.m_tyresSurfaceTemperature[3]),
                float(t.m_tyresInnerTemperature[0]), float(t.m_tyresInnerTemperature[1]),
                float(t.m_tyresInnerTemperature[2]), float(t.m_tyresInnerTemperature[3]),
                float(t.m_tyresPressure[0]), float(t.m_tyresPressure[1]),
                float(t.m_tyresPressure[2]), float(t.m_tyresPressure[3]),
                float(wear[0]), float(wear[1]), float(wear[2]), float(wear[3]),
                float(dmg[0]), float(dmg[1]), float(dmg[2]), float(dmg[3]),
                int(t.m_surfaceType[0]), int(t.m_surfaceType[1]),
                int(t.m_surfaceType[2]), int(t.m_surfaceType[3]),
                float(t.m_engineTemperature),
            ))
            st.detail_idx += 1

    def _on_status(self, pkt: PacketCarStatus) -> None:
        i = self.player_car_index
        if i >= MAX_CARS:
            return
        s = pkt.m_carStatusData[i]
        self._status = s
        self._drs_allowed = int(s.m_drsAllowed)

        st = self.cars.get(i)
        if st is None:
            return

        compound = int(s.m_actualTyreCompound)
        age = int(s.m_tyresAgeLaps)
        if st.compound_actual and compound and compound != st.compound_actual:
            st.stint_index += 1
        elif st.tyre_age and age < st.tyre_age:
            # возраст шины уменьшился — новый комплект (пит-стоп)
            st.stint_index += 1
        st.compound_actual = compound
        st.compound_visual = int(s.m_visualTyreCompound)
        st.tyre_age = age
        if st.fuel_at_start is None:
            st.fuel_at_start = float(s.m_fuelInTank)
        st.ers_deployed_max = max(st.ers_deployed_max, float(s.m_ersDeployedThisLap))

        uid = int(self.session_uid or 0)
        if self.session_time >= self._next_status_t:
            self._next_status_t = self.session_time + self._status_dt
            self._pending_status.append((
                uid, i, st.lap_num, st.status_idx,
                float(s.m_fuelInTank), float(s.m_fuelRemainingLaps),
                float(s.m_ersStoreEnergy), float(s.m_ersDeployedThisLap),
                float(s.m_ersHarvestedThisLapMGUK) + float(s.m_ersHarvestedThisLapMGUH),
                float(s.m_enginePowerICE), float(s.m_enginePowerMGUK),
            ))
            st.status_idx += 1

    def _on_motion(self, pkt: PacketMotion) -> None:
        i = self.player_car_index
        if i >= MAX_CARS:
            return
        m = pkt.m_carMotionData[i]
        self._motion = {
            "x": float(m.m_worldPositionX),
            "y": float(m.m_worldPositionY),
            "z": float(m.m_worldPositionZ),
            "vx": float(m.m_worldVelocityX),
            "vy": float(m.m_worldVelocityY),
            "vz": float(m.m_worldVelocityZ),
            "g_lat": float(m.m_gForceLateral),
            "g_lon": float(m.m_gForceLongitudinal),
            "g_vert": float(m.m_gForceVertical),
            "yaw": float(m.m_yaw),
            "pitch": float(m.m_pitch),
            "roll": float(m.m_roll),
        }

    def _on_motion_ex(self, pkt: PacketMotionEx) -> None:
        self._motion_ex = {
            "front_wheels_angle": float(pkt.m_frontWheelsAngle),
            "chassis_yaw": float(pkt.m_chassisYaw),
            "chassis_pitch": float(pkt.m_chassisPitch),
            "local_vx": float(pkt.m_localVelocityX),
            "local_vy": float(pkt.m_localVelocityY),
            "local_vz": float(pkt.m_localVelocityZ),
        }

    def _on_damage(self, pkt: PacketCarDamage) -> None:
        i = self.player_car_index
        if i < MAX_CARS:
            self._damage = pkt.m_carDamageData[i]

    def _on_final(self, pkt: PacketFinalClassification) -> None:
        uid = int(self.session_uid or 0)
        n = int(pkt.m_numCars)
        for i, f in enumerate(pkt.m_classification[:n] if n else pkt.m_classification):
            if i >= MAX_CARS or int(f.m_position) == 0:
                continue
            row = {
                "position": int(f.m_position),
                "num_laps": int(f.m_numLaps),
                "grid_position": int(f.m_gridPosition),
                "points": int(f.m_points),
                "num_pit_stops": int(f.m_numPitStops),
                "result_status": int(f.m_resultStatus),
                "result_reason": int(f.m_resultReason),
                "best_lap_ms": int(f.m_bestLapTimeInMS),
                "total_race_time": float(f.m_totalRaceTime),
                "penalties_time": int(f.m_penaltiesTime),
                "num_penalties": int(f.m_numPenalties),
                "stints_actual_json": list(f.m_tyreStintsActual[: int(f.m_numTyreStints)]),
                "stints_visual_json": list(f.m_tyreStintsVisual[: int(f.m_numTyreStints)]),
                "stints_end_laps_json": list(f.m_tyreStintsEndLaps[: int(f.m_numTyreStints)]),
            }
            self.finals[i] = row
            import json as _json
            self._queue(
                "upsert_final_classification", uid, i,
                **{k: (_json.dumps(v) if isinstance(v, list) else v) for k, v in row.items()},
            )

    def _on_session_history(self, pkt: PacketSessionHistory) -> None:
        """Авторитетная история кругов одной машины (20 Гц, по кругу)."""
        uid = int(self.session_uid or 0)
        car_idx = int(pkt.m_carIdx)
        st = self._car(car_idx)
        written = self._laps_written.setdefault(car_idx, set())
        n_laps = min(int(pkt.m_numLaps), 100)
        import json as _json

        for lap_i in range(n_laps):
            h = pkt.m_lapHistoryData[lap_i]
            lap_ms = int(h.m_lapTimeInMS)
            lap_num = lap_i + 1
            if lap_ms <= 0 or lap_num in written:
                continue
            flags = int(h.m_lapValidBitFlags)
            s1 = sector_ms(h.m_sector1TimeMinutesPart, h.m_sector1TimeMSPart)
            s2 = sector_ms(h.m_sector2TimeMinutesPart, h.m_sector2TimeMSPart)
            s3 = sector_ms(h.m_sector3TimeMinutesPart, h.m_sector3TimeMSPart)
            rec = LapRecord(
                session_uid=uid,
                car_index=car_idx,
                lap_num=lap_num,
                lap_time_ms=lap_ms,
                sector1_ms=s1, sector2_ms=s2, sector3_ms=s3,
                valid=1 if flags & VALID_LAP else 0,
                s1_valid=1 if flags & VALID_S1 else 0,
                s2_valid=1 if flags & VALID_S2 else 0,
                s3_valid=1 if flags & VALID_S3 else 0,
            )
            self._queue("upsert_lap", rec)
            written.add(lap_num)
            hist = self._car_history.setdefault(car_idx, [])
            hist.append({
                "lap_num": lap_num, "lap_time_ms": lap_ms,
                "s1_ms": s1, "s2_ms": s2, "s3_ms": s3,
                "valid": rec.valid, "stint_index": 0,
                "tyre_age_laps": 0, "compound_actual": 0,
                "compound_visual": 0, "is_pit_lap": False,
            })

        n_stints = int(pkt.m_numTyreStints)
        stints = [
            {
                "end_lap": int(pkt.m_tyreStintsHistoryData[k].m_endLap),
                "actual": int(pkt.m_tyreStintsHistoryData[k].m_tyreActualCompound),
                "visual": int(pkt.m_tyreStintsHistoryData[k].m_tyreVisualCompound),
            }
            for k in range(min(n_stints, 8))
        ]
        if stints:
            start = 1
            for idx, s in enumerate(stints):
                end = int(s["end_lap"])
                self._queue(
                    "upsert_stint", uid, car_idx, idx,
                    start_lap=start, end_lap=end,
                    compound_actual=s["actual"], compound_visual=s["visual"],
                    laps_count=max(0, end - start + 1), avg_lap_ms=0,
                    deg_ms_per_lap=0.0,
                )
                start = end + 1

    def _on_tyre_sets(self, pkt: PacketTyreSets) -> None:
        uid = int(self.session_uid or 0)
        car_idx = int(pkt.m_carIdx)
        for k, ts in enumerate(pkt.m_tyreSetData):
            if int(ts.m_available) == 0 and int(ts.m_fitted) == 0:
                continue
            self._queue(
                "upsert_tyre_set", uid, car_idx, k,
                compound_actual=int(ts.m_actualTyreCompound),
                compound_visual=int(ts.m_visualTyreCompound),
                wear=int(ts.m_wear),
                available=int(ts.m_available),
                recommended_session=int(ts.m_recommendedSession),
                life_span=int(ts.m_lifeSpan),
                usable_life=int(ts.m_usableLife),
                lap_delta_ms=int(ts.m_lapDeltaTime),
                fitted=int(ts.m_fitted),
            )

    def _on_lap_positions(self, pkt: PacketLapPositions) -> None:
        uid = int(self.session_uid or 0)
        num_laps = int(pkt.m_numLaps)
        lap_start = int(pkt.m_lapStart)
        rows: list[tuple[int, int, int, int]] = []
        for row in range(min(num_laps, 50)):
            lap_num = lap_start + row + 1
            for car in range(MAX_CARS):
                pos = int(pkt.m_positionForVehicleIdx[row][car])
                if pos > 0:
                    rows.append((uid, lap_num, car, pos))
        if rows:
            self._pending_lap_pos.extend(rows)

    def _on_event(self, parsed: ParsedPacket, result: PacketResult) -> None:
        ev = parsed.event
        if ev is None:
            return
        uid = int(self.session_uid or 0)
        d = ev.details
        code = ev.code
        result.event_code = code

        car_idx = d.get("vehicle_idx")
        other_idx: int | None = None
        if code == "COLL":
            car_idx = d.get("vehicle1_idx")
            other_idx = d.get("vehicle2_idx")
        elif code == "OVTK":
            car_idx = d.get("overtaking_vehicle_idx")
            other_idx = d.get("being_overtaken_vehicle_idx")

        payload = dict(d)
        name = self.participants.get(int(car_idx), {}).get("name") if car_idx is not None else None
        if name:
            payload["name"] = name
        self._queue(
            "insert_event", uid, float(parsed.header.session_time), code,
            int(car_idx) if car_idx is not None else None,
            other_idx, payload,
        )

        if code == "FLBK":
            # игра откатилась назад: помечаем текущий круг и сбрасываем буферы
            self._flashback_seen = True
            t_back = float(d.get("session_time_s", parsed.header.session_time))
            self._flashback_until = max(self._flashback_until, parsed.header.session_time + 0.5)
            st = self.cars.get(self.player_car_index)
            if st is not None:
                self._mark_flashback_lap(self.player_car_index, st, st.sample_idx)
                self._reset_lap_buffers(st)
                st.s1_ms = 0
                st.s2_ms = 0
            result.flashback = True
            log.info("flashback to t=%.2f s", t_back)
