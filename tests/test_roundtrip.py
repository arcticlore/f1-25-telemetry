"""Round-trip: симулятор -> разбор -> проверка формата 2025."""

from __future__ import annotations

import struct

import pytest

import simulate as sim
from f1telemetry.constants import PACKET_SIZES
from f1telemetry.parser import (
    TooShortError,
    UnknownPacketError,
    UnsupportedFormatError,
    parse_header,
    parse_packet,
    parse_or_none,
)
from f1telemetry.recorder import iter_fixture, write_fixture


def _sim(laps: int = 1, hz: float = 30.0, track_length: float = 400.0, seed: int = 3):
    cfg = sim.SimConfig(
        hz=hz, laps=laps, cars=22, track_length=track_length, seed=seed,
        session_uid=4242424242, verbose=False,
    )
    return sim.Simulator(cfg)


def _ticks(s: sim.Simulator, seconds: float):
    while s.t < seconds:
        yield from s.tick()


def test_all_packet_types_parse():
    """Каждый из 16 типов пакетов успешно разбирается и имеет верный размер."""
    s = _sim()
    datagrams: list[bytes] = []
    for d in _ticks(s, 3.0):
        datagrams.append(d)
    # Final Classification, Chequered flag и Session Ended шлёт только `run()`
    datagrams.append(sim.pack_event(s._hdr(3), "CHQF"))
    datagrams.append(sim.pack_final(s._hdr(8), s.cars))
    datagrams.append(sim.pack_event(s._hdr(3), "SEND"))

    seen: dict[int, int] = {}
    for d in datagrams:
        p = parse_packet(d)
        seen[p.packet_id] = p.size
    assert set(seen) == set(PACKET_SIZES), set(PACKET_SIZES) - set(seen)
    for pid, size in seen.items():
        assert size == PACKET_SIZES[pid], (pid, size, PACKET_SIZES[pid])


def test_header_fields():
    s = _sim()
    d = next(_ticks(s, 0.2))
    h = parse_header(d)
    assert h.packet_format == 2025
    assert h.game_year == 25
    assert h.session_uid == 4242424242
    assert h.player_car_index == sim.PLAYER_CAR_INDEX
    assert h.secondary_player_car_index == 255
    assert not h.has_secondary_player


def test_session_and_participants_content():
    s = _sim()
    for d in _ticks(s, 1.0):
        p = parse_packet(d)
        if p.packet_id == 1:
            pkt = p.packet
            assert pkt.m_sessionType == 15
            assert pkt.m_trackId == 7
            assert pkt.m_trackLength == 400
        elif p.packet_id == 4:
            pkt = p.packet
            assert pkt.m_numActiveCars == 22
            name = bytes(pkt.m_participants[0].m_name).split(b"\x00")[0]
            assert name == b"Player One"
            break


def test_event_fastest_lap_decoding():
    s = _sim()
    s.cars[0].best_lap_ms = 0
    for d in _ticks(s, s.profile.lap_time_s * 1.2):
        p = parse_packet(d)
        if p.packet_id == 3 and p.event is not None and p.event.code == "FTLP":
            idx = p.event.details["vehicle_idx"]
            assert 0 <= idx < 22
            assert p.event.details["lap_time_s"] > 0
            assert "Fastest lap" in p.event.describe()
            return
    pytest.fail("no FTLP event observed")


def test_lap_positions_indexing_is_lap_major():
    """``m_positionForVehicleIdx[lap][car]`` — 50 строк по 22 машины."""
    s = _sim()
    s.lap_positions_history = [
        [car + 1 for car in range(22)],
        [((car + 5) % 22) + 1 for car in range(22)],
    ]
    d = sim.pack_lap_positions(s._hdr(15), s.cars, s.lap_positions_history)
    pkt = parse_packet(d).packet
    assert pkt.m_numLaps == 2
    assert pkt.m_lapStart == 0
    assert int(pkt.m_positionForVehicleIdx[0][0]) == 1
    assert int(pkt.m_positionForVehicleIdx[0][21]) == 22
    assert int(pkt.m_positionForVehicleIdx[1][0]) == 6


def test_garbage_bytes_are_rejected():
    assert parse_or_none(b"") is None
    assert parse_or_none(b"\x00" * 28) is None
    assert parse_or_none(b"hello world, not a packet at all") is None
    assert parse_or_none(bytes(range(255))) is None


def test_truncated_packet_rejected():
    s = _sim()
    d = next(_ticks(s, 0.1))
    assert parse_or_none(d[:-1]) is None
    with pytest.raises(TooShortError):
        parse_packet(d[:20])


def test_unknown_packet_id_rejected():
    s = _sim()
    d = bytearray(next(_ticks(s, 0.1)))
    d[6] = 99  # m_packetId
    assert parse_or_none(bytes(d)) is None
    with pytest.raises(UnknownPacketError):
        parse_packet(bytes(d))


def test_unsupported_format_rejected():
    """Игра с Season Pack шлёт формат 2026 — парсер обязан отказаться."""
    s = _sim()
    d = bytearray(next(_ticks(s, 0.1)))
    struct.pack_into("<H", d, 0, 2026)
    assert parse_or_none(bytes(d)) is None
    with pytest.raises(UnsupportedFormatError):
        parse_packet(bytes(d))


def test_fixture_write_and_read(tmp_path):
    s = _sim()
    datagrams = list(_ticks(s, 1.0))
    path = tmp_path / "fx.bin"
    n = write_fixture(path, datagrams)
    assert n == len(datagrams)
    assert path.stat().st_size == sum(len(d) + 2 for d in datagrams)

    read_back = list(iter_fixture(path))
    assert read_back == datagrams


def test_fixture_truncated_is_reported(tmp_path):
    path = tmp_path / "bad.bin"
    path.write_bytes(struct.pack("<H", 100) + b"\x00" * 10)
    out = list(iter_fixture(path))
    assert out == []


def test_car_damage_size_is_1041_not_1129():
    """Car Damage = 1041 (29 + 22×46), см. packets.py и заметки в AGENTS.md."""
    s = _sim()
    for d in _ticks(s, 0.5):
        p = parse_packet(d)
        if p.packet_id == 10:
            assert p.size == 1041
            assert len(p.packet.m_carDamageData) == 22
            return
    pytest.fail("no Car Damage packet observed")
