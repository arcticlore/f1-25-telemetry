"""Тесты CLI: selftest, stats, export."""

from __future__ import annotations

import json

import simulate as sim
from f1telemetry import cli
from f1telemetry.recorder import write_fixture


def _make_db(tmp_path, laps: float = 1.5, uid: int = 888001):
    """Создаёт непустую БД через Recorder.feed."""
    from f1telemetry.recorder import Recorder

    s = sim.Simulator(sim.SimConfig(
        hz=20.0, laps=int(laps), cars=22, track_length=400.0,
        seed=21, session_uid=uid, verbose=False,
    ))
    db = tmp_path / "cli.sqlite"
    rec = Recorder(db)
    datagrams = []
    while s.t < s.profile.lap_time_s * laps:
        datagrams.extend(s.tick())
    rec.feed(datagrams)
    rec.stop()
    return db, uid


def test_selftest_ok(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["selftest"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "SELFTEST OK" in out


def test_stats_output(capsys, tmp_path):
    db, uid = _make_db(tmp_path)
    rc = cli.main(["stats", "--db", str(db), "--session", str(uid), "--list-sessions"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "sessions:          1" in out
    assert "telemetry points:" in out
    assert str(uid) in out


def test_stats_json(capsys, tmp_path):
    db, uid = _make_db(tmp_path)
    rc = cli.main(["stats", "--db", str(db), "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data["sessions"] == 1
    assert data["telemetry_points"] > 0
    assert data["laps"] > 0


def test_stats_missing_db(tmp_path, capsys):
    rc = cli.main(["stats", "--db", str(tmp_path / "nope.sqlite")])
    assert rc == 2
    assert "db not found" in capsys.readouterr().err


def test_export_csv(capsys, tmp_path):
    db, uid = _make_db(tmp_path)
    out_dir = tmp_path / "export_csv"
    rc = cli.main([
        "export", "--db", str(db), "--session", str(uid),
        "--out", str(out_dir), "--format", "csv",
    ])
    assert rc == 0
    files = sorted(p.name for p in out_dir.iterdir())
    assert any(f.startswith("laps_") for f in files)
    assert any(f.startswith("participants_") for f in files)
    assert any(f.startswith("telemetry_") for f in files)
    laps_csv = next(out_dir.glob("laps_*.csv"))
    text = laps_csv.read_text(encoding="utf-8")
    assert "lap_time_ms" in text.splitlines()[0]
    assert len(text.splitlines()) > 2


def test_export_json(capsys, tmp_path):
    db, uid = _make_db(tmp_path)
    out_dir = tmp_path / "export_json"
    rc = cli.main([
        "export", "--db", str(db), "--out", str(out_dir), "--format", "json",
    ])
    assert rc == 0
    path = next(out_dir.glob("session_*.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["sessions"][0]["session_uid"] == uid
    assert data["laps"]
    assert data["participants"]


def test_export_bad_format(tmp_path):
    db, uid = _make_db(tmp_path)
    import pytest
    from f1telemetry.export import export_session
    with pytest.raises(ValueError):
        export_session(db, uid, tmp_path / "x", fmt="xml")


def test_replay_command(capsys, tmp_path):
    s = sim.Simulator(sim.SimConfig(
        hz=20.0, laps=1, cars=22, track_length=400.0,
        seed=33, session_uid=999001, verbose=False,
    ))
    datagrams = []
    while s.t < 2.0:
        datagrams.extend(s.tick())
    fx = tmp_path / "r.bin"
    write_fixture(fx, datagrams)

    db = tmp_path / "replayed.sqlite"
    rc = cli.main([
        "replay", "--fixture", str(fx), "--db", str(db), "--quiet",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"replayed {len(datagrams)} packets" in out

    from f1telemetry.storage import Storage
    st = Storage(db, read_only=True)
    try:
        assert st.get_session(999001) is not None
        assert st.stats(999001)["participants"] == 22
    finally:
        st.close()


def test_replay_missing_fixture(tmp_path, capsys):
    rc = cli.main(["replay", "--fixture", str(tmp_path / "no.bin")])
    assert rc == 2
    assert "fixture not found" in capsys.readouterr().err
