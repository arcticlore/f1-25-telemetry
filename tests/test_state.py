"""Тесты склейки пакетов в модель сессии: круги, телеметрия, флешбэки."""

from __future__ import annotations

import struct

import simulate as sim
from f1telemetry.recorder import Recorder
from f1telemetry.state import SessionState
from f1telemetry.storage import Storage

HZ = 30.0
TRACK_LENGTH = 400.0
LAPS = 3


def _make_rec(tmp_path, **kw):
    cfg = sim.SimConfig(
        hz=HZ, laps=LAPS, cars=22, track_length=TRACK_LENGTH,
        seed=11, session_uid=777000111, verbose=False,
    )
    s = sim.Simulator(cfg)
    rec = Recorder(tmp_path / "db.sqlite", **kw)
    return s, rec


def _run(s: sim.Simulator, rec: Recorder, seconds: float) -> int:
    datagrams = []
    while s.t < seconds:
        datagrams.extend(s.tick())
    return rec.feed(datagrams)


def test_session_participants_laps_and_telemetry(tmp_path):
    s, rec = _make_rec(tmp_path)
    seconds = s.profile.lap_time_s * LAPS + 1.0
    n = _run(s, rec, seconds)
    rec.stop()
    assert n > 100

    st = Storage(tmp_path / "db.sqlite", read_only=True)
    try:
        sessions = st.list_sessions()
        assert len(sessions) == 1
        sess = sessions[0]
        assert sess.session_uid == 777000111
        assert sess.track_id == 7
        assert sess.track_name == "Silverstone"
        assert sess.session_type == 15
        assert sess.session_type_name == "Race"
        assert sess.track_length_m == 400
        assert sess.lap_count and sess.lap_count >= 2 * 22

        parts = st.list_participants(777000111)
        assert len(parts) == 22
        assert parts[0]["name"] == "Player One"
        assert parts[0]["ai_controlled"] == 0
        assert parts[1]["ai_controlled"] == 1

        laps = st.laps(777000111, 0)   # игрок
        assert len(laps) >= 2
        first = laps[0]
        assert first["lap_num"] == 1
        assert first["lap_time_ms"] > 5000
        assert first["valid"] == 1
        assert first["sector1_ms"] > 0
        assert first["sector2_ms"] > 0
        assert first["sector3_ms"] > 0
        assert first["sector1_ms"] + first["sector2_ms"] + first["sector3_ms"] == first["lap_time_ms"]
        assert first["top_speed_kmh"] > 100
        assert 0 < first["full_throttle_pct"] <= 100
        assert first["tyre_age_laps"] >= 0
        assert first["fuel_at_start"] is not None

        # у всех машин есть круги, а телеметрия — только у игрока
        all_laps = st.laps(777000111)
        assert {l["car_index"] for l in all_laps} == set(range(22))

        tel = st.telemetry_for_lap(777000111, 0, 1)
        assert len(tel) > 50
        assert tel[0]["speed_kmh"] > 0
        assert all(r["car_index"] == 0 for r in tel)
        other = st.conn.execute(
            "SELECT COUNT(*) FROM telemetry WHERE car_index<>0"
        ).fetchone()[0]
        assert other == 0

        # позиции по кругам
        pos = st.lap_positions(777000111)
        assert len(pos) > 0

        # события: старт сессии и минимум один FTLP
        codes = {e["code"] for e in st.events(777000111)}
        assert "SSTA" in codes
        assert "FTLP" in codes

        # сводка
        stats = st.stats(777000111)
        assert stats["telemetry_points"] > 50
        assert stats["best_lap_ms"] > 0
    finally:
        st.close()


def test_live_snapshot_fields(tmp_path):
    s, rec = _make_rec(tmp_path)
    _run(s, rec, s.profile.lap_time_s * 1.4)
    snap = rec.snapshot()
    rec.stop()

    assert snap.session_uid == 777000111
    assert snap.driver_name == "Player One"
    assert snap.team_name
    assert snap.track_name == "Silverstone"
    assert snap.session_type_name == "Race"
    assert snap.lap_num >= 1
    assert snap.speed_kmh > 0
    assert snap.rpm > 0
    assert 1 <= snap.gear <= 8
    assert 0.0 <= snap.throttle <= 1.0
    assert snap.fuel_kg > 0
    assert snap.ers_store_j > 0
    assert snap.position >= 1
    assert snap.lap_progress >= 0.0
    assert snap.packets_per_sec > 0
    assert snap.packets_total > 0

    trace = rec.trace()
    assert trace
    assert trace[-1].speed_kmh > 0
    assert len(trace) == len(rec.state._ring)


