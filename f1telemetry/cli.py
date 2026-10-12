"""Headless-режим: запись без GUI, статистика, replay, экспорт, анонимизация.

Пути по умолчанию — XDG (работает из любого cwd): БД в
``~/.local/share/f1-telemetry/f1.sqlite``, логи в
``~/.local/state/f1-telemetry/logs``. Флаг ``--db`` переопределяет путь;
host/port берутся из ``~/.config/f1-telemetry/config.json``, если не заданы.

Примеры::

    python -m f1telemetry.cli record --seconds 30
    python -m f1telemetry.cli stats
    python -m f1telemetry.cli replay --fixture tests/fixtures/session_short.bin --db /tmp/r.sqlite
    python -m f1telemetry.cli export --session <uid> --out export/ --format csv
    python -m f1telemetry.cli anonymize            # перед передачей файла третьим лицам
    python -m f1telemetry.cli selftest
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from .constants import DEFAULT_UDP_PORT
from .export import export_session
from .paths import AppConfig, load_config, setup_logging
from .recorder import Recorder, iter_fixture
from .storage import Storage

log = logging.getLogger("f1telemetry.cli")


# --- Разрешение путей/настроек ------------------------------------------------

def _cfg() -> AppConfig:
    """Конфиг приложения (config.json в XDG-config-home)."""
    return load_config()


def _resolve_db(value: str | None, *, default_name: str = "f1.sqlite") -> Path:
    """Путь к БД: явный ``--db`` > конфиг > XDG-дефолт.

    Args:
        value: значение ``--db`` (может быть None).
        default_name: имя файла для XDG-дефолта (replay использует своё).

    Returns:
        Путь; каталог создаётся (best-effort).
    """
    if value:
        p = Path(value).expanduser()
    else:
        cfg = _cfg()
        p = cfg.resolved_db() if default_name == "f1.sqlite" else (
            cfg.resolved_db().with_name(default_name)
        )
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def _resolve_host(value: str | None) -> str:
    """Адрес: явный ``--host`` > конфиг > 0.0.0.0."""
    return value if value else _cfg().host


def _resolve_port(value: int | None) -> int:
    """Порт: явный ``--port`` > конфиг > 20777."""
    return int(value) if value else int(_cfg().port or DEFAULT_UDP_PORT)


# --- Команды ------------------------------------------------------------------

def cmd_record(args: argparse.Namespace) -> int:
    """Запись UDP-потока в SQLite без GUI."""
    db = _resolve_db(args.db)
    host = _resolve_host(args.host)
    port = _resolve_port(args.port)
    rec = Recorder(
        db,
        host=host,
        port=port,
        sample_hz=args.sample_hz,
        detail_hz=args.detail_hz,
        status_hz=args.status_hz,
        source_filter=args.source_filter,
    )
    _install_print_hooks(rec, quiet=args.quiet)
    try:
        rec.start()
    except OSError as exc:
        print(f"cannot listen on {host}:{port}: {exc}", file=sys.stderr)
        return 2
    print(f"listening on {host}:{port} -> {db}", flush=True)
    if args.source_filter:
        print(f"source filter: only {args.source_filter}", flush=True)
    deadline = (
        time.monotonic() + args.seconds
        if args.seconds and args.seconds > 0
        else None
    )
    last_h = time.monotonic()
    last_total = 0
    try:
        while True:
            time.sleep(0.5)
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                break
            if now - last_h >= 5.0:
                h = rec.health()
                total = int(h.get("datagrams", 0))
                rate = (total - last_total) / (now - last_h)
                last_total, last_h = total, now
                lst = h.get("listener") or {}
                print(
                    f"[health] {rate:.0f} datagrams/s, total={total}, "
                    f"parse_errors={h.get('parse_errors', 0)}, "
                    f"format_errors={h.get('format_errors', 0)}, "
                    f"queue_drops={lst.get('drops', 'n/a')}, "
                    f"lost_frames={h.get('lost_frames', 'n/a')}",
                    flush=True,
                )
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
    finally:
        rec.stop()
        _print_health(rec)
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    """Воспроизведение фикстуры в SQLite (без сети)."""
    fixture = Path(args.fixture)
    if not fixture.exists():
        print(f"fixture not found: {fixture}", file=sys.stderr)
        return 2
    db = _resolve_db(args.db, default_name="replay.sqlite")
    rec = Recorder(db, fixture=fixture, replay_speed=args.speed)
    _install_print_hooks(rec, quiet=args.quiet)
    rec.start()
    try:
        while rec.is_running:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
    finally:
        rec.stop()
        _print_health(rec)
    n = sum(1 for _ in iter_fixture(fixture))
    print(f"replayed {n} packets from {fixture}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    """Сводка по базе или одной сессии."""
    db = _resolve_db(args.db)
    if not db.exists():
        print(f"db not found: {db}", file=sys.stderr)
        return 2
    st = Storage(db, read_only=True)
    try:
        stats = st.stats(args.session)
        if args.json:
            print(json.dumps(stats, ensure_ascii=False, indent=2))
            return 0
        print(f"db:                {db}")
        print(f"sessions:          {stats['sessions']}")
        print(f"laps:              {stats['laps']} (cars with laps: {stats['cars_with_laps']})")
        best = stats["best_lap_ms"]
        print(f"best lap:          {_fmt_ms(best)}")
        print(f"avg lap:           {_fmt_ms(stats['avg_lap_ms'])}")
        print(f"telemetry points:  {stats['telemetry_points']}")
        print(f"events:            {stats['events']}")
        print(f"participants:      {stats['participants']}")
        print(f"stints:            {stats['stints']}")
        print(f"size:              {stats['size_bytes']} bytes")

        if args.list_sessions:
            print("\nsessions:")
            for s in st.list_sessions(limit=50):
                print(
                    f"  {s.session_uid}  {s.track_name or '#'+str(s.track_id)}  "
                    f"{s.session_type_name or ''}  laps={s.lap_count}  "
                    f"best={_fmt_ms(s.best_lap_ms)}"
                )
        return 0
    finally:
        st.close()


def cmd_export(args: argparse.Namespace) -> int:
    """Экспорт сессии в CSV/JSON."""
    db = _resolve_db(args.db)
    if not db.exists():
        print(f"db not found: {db}", file=sys.stderr)
        return 2
    st = Storage(db, read_only=True)
    try:
        if args.session is None:
            sessions = st.list_sessions(limit=1)
            if not sessions:
                print("no sessions in db", file=sys.stderr)
                return 2
            args.session = sessions[0].session_uid
            print(f"exporting latest session {args.session}")
    finally:
        st.close()

    written = export_session(
        db, int(args.session), args.out, fmt=args.format, tables=args.tables
    )
    for name, path in written.items():
        print(f"  {name}: {path}")
    print(f"{len(written)} file(s) written to {args.out}")
    return 0


def cmd_anonymize(args: argparse.Namespace) -> int:
    """Анонимизация БД: имена → «Driver N», name из событий удаляется.

    Заменяет ``participants.name`` на «Driver N» (N = car_index+1, стабильно
    внутри сессии) и вычищает ключ ``name`` из ``events.payload_json``.
    Нужно перед передачей файла третьим лицам: никнеймы из мультиплеера
    лежат в локальной БД.
    """
    db = _resolve_db(args.db)
    if not db.exists():
        print(f"db not found: {db}", file=sys.stderr)
        return 2
    st = Storage(db)
    try:
        n = st.anonymize(args.session)
    finally:
        st.close()
    scope = f"session {args.session}" if args.session is not None else "all sessions"
    print(f"anonymized {n} session(s) ({scope}) in {db}")
    if n == 0:
        print("nothing to anonymize", file=sys.stderr)
        return 1
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    """Headless-проверка: мини-фикстура -> состояние -> SQLite -> выборка."""
    import tempfile

    from .parser import parse_or_none

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "selftest.sqlite"
        fixture = _make_selftest_fixture()
        rec = Recorder(db, fixture=fixture)
        n = rec.feed(iter_fixture(fixture))
        rec.stop()

        st = Storage(db)
        try:
            stats = st.stats()
            sessions = st.list_sessions()
        finally:
            st.close()

        ok = (
            n > 0
            and stats["sessions"] >= 1
            and stats["telemetry_points"] > 0
            and stats["laps"] > 0
            and sessions
        )
        print(f"selftest: {n} packets, stats={json.dumps(stats, default=str)}")
        if not ok:
            print("SELFTEST FAILED", file=sys.stderr)
            return 1
        print("SELFTEST OK")
        del parse_or_none
        return 0


def _make_selftest_fixture() -> Path:
    """Генерирует короткую фикстуру симулятором во временном файле."""
    import tempfile

    tools = Path(__file__).resolve().parent.parent / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    import simulate as sim  # type: ignore[import-not-found]

    cfg = sim.SimConfig(
        hz=30.0, laps=2, cars=22, track_length=400.0, seed=7,
        session_uid=999001, verbose=False,
    )
    s = sim.Simulator(cfg)
    seconds = s.profile.lap_time_s * 2 + 3.0
    datagrams: list[bytes] = []
    while s.t < seconds:
        datagrams.extend(s.tick())
    path = Path(tempfile.mkstemp(suffix=".bin")[1])
    from .recorder import write_fixture

    write_fixture(path, datagrams)
    return path


def _install_print_hooks(rec: Recorder, *, quiet: bool = False) -> None:
    if quiet:
        return

    def on_session(uid: int) -> None:
        print(f"[session] uid={uid}", flush=True)

    def on_laps(cars: tuple[int, ...]) -> None:
        player = rec.state.player_car_index
        mark = " (player)" if player in cars else ""
        print(f"[lap] cars={list(cars)}{mark}", flush=True)

    def on_event(code: str, details: dict[str, Any]) -> None:
        print(f"[event] {code} {details}", flush=True)

    def on_format_warning(fmt: int, src: str = "?") -> None:
        print(
            f"[warning] UDP format {fmt} from {src}, expected 2025 — "
            "set in game: Settings → UDP Telemetry → UDP Format = 2025",
            file=sys.stderr, flush=True,
        )

    rec.on_session_change = on_session
    rec.on_laps = on_laps
    rec.on_event = on_event
    rec.on_format_warning = on_format_warning


def _print_health(rec: Recorder) -> None:
    h = rec.health()
    print(
        "recorded: "
        f"{h['datagrams']} datagrams, {h['parsed']} parsed, "
        f"{h['parse_errors']} parse errors, {h['format_errors']} format errors, "
        f"{h['laps_completed']} laps, {h['events']} events, "
        f"writer: {h['ops']} ops / {h['rows']} rows / {h['errors']} errors"
    )


def _fmt_ms(ms: int | None) -> str:
    if not ms:
        return "—"
    ms = int(ms)
    return f"{ms // 60000}:{(ms % 60000) / 1000:06.3f}"


# --- Разбор аргументов --------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="f1telemetry", description="F1 25 Telemetry Recorder (headless mode)"
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("record", help="listen UDP and write to SQLite")
    r.add_argument("--db", default=None, help="путь к БД (дефолт: XDG или конфиг)")
    r.add_argument("--host", default=None, help="адрес (дефолт: конфиг или 0.0.0.0)")
    r.add_argument("--port", type=int, default=None, help="порт (дефолт: конфиг или 20777)")
    r.add_argument("--seconds", type=float, default=0.0, help="0 = until Ctrl+C")
    r.add_argument("--sample-hz", type=float, default=20.0)
    r.add_argument("--detail-hz", type=float, default=10.0)
    r.add_argument("--status-hz", type=float, default=5.0)
    r.add_argument("--source-filter", default=None, metavar="IP",
                   help="принимать только датаграммы с этого IP "
                        "(адрес игровой машины)")
    r.add_argument("--quiet", action="store_true")
    r.set_defaults(func=cmd_record)

    rp = sub.add_parser("replay", help="replay a fixture file into SQLite")
    rp.add_argument("--fixture", required=True)
    rp.add_argument("--db", default=None, help="путь к БД (дефолт: XDG/replay.sqlite)")
    rp.add_argument("--speed", type=float, default=0.0, help="0 = as fast as possible")
    rp.add_argument("--quiet", action="store_true")
    rp.set_defaults(func=cmd_replay)

    s = sub.add_parser("stats", help="show database summary")
    s.add_argument("--db", default=None, help="путь к БД (дефолт: XDG или конфиг)")
    s.add_argument("--session", type=int, default=None)
    s.add_argument("--list-sessions", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_stats)

    e = sub.add_parser("export", help="export a session to CSV/JSON")
    e.add_argument("--db", default=None, help="путь к БД (дефолт: XDG или конфиг)")
    e.add_argument("--session", type=int, default=None)
    e.add_argument("--out", default="export")
    e.add_argument("--format", default="csv", choices=("csv", "json"))
    e.add_argument("--tables", nargs="*", default=None)
    e.set_defaults(func=cmd_export)

    st = sub.add_parser("selftest", help="headless smoke test (no game, no GUI)")
    st.set_defaults(func=cmd_selftest)

    an = sub.add_parser(
        "anonymize",
        help="replace participant names with 'Driver N', strip names from events",
    )
    an.add_argument("--db", default=None, help="путь к БД (дефолт: XDG или конфиг)")
    an.add_argument("--session", type=int, default=None,
                    help="только одна сессия (дефолт — все)")
    an.set_defaults(func=cmd_anonymize)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа ``python -m f1telemetry.cli``."""
    args = build_parser().parse_args(argv)
    setup_logging(verbose=args.verbose)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
