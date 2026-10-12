"""UDP-приём телеметрии F1 25 в отдельном потоке.

Сокет слушает ``0.0.0.0:20777`` (настраивается): игра шлёт широковещательные
или адресные датаграммы на хост в той же LAN/сети виртуальной машины —
привязка к ``127.0.0.1`` была бы ошибкой.

Поток только принимает байты и кладёт их в очередь; разбор выполняется
вызывающей стороной (см. :mod:`f1telemetry.recorder`). Никакой обратной
отправки наружу нет.
"""

from __future__ import annotations

import logging
import queue
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator

from .constants import DEFAULT_LISTEN_HOST, DEFAULT_UDP_PORT, RECV_BUFFER_SIZE

log = logging.getLogger(__name__)

__all__ = ["Listener", "ListenerStats"]


@dataclass(slots=True)
class ListenerStats:
    """Счётчики приёма (для панели диагностики)."""

    packets_received: int = 0
    bytes_received: int = 0
    queue_drops: int = 0
    bind_errors: int = 0
    started_at: float | None = None
    last_datagram_at: float | None = None
    local_addr: str = ""
    errors: list[str] = field(default_factory=list)


class Listener(threading.Thread):
    """Приём UDP-датаграмм в daemon-потоке.

    Args:
        host: адрес привязки (по умолчанию ``0.0.0.0`` — все интерфейсы).
        port: порт (по умолчанию 20777).
        max_queue: глубина очереди датаграмм; при переполнении старые
            датаграммы отбрасываются (UDP и так не гарантирует доставку).
        on_error: опциональный колбэк ``(exc) -> None`` при ошибках сокета.
    """

    def __init__(
        self,
        host: str = DEFAULT_LISTEN_HOST,
        port: int = DEFAULT_UDP_PORT,
        *,
        max_queue: int = 4096,
        recv_buffer: int = RECV_BUFFER_SIZE,
        on_error: Callable[[Exception], None] | None = None,
        name: str = "udp-listener",
    ) -> None:
        super().__init__(name=name, daemon=True)
        self.host = host
        self.port = port
        self.max_queue = max_queue
        self.recv_buffer = recv_buffer
        self.on_error = on_error
        self.stats = ListenerStats()
        self.queue: queue.Queue[tuple[bytes, str] | None] = queue.Queue(
            maxsize=max_queue
        )
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._closed = False

    # --- жизненный цикл ----------------------------------------------------

    def start(self) -> None:
        """Создаёт сокет (сокет создаётся здесь, чтобы ошибки увидел вызывающий)."""
        self._sock = self._create_socket()
        super().start()

    def _create_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        except OSError:  # pragma: no cover — зависит от ОС
            pass
        sock.bind((self.host, self.port))
        sock.settimeout(0.3)
        addr, port_ = sock.getsockname()
        self.stats.local_addr = f"{addr}:{port_}"
        log.info("listening on %s", self.stats.local_addr)
        return sock

    def stop(self, timeout: float = 2.0) -> None:
        """Останавливает поток и закрывает сокет."""
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if self.is_alive():
            self.join(timeout=timeout)
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:  # pragma: no cover
                pass
            self._sock = None

    # --- приём -------------------------------------------------------------

    def run(self) -> None:  # pragma: no cover — покрыто интеграционными тестами
        assert self._sock is not None
        self.stats.started_at = time.time()
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(self.recv_buffer)
            except socket.timeout:
                continue
            except OSError as exc:
                if self._stop.is_set():
                    break
                self.stats.bind_errors += 1
                self.stats.errors.append(str(exc))
                log.error("recvfrom failed: %s", exc)
                if self.on_error is not None:
                    self.on_error(exc)
                time.sleep(0.2)
                continue

            self.stats.packets_received += 1
            self.stats.bytes_received += len(data)
            self.stats.last_datagram_at = time.time()
            try:
                self.queue.put_nowait((data, addr[0]))
            except queue.Full:
                # Отбрасываем самую старую датаграмму: лучше иметь свежие.
                self.stats.queue_drops += 1
                try:
                    self.queue.get_nowait()
                    self.queue.put_nowait((data, addr[0]))
                except (queue.Empty, queue.Full):  # pragma: no cover
                    pass

    def get(self, timeout: float = 0.1) -> tuple[bytes, str] | None:
        """Забирает датаграмму из очереди (``None`` по таймауту).

        Returns:
            ``(data, src_ip)`` — сырые байты и IP-адрес отправителя
            (нужен для ``--source-filter`` и диагностики чужих пакетов).
        """
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def __iter__(self) -> Iterator[tuple[bytes, str]]:
        """Итерирует ``(data, src_ip)`` до остановки потока."""
        while not self._stop.is_set() or not self.queue.empty():
            item = self.get(timeout=0.2)
            if item is not None:
                yield item
