"""Разбор бинарных UDP-пакетов F1 25.

Публичный API:

``parse_packet(data)``
    Разбирает пакет; бросает :class:`ValueError` / :class:`UnsupportedFormatError`
    при некорректных данных.

``parse_or_none(data)``
    Безопасный вариант: любая ошибка -> ``None`` (плюс счётчик/лог у вызывающего).

Единицы измерения в docstring: км/ч, мс, метры, Джоули, °C.
"""

from __future__ import annotations

import ctypes
import logging
import struct
from dataclasses import dataclass, field
from typing import Any

from .constants import (
    EVENT_CODES,
    EXPECTED_PACKET_FORMAT,
    PacketId,
    packet_name,
)
from .packets import (
    EVENT_DETAILS_SIZE,
    PACKET_STRUCTS,
    EventPacket,
    Header,
)

log = logging.getLogger(__name__)

__all__ = [
    "HeaderInfo",
    "EventInfo",
    "ParsedPacket",
    "UnsupportedFormatError",
    "TooShortError",
    "UnknownPacketError",
    "parse_header",
    "parse_packet",
    "parse_or_none",
    "decode_event_details",
    "sector_ms",
]


class UnsupportedFormatError(ValueError):
    """``m_packetFormat`` не равен 2025 (например, 2026 Season Pack)."""


class TooShortError(ValueError):
    """Датаграмма короче заголовка или ожидаемого размера пакета."""


class UnknownPacketError(ValueError):
    """Неизвестный ``m_packetId``."""


@dataclass(frozen=True, slots=True)
class HeaderInfo:
    """Распарсенный заголовок пакета (29 байт)."""

    packet_format: int
    game_year: int
    game_major_version: int
    game_minor_version: int
    packet_version: int
    packet_id: int
    session_uid: int
    session_time: float  # секунды с начала сессии
    frame_identifier: int
    overall_frame_identifier: int
    player_car_index: int
    secondary_player_car_index: int

    @property
    def has_secondary_player(self) -> bool:
        """True, если второй игрок присутствует (индекс != 255)."""
        return self.secondary_player_car_index != 255

    @property
    def packet_type_name(self) -> str:
        """Человекочитаемое имя пакета."""
        return packet_name(self.packet_id)


@dataclass(frozen=True, slots=True)
class EventInfo:
    """Расшифрованное событие Event-пакета (packet id 3)."""

    code: str
    name: str
    details: dict[str, Any] = field(default_factory=dict)

    def describe(self, car_name: str | None = None, other_name: str | None = None) -> str:
        """Описание события человеческим языком.

        Args:
            car_name: имя пилота, к которому относится событие.
            other_name: имя второго пилота (для коллизий и обгонов).
        """
        who = car_name or "—"
        d = self.details
        base = f"{self.name}"
        if self.code == "FTLP":
            lap = d.get("lap_time_s")
            base = f"Fastest lap: {who}" + (f" ({lap:.3f} s)" if lap else "")
        elif self.code == "RTMT":
            base = f"Retirement: {who} (reason #{d.get('reason', '?')})"
        elif self.code == "DRSD":
            base = f"DRS disabled (reason #{d.get('reason', '?')})"
        elif self.code == "TMPT":
            base = f"Team mate in pits: {who}"
        elif self.code == "RCWN":
            base = f"Race winner: {who}"
        elif self.code == "PENA":
            base = (
                f"Penalty for {who}: type #{d.get('penalty_type', '?')}, "
                f"infringement #{d.get('infringement_type', '?')}, "
                f"{d.get('time_s', 0)} s on lap {d.get('lap_num', '?')}"
            )
        elif self.code == "SPTP":
            base = (
                f"Speed trap {who}: {d.get('speed_kmh', 0):.1f} km/h "
                f"(session best {d.get('fastest_speed_kmh', 0):.1f} km/h)"
            )
        elif self.code == "STLG":
            base = f"Start lights: {d.get('num_lights', 0)}"
        elif self.code == "DTSV":
            base = f"Drive through served: {who}"
        elif self.code == "SGSV":
            base = f"Stop-go served: {who} ({d.get('stop_time_s', 0):.1f} s)"
        elif self.code == "FLBK":
            base = f"Flashback at t={d.get('session_time_s', 0):.1f} s"
        elif self.code == "OVTK":
            base = f"Overtake: {who} -> {other_name or '#?'}"
        elif self.code == "SCAR":
            base = (
                f"Safety car: type={d.get('safety_car_type', '?')}, "
                f"event={d.get('event_type', '?')}"
            )
        elif self.code == "COLL":
            base = f"Collision: {who} <-> {other_name or '#?'}"
        return base


