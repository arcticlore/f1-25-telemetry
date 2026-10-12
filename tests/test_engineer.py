"""Тесты вкладки Engineer: схема v2, пит-окно, сводка БД, виджет, алерты."""

from __future__ import annotations

import json
import sqlite3
import time

import pytest
import simulate as sim
from f1telemetry.recorder import Recorder
from f1telemetry.state import LiveSnapshot
from f1telemetry.storage import SCHEMA_VERSION, Storage

HZ = 30.0
TRACK_LENGTH = 400.0
UID = 555000222


def _make_rec(tmp_path, **kw):
    cfg = sim.SimConfig(
        hz=HZ, laps=2, cars=22, track_length=TRACK_LENGTH,
        seed=7, session_uid=UID, verbose=False,
    )
    s = sim.Simulator(cfg)
    rec = Recorder(tmp_path / "db.sqlite", **kw)
    return s, rec


# --- storage: v2 ------------------------------------------------------------

def test_schema_v2_migrates_from_v1(tmp_path):
    """Старая БД v1 получает колонки пит-окна/погоды без потери данных."""
    db = tmp_path / "v1.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE sessions (
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
        INSERT INTO sessions (session_uid, track_name, weather, num_cars)
        VALUES (42, 'Monaco', 1, 20);
        PRAGMA user_version=1;
    """)
    conn.commit()
    conn.close()

    st = Storage(db)
    try:
        row = st.get_session(42)
        assert row is not None
        assert row["track_name"] == "Monaco"          # данные не потеряны
        assert row["pit_ideal_lap"] is None            # колонки добавлены
        assert row["forecast_json"] is None
        cur = st.conn.execute("PRAGMA user_version")
        assert int(cur.fetchone()[0]) == SCHEMA_VERSION
    finally:
        st.close()


def test_upsert_session_pit_window_and_forecast(tmp_path):
    st = Storage(tmp_path / "s.sqlite")
    try:
        st.upsert_session(
            7, track_name="Monaco", pit_ideal_lap=24, pit_latest_lap=29,
            track_temp_c=38, air_temp_c=24,
            forecast_json=json.dumps([[10, 2, 0, 38, 24]]),
        )
        st.commit()
        row = st.get_session(7)
        assert row["pit_ideal_lap"] == 24
        assert row["pit_latest_lap"] == 29
        assert row["track_temp_c"] == 38
        assert json.loads(row["forecast_json"]) == [[10, 2, 0, 38, 24]]
        # COALESCE: повторный апsert без пит-окна не затирает
        st.upsert_session(7, weather=3)
        st.commit()
        assert st.get_session(7)["pit_ideal_lap"] == 24
    finally:
        st.close()


# --- state → snapshot → БД --------------------------------------------------

def test_sim_pit_window_flows_to_snapshot_and_db(tmp_path):
    s, rec = _make_rec(tmp_path)
    datagrams = []
    while s.t < 1.0:
        datagrams.extend(s.tick())
    rec.feed(datagrams)
    snap = rec.snapshot()
    assert snap.pit_ideal_lap == max(2, int(2 * 0.45))  # = 2
    assert snap.pit_latest_lap == snap.pit_ideal_lap + 5
    assert len(snap.forecast) == 3
    assert snap.forecast[0] == (10, 2, 0, 38, 24)

    rec._flush_writes(sync=True)
    rec.stop()
    st = Storage(tmp_path / "db.sqlite", read_only=True)
    try:
        row = st.get_session(UID)
        assert row["pit_ideal_lap"] == snap.pit_ideal_lap
        assert row["pit_latest_lap"] == snap.pit_latest_lap
        assert row["track_temp_c"] == 38
        assert json.loads(row["forecast_json"])[2][2] == 55  # rain% третьего сэмпла
    finally:
        st.close()


def test_engineer_summary_from_db(tmp_path):
    s, rec = _make_rec(tmp_path)
    seconds = s.profile.lap_time_s * 2 + 1.0
    while s.t < seconds:
        rec.feed(s.tick())
    rec._flush_writes(sync=True)
    rec.stop()
    st = Storage(tmp_path / "db.sqlite", read_only=True)
    try:
        summ = st.engineer_summary(UID)
        assert summ["session"]["track_name"] == "Silverstone"
        assert summ["car_index"] == 0
        assert summ["last_lap"] is not None
        assert summ["last_status"] is not None
        assert summ["events"].get("SSTA") == 1
    finally:
        st.close()


# --- виджет -----------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from f1telemetry.ui.theme import apply_theme
    apply_theme(app)
    yield app


def _rich_snapshot() -> LiveSnapshot:
    snap = LiveSnapshot(session_uid=9)
    snap.last_packet_age_s = 0.05
    snap.track_name = "Monaco"
    snap.session_type_name = "Race"
    snap.pit_ideal_lap = 24
    snap.pit_latest_lap = 29
    snap.tyre_compound_visual = 17
    snap.tyre_visual_name = "Medium"
    snap.tyre_age_laps = 12
    snap.tyre_wear_pct = (31.0, 29.5, 81.0, 79.0)
    snap.tyre_inner_c = (92, 95, 110, 108)
    snap.tyre_surf_c = (88, 90, 104, 102)
    snap.fuel_kg = 41.2
    snap.fuel_capacity_kg = 110.0
    snap.fuel_laps = 0.6
    snap.ers_store_j = 2.5e6
    snap.ers_capacity_j = 4e6
    snap.ers_mode = 2
    snap.position = 3
    snap.lap_num = 18
    snap.total_laps = 52
    snap.session_time_left_s = 1854
    snap.delta_front_ms = -212
    snap.delta_leader_ms = 8431
    snap.weather = 2
    snap.air_temp_c = 24
    snap.track_temp_c = 38
    snap.forecast = ((10, 2, 0, 38, 24), (30, 2, 25, 38, 24),
                     (60, 3, 55, 38, 24))
    snap.safety_car_status = 2
    snap.fia_flag = 3
    return snap


def test_engineer_renders_live_snapshot(qapp, tmp_path, monkeypatch):
    from f1telemetry.ui.widgets import engineer as eng
    from f1telemetry.ui.widgets.engineer import EngineerTab

    played: list[str] = []
    monkeypatch.setattr(eng.AlertSounds, "play",
                        lambda self, name: played.append(name) or True)

    tab = EngineerTab(tmp_path / "none.sqlite")
    tab.update_snapshot(_rich_snapshot())
    qapp.processEvents()

    assert tab._pit_lbl.text() == "24 / 29"
    assert tab._fuel_laps_lbl.text() == "0.6"
    assert "Medium" in tab._compound_badge.text()
    assert tab._age_lbl.text() == "12"
    assert tab._delta_front_lbl.text() == "-0.212"
    assert tab._delta_leader_lbl.text() == "+8.431"
    assert tab._sc_badge.text() == "SC: VSC"
    assert tab._pos_lbl.text() == "3"
    assert tab._lap_lbl.text() == "18/52"
    assert tab._time_lbl.text() == "30:54"
    assert "Пасмурно" in tab._weather_lbl.text()
    assert "дождь 25%" in tab._forecast_lbls[1].text()
    # алерты: шины 81% + топливо 0.6 + дождь 55% (текст — последний сработавший)
    assert "tyres" in played and "fuel" in played and "rain" in played
    assert tab._alert_lbl.text()  # что-то показано
    tab.close()


def test_engineer_alert_events_and_cooldown(qapp, tmp_path, monkeypatch):
    from f1telemetry.ui.widgets import engineer as eng
    from f1telemetry.ui.widgets.engineer import EngineerTab

    played: list[str] = []
    monkeypatch.setattr(eng.AlertSounds, "play",
                        lambda self, name: played.append(name) or True)
    tab = EngineerTab(tmp_path / "none.sqlite")

    tab.on_event("TMPT", {"name": "VER", "vehicle_idx": 1})
    assert "VER" in tab._alert_lbl.text()
    assert played[-1] == "tmpt"
    tab.on_event("DRSE", {})
    assert played[-1] == "drs_on"
    tab.on_event("DRSD", {"reason": 1})
    assert played[-1] == "drs_off"

    n = len(played)
    tab.on_event("TMPT", {"name": "VER"})     # в пределах cooldown — тихо
    assert len(played) == n

    # mute — звук не играет, визуал остаётся
    tab._mute.setChecked(False)
    time.sleep(0.01)
    tab._alert_ts.clear()
    tab.on_event("DRSE", {})
    assert len(played) == n
    assert "DRS" in tab._alert_lbl.text()
    tab.close()


def test_engineer_historical_from_db(qapp, tmp_path):
    """Вкладка рисует записанную сессию из БД (pit-окно None → «—»)."""
    s, rec = _make_rec(tmp_path)
    while s.t < s.profile.lap_time_s + 1.0:
        rec.feed(s.tick())
    rec._flush_writes(sync=True)
    rec.stop()

    from f1telemetry.ui.widgets.engineer import EngineerTab
    tab = EngineerTab(tmp_path / "db.sqlite")
    tab.set_historical_session(UID)
    qapp.processEvents()
    assert tab._pit_lbl.text() == "2 / 7"
    assert tab._rendered_uid == UID
    assert tab._source_lbl.text().startswith("сессия")
    tab.close()


def test_engineer_live_overrides_history(qapp, tmp_path, monkeypatch):
    """Свежие live-пакеты перекрывают историческую отрисовку."""
    from f1telemetry.ui.widgets import engineer as eng
    from f1telemetry.ui.widgets.engineer import EngineerTab
    monkeypatch.setattr(eng.AlertSounds, "play", lambda self, name: True)

    s, rec = _make_rec(tmp_path)
    while s.t < 1.0:
        rec.feed(s.tick())
    rec._flush_writes(sync=True)
    rec.stop()

    tab = EngineerTab(tmp_path / "db.sqlite")
    tab.set_historical_session(UID)
    snap = rec.snapshot()
    snap.last_packet_age_s = 0.01
    tab.update_snapshot(snap)
    assert tab._source_lbl.text().startswith("LIVE")
    assert tab._rendered_uid == UID
    tab.close()
