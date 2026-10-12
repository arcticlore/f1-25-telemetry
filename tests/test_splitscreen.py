"""Splitscreen (второй игрок) и «активные машины» симулятора.

Требования:
- ``--cars`` меняет только ``m_numActiveCars``; пакеты всегда на 22 машины.
- Запись с дефолтным ``--cars`` проходит UDP-приём без assert.
- В splitscreen (game mode 6) второй игрок приходит из
  ``header.m_secondaryPlayerCarIndex`` (255 = второго нет) и пишется
  так же, как первый.
"""

from __future__ import annotations

import socket
import time

import simulate as sim
from f1telemetry.parser import parse_packet
from f1telemetry.recorder import Recorder
from f1telemetry.storage import Storage

HZ = 30.0
TRACK_LENGTH = 400.0
SPLIT_UID = 888_000_011
DEFAULT_UID = 888_000_022


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _send(host: str, port: int, datagrams: list[bytes]) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for d in datagrams:
            sock.sendto(d, (host, port))
    finally:
        sock.close()


def _collect(sim_obj: sim.Simulator, seconds: float) -> list[bytes]:
    out: list[bytes] = []
    while sim_obj.t < seconds:
        out.extend(sim_obj.tick())
    return out


# --- активные машины ---------------------------------------------------------

def test_default_cars_are_22_and_sizes_fixed():
    """Дефолтный конфиг: 22 активные машины, все пакеты по спеке 2025."""
    s = sim.Simulator(sim.SimConfig(laps=1, hz=HZ, seed=3, verbose=False))
    assert len(s.cars) == 22
    assert all(c.active for c in s.cars)
    assert s.active_cars == 22

    dgrams = s.tick()
    known = set(sim.EXPECTED_SIZES.values())
    for d in dgrams:
        assert len(d) in known, f"unexpected size {len(d)}"
        parse_packet(d)   # не должно бросать

    participants = next(d for d in dgrams if len(d) == sim.EXPECTED_SIZES[4])
    assert participants[29] == 22   # m_numActiveCars


def test_cars_flag_changes_only_num_active_cars():
    """``cars=5``: пакеты по-прежнему на 22 машины, активных — 5."""
    s = sim.Simulator(sim.SimConfig(
        cars=5, laps=1, hz=HZ, seed=3, verbose=False))
    assert len(s.cars) == 22
    assert s.active_cars == 5
    assert sum(1 for c in s.cars if c.active) == 5

    dgrams = s.tick()
    known = set(sim.EXPECTED_SIZES.values())
    for d in dgrams:
        assert len(d) in known, f"unexpected size {len(d)}"
        parse_packet(d)

    participants = next(d for d in dgrams if len(d) == sim.EXPECTED_SIZES[4])
    assert participants[29] == 5

    # неактивные машины не двигаются
    pkt = parse_packet(next(d for d in dgrams if d[6] == 2))  # PacketId.LapData
    inactive = pkt.packet.m_lapData[10]
    assert inactive.m_currentLapTimeInMS == 0
    assert inactive.m_lapDistance == 0.0


def test_default_cars_udp_record_without_assert(tmp_path):
    """Запись с дефолтным --cars через реальный UDP-приём без ошибок."""
    port = _free_udp_port()
    db = tmp_path / "default_cars.sqlite"
    rec = Recorder(db, host="127.0.0.1", port=port, sample_hz=20.0)
    rec.start()
    try:
        s = sim.Simulator(sim.SimConfig(
            hz=HZ, laps=1, track_length=TRACK_LENGTH,
            seed=9, session_uid=DEFAULT_UID, verbose=False,   # cars=22 по умолчанию
        ))
        dgrams = _collect(s, s.profile.lap_time_s * 0.7 + 1.0)
        _send("127.0.0.1", port, dgrams)
        deadline = time.time() + 15.0
        while time.time() < deadline:
            if rec.health()["datagrams"] >= len(dgrams) - 5:
                break
            time.sleep(0.1)
        time.sleep(0.3)
    finally:
        rec.stop()

    health = rec.health()
    assert health["parse_errors"] == 0
    assert health["format_errors"] == 0
    assert health["datagrams"] > 50

    st = Storage(db, read_only=True)
    try:
        assert st.get_session(DEFAULT_UID) is not None
        assert len(st.list_participants(DEFAULT_UID)) == 22
        assert st.stats(DEFAULT_UID)["telemetry_points"] > 0
    finally:
        st.close()


# --- splitscreen -------------------------------------------------------------

def test_splitscreen_secondary_is_tracked(tmp_path):
    """Второй игрок (m_secondaryPlayerCarIndex) пишется наравне с первым."""
    s = sim.Simulator(sim.SimConfig(
        hz=HZ, laps=1, cars=22, track_length=TRACK_LENGTH,
        seed=17, session_uid=SPLIT_UID, secondary_car=1, verbose=False,
    ))
    rec = Recorder(tmp_path / "split.sqlite")
    seconds = s.profile.lap_time_s + 2.0
    datagrams: list[bytes] = []
    while s.t < seconds:
        datagrams.extend(s.tick())
    rec.feed(datagrams)
    rec.stop()
    state = rec.state

    assert state.player_car_index == 0
    assert state.secondary_player_car_index == 1
    assert state.tracked_cars() == (0, 1)

    # снапшоты обоих игроков
    snap0 = state.snapshot()
    snap1 = state.snapshot(1)
    assert snap0.car_index == 0
    assert snap1.car_index == 1
    assert snap1.secondary_car_index == 1
    assert snap0.speed_kmh > 0
    assert snap1.speed_kmh > 0
    assert snap0.driver_name == "Player One"
    assert snap1.driver_name  # имя второго участника

    # у каждого игрока свой кольцевой буфер
    trace0 = state.trace()
    trace1 = state.trace(1)
    assert trace0 and trace1
    assert trace0 is not trace1

    st = Storage(tmp_path / "split.sqlite", read_only=True)
    try:
        n0 = st.conn.execute(
            "SELECT COUNT(*) FROM telemetry WHERE session_uid=? AND car_index=0",
            (SPLIT_UID,)).fetchone()[0]
        n1 = st.conn.execute(
            "SELECT COUNT(*) FROM telemetry WHERE session_uid=? AND car_index=1",
            (SPLIT_UID,)).fetchone()[0]
        assert n0 > 0, "у игрока 1 нет телеметрии"
        assert n1 > 0, "у игрока 2 нет телеметрии"
        # статус (топливо/ERS) тоже для обоих
        s0 = st.conn.execute(
            "SELECT COUNT(*) FROM car_status_samples"
            " WHERE session_uid=? AND car_index=0", (SPLIT_UID,)).fetchone()[0]
        s1 = st.conn.execute(
            "SELECT COUNT(*) FROM car_status_samples"
            " WHERE session_uid=? AND car_index=1", (SPLIT_UID,)).fetchone()[0]
        assert s0 > 0 and s1 > 0
    finally:
        st.close()


def test_no_secondary_when_header_says_255(tmp_path):
    """Без второго игрока tracked_cars() возвращает только игрока."""
    s = sim.Simulator(sim.SimConfig(
        hz=HZ, laps=1, track_length=TRACK_LENGTH,
        seed=18, session_uid=888_000_033, verbose=False,   # secondary=255
    ))
    rec = Recorder(tmp_path / "nosplit.sqlite")
    datagrams: list[bytes] = []
    for _ in range(5):
        datagrams.extend(s.tick())
    rec.feed(datagrams)
    assert rec.state.secondary_player_car_index == 255
    assert rec.state.tracked_cars() == (0,)
    assert rec.state.snapshot().secondary_car_index == 255