@dataclass(frozen=True, slots=True)
class ParsedPacket:
    """Результат разбора одной датаграммы."""

    header: HeaderInfo
    packet_id: int
    size: int
    packet: Any  # ctypes-экземпляр соответствующего Packet*
    event: EventInfo | None = None
    """Расшифровка события — только для packet id 3, иначе ``None``."""


def parse_header(data: bytes) -> HeaderInfo:
    """Разбирает заголовок (29 байт) без проверки формата.

    Args:
        data: сырая датаграмма.

    Returns:
        :class:`HeaderInfo`.

    Raises:
        TooShortError: если ``len(data) < 29``.
    """
    if len(data) < 29:
        raise TooShortError(f"datagram too short for header: {len(data)} bytes")
    h = Header.from_buffer_copy(data[:29])
    return HeaderInfo(
        packet_format=h.m_packetFormat,
        game_year=h.m_gameYear,
        game_major_version=h.m_gameMajorVersion,
        game_minor_version=h.m_gameMinorVersion,
        packet_version=h.m_packetVersion,
        packet_id=h.m_packetId,
        session_uid=h.m_sessionUID,
        session_time=h.m_sessionTime,
        frame_identifier=h.m_frameIdentifier,
        overall_frame_identifier=h.m_overallFrameIdentifier,
        player_car_index=h.m_playerCarIndex,
        secondary_player_car_index=h.m_secondaryPlayerCarIndex,
    )


def decode_event_details(code: str, blob: bytes) -> dict[str, Any]:
    """Расшифровывает union ``EventDataDetails`` (12 байт) по коду события.

    Структуры деталей — AGENTS.md, раздел 3.5.

    Args:
        code: четырёхсимвольный ``eventStringCode``.
        blob: 12 байт union-а.

    Returns:
        Словарь расшифрованных полей (может быть пустым).
    """
    raw = blob[:EVENT_DETAILS_SIZE]
    if len(raw) < EVENT_DETAILS_SIZE:
        raw = raw + b"\x00" * (EVENT_DETAILS_SIZE - len(raw))

    def _unpack(fmt: str) -> tuple[Any, ...]:
        return struct.unpack_from("<" + fmt, raw)

    match code:
        case "FTLP":
            idx, lap = _unpack("Bf")
            return {"vehicle_idx": int(idx), "lap_time_s": float(lap)}
        case "RTMT":
            idx, reason = _unpack("BB")
            return {"vehicle_idx": int(idx), "reason": int(reason)}
        case "DRSD":
            (reason,) = _unpack("B")
            return {"reason": int(reason)}
        case "TMPT":
            (idx,) = _unpack("B")
            return {"vehicle_idx": int(idx)}
        case "RCWN":
            (idx,) = _unpack("B")
            return {"vehicle_idx": int(idx)}
        case "PENA":
            ptype, infr, idx, other, tsec, lap, places = _unpack("BBBBBBB")
            return {
                "penalty_type": int(ptype),
                "infringement_type": int(infr),
                "vehicle_idx": int(idx),
                "other_vehicle_idx": int(other),
                "time_s": int(tsec),
                "lap_num": int(lap),
                "places_gained": int(places),
            }
        case "SPTP":
            idx, speed, overall, driver, fastest_idx, fastest_speed = _unpack("BfBBBf")
            return {
                "vehicle_idx": int(idx),
                "speed_kmh": float(speed),
                "is_overall_fastest": int(overall),
                "is_driver_fastest": int(driver),
                "fastest_vehicle_idx": int(fastest_idx),
                "fastest_speed_kmh": float(fastest_speed),
            }
        case "STLG":
            (num,) = _unpack("B")
            return {"num_lights": int(num)}
        case "DTSV":
            (idx,) = _unpack("B")
            return {"vehicle_idx": int(idx)}
        case "SGSV":
            idx, tsec = _unpack("Bf")
            return {"vehicle_idx": int(idx), "stop_time_s": float(tsec)}
        case "FLBK":
            frame, t = _unpack("If")
            return {"flashback_frame": int(frame), "session_time_s": float(t)}
        case "BUTN":
            (status,) = _unpack("I")
            return {"button_status": int(status)}
        case "OVTK":
            overtaker, overtaken = _unpack("BB")
            return {
                "overtaking_vehicle_idx": int(overtaker),
                "being_overtaken_vehicle_idx": int(overtaken),
            }
        case "SCAR":
            sc_type, ev_type = _unpack("BB")
            return {"safety_car_type": int(sc_type), "event_type": int(ev_type)}
        case "COLL":
            v1, v2 = _unpack("BB")
            return {"vehicle1_idx": int(v1), "vehicle2_idx": int(v2)}
        case _:
            return {}


