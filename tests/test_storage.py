"""Тесты хранилища SQLite: схема, запись, запросы, writer-поток."""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from f1telemetry.storage import (
    LapRecord,
    SCHEMA_VERSION,
    Storage,
    StorageWriter,
    utc_now_iso,
)

UID = 424242


# --- Базовые операции ---------------------------------------------------------

def test_schema_created(tmp_path):
    db = tmp_path / "t.sqlite"
    st = Storage(db)
    st.conn.execute("PRAGMA user_version")
    assert st.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    tables = {
        r[0]
        for r in st.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for t in (
        "sessions", "participants", "laps", "telemetry", "car_telemetry_detail",
        "car_status_samples", "stints", "events", "final_classification",
        "setups", "tyre_sets", "lap_positions",
    ):
        assert t in tables, t
    st.close()


def test_journal_mode_wal(tmp_path):
    st = Storage(tmp_path / "wal.sqlite")
    mode = st.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"
    st.close()


def test_upsert_and_list_sessions(tmp_path):
    st = Storage(tmp_path / "s.sqlite")
    st.upsert_session(
        UID, packet_format=2025, game_year=25, game_version="1.25",
        track_id=7, track_name="Silverstone", session_type=15,
        session_type_name="Race", weather=0, total_laps=52,
        track_length_m=5891, started_at_utc=utc_now_iso(),
        player_car_index=0, num_cars=20,
    )
    st.commit()
    rows = st.list_sessions()
    assert len(rows) == 1
    assert rows[0].track_name == "Silverstone"
    assert rows[0].session_type_name == "Race"
    assert rows[0].best_lap_ms is None
    assert rows[0].lap_count == 0

    # обновление того же UID не создаёт вторую строку
    st.upsert_session(UID, weather=3, total_laps=52, track_id=7)
    st.commit()
    assert len(st.list_sessions()) == 1
    assert st.list_sessions()[0].weather == 3

    # фильтры
    assert st.list_sessions(track_id=11) == []
    assert len(st.list_sessions(track_id=7)) == 1
    assert st.list_sessions(session_type=15)
    assert st.list_sessions(session_type=99) == []
    st.close()


def test_participants(tmp_path):
    st = Storage(tmp_path / "p.sqlite")
    st.upsert_session(UID)
    st.upsert_participant(
        UID, 0, name="Player One", driver_id=1, team_id=8,
        team_name="McLaren", race_number=4, nationality=10,
        ai_controlled=0, platform=6, telemetry_public=1,
    )
    st.upsert_participant(UID, 0, name="Player One Renamed")
    st.commit()
    rows = st.list_participants(UID)
    assert len(rows) == 1
    assert rows[0]["name"] == "Player One Renamed"
    assert rows[0]["team_name"] == "McLaren"
    st.close()


def test_laps_upsert_and_best_sectors(tmp_path):
    st = Storage(tmp_path / "l.sqlite")
    st.upsert_session(UID)
    for lap, t, s1, s2, s3 in (
        (1, 91000, 30000, 31000, 30000),
        (2, 90500, 30500, 30000, 30000),
        (3, 95000, 31000, 32000, 32000),
        (4, 0, 0, 0, 0),          # недокатанный / нулевой
        (5, 94000, 0, 0, 0),       # без секторов
    ):
        st.upsert_lap(LapRecord(
            session_uid=UID, car_index=0, lap_num=lap, lap_time_ms=t,
            sector1_ms=s1, sector2_ms=s2, sector3_ms=s3, valid=1,
            position=lap, stint_index=0,
            compound_actual=18, compound_visual=17, tyre_age_laps=lap,
        ))
    st.commit()

    laps = st.laps(UID, 0)
    assert [l["lap_num"] for l in laps] == [1, 2, 3, 4, 5]
    assert laps[1]["lap_time_ms"] == 90500

    best = st.best_sectors(UID, 0)
    assert best["lap"] == 90500
    assert best["s1"] == 30000
    assert best["s2"] == 30000
    assert best["s3"] == 30000

    # список сессий теперь видит лучший круг и число кругов
    info = st.list_sessions()[0]
    assert info.best_lap_ms == 90500
    assert info.lap_count == 5

    # перезапись UNIQUE(session, car, lap)
    st.upsert_lap(LapRecord(
        session_uid=UID, car_index=0, lap_num=2, lap_time_ms=90100,
        sector1_ms=29900, sector2_ms=30100, sector3_ms=30100, valid=1,
    ))
    st.commit()
    lap2 = [l for l in st.laps(UID, 0) if l["lap_num"] == 2][0]
    assert lap2["lap_time_ms"] == 90100
    assert len(st.laps(UID, 0)) == 5
    st.close()


def test_telemetry_roundtrip_and_ordering(tmp_path):
    st = Storage(tmp_path / "t.sqlite")
    st.upsert_session(UID)
    rows = []
    for i in range(10):
        rows.append((
            UID, 0, 3, i,            # session, car, lap, sample_idx
            100.0 + i * 0.05,        # session_time
            i * 50.0,                # lap_distance_m
            250.0 + i, 0.8, 0.0, 0.1, 6, 11000, 1, 0,   # speed..ers_mode
            10.0, 0.0, 0.0,          # pos
            60.0, 0.0, 0.0,          # vel
            0.5, 0.1, -1.0,          # g
            1.5, 0.02, 0.03,         # yaw, pitch, roll
            0,                       # flashback
        ))
    st.insert_telemetry(rows)
    st.commit()
    got = st.telemetry_for_lap(UID, 0, 3)
    assert len(got) == 10
    assert [r["sample_idx"] for r in got] == list(range(10))
    assert [r["lap_distance_m"] for r in got] == [i * 50.0 for i in range(10)]
    assert got[0]["speed_kmh"] == 250.0
    assert got[9]["speed_kmh"] == 259.0
    assert got[0]["flashback"] == 0

    # INSERT OR REPLACE по первичному ключу
    st.insert_telemetry(rows[:1])
    st.commit()
    assert len(st.telemetry_for_lap(UID, 0, 3)) == 10
    st.close()


def test_detail_and_status_rows(tmp_path):
    st = Storage(tmp_path / "d.sqlite")
    st.upsert_session(UID)
    detail = (UID, 0, 1, 0) + tuple(float(i) for i in range(28)) + (900.0,)
    st.insert_detail([detail])
    status = (UID, 0, 1, 0, 100.5, 24.0, 4.0e6, 1.2e5, 3.4e4, 4.0e5, 8.0e4)
    st.insert_status_samples([status])
    st.commit()

    d = st.conn.execute(
        "SELECT * FROM car_telemetry_detail WHERE session_uid=?", (UID,)
    ).fetchone()
    assert d is not None
    assert d["engine_temp"] == 900.0

    s = st.conn.execute(
        "SELECT * FROM car_status_samples WHERE session_uid=?", (UID,)
    ).fetchone()
    assert s["fuel_in_tank"] == 100.5
    assert s["ers_store_j"] == 4.0e6
    st.close()


def test_stints_events_positions_final(tmp_path):
    st = Storage(tmp_path / "x.sqlite")
    st.upsert_session(UID)

    st.upsert_stint(
        UID, 0, 0, start_lap=1, end_lap=12, compound_actual=18,
        compound_visual=17, laps_count=12, avg_lap_ms=90500, deg_ms_per_lap=80.5,
    )
    st.upsert_stint(
        UID, 0, 0, start_lap=1, end_lap=14, compound_actual=18,
        compound_visual=17, laps_count=14, avg_lap_ms=90700, deg_ms_per_lap=95.0,
    )
    st.insert_event(UID, 12.5, "FTLP", 0, None, {"lap_time": 90.5})
    st.insert_event(UID, 0.0, "SSTA", None, None, {})
    st.upsert_lap_positions([
        (UID, 1, 0, 3), (UID, 1, 1, 1), (UID, 1, 2, 2),
        (UID, 2, 0, 2),
    ])
    st.upsert_final_classification(
        UID, 0, position=1, num_laps=52, grid_position=3, points=25,
        num_pit_stops=1, result_status=3, result_reason=2,
        best_lap_ms=90100, total_race_time=5400.25,
        stints_actual_json="[18,17]", stints_visual_json="[17,17]",
        stints_end_laps_json="[26,52]",
    )
    st.upsert_setup(UID, 0, {"frontWing": 8, "rearWing": 12, "fuelLoad": 100})
    st.upsert_tyre_set(UID, 0, 0, compound_actual=18, compound_visual=17,
                       wear=0.0, available=1, fitted=1, life_span=15)
    st.commit()

    assert len(st.stints(UID)) == 1
    assert st.stints(UID)[0]["avg_lap_ms"] == 90700  # обновление

    ev = st.events(UID)
    assert [e["code"] for e in ev] == ["SSTA", "FTLP"]
    assert ev[1]["car_index"] == 0

    pos = st.lap_positions(UID)
    assert len(pos) == 4
    assert st.execute(
        "SELECT position FROM lap_positions WHERE session_uid=? AND lap_num=1"
        " AND car_index=0", (UID,),
    ).fetchone()[0] == 3

    fc = st.final_classification(UID)
    assert fc[0]["points"] == 25
    assert fc[0]["stints_actual_json"] == "[18,17]"

    assert st.setups(UID)[0]["captured_at"]
    assert len(st.tyre_sets(UID)) == 1
    st.close()


def test_mark_flashback_and_delete_session(tmp_path):
    st = Storage(tmp_path / "f.sqlite")
    st.upsert_session(UID)
    st.insert_telemetry([
        (UID, 0, 1, i, float(i), float(i * 10), 200.0, 0.5, 0.0, 0.0, 5,
         10000, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        for i in range(5)
    ])
    st.mark_flashback(UID, 0, 1, 2)
    st.commit()
    rows = st.telemetry_for_lap(UID, 0, 1)
    assert [r["flashback"] for r in rows] == [1, 1, 1, 0, 0]

    st.insert_event(UID, 1.0, "FLBK", 0, None, {"frame": 5})
    st.upsert_participant(UID, 0, name="X")
    st.commit()
    n = st.delete_session(UID)
    st.commit()
    assert n >= 12
    assert st.laps(UID) == []
    assert st.list_participants(UID) == []
    assert st.events(UID) == []
    assert st.get_session(UID) is None
    st.close()


def test_stats(tmp_path):
    st = Storage(tmp_path / "st.sqlite")
    st.upsert_session(UID, track_id=7)
    st.upsert_participant(UID, 0, name="P")
    st.upsert_lap(LapRecord(UID, 0, 1, 90000, 30000, 30000, 30000, valid=1))
    st.upsert_lap(LapRecord(UID, 0, 2, 91000, 30500, 30200, 30300, valid=1))
    st.insert_telemetry([
        (UID, 0, 1, i, float(i), float(i), 200.0, 0.5, 0.0, 0.0, 5,
         10000, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        for i in range(7)
    ])
    st.insert_event(UID, 0.0, "SSTA", None, None, {})
    st.commit()

    s = st.stats(UID)
    assert s["sessions"] == 1
    assert s["laps"] == 2
    assert s["best_lap_ms"] == 90000
    assert s["telemetry_points"] == 7
    assert s["events"] == 1
    assert s["participants"] == 1
    assert s["size_bytes"] > 0

    all_stats = st.stats()
    assert all_stats["sessions"] == 1
    assert all_stats["laps"] == 2
    st.close()


def test_read_only_open(tmp_path):
    db = tmp_path / "ro.sqlite"
    st = Storage(db)
    st.upsert_session(UID, track_id=7)
    st.commit()
    st.close()

    ro = Storage(db, read_only=True)
    assert ro.get_session(UID) is not None
    with pytest.raises(sqlite3.OperationalError):
        ro.conn.execute("DELETE FROM sessions")
    ro.close()


def test_reopen_keeps_data(tmp_path):
    db = tmp_path / "re.sqlite"
    st = Storage(db)
    st.upsert_session(UID, track_id=7)
    st.commit()
    st.close()

    st2 = Storage(db)
    assert st2.get_session(UID)["track_id"] == 7
    assert st2.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    st2.close()


# --- Writer-поток -------------------------------------------------------------

def test_storage_writer_thread(tmp_path):
    db = tmp_path / "w.sqlite"
    w = StorageWriter(db, flush_interval=0.05, batch_size=10)
    w.start()

    w.submit("upsert_session", UID, track_id=7, track_name="Monza")
    w.submit("upsert_participant", UID, 0, name="Player")
    w.submit("upsert_lap", LapRecord(UID, 0, 1, 89000, 29000, 30000, 30000, valid=1))
    w.submit("insert_telemetry", [
        (UID, 0, 1, i, float(i), float(i * 25), 260.0, 1.0, 0.0, 0.0, 7,
         12000, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        for i in range(25)
    ])
    w.submit("insert_event", UID, 3.0, "DRSE", None, None, None)
    w.flush()
    w.close()

    st = Storage(db)
    s = st.get_session(UID)
    assert s is not None and s["track_name"] == "Monza"
    assert len(st.laps(UID)) == 1
    assert len(st.telemetry_for_lap(UID, 0, 1)) == 25
    assert len(st.events(UID)) == 1
    st.close()

    assert w.error_count == 0
    assert w.ops_written == 5
    assert w.rows_written > 0


def test_storage_writer_survives_bad_op(tmp_path):
    db = tmp_path / "bad.sqlite"
    w = StorageWriter(db, flush_interval=0.05)
    w.start()
    w.submit("no_such_method", 1)     # должно попасть в error_count, не уронить поток
    w.submit("upsert_session", UID, track_id=7)
    w.flush()
    w.close()
    assert w.error_count == 1
    st = Storage(db)
    assert st.get_session(UID) is not None
    st.close()


def test_storage_writer_stats(tmp_path):
    w = StorageWriter(tmp_path / "s.sqlite", flush_interval=0.05)
    w.start()
    w.submit("upsert_session", UID)
    for _ in range(50):
        w.submit("insert_event", UID, 0.0, "BUTN", None, None, None)
    w.flush()
    deadline = time.time() + 3
    while w.stats()["ops"] < 51 and time.time() < deadline:
        time.sleep(0.02)
    w.close()
    st = w.stats()
    assert st["ops"] >= 51
    assert st["errors"] == 0
    assert st["queued"] >= 0


def test_session_uid_u64_overflow(tmp_path):
    """sessionUID > 2^63-1 (реальные u64 из F1 25) не роняет запись.

    SQLite INTEGER — знаковый int64; игра шлёт полный u64. Регрессия:
    UID 14147937499142393072 ронял каждый op с OverflowError (FP3 в БД
    не писалась совсем). Адаптер в storage.py конвертирует бит-в-бит.
    """
    big = 14147937499142393072  # > 2^63-1
    db = tmp_path / "big.sqlite"
    st = Storage(db)
    st.upsert_session(big, packet_format=2025, track_id=5, track_name="Monaco",
                      session_type=2, session_type_name="P3")
    st.upsert_participant(big, 0, name="Test Driver", team_id=0,
                          team_name="Mercedes", race_number=44)
    st.insert_event(big, 1.0, "SSTA", None, None, {"x": 1})
    st.commit()

    sessions = st.list_sessions()
    assert [s.session_uid for s in sessions] == [big]
    assert sessions[0].track_name == "Monaco"
    assert st.list_participants(big)[0]["name"] == "Test Driver"
    assert len(st.events(big)) == 1
    assert st.get_session(big) is not None

    # и в обратную сторону: маленькие uid не ломаются
    st.upsert_session(UID)
    st.commit()
    assert {s.session_uid for s in st.list_sessions()} == {big, UID}
    st.close()


def test_anonymize(tmp_path):
    """Имена → «Driver N», name из payload событий удаляется."""
    st = Storage(tmp_path / "anon.sqlite")
    uid = 777001
    st.upsert_session(uid, track_id=5, track_name="Monaco")
    st.upsert_participant(uid, 0, name="Секретный Ник", team_id=0,
                          team_name="Mercedes", race_number=44)
    st.upsert_participant(uid, 3, name="Rival Name", team_id=1,
                          team_name="Ferrari", race_number=16)
    st.insert_event(uid, 1.0, "FTLP", 3, None,
                    {"vehicleIdx": 3, "lap_time": 90.5, "name": "Rival Name"})
    st.insert_event(uid, 2.0, "SSTA", None, None, None)
    st.commit()

    assert st.anonymize(uid) == 1

    parts = {p["car_index"]: p["name"] for p in st.list_participants(uid)}
    assert parts == {0: "Driver 1", 3: "Driver 4"}

    ev = st.events(uid)
    ftlp = json.loads(ev[0]["payload_json"])
    assert "name" not in ftlp
    assert ftlp["lap_time"] == 90.5
    assert ev[1]["payload_json"] == "{}"  # SSTA не тронут

    # повторный вызов идемпотентен
    assert st.anonymize(uid) == 1
    st.close()


def test_backfill_lap_tyres(tmp_path):
    """backfill_lap_tyres: compound=0 → реальный, чужие/поздние строки не трогает."""
    st = Storage(tmp_path / "bf.sqlite")
    st.upsert_session(UID)
    for lap, ca, cv in ((1, 0, 0), (2, 18, 17), (5, 0, 0)):
        st.upsert_lap(LapRecord(
            session_uid=UID, car_index=0, lap_num=lap, lap_time_ms=90000 + lap,
            compound_actual=ca, compound_visual=cv,
        ))
    st.commit()
    n = st.backfill_lap_tyres(UID, 0, 18, 17, upto_lap=3)
    st.commit()
    assert n == 1  # только lap 1: lap 2 уже реальный, lap 5 вне окна
    by = {l["lap_num"]: l for l in st.laps(UID, 0)}
    assert by[1]["compound_actual"] == 18
    assert by[1]["compound_visual"] == 17
    assert by[2]["compound_actual"] == 18
    assert by[5]["compound_actual"] == 0
    # идемпотентно: повторный вызов ничего не меняет
    assert st.backfill_lap_tyres(UID, 0, 18, 17, upto_lap=3) == 0
    st.close()
