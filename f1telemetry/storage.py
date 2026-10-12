"""SQLite-хранилище: схема, writer-поток с батч-коммитом, запросы для UI.

Модуль не импортирует Qt. Схema — AGENTS.md, раздел 5 (таблицы можно
расширять, но не переименовывать). Режим ``WAL``, батч-коммит каждые ~0.75 с
или по 500 записей.

Единицы измерения: миллисекунды (времена кругов/секторов), метры (дистанция),
километры в час (скорость), килограммы (топливо), Джоули (ERS),
проценты (износ/повреждения), градусы Цельсия (температуры).
"""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

WAL_TIMEOUT_S = 10.0


def _adapt_int(v: int) -> int:
    """u64 из игры → signed int64 для SQLite (бит-в-бит, two's complement).

    sessionUID в F1 25 — это u64 (полный диапазон), а SQLite INTEGER —
    знаковый int64. Без конвертации UID вида 1.4e19 роняет каждый op
    с OverflowError. Значения других колонок всегда влезают в int64,
    адаптер пропускает их без изменений.
    """
    if -0x8000000000000000 <= v < 0x8000000000000000:
        return v
    v &= 0xFFFFFFFFFFFFFFFF
    return v - 0x10000000000000000 if v >= 0x8000000000000000 else v


def _uid_from_db(v: int | None) -> int | None:
    """signed int64 из SQLite → исходный u64 sessionUID."""
    if v is not None and v < 0:
        return v + 0x10000000000000000
    return v


sqlite3.register_adapter(int, _adapt_int)

__all__ = [
    "SCHEMA_VERSION",
    "Storage",
    "StorageWriter",
    "utc_now_iso",
    "LapRecord",
    "SessionInfo",
    "utc_now_iso",
]