def parse_packet(data: bytes) -> ParsedPacket:
    """Разбирает одну UDP-датаграмму F1 25.

    Args:
        data: сырые байты датаграммы.

    Returns:
        :class:`ParsedPacket`.

    Raises:
        TooShortError: датаграмма короче заголовка или ожидаемого размера.
        UnsupportedFormatError: ``m_packetFormat`` != 2025.
        UnknownPacketError: неизвестный ``m_packetId``.
    """
    header = parse_header(data)

    if header.packet_format != EXPECTED_PACKET_FORMAT:
        raise UnsupportedFormatError(
            f"unsupported packet format {header.packet_format}, "
            f"expected {EXPECTED_PACKET_FORMAT}"
        )

    cls = PACKET_STRUCTS.get(header.packet_id)
    if cls is None:
        raise UnknownPacketError(f"unknown packet id {header.packet_id}")

    expected = ctypes.sizeof(cls)
    if len(data) < expected:
        raise TooShortError(
            f"{packet_name(header.packet_id)}: got {len(data)} bytes, "
            f"expected {expected}"
        )

    packet = cls.from_buffer_copy(data[:expected])

    event: EventInfo | None = None
    if header.packet_id == PacketId.EVENT:
        assert isinstance(packet, EventPacket)
        code = bytes(packet.m_eventStringCode).decode("ascii", errors="replace")
        details = decode_event_details(code, bytes(bytearray(packet.m_eventDetails)))
        event = EventInfo(
            code=code,
            name=EVENT_CODES.get(code, f"Unknown event ({code})"),
            details=details,
        )

    return ParsedPacket(
        header=header,
        packet_id=header.packet_id,
        size=len(data),
        packet=packet,
        event=event,
    )


def parse_or_none(data: bytes) -> ParsedPacket | None:
    """Безопасный разбор: любая ошибка -> ``None`` (без исключений)."""
    try:
        return parse_packet(data)
    except Exception as exc:  # noqa: BLE001 — намеренный catch-all для UDP-мусора
        log.debug("dropped datagram (%d bytes): %s", len(data), exc)
        return None


def sector_ms(minutes_part: int, ms_part: int) -> int:
    """Собирает время сектора из двух полей LapData.

    Args:
        minutes_part: ``m_sectorNTimeMinutesPart`` (минуты).
        ms_part: ``m_sectorNTimeMSPart`` (миллисекунды).

    Returns:
        Время сектора в миллисекундах.
    """
    return int(minutes_part) * 60000 + int(ms_part)
