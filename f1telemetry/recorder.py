"""Оркестратор: приём UDP -> разбор -> состояние -> хранилище -> колбэки.

Класс :class:`Recorder` связывает:

* :class:`f1telemetry.listener.Listener` (поток приёма) **или** фикстуру
  (``.bin``, формат ``<u16 length><datagram>``);
* :class:`f1telemetry.parser.parse_packet`;
* :class:`f1telemetry.state.SessionState`;
* :class:`f1telemetry.storage.StorageWriter` (поток записи в SQLite).

Ядро не знает про Qt: GUI подписывается на колбэки и передаёт их в
Qt-сигналы. Отладочный/безголовый режим использует те же колбэки напрямую.

Наружу ничего не отправляется: приём только.
"""

from __future__ import annotations

import logging
import queue
import struct
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .constants import DEFAULT_LISTEN_HOST, DEFAULT_UDP_PORT, EXPECTED_PACKET_FORMAT
from .listener import Listener, ListenerStats
from .parser import ParsedPacket, UnsupportedFormatError, parse_packet
from .state import LiveSnapshot, PacketResult, SessionState
from .storage import Storage, StorageWriter

log = logging.getLogger(__name__)

__all__ = [
    "Recorder",
    "RecorderStats",
    "iter_fixture",
    "write_fixture",
]


# --- Фикстуры -----------------------------------------------------------------

def iter_fixture(path: str | Path) -> Iterator[bytes]:
    """Читает бинарную фикстуру ``<u16 length><datagram>``.

    Args:
        path: путь к файлу (создан ``tools/simulate.py --record-fixture``).

    Yields:
        Сырые датаграммы в порядке записи.
    """
    with Path(path).open("rb") as fh:
        while True:
            hdr = fh.read(2)
            if len(hdr) < 2:
                break
            (length,) = struct.unpack("<H", hdr)
            data = fh.read(length)
            if len(data) < length:
                log.warning("fixture truncated: expected %d bytes, got %d", length, len(data))
                break
            yield data


def write_fixture(path: str | Path, datagrams: Iterable[bytes]) -> int:
    """Записывает датаграммы в фикстуру. Возвращает число пакетов."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with p.open("wb") as fh:
        for d in datagrams:
            fh.write(struct.pack("<H", len(d)))
            fh.write(d)
            count += 1
    return count


# --- Статистика ---------------------------------------------------------------

class RecorderStats:
    """Счётчики оркестратора (потокобезопасно на уровне GIL)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.datagrams = 0
        self.parsed = 0
        self.parse_errors = 0
        self.format_errors = 0
        self.unsupported_formats: set[int] = set()
        self.laps_completed = 0
        self.sessions_seen = 0
        self.flashbacks = 0
        self.events = 0
        self.started_at: float | None = None
        self.last_packet_at: float | None = None

    def snapshot(self) -> dict[str, Any]:
        """Снимок счётчиков."""
        with self.lock:
            return {
                "datagrams": self.datagrams,
                "parsed": self.parsed,
                "parse_errors": self.parse_errors,
                "format_errors": self.format_errors,
                "unsupported_formats": sorted(self.unsupported_formats),
                "laps_completed": self.laps_completed,
                "sessions_seen": self.sessions_seen,
                "flashbacks": self.flashbacks,
                "events": self.events,
                "started_at": self.started_at,
                "last_packet_at": self.last_packet_at,
            }


# --- Оркестратор --------------------------------------------------------------