def utc_now_iso() -> str:
    """Текущее время в ISO-8601 UTC (``2026-01-01T12:00:00+00:00``)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- Схема -------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
  session_uid INTEGER PRIMARY KEY,
  packet_format INTEGER, game_year INTEGER, game_version TEXT,
  track_id INTEGER, track_name TEXT,
  session_type INTEGER, session_type_name TEXT,
  weather INTEGER, total_laps INTEGER, track_length_m INTEGER,
  season_link_id INTEGER, weekend_link_id INTEGER, session_link_id INTEGER,
  identical_session INTEGER,
  started_at_utc TEXT, ended_at_utc TEXT,
  player_car_index INTEGER, num_cars INTEGER,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS participants (
  session_uid INTEGER, car_index INTEGER,
  name TEXT, driver_id INTEGER, team_id INTEGER, team_name TEXT,
  race_number INTEGER, nationality INTEGER, ai_controlled INTEGER,
  platform INTEGER, telemetry_public INTEGER,
  PRIMARY KEY (session_uid, car_index)
);

CREATE TABLE IF NOT EXISTS laps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_uid INTEGER, car_index INTEGER, lap_num INTEGER,
  lap_time_ms INTEGER, sector1_ms INTEGER, sector2_ms INTEGER, sector3_ms INTEGER,
  valid INTEGER, s1_valid INTEGER, s2_valid INTEGER, s3_valid INTEGER,
  position INTEGER, stint_index INTEGER,
  compound_actual INTEGER, compound_visual INTEGER, tyre_age_laps INTEGER,
  fuel_at_start REAL, ers_deployed_j REAL,
  top_speed_kmh REAL, avg_throttle REAL, brake_time_pct REAL, full_throttle_pct REAL,
  UNIQUE (session_uid, car_index, lap_num) ON CONFLICT REPLACE
);

CREATE TABLE IF NOT EXISTS telemetry (
  session_uid INTEGER, car_index INTEGER, lap_num INTEGER, sample_idx INTEGER,
  session_time REAL, lap_distance_m REAL,
  speed_kmh REAL, throttle REAL, brake REAL, steer REAL, gear INTEGER, rpm INTEGER,
  drs INTEGER, ers_mode INTEGER,
  pos_x REAL, pos_y REAL, pos_z REAL,
  world_vel_x REAL, world_vel_y REAL, world_vel_z REAL,
  g_lat REAL, g_lon REAL, g_vert REAL,
  yaw REAL, pitch REAL, roll REAL,
  flashback INTEGER DEFAULT 0,
  PRIMARY KEY (session_uid, car_index, lap_num, sample_idx)
);
CREATE INDEX IF NOT EXISTS idx_telemetry_lap
  ON telemetry(session_uid, car_index, lap_num, lap_distance_m);

CREATE TABLE IF NOT EXISTS car_telemetry_detail (
  session_uid INTEGER, car_index INTEGER, lap_num INTEGER, sample_idx INTEGER,
  brake_temp_rl REAL, brake_temp_rr REAL, brake_temp_fl REAL, brake_temp_fr REAL,
  tyre_surf_rl REAL, tyre_surf_rr REAL, tyre_surf_fl REAL, tyre_surf_fr REAL,
  tyre_inner_rl REAL, tyre_inner_rr REAL, tyre_inner_fl REAL, tyre_inner_fr REAL,
  tyre_press_rl REAL, tyre_press_rr REAL, tyre_press_fl REAL, tyre_press_fr REAL,
  tyre_wear_rl REAL, tyre_wear_rr REAL, tyre_wear_fl REAL, tyre_wear_fr REAL,
  tyre_dmg_rl REAL, tyre_dmg_rr REAL, tyre_dmg_fl REAL, tyre_dmg_fr REAL,
  surface_rl INTEGER, surface_rr INTEGER, surface_fl INTEGER, surface_fr INTEGER,
  engine_temp REAL,
  PRIMARY KEY (session_uid, car_index, lap_num, sample_idx)
);

CREATE TABLE IF NOT EXISTS car_status_samples (
  session_uid INTEGER, car_index INTEGER, lap_num INTEGER, sample_idx INTEGER,
  fuel_in_tank REAL, fuel_remaining_laps REAL, ers_store_j REAL,
  ers_deployed_lap_j REAL, ers_harvested_lap_j REAL,
  engine_power_ice_w REAL, engine_power_mguk_w REAL,
  PRIMARY KEY (session_uid, car_index, lap_num, sample_idx)
);

CREATE TABLE IF NOT EXISTS stints (
  session_uid INTEGER, car_index INTEGER, stint_index INTEGER,
  end_lap INTEGER, start_lap INTEGER,
  compound_actual INTEGER, compound_visual INTEGER,
  laps_count INTEGER, avg_lap_ms INTEGER, deg_ms_per_lap REAL,
  PRIMARY KEY (session_uid, car_index, stint_index)
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_uid INTEGER, session_time REAL, code TEXT,
  car_index INTEGER, other_car_index INTEGER, payload_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_uid, session_time);

CREATE TABLE IF NOT EXISTS final_classification (
  session_uid INTEGER, car_index INTEGER, position INTEGER, num_laps INTEGER,
  grid_position INTEGER, points INTEGER, num_pit_stops INTEGER,
  result_status INTEGER, result_reason INTEGER, best_lap_ms INTEGER,
  total_race_time REAL, penalties_time INTEGER, num_penalties INTEGER,
  stints_actual_json TEXT, stints_visual_json TEXT, stints_end_laps_json TEXT,
  PRIMARY KEY (session_uid, car_index)
);

CREATE TABLE IF NOT EXISTS setups (
  session_uid INTEGER, car_index INTEGER, captured_at TEXT, setup_json TEXT,
  PRIMARY KEY (session_uid, car_index)
);

CREATE TABLE IF NOT EXISTS tyre_sets (
  session_uid INTEGER, car_index INTEGER, set_index INTEGER,
  compound_actual INTEGER, compound_visual INTEGER, wear REAL, available INTEGER,
  recommended_session INTEGER, life_span INTEGER, usable_life INTEGER,
  lap_delta_ms INTEGER, fitted INTEGER, updated_at TEXT,
  PRIMARY KEY (session_uid, car_index, set_index)
);

CREATE TABLE IF NOT EXISTS lap_positions (
  session_uid INTEGER, lap_num INTEGER, car_index INTEGER, position INTEGER,
  PRIMARY KEY (session_uid, lap_num, car_index)
);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_laps_session ON laps(session_uid, car_index, lap_num);
CREATE INDEX IF NOT EXISTS idx_participants_session ON participants(session_uid);
CREATE INDEX IF NOT EXISTS idx_lap_positions_session ON lap_positions(session_uid, lap_num);
CREATE INDEX IF NOT EXISTS idx_stints_session ON stints(session_uid, car_index);
"""


# --- Записываемые записи -----------------------------------------------------

@dataclass(frozen=True, slots=True)
class LapRecord:
    """Готовая строка таблицы ``laps``."""

    session_uid: int
    car_index: int
    lap_num: int
    lap_time_ms: int
    sector1_ms: int = 0
    sector2_ms: int = 0
    sector3_ms: int = 0
    valid: int = 1
    s1_valid: int = 1
    s2_valid: int = 1
    s3_valid: int = 1
    position: int = 0
    stint_index: int = 0
    compound_actual: int = 0
    compound_visual: int = 0
    tyre_age_laps: int = 0
    fuel_at_start: float | None = None
    ers_deployed_j: float | None = None
    top_speed_kmh: float | None = None
    agg_throttle: float | None = None
    brake_time_pct: float | None = None
    full_throttle_pct: float | None = None


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """Строка таблицы ``sessions`` для списка в UI."""

    session_uid: int
    track_id: int | None
    track_name: str | None
    session_type: int | None
    session_type_name: str | None
    weather: int | None
    total_laps: int | None
    track_length_m: int | None
    started_at_utc: str | None
    ended_at_utc: str | None
    num_cars: int | None
    best_lap_ms: int | None
    lap_count: int | None


# --- Хранилище ---------------------------------------------------------------