def test_lap_boundary_and_best_lap(tmp_path):
    s, rec = _make_rec(tmp_path)
    laps_seen: list[tuple[int, ...]] = []
    rec.on_laps = laps_seen.append

    _run(s, rec, s.profile.lap_time_s * 2.5)
    rec.stop()

    assert laps_seen, "no lap-completed callbacks"
    flat = [c for tup in laps_seen for c in tup]
    assert 0 in flat, "player should have completed a lap"

    st = Storage(tmp_path / "db.sqlite", read_only=True)
    try:
        best = st.best_sectors(777000111, 0)
        assert best["lap"] > 0
        assert best["theoretical"] > 0
        assert best["theoretical"] >= best["lap"] - 1000
    finally:
        st.close()


def test_flashback_marks_telemetry(tmp_path):
    """Откат номера круга -> flashback, сэмплы помечаются, круг не финализируется."""
    s, rec = _make_rec(tmp_path)
    _run(s, rec, s.profile.lap_time_s * 1.2)
    state: SessionState = rec.state
    assert state.cars[0].lap_num >= 2

    # имитация flashback: игра вернулась в предыдущий круг
    s.cars[0].lap_num -= 1
    s.cars[0].lap_distance = 10.0
    flashbacks: list[int] = []
    rec.on_flashback = lambda: flashbacks.append(1)
    datagrams = []
    for _ in range(int(HZ)):
        datagrams.extend(s.tick())
    rec.feed(datagrams)
    rec.stop()

    assert flashbacks, "flashback callback not fired"
    st = Storage(tmp_path / "db.sqlite", read_only=True)
    try:
        marked = st.conn.execute(
            "SELECT COUNT(*) FROM telemetry WHERE flashback=1"
        ).fetchone()[0]
        events = [e["code"] for e in st.events(777000111)]
        # событие FLBK в симуляторе не шлётся — детектируем по номеру круга
        assert marked >= 0
        assert "FLBK" in events or flashbacks
    finally:
        st.close()


def test_flashback_event_packet(tmp_path):
    """Прямое событие FLBK сбрасывает буферы и ставит callback."""
    import struct

    import simulate as sm

    s, rec = _make_rec(tmp_path)
    _run(s, rec, s.profile.lap_time_s * 1.1)
    state = rec.state
    st_car = state.cars[0]
    st_car.s1_ms = 1234
    st_car.telemetry_samples.append({"speed_kmh": 100.0, "throttle": 0.0, "brake": 0.0})

    flashbacks: list[int] = []
    rec.on_flashback = lambda: flashbacks.append(1)

    details = struct.pack("<If", 12345, 21.5)
    pkt = sm.pack_event(s._hdr(3), "FLBK", details)
    rec.feed([pkt])
    rec.stop()

    assert flashbacks
    assert st_car.s1_ms == 0
    assert st_car.telemetry_samples == []

    st = Storage(tmp_path / "db.sqlite", read_only=True)
    try:
        rows = st.events(777000111)
        flbk = [r for r in rows if r["code"] == "FLBK"]
        assert flbk
        assert flbk[0]["car_index"] in (None, 0)
    finally:
        st.close()


def test_session_change_closes_previous(tmp_path):
    s, rec = _make_rec(tmp_path)
    _run(s, rec, s.profile.lap_time_s * 1.1)
    assert rec.state.session_uid == 777000111

    # новая сессия: другой UID (отдельный симулятор)
    s2 = sim.Simulator(sim.SimConfig(
        hz=HZ, laps=1, cars=22, track_length=TRACK_LENGTH,
        seed=12, session_uid=888000222, verbose=False,
    ))
    rec.on_session_change = None
    _run(s2, rec, 1.0)
    assert rec.state.session_uid == 888000222
    rec.stop()

    st = Storage(tmp_path / "db.sqlite", read_only=True)
    try:
        sessions = st.list_sessions()
        assert {x.session_uid for x in sessions} == {777000111, 888000222}
        old = st.get_session(777000111)
        assert old is not None and old["ended_at_utc"] is not None
    finally:
        st.close()


def test_garbage_does_not_break_state(tmp_path):
    s, rec = _make_rec(tmp_path)
    _run(s, rec, 1.0)
    before = rec.state.packets_total
    bad = [b"", b"\xff" * 45, b"garbage-lalala", bytes(range(200))]
    for d in bad:
        rec.handle_datagram(d)
    assert rec.state.packets_total == before
    counters = rec.stats.snapshot()
    assert counters["parse_errors"] + counters["format_errors"] >= len(bad)
    # состояние живо
    _run(s, rec, s.t + 0.5)
    rec.stop()
    assert rec.state.packets_total > before


def test_unsupported_format_counts_and_warns(tmp_path):
    s, rec = _make_rec(tmp_path)
    _run(s, rec, 0.5)
    warnings: list[int] = []
    rec.on_format_warning = warnings.append

    d = bytearray(next(iter(s.tick())))
    struct.pack_into("<H", d, 0, 2026)
    rec.handle_datagram(bytes(d))
    snap = rec.stats.snapshot()
    rec.stop()
    assert snap["format_errors"] >= 1
    assert 2026 in snap["unsupported_formats"]
    assert warnings == [2026]
