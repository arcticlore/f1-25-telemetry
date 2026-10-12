"""Тесты: ctypes-размеры всех 16 пакетов равны таблице из спецификации."""

from __future__ import annotations

import ctypes

import pytest

from f1telemetry import packets
from f1telemetry.constants import PACKET_SIZES, PacketId


@pytest.mark.parametrize(
    "packet_id,cls",
    sorted(packets.PACKET_STRUCTS.items()),
    ids=lambda v: PacketId(v).name if isinstance(v, int) else str(v),
)
def test_packet_sizes_match_spec(packet_id: int, cls: type) -> None:
    """``ctypes.sizeof`` каждого пакета совпадает с таблицей размеров."""
    expected = PACKET_SIZES[packet_id]
    assert ctypes.sizeof(cls) == expected, (
        f"{cls.__name__} (packet id {packet_id}): "
        f"sizeof={ctypes.sizeof(cls)}, expected={expected}"
    )


def test_all_sixteen_packets_covered() -> None:
    """Реестр содержит ровно 16 пакетов формата 2025."""
    assert len(packets.PACKET_STRUCTS) == 16
    assert set(packets.PACKET_STRUCTS) == set(int(p) for p in PacketId)


def test_header_size() -> None:
    """Заголовок — 29 байт."""
    assert ctypes.sizeof(packets.Header) == 29


@pytest.mark.parametrize(
    "cls,expected",
    [
        (packets.CarMotionData, 60),
        (packets.MarshalZone, 5),
        (packets.WeatherForecastSample, 8),
        (packets.LapData, 57),
        (packets.LiveryColour, 3),
        (packets.ParticipantData, 57),
        (packets.CarSetupData, 50),
        (packets.CarTelemetryData, 60),
        (packets.CarStatusData, 55),
        (packets.FinalClassificationData, 46),
        (packets.LobbyInfoData, 42),
        (packets.CarDamageData, 46),
        (packets.LapHistoryData, 14),
        (packets.TyreStintHistoryData, 3),
        (packets.TyreSetData, 10),
        (packets.TimeTrialDataSet, 24),
    ],
)
def test_substruct_sizes(cls: type, expected: int) -> None:
    """Размеры вложенных структур равны таблице 3.3 AGENTS.md."""
    assert ctypes.sizeof(cls) == expected


def test_car_damage_arithmetic() -> None:
    """Арифметика Car Damage: 29 + 22×46 = 1041 (не 1129).

    В AGENTS.md 3.2 заявлено 1129 = 29 + 22×50, но поле ``m_tyreBlisters[4]``
    добавлено к структуре F1 24 размером 42 байта, а не 46. Полный перечень
    полей из AGENTS.md 3.4 (и из PDF EA v3) даёт 46 байт на машину.
    Перекрёстная проверка тем же PDF: для 2026 Season Pack (24 машины) размер
    Car Damage = 1133 = 29 + 24×46.
    """
    per_car = ctypes.sizeof(packets.CarDamageData)
    total = ctypes.sizeof(packets.PacketCarDamage)
    assert per_car == 46
    assert 29 + 22 * per_car == total == 1041
    # из 1129 следует 50 байт на машину — на 4 байта больше, чем полей
    assert (1129 - 29) / 22 == 50
    assert 29 + 24 * per_car == 1133  # размер из PDF для 2026 Season Pack


def test_packed_little_endian() -> None:
    """Все структуры packed и little-endian."""
    for cls in packets.PACKET_STRUCTS.values():
        assert getattr(cls, "_pack_", None) == 1
        assert issubclass(cls, ctypes.LittleEndianStructure)