class Recorder:
    """Записывает UDP-поток F1 25 в SQLite и рассылает колбэки.

    Args:
        db_path: путь к SQLite-файлу.
        host: адрес привязки сокета (``0.0.0.0`` — все интерфейсы).
        port: UDP-порт (20777).
        sample_hz: частота прореживания телеметрии игрока в БД.
        detail_hz: частота записи деталей шин/тормозов.
        status_hz: частота записи топлива/ERS.
        buffer_seconds: глубина кольцевого буфера живого графика.
        fixture: путь к фикстуре — вместо UDP-приёма (для replay/тестов).
        replay_speed: реальная скорость воспроизведения фикстуры (0 — мгновенно).
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        host: str = DEFAULT_LISTEN_HOST,
        port: int = DEFAULT_UDP_PORT,
        sample_hz: float = 20.0,
        detail_hz: float = 10.0,
        status_hz: float = 5.0,
        buffer_seconds: float = 120.0,
        fixture: str | Path | None = None,
        replay_speed: float = 0.0,
        flush_interval: float = 0.5,
        batch_size: int = 500,
    ) -> None:
        self.db_path = Path(db_path)
        self.host = host
        self.port = port
        self.fixture = Path(fixture) if fixture is not None else None
        self.replay_speed = replay_speed

        self.state = SessionState(
            sample_hz=sample_hz,
            detail_hz=detail_hz,
            status_hz=status_hz,
            buffer_seconds=buffer_seconds,
        )
        self.writer = StorageWriter(
            self.db_path, flush_interval=flush_interval, batch_size=batch_size
        )
        self.listener: Listener | None = None
        if self.fixture is None:
            self.listener = Listener(host, port)

        self.stats = RecorderStats()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._replay_done = False
        self._fixture_iter: Iterator[bytes] | None = None
        self._fixture_last_t: float | None = None
        self._sync_storage: Storage | None = None
        self.sync_ops = 0
        self.sync_rows = 0

        # колбэки (GUI оборачивает в Qt-сигналы)
        self.on_session_change: Callable[[int], None] | None = None
        self.on_laps: Callable[[tuple[int, ...]], None] | None = None
        self.on_event: Callable[[str, dict[str, Any]], None] | None = None
        self.on_flashback: Callable[[], None] | None = None
        self.on_format_warning: Callable[[int], None] | None = None
        self.on_stats: Callable[[dict[str, Any]], None] | None = None

    # --- жизненный цикл ----------------------------------------------------

    def start(self) -> None:
        """Запускает приём/воспроизведение, writer и цикл обработки."""
        self.writer.start()
        if self.listener is not None:
            self.listener.start()
        self.stats.started_at = time.time()
        self._thread = threading.Thread(
            target=self._loop, name="recorder-loop", daemon=True
        )
        self._thread.start()
        log.info(
            "recorder started: db=%s source=%s",
            self.db_path, self.fixture or f"udp://{self.host}:{self.port}",
        )

    def stop(self, timeout: float = 5.0) -> None:
        """Останавливает всё и дожидается коммита."""
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        if self.listener is not None:
            self.listener.stop()
        self.writer.flush()
        self.writer.close()
        if self._sync_storage is not None:
            try:
                self._sync_storage.commit()
            finally:
                self._sync_storage.close()
                self._sync_storage = None
        log.info("recorder stopped: %s", self.stats.snapshot())

    def __enter__(self) -> "Recorder":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # --- доступ ------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """True, пока жив цикл обработки."""
        return self._thread is not None and self._thread.is_alive()

    def snapshot(self) -> LiveSnapshot:
        """Мгновенный срез для GUI."""
        return self.state.snapshot()

    def trace(self, max_points: int | None = None) -> list[Any]:
        """Кольцевой буфер живого графика."""
        return self.state.trace(max_points)

    def health(self) -> dict[str, Any]:
        """Сводка для статусной строки."""
        out = self.stats.snapshot()
        if self.writer.is_alive() or self.writer.ops_written:
            out.update(self.writer.stats())
        else:
            out.update({
                "queued": 0, "ops": self.sync_ops, "rows": self.sync_rows, "errors": 0,
            })
        out["state"] = self.state.packets_total
        out["state_errors"] = self.state.parse_errors
        if self.listener is not None:
            ls: ListenerStats = self.listener.stats
            out["listener"] = {
                "packets": ls.packets_received,
                "bytes": ls.bytes_received,
                "drops": ls.queue_drops,
                "bind_errors": ls.bind_errors,
                "local_addr": ls.local_addr,
            }
        else:
            out["listener"] = None
        if self.state.session_uid is not None:
            out["session_uid"] = self.state.session_uid
        return out

    # --- цикл -------------------------------------------------------------

    def _loop(self) -> None:  # pragma: no cover — покрыто интеграционными тестами
        last_stats_emit = 0.0
        while not self._stop.is_set():
            data = self._next_datagram()
            if data is not None:
                self.handle_datagram(data)
            now = time.monotonic()
            if now - last_stats_emit >= 1.0:
                last_stats_emit = now
                self._flush_writes()
                if self.on_stats is not None:
                    try:
                        self.on_stats(self.health())
                    except Exception:  # noqa: BLE001
                        log.exception("on_stats callback failed")
            if self._replay_done and self.fixture is not None:
                if self.listener is None:
                    break
        self._flush_writes()

    def _next_datagram(self) -> bytes | None:
        if self.fixture is not None:
            return self._next_from_fixture()
        if self.listener is None:  # pragma: no cover
            return None
        return self.listener.get(timeout=0.05)

    def _next_from_fixture(self) -> bytes | None:
        if self._fixture_iter is None:
            self._fixture_iter = iter_fixture(self.fixture)  # type: ignore[arg-type]
        try:
            data = next(self._fixture_iter)
        except StopIteration:
            self._replay_done = True
            return None
        if self.replay_speed > 0:
            # пауза пропорционально session_time (best-effort)
            if len(data) >= 18:
                (t,) = struct.unpack_from("<f", data, 13)
                now = time.monotonic()
                if self._fixture_last_t is not None:
                    dt = max(0.0, t - self._fixture_last_t) / self.replay_speed
                    if dt > 0:
                        time.sleep(min(dt, 0.5))
                self._fixture_last_t = t
        return data

    def handle_datagram(self, data: bytes) -> PacketResult:
        """Разбирает и применяет одну датаграмму. Возвращает :class:`PacketResult`.

        Некорректные датаграммы не роняют обработчик: счётчик + лог.
        """
        with self.stats.lock:
            self.stats.datagrams += 1

        try:
            parsed: ParsedPacket = parse_packet(data)
        except UnsupportedFormatError as exc:
            fmt = struct.unpack_from("<H", data, 0)[0] if len(data) >= 2 else -1
            with self.stats.lock:
                self.stats.format_errors += 1
                self.stats.unsupported_formats.add(fmt)
            if self.on_format_warning is not None and fmt not in (EXPECTED_PACKET_FORMAT,):
                try:
                    self.on_format_warning(fmt)
                except Exception:  # noqa: BLE001
                    log.exception("on_format_warning failed")
            log.warning("unsupported UDP format %s (%s)", fmt, exc)
            return PacketResult()
        except Exception as exc:  # noqa: BLE001 — UDP-мусор не должен ронять цикл
            with self.stats.lock:
                self.stats.parse_errors += 1
            log.debug("dropped datagram (%d bytes): %s", len(data), exc)
            return PacketResult()

        with self.stats.lock:
            self.stats.parsed += 1
            self.stats.last_packet_at = time.time()

        result = self.state.on_packet(parsed)
        if result.session_changed:
            with self.stats.lock:
                self.stats.sessions_seen += 1
            if self.on_session_change is not None:
                self.on_session_change(int(self.state.session_uid or 0))
        if result.laps_completed:
            with self.stats.lock:
                self.stats.laps_completed += len(result.laps_completed)
            if self.on_laps is not None:
                self.on_laps(result.laps_completed)
        if result.flashback:
            with self.stats.lock:
                self.stats.flashbacks += 1
            if self.on_flashback is not None:
                self.on_flashback()
        if result.event_code:
            with self.stats.lock:
                self.stats.events += 1
            if self.on_event is not None:
                details = dict(parsed.event.details) if parsed.event is not None else {}
                try:
                    self.on_event(result.event_code, details)
                except Exception:  # noqa: BLE001
                    log.exception("on_event callback failed")
        return result

    def _flush_writes(self, *, sync: bool = False) -> None:
        ops = self.state.drain_writes()
        if not ops:
            return
        if sync:
            if self._sync_storage is None:
                self._sync_storage = Storage(self.db_path)
            for op, args, kwargs in ops:
                getattr(self._sync_storage, op)(*args, **kwargs)
                self.sync_ops += 1
            self._sync_storage.commit()
            self.sync_rows += len(ops)
        else:
            for op, args, kwargs in ops:
                self.writer.submit(op, *args, **kwargs)

    # --- синхронная обработка (для тестов/CLI) ------------------------------

    def feed(self, datagrams: Iterable[bytes], *, drain: bool = True) -> int:
        """Синхронно обрабатывает датаграммы (без потоков). Возвращает их число."""
        n = 0
        for d in datagrams:
            self.handle_datagram(d)
            n += 1
            if drain:
                self._flush_writes(sync=True)
        if drain:
            self._flush_writes(sync=True)
        return n