class Storage:
    """Синхронная обёртка над одним соединением SQLite."""

    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = read_only
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        uri = f"file:{self.path.as_posix()}?mode={'ro' if read_only else 'rwc'}"
        self.conn = sqlite3.connect(
            uri, uri=True, timeout=WAL_TIMEOUT_S, check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row
        if not read_only:
            self._configure()
            self.migrate()

    def _configure(self) -> None:
        """WAL + NORMAL + отключённые ненужные фичи."""
        cur = self.conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=OFF")
        cur.execute("PRAGMA temp_store=MEMORY")
        cur.execute(f"PRAGMA busy_timeout={int(WAL_TIMEOUT_S * 1000)}")
        self.conn.commit()

    # --- миграции ----------------------------------------------------------

    def migrate(self) -> None:
        """Создаёт/обновляет схему до :data:`SCHEMA_VERSION`."""
        cur = self.conn.cursor()
        cur.execute("PRAGMA user_version")
        current = int(cur.fetchone()[0])
        if current >= SCHEMA_VERSION:
            return
        cur.executescript(SCHEMA_SQL)
        cur.executescript(INDEX_SQL)
        cur.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self.conn.commit()
        log.info("storage schema migrated to v%s (%s)", SCHEMA_VERSION, self.path)

    def close(self) -> None:
        """Закрывает соединение."""
        try:
            self.conn.commit()
        except sqlite3.Error:  # pragma: no cover
            pass
        self.conn.close()

    # --- запись -------------------------------------------------------------

    def upsert_session(
        self,
        session_uid: int,
        *,
        packet_format: int | None = None,
        game_year: int | None = None,
        game_version: str | None = None,
        track_id: int | None = None,
        track_name: str | None = None,
        session_type: int | None = None,
        session_type_name: str | None = None,
        weather: int | None = None,
        total_laps: int | None = None,
        track_length_m: int | None = None,
        season_link_id: int | None = None,
        weekend_link_id: int | None = None,
        session_link_id: int | None = None,
        identical_session: int | None = None,
        started_at_utc: str | None = None,
        ended_at_utc: str | None = None,
        player_car_index: int | None = None,
        num_cars: int | None = None,
        notes: str | None = None,
    ) -> None:
        """Вставляет или обновляет строку сессии (поля из последнего пакета Session)."""
        self.conn.execute(
            """
            INSERT INTO sessions (
              session_uid, packet_format, game_year, game_version,
              track_id, track_name, session_type, session_type_name,
              weather, total_laps, track_length_m,
              season_link_id, weekend_link_id, session_link_id, identical_session,
              started_at_utc, ended_at_utc, player_car_index, num_cars, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(session_uid) DO UPDATE SET
              packet_format=excluded.packet_format,
              game_year=excluded.game_year,
              game_version=excluded.game_version,
              track_id=COALESCE(excluded.track_id, sessions.track_id),
              track_name=COALESCE(excluded.track_name, sessions.track_name),
              session_type=COALESCE(excluded.session_type, sessions.session_type),
              session_type_name=COALESCE(excluded.session_type_name, sessions.session_type_name),
              weather=COALESCE(excluded.weather, sessions.weather),
              total_laps=COALESCE(excluded.total_laps, sessions.total_laps),
              track_length_m=COALESCE(excluded.track_length_m, sessions.track_length_m),
              season_link_id=COALESCE(excluded.season_link_id, sessions.season_link_id),
              weekend_link_id=COALESCE(excluded.weekend_link_id, sessions.weekend_link_id),
              session_link_id=COALESCE(excluded.session_link_id, sessions.session_link_id),
              identical_session=COALESCE(excluded.identical_session, sessions.identical_session),
              started_at_utc=COALESCE(sessions.started_at_utc, excluded.started_at_utc),
              ended_at_utc=COALESCE(excluded.ended_at_utc, sessions.ended_at_utc),
              player_car_index=COALESCE(excluded.player_car_index, sessions.player_car_index),
              num_cars=COALESCE(excluded.num_cars, sessions.num_cars),
              notes=COALESCE(excluded.notes, sessions.notes)
            """,
            (
                session_uid, packet_format, game_year, game_version,
                track_id, track_name, session_type, session_type_name,
                weather, total_laps, track_length_m,
                season_link_id, weekend_link_id, session_link_id, identical_session,
                started_at_utc, ended_at_utc, player_car_index, num_cars, notes,
            ),
        )

    def close_session(self, session_uid: int, ended_at_utc: str | None = None) -> None:
        """Помечает сессию завершённой."""
        self.conn.execute(
            "UPDATE sessions SET ended_at_utc=? WHERE session_uid=? AND ended_at_utc IS NULL",
            (ended_at_utc or utc_now_iso(), session_uid),
        )

    def upsert_participant(self, session_uid: int, car_index: int, **fields: Any) -> None:
        """Вставляет/обновляет участника сессии."""
        allowed = {
            "name", "driver_id", "team_id", "team_name", "race_number",
            "nationality", "ai_controlled", "platform", "telemetry_public",
        }
        data = {k: v for k, v in fields.items() if k in allowed}
        cols = ["session_uid", "car_index", *data.keys()]
        vals: list[Any] = [session_uid, car_index, *data.values()]
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(
            f"{k}=excluded.{k}" for k in data
        )
        sql = (
            f"INSERT INTO participants ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(session_uid, car_index) DO UPDATE SET {updates}"
        )
        self.conn.execute(sql, vals)

    def upsert_lap(self, rec: LapRecord) -> None:
        """Вставляет/обновляет круг (UNIQUE session, car, lap)."""
        self.conn.execute(
            """
            INSERT INTO laps (
              session_uid, car_index, lap_num, lap_time_ms,
              sector1_ms, sector2_ms, sector3_ms,
              valid, s1_valid, s2_valid, s3_valid,
              position, stint_index, compound_actual, compound_visual, tyre_age_laps,
              fuel_at_start, ers_deployed_j,
              top_speed_kmh, avg_throttle, brake_time_pct, full_throttle_pct
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(session_uid, car_index, lap_num) DO UPDATE SET
              lap_time_ms=excluded.lap_time_ms,
              sector1_ms=excluded.sector1_ms,
              sector2_ms=excluded.sector2_ms,
              sector3_ms=excluded.sector3_ms,
              valid=excluded.valid, s1_valid=excluded.s1_valid,
              s2_valid=excluded.s2_valid, s3_valid=excluded.s3_valid,
              position=excluded.position, stint_index=excluded.stint_index,
              compound_actual=excluded.compound_actual,
              compound_visual=excluded.compound_visual,
              tyre_age_laps=excluded.tyre_age_laps,
              fuel_at_start=excluded.fuel_at_start,
              ers_deployed_j=excluded.ers_deployed_j,
              top_speed_kmh=excluded.top_speed_kmh,
              avg_throttle=excluded.avg_throttle,
              brake_time_pct=excluded.brake_time_pct,
              full_throttle_pct=excluded.full_throttle_pct
            """,
            (
                rec.session_uid, rec.car_index, rec.lap_num, rec.lap_time_ms,
                rec.sector1_ms, rec.sector2_ms, rec.sector3_ms,
                rec.valid, rec.s1_valid, rec.s2_valid, rec.s3_valid,
                rec.position, rec.stint_index, rec.compound_actual,
                rec.compound_visual, rec.tyre_age_laps,
                rec.fuel_at_start, rec.ers_deployed_j,
                rec.top_speed_kmh, rec.agg_throttle, rec.brake_time_pct,
                rec.full_throttle_pct,
            ),
        )

    def insert_telemetry(self, rows: Sequence[Sequence[Any]]) -> None:
        """Пакетная вставка точек телеметрии (прорежённых)."""
        if not rows:
            return
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO telemetry (
              session_uid, car_index, lap_num, sample_idx,
              session_time, lap_distance_m,
              speed_kmh, throttle, brake, steer, gear, rpm, drs, ers_mode,
              pos_x, pos_y, pos_z, world_vel_x, world_vel_y, world_vel_z,
              g_lat, g_lon, g_vert, yaw, pitch, roll, flashback
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )

    def insert_detail(self, rows: Sequence[Sequence[Any]]) -> None:
        """Пакетная вставка деталей шин/тормозов (10 Гц)."""
        if not rows:
            return
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO car_telemetry_detail (
              session_uid, car_index, lap_num, sample_idx,
              brake_temp_rl, brake_temp_rr, brake_temp_fl, brake_temp_fr,
              tyre_surf_rl, tyre_surf_rr, tyre_surf_fl, tyre_surf_fr,
              tyre_inner_rl, tyre_inner_rr, tyre_inner_fl, tyre_inner_fr,
              tyre_press_rl, tyre_press_rr, tyre_press_fl, tyre_press_fr,
              tyre_wear_rl, tyre_wear_rr, tyre_wear_fl, tyre_wear_fr,
              tyre_dmg_rl, tyre_dmg_rr, tyre_dmg_fl, tyre_dmg_fr,
              surface_rl, surface_rr, surface_fl, surface_fr,
              engine_temp
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )

    def insert_status_samples(self, rows: Sequence[Sequence[Any]]) -> None:
        """Пакетная вставка сэмплов статуса (топливо/ERS)."""
        if not rows:
            return
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO car_status_samples (
              session_uid, car_index, lap_num, sample_idx,
              fuel_in_tank, fuel_remaining_laps, ers_store_j,
              ers_deployed_lap_j, ers_harvested_lap_j,
              engine_power_ice_w, engine_power_mguk_w
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )

    def upsert_stint(
        self,
        session_uid: int,
        car_index: int,
        stint_index: int,
        *,
        start_lap: int,
        end_lap: int,
        compound_actual: int,
        compound_visual: int,
        laps_count: int,
        avg_lap_ms: int,
        deg_ms_per_lap: float,
    ) -> None:
        """Вставляет/обновляет агрегат стинта."""
        self.conn.execute(
            """
            INSERT INTO stints (
              session_uid, car_index, stint_index, start_lap, end_lap,
              compound_actual, compound_visual, laps_count, avg_lap_ms, deg_ms_per_lap
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(session_uid, car_index, stint_index) DO UPDATE SET
              start_lap=excluded.start_lap, end_lap=excluded.end_lap,
              compound_actual=excluded.compound_actual,
              compound_visual=excluded.compound_visual,
              laps_count=excluded.laps_count, avg_lap_ms=excluded.avg_lap_ms,
              deg_ms_per_lap=excluded.deg_ms_per_lap
            """,
            (
                session_uid, car_index, stint_index, start_lap, end_lap,
                compound_actual, compound_visual, laps_count, avg_lap_ms,
                deg_ms_per_lap,
            ),
        )

    def insert_event(
        self,
        session_uid: int,
        session_time: float,
        code: str,
        car_index: int | None,
        other_car_index: int | None,
        payload: dict[str, Any] | None,
    ) -> None:
        """Вставляет событие."""
        self.conn.execute(
            """
            INSERT INTO events (session_uid, session_time, code, car_index,
                                other_car_index, payload_json)
            VALUES (?,?,?,?,?,?)
            """,
            (
                session_uid, session_time, code, car_index, other_car_index,
                json.dumps(payload or {}, ensure_ascii=False),
            ),
        )

    def upsert_final_classification(self, session_uid: int, car_index: int, **f: Any) -> None:
        """Вставляет/обновляет финальную классификацию одной машины."""
        allowed = [
            "position", "num_laps", "grid_position", "points", "num_pit_stops",
            "result_status", "result_reason", "best_lap_ms", "total_race_time",
            "penalties_time", "num_penalties",
            "stints_actual_json", "stints_visual_json", "stints_end_laps_json",
        ]
        data = {k: f.get(k) for k in allowed if k in f}
        cols = ["session_uid", "car_index", *data.keys()]
        vals: list[Any] = [session_uid, car_index, *data.values()]
        updates = ",".join(f"{k}=excluded.{k}" for k in data)
        self.conn.execute(
            f"INSERT INTO final_classification ({','.join(cols)}) "
            f"VALUES ({','.join('?' for _ in cols)}) "
            f"ON CONFLICT(session_uid, car_index) DO UPDATE SET {updates}",
            vals,
        )

    def upsert_setup(self, session_uid: int, car_index: int, setup: dict[str, Any]) -> None:
        """Сохраняет сетап машины в JSON."""
        self.conn.execute(
            """
            INSERT INTO setups (session_uid, car_index, captured_at, setup_json)
            VALUES (?,?,?,?)
            ON CONFLICT(session_uid, car_index) DO UPDATE SET
              captured_at=excluded.captured_at, setup_json=excluded.setup_json
            """,
            (session_uid, car_index, utc_now_iso(), json.dumps(setup, ensure_ascii=False)),
        )

    def upsert_tyre_set(self, session_uid: int, car_index: int, set_index: int, **f: Any) -> None:
        """Вставляет/обновляет комплект шин."""
        allowed = [
            "compound_actual", "compound_visual", "wear", "available",
            "recommended_session", "life_span", "usable_life", "lap_delta_ms",
            "fitted",
        ]
        data = {k: f.get(k) for k in allowed if k in f}
        data["updated_at"] = utc_now_iso()
        cols = ["session_uid", "car_index", "set_index", *data.keys()]
        vals: list[Any] = [session_uid, car_index, set_index, *data.values()]
        updates = ",".join(f"{k}=excluded.{k}" for k in data)
        self.conn.execute(
            f"INSERT INTO tyre_sets ({','.join(cols)}) "
            f"VALUES ({','.join('?' for _ in cols)}) "
            f"ON CONFLICT(session_uid, car_index, set_index) DO UPDATE SET {updates}",
            vals,
        )

    def upsert_lap_positions(self, rows: Iterable[tuple[int, int, int, int]]) -> None:
        """Пакетная вставка позиций ``(session_uid, lap_num, car_index, position)``."""
        self.conn.executemany(
            """
            INSERT INTO lap_positions (session_uid, lap_num, car_index, position)
            VALUES (?,?,?,?)
            ON CONFLICT(session_uid, lap_num, car_index) DO UPDATE SET
              position=excluded.position
            """,
            list(rows),
        )

    def mark_flashback(
        self, session_uid: int, car_index: int, lap_num: int, up_to_sample: int
    ) -> None:
        """Помечает сэмплы телеметрии как флешбэк (ничего не удаляя)."""
        self.conn.execute(
            """
            UPDATE telemetry SET flashback=1
            WHERE session_uid=? AND car_index=? AND lap_num=? AND sample_idx<=?
            """,
            (session_uid, car_index, lap_num, up_to_sample),
        )

    def delete_session(self, session_uid: int) -> int:
        """Удаляет сессию и все связанные строки. Возвращает число затронутых таблиц."""
        tables = [
            "sessions", "participants", "laps", "telemetry",
            "car_telemetry_detail", "car_status_samples", "stints", "events",
            "final_classification", "setups", "tyre_sets", "lap_positions",
        ]
        for t in tables:
            key = "session_uid"
            self.conn.execute(f"DELETE FROM {t} WHERE {key}=?", (session_uid,))
        return len(tables)

    def backfill_lap_tyres(
        self,
        session_uid: int,
        car_index: int,
        compound_actual: int,
        compound_visual: int,
        upto_lap: int,
    ) -> int:
        """Дозаполняет compound в уже записанных кругах.

        Игра может слать ``actualTyreCompound=0`` в первые круги — круги
        пишутся с compound=0, а первый «настоящий» Car Status приходит позже.
        Обновляются только строки с ``compound_actual=0`` и ``lap_num<=upto_lap``
        (значения других стинтов не трогаются). Возвращает число обновлённых строк.

        Args:
            session_uid: сессия (u64, знаковый int64 в SQLite).
            car_index: машина.
            compound_actual: фактический compound (16=C5 … 22=C6, 7=Inter, 8=Wet).
            compound_visual: визуальный compound (16=Soft, 17=Medium, 18=Hard …).
            upto_lap: обновить круги с номером ≤ этого.

        Returns:
            Число обновлённых строк ``laps``.
        """
        cur = self.conn.execute(
            "UPDATE laps SET compound_actual=?, compound_visual=?"
            " WHERE session_uid=? AND car_index=?"
            " AND compound_actual=0 AND lap_num<=?",
            (compound_actual, compound_visual, session_uid, car_index, upto_lap),
        )
        return int(cur.rowcount)

    def anonymize(self, session_uid: int | None = None) -> int:
        """Анонимизация: имена участников → «Driver N», name из событий удаляется.

        N = car_index + 1 (стабильно в пределах сессии). Никнеймы из
        мультиплеера лежат в ``participants.name`` и в ``events.payload_json``
        (ключ ``name``) — прогоняйте перед передачей файла третьим лицам.

        Args:
            session_uid: только одна сессия; None — все сессии в БД.

        Returns:
            Число обработанных сессий.
        """
        if session_uid is None:
            uids = [int(r[0]) for r in self.conn.execute(
                "SELECT session_uid FROM sessions")]
        else:
            uids = [int(session_uid)]
        for uid in uids:
            self.conn.execute(
                "UPDATE participants SET name='Driver ' || (car_index + 1)"
                " WHERE session_uid=?",
                (uid,),
            )
            rows = self.conn.execute(
                "SELECT id, payload_json FROM events WHERE session_uid=?"
                " AND payload_json LIKE '%\"name\"%'",
                (uid,),
            ).fetchall()
            for r in rows:
                try:
                    payload = json.loads(r["payload_json"] or "{}")
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and "name" in payload:
                    payload.pop("name", None)
                    self.conn.execute(
                        "UPDATE events SET payload_json=? WHERE id=?",
                        (json.dumps(payload, ensure_ascii=False), r["id"]),
                    )
        self.conn.commit()
        return len(uids)

    def commit(self) -> None:
        """Коммит накопленных изменений."""
        self.conn.commit()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        """Выполняет SQL-запрос."""
        return self.conn.execute(sql, params)

    # --- запросы -----------------------------------------------------------

    def list_sessions(
        self,
        *,
        track_id: int | None = None,
        session_type: int | None = None,
        limit: int = 500,
    ) -> list[SessionInfo]:
        """Список сессий (новые сверху) с лучшим кругом и числом кругов."""
        sql = [
            "SELECT s.session_uid, s.track_id, s.track_name, s.session_type,",
            "       s.session_type_name, s.weather, s.total_laps, s.track_length_m,",
            "       s.started_at_utc, s.ended_at_utc, s.num_cars,",
            "       (SELECT MIN(lap_time_ms) FROM laps l",
            "         WHERE l.session_uid=s.session_uid AND l.valid=1",
            "           AND l.lap_time_ms>0) AS best_lap_ms,",
            "       (SELECT COUNT(*) FROM laps l WHERE l.session_uid=s.session_uid) AS lap_count",
            "  FROM sessions s WHERE 1=1",
        ]
        params: list[Any] = []
        if track_id is not None:
            sql.append(" AND s.track_id=?")
            params.append(track_id)
        if session_type is not None:
            sql.append(" AND s.session_type=?")
            params.append(session_type)
        sql.append(" ORDER BY s.session_uid DESC LIMIT ?")
        params.append(limit)
        rows = self.conn.execute("".join(sql), params).fetchall()
        out: list[SessionInfo] = []
        for r in rows:
            d = dict(r)
            d["session_uid"] = int(_uid_from_db(d["session_uid"]) or 0)
            out.append(SessionInfo(**d))
        return out

    def get_session(self, session_uid: int) -> sqlite3.Row | None:
        """Строка сессии или ``None``."""
        return self.conn.execute(
            "SELECT * FROM sessions WHERE session_uid=?", (session_uid,)
        ).fetchone()

    def list_participants(self, session_uid: int) -> list[sqlite3.Row]:
        """Участники сессии, отсортированные по индексу машины."""
        return self.conn.execute(
            "SELECT * FROM participants WHERE session_uid=? ORDER BY car_index",
            (session_uid,),
        ).fetchall()

    def laps(
        self, session_uid: int, car_index: int | None = None
    ) -> list[sqlite3.Row]:
        """Круги сессии (опционально одной машины) по возрастанию номера."""
        if car_index is None:
            return self.conn.execute(
                "SELECT * FROM laps WHERE session_uid=? ORDER BY car_index, lap_num",
                (session_uid,),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM laps WHERE session_uid=? AND car_index=? ORDER BY lap_num",
            (session_uid, car_index),
        ).fetchall()

    def best_sectors(
        self, session_uid: int, car_index: int | None = None
    ) -> dict[str, int]:
        """Лучшие валидные секторы сессии (мс): ``s1``/``s2``/``s3``/``lap``/``theoretical``."""
        sql = [
            "SELECT MIN(sector1_ms) s1, MIN(sector2_ms) s2, MIN(sector3_ms) s3,",
            "       MIN(lap_time_ms) lap FROM laps",
            " WHERE session_uid=? AND valid=1",
            "   AND sector1_ms>0 AND sector2_ms>0 AND sector3_ms>0",
        ]
        params: list[Any] = [session_uid]
        if car_index is not None:
            sql.append(" AND car_index=?")
            params.append(car_index)
        row = self.conn.execute("".join(sql), params).fetchone()
        out = {k: int(row[k] or 0) for k in ("s1", "s2", "s3", "lap")}
        out["theoretical"] = out["s1"] + out["s2"] + out["s3"] if all(
            out[k] > 0 for k in ("s1", "s2", "s3")
        ) else 0
        return out

    def telemetry_for_lap(
        self, session_uid: int, car_index: int, lap_num: int
    ) -> list[sqlite3.Row]:
        """Точки телеметрии круга, упорядоченные по дистанции."""
        return self.conn.execute(
            """
            SELECT * FROM telemetry
             WHERE session_uid=? AND car_index=? AND lap_num=?
             ORDER BY lap_distance_m, sample_idx
            """,
            (session_uid, car_index, lap_num),
        ).fetchall()

    def stints(self, session_uid: int, car_index: int | None = None) -> list[sqlite3.Row]:
        """Стинты сессии (или одной машины)."""
        sql = "SELECT * FROM stints WHERE session_uid=?"
        params: list[Any] = [session_uid]
        if car_index is not None:
            sql += " AND car_index=?"
            params.append(car_index)
        sql += " ORDER BY car_index, stint_index"
        return self.conn.execute(sql, params).fetchall()

    def events(self, session_uid: int) -> list[sqlite3.Row]:
        """События сессии по времени."""
        return self.conn.execute(
            "SELECT * FROM events WHERE session_uid=? ORDER BY session_time, id",
            (session_uid,),
        ).fetchall()

    def lap_positions(self, session_uid: int) -> list[sqlite3.Row]:
        """Позиции по кругам для графика позиций."""
        return self.conn.execute(
            "SELECT lap_num, car_index, position FROM lap_positions"
            " WHERE session_uid=? ORDER BY lap_num, car_index",
            (session_uid,),
        ).fetchall()

    def final_classification(self, session_uid: int) -> list[sqlite3.Row]:
        """Итоговая классификация по позициям."""
        return self.conn.execute(
            "SELECT * FROM final_classification WHERE session_uid=? ORDER BY position",
            (session_uid,),
        ).fetchall()

    def setups(self, session_uid: int) -> list[sqlite3.Row]:
        """Сетапы машин сессии."""
        return self.conn.execute(
            "SELECT * FROM setups WHERE session_uid=? ORDER BY car_index",
            (session_uid,),
        ).fetchall()

    def tyre_sets(self, session_uid: int, car_index: int | None = None) -> list[sqlite3.Row]:
        """Комплекты шин сессии."""
        sql = "SELECT * FROM tyre_sets WHERE session_uid=?"
        params: list[Any] = [session_uid]
        if car_index is not None:
            sql += " AND car_index=?"
            params.append(car_index)
        return self.conn.execute(sql + " ORDER BY car_index, set_index", params).fetchall()

    def stats(self, session_uid: int | None = None) -> dict[str, Any]:
        """Сводка по базе (или одной сессии) для CLI ``stats``."""
        w = "" if session_uid is None else " WHERE session_uid=?"
        p: list[Any] = [] if session_uid is None else [session_uid]

        def scalar(sql: str, params: Sequence[Any] = p) -> Any:
            return self.conn.execute(sql + w, params).fetchone()[0]

        laps_rows = self.conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT car_index),"
            " MIN(CASE WHEN lap_time_ms>0 THEN lap_time_ms END),"
            " AVG(CASE WHEN lap_time_ms>0 THEN lap_time_ms END)"
            " FROM laps" + w, p,
        ).fetchone()
        return {
            "sessions": self.conn.execute(
                "SELECT COUNT(*)" + (" FROM sessions" if session_uid is None
                                     else " FROM sessions WHERE session_uid=?"), p,
            ).fetchone()[0],
            "laps": int(laps_rows[0] or 0),
            "cars_with_laps": int(laps_rows[1] or 0),
            "best_lap_ms": laps_rows[2],
            "avg_lap_ms": int(laps_rows[3] or 0) if laps_rows[3] else None,
            "telemetry_points": int(scalar("SELECT COUNT(*) FROM telemetry") or 0),
            "events": int(scalar("SELECT COUNT(*) FROM events") or 0),
            "participants": int(scalar("SELECT COUNT(*) FROM participants") or 0),
            "stints": int(scalar("SELECT COUNT(*) FROM stints") or 0),
            "size_bytes": self.path.stat().st_size if self.path.exists() else 0,
        }


