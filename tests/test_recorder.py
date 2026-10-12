"""Тесты UDP-приёмника и оркестратора поверх реального сокета."""

from __future__ import annotations

import socket
import time

import simulate as sim
from f1telemetry.listener import Listener
from f1telemetry.recorder import Recorder

HZ = 30.0
TRACK_LENGTH = 400.0


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


def test_listener_binds_and_receives():
    port = _free_udp_port()
    lis = Listener("127.0.0.1", port, max_queue=64)
    lis.start()
    try:
        _send("127.0.0.1", port, [b"hello", b"world"])
        got = []
        deadline = time.time() + 3.0
        while len(got) < 2 and time.time() < deadline:
            d = lis.get(timeout=0.2)
            if d is not None:
                got.append(d[0])
        assert got == [b"hello", b"world"]
        assert lis.stats.packets_received == 2
        assert lis.stats.bytes_received == 10
        assert lis.stats.local_addr.endswith(f":{port}")
    finally:
        lis.stop()


def test_listener_drops_oldest_when_queue_full():
    port = _free_udp_port()
    lis = Listener("127.0.0.1", port, max_queue=2)
    lis.start()
    try:
        _send("127.0.0.1", port, [b"a", b"b", b"c", b"d"])
        time.sleep(0.3)
        assert lis.stats.packets_received >= 3
        assert lis.stats.queue_drops >= 1
        got = []
        while True:
            d = lis.get(timeout=0.05)
            if d is None:
                break
            got.append(d[0])
        assert got  # самые свежие датаграммы сохранены
        assert got[-1] in (b"c", b"d")
    finally:
        lis.stop()


def test_listener_iter_stops():
    port = _free_udp_port()
    lis = Listener("127.0.0.1", port)
    lis.start()
    try:
        _send("127.0.0.1", port, [b"x"])
        out = []
        deadline = time.time() + 2.0
        for d in lis:
            out.append(d[0])
            if len(out) >= 1 or time.time() > deadline:
                break
        assert out == [b"x"]
    finally:
        lis.stop()


def test_recorder_over_real_udp(tmp_path):
    """Полный путь: симулятор -> UDP -> Listener -> Recorder -> SQLite."""
    port = _free_udp_port()
    db = tmp_path / "udp.sqlite"
    rec = Recorder(db, host="127.0.0.1", port=port, sample_hz=20.0)
    rec.start()
    try:
        s = sim.Simulator(sim.SimConfig(
            hz=HZ, laps=1, cars=22, track_length=TRACK_LENGTH,
            seed=5, session_uid=555001, verbose=False,
        ))
        datagrams: list[bytes] = []
        while s.t < s.profile.lap_time_s + 1.0:
            datagrams.extend(s.tick())
        _send("127.0.0.1", port, datagrams)

        deadline = time.time() + 15.0
        while time.time() < deadline:
            snap = rec.health()
            if snap["datagrams"] >= len(datagrams) - 5:
                break
            time.sleep(0.1)
        time.sleep(0.5)   # дать writer'у дописать
    finally:
        rec.stop()

    snap = rec.health()
    assert snap["datagrams"] > 100
    assert snap["parse_errors"] == 0
    assert snap["format_errors"] == 0
    assert snap["listener"]["packets"] == snap["datagrams"]
    assert snap["ops"] > 0 or snap["rows"] > 0

    from f1telemetry.storage import Storage
    st = Storage(db, read_only=True)
    try:
        assert st.get_session(555001) is not None
        assert len(st.list_participants(555001)) == 22
    finally:
        st.close()


def test_recorder_replay_fixture_is_fast(tmp_path):
    """Реплей фикстуры не использует сеть и завершается сам."""
    import simulate as sm
    from f1telemetry.recorder import iter_fixture, write_fixture

    s = sm.Simulator(sm.SimConfig(
        hz=20.0, laps=1, cars=22, track_length=TRACK_LENGTH,
        seed=9, session_uid=666001, verbose=False,
    ))
    datagrams = []
    while s.t < 2.0:
        datagrams.extend(s.tick())
    fx = tmp_path / "fx.bin"
    write_fixture(fx, datagrams)
    assert sum(1 for _ in iter_fixture(fx)) == len(datagrams)

    rec = Recorder(tmp_path / "replay.sqlite", fixture=fx)
    rec.start()
    deadline = time.time() + 20.0
    while rec._thread is not None and rec._thread.is_alive() and time.time() < deadline:
        time.sleep(0.05)
    rec.stop()
    assert not (rec._thread and rec._thread.is_alive())
    assert rec.health()["parsed"] == len(datagrams)


def test_recorder_callbacks(tmp_path):
    import simulate as sm

    s = sm.Simulator(sm.SimConfig(
        hz=HZ, laps=2, cars=22, track_length=TRACK_LENGTH,
        seed=13, session_uid=777001, verbose=False,
    ))
    rec = Recorder(tmp_path / "cb.sqlite")
    sessions: list[int] = []
    laps: list[tuple[int, ...]] = []
    events: list[str] = []
    rec.on_session_change = sessions.append
    rec.on_laps = laps.append
    rec.on_event = lambda code, details: events.append(code)

    datagrams = []
    while s.t < s.profile.lap_time_s * 1.5:
        datagrams.extend(s.tick())
    rec.feed(datagrams)
    rec.stop()

    assert sessions == [777001]
    assert laps and any(0 in t for t in laps)
    assert "SSTA" in events
    assert "FTLP" in events


def test_listener_passes_source_ip():
    """Очередь Listener несёт (data, src_ip) — IP нужен для --source-filter."""
    port = _free_udp_port()
    lis = Listener("127.0.0.1", port)
    lis.start()
    try:
        _send("127.0.0.1", port, [b"z"])
        item = None
        deadline = time.time() + 2.0
        while item is None and time.time() < deadline:
            item = lis.get(timeout=0.2)
        assert item is not None
        data, src = item
        assert data == b"z"
        assert src == "127.0.0.1"
    finally:
        lis.stop()


def test_source_filter_and_warning_carries_src(tmp_path):
    """--source-filter: чужой IP отбрасывается, ворнинг несёт IP источника."""
    from f1telemetry.recorder import Recorder

    rec = Recorder(tmp_path / "sf.sqlite", source_filter="10.0.0.5")
    warnings: list[tuple[int, str]] = []
    rec.on_format_warning = lambda fmt, src: warnings.append((fmt, src))

    # чужой IP: отброшено без разбора
    rec.handle_datagram(b"\xff" * 45, src_addr="192.168.1.7")
    snap = rec.stats.snapshot()
    assert snap["source_filtered"] == 1
    assert snap["format_errors"] == 0
    assert warnings == []

    # свой IP: разбирается (формат 0xffff → UnsupportedFormatError) + IP в ворнинге
    rec.handle_datagram(b"\xff" * 45, src_addr="10.0.0.5")
    snap = rec.stats.snapshot()
    assert snap["source_filtered"] == 1
    assert snap["format_errors"] == 1
    assert warnings == [(0xFFFF, "10.0.0.5")]

    # без src_addr (feed/фикстура) фильтр не действует
    rec.handle_datagram(b"\xff" * 45)
    assert rec.stats.snapshot()["format_errors"] == 2