# --- Writer-поток -------------------------------------------------------------

_SENTINEL = object()


class StorageWriter(threading.Thread):
    """Поток-писатель: очередь операций + батч-коммит.

    Использование::

        w = StorageWriter("data/f1.sqlite")
        w.start()
        w.submit("upsert_session", uid, track_id=7, ...)
        w.submit("insert_telemetry", rows)
        w.close()
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        flush_interval: float = 0.75,
        batch_size: int = 500,
        name: str = "storage-writer",
    ) -> None:
        super().__init__(name=name, daemon=True)
        self.db_path = Path(db_path)
        self.flush_interval = flush_interval
        self.batch_size = batch_size
        self.queue: queue.Queue[Any] = queue.Queue(maxsize=20000)
        self.storage: Storage | None = None
        self.error_count = 0
        self.rows_written = 0
        self.ops_written = 0
        self._stop = threading.Event()
        self._closed = False
        self._flush_request = threading.Event()

    def start(self) -> None:
        """Запускает поток (переопределяет ``Thread.start`` для создания БД)."""
        self.storage = Storage(self.db_path)
        super().start()

    def submit(self, op: str, *args: Any, **kwargs: Any) -> None:
        """Ставит операцию в очередь (не блокируясь надолго).

        Args:
            op: имя метода :class:`Storage`.
            args: позиционные аргументы метода.
            kwargs: именованные аргументы метода.
        """
        if self._closed:
            return
        try:
            self.queue.put_nowait((op, args, kwargs))
        except queue.Full:  # pragma: no cover — защита от зависания UI
            log.warning("storage queue full, dropping %s", op)

    def flush(self) -> None:
        """Запрашивает немедленный коммит (дождётся в ``join``/``close``)."""
        self._flush_request.set()

    def stats(self) -> dict[str, Any]:
        """Счётчики для панели диагностики."""
        return {
            "queued": self.queue.qsize(),
            "ops": self.ops_written,
            "rows": self.rows_written,
            "errors": self.error_count,
        }

    def run(self) -> None:  # pragma: no cover — покрыто интеграционными тестами
        assert self.storage is not None
        last_commit = 0.0
        pending = 0
        import time as _time

        while True:
            try:
                item = self.queue.get(timeout=0.2)
            except queue.Empty:
                item = None
            if item is _SENTINEL:
                break
            if item is not None:
                op, args, kwargs = item
                try:
                    getattr(self.storage, op)(*args, **kwargs)
                    self.ops_written += 1
                    pending += 1
                except Exception:
                    self.error_count += 1
                    log.exception("storage op %s failed", op)
                continue
            # очередь пуста: коммитим накопленное и выходим только по остановке
            now = _time.monotonic()
            if pending and (
                now - last_commit >= self.flush_interval
                or pending >= self.batch_size
                or self._flush_request.is_set()
            ):
                try:
                    self.storage.commit()
                except sqlite3.Error:
                    self.error_count += 1
                    log.exception("storage commit failed")
                self.rows_written += pending
                pending = 0
                last_commit = now
                self._flush_request.clear()
            if self._stop.is_set() and self.queue.empty():
                break

        if pending and self.storage is not None:
            try:
                self.storage.commit()
                self.rows_written += pending
            except sqlite3.Error:  # pragma: no cover
                log.exception("final storage commit failed")

    def close(self, timeout: float = 5.0) -> None:
        """Останавливает поток и закрывает БД (вызывать при выходе/Ctrl+C)."""
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        try:
            self.queue.put_nowait(_SENTINEL)
        except queue.Full:  # pragma: no cover
            pass
        if self.is_alive():
            self.join(timeout=timeout)
        if self.storage is not None:
            try:
                self.storage.commit()
            except sqlite3.Error:  # pragma: no cover
                pass
            self.storage.close()
            self.storage = None
        log.info(
            "storage closed: %d ops, %d rows, %d errors",
            self.ops_written, self.rows_written, self.error_count,
        )
