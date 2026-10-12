"""Тесты разбора пакетов: ctypes -> bytes -> parse_packet -> сравнение полей."""

from __future__ import annotations

import ctypes
import struct

import pytest

from f1telemetry import packets as P
from f1telemetry.constants import EXPECTED_PACKET_FORMAT, PacketId
from f1telemetry.parser import (
    EventInfo,
    HeaderInfo,
    TooShortError,
    UnknownPacketError,
    UnsupportedFormatError,
    decode_event_details,
    parse_header,
    parse_or_none,
    parse_packet,
    sector_ms,
)


def _blob(obj: ctypes.Structure) -> bytes:
    """Сериализует ctypes-структуру в байты."""
    return bytes(memoryview(obj).cast("B"))


def _header(packet_id: int, uid: int = 0xDEADBEEF1234, t: float = 12.5) -> P.Header:
    h = P.Header()
    h.m_packetFormat = EXPECTED_PACKET_FORMAT
    h.m_gameYear = 25
    h.m_gameMajorVersion = 1
    h.m_gameMinorVersion = 2
    h.m_packetVersion = 1
    h.m_packetId = packet_id
    h.m_sessionUID = uid
    h.m_sessionTime = t
    h.m_frameIdentifier = 1000
    h.m_overallFrameIdentifier = 2000
    h.m_playerCarIndex = 3
    h.m_secondaryPlayerCarIndex = 255
    return h


def _check_header(h: HeaderInfo, packet_id: int) -> None:
    assert h.packet_format == EXPECTED_PACKET_FORMAT
    assert h.game_year == 25
    assert h.packet_id == packet_id
    assert h.session_uid == 0xDEADBEEF1234
    assert h.session_time == pytest.approx(12.5)
    assert h.frame_identifier == 1000
    assert h.overall_frame_identifier == 2000
    assert h.player_car_index == 3
    assert not h.has_secondary_player


# --- Заголовок ---------------------------------------------------------------

def test_parse_header() -> None:
    """Заголовок разбирается из сырых 29 байт."""
    data = _blob(_header(PacketId.SESSION))
    h = parse_header(data)
    _check_header(h, PacketId.SESSION)
    assert h.packet_type_name == "Session"


def test_header_too_short() -> None:
    """Короче 29 байт — TooShortError."""
    with pytest.raises(TooShortError):
        parse_header(b"\x00" * 10)


# --- Ошибки формата ----------------------------------------------------------

def test_unsupported_format() -> None:
    """m_packetFormat != 2025 -> UnsupportedFormatError."""
    pkt = P.PacketSession()
    pkt.m_header = _header(PacketId.SESSION)
    pkt.m_header.m_packetFormat = 2026
    with pytest.raises(UnsupportedFormatError):
        parse_packet(_blob(pkt))


def test_unknown_packet_id() -> None:
    """Неизвестный packet id -> UnknownPacketError."""
    with pytest.raises(UnknownPacketError):
        parse_packet(_blob(_header(99)))


def test_truncated_packet() -> None:
    """Обрезанный пакет -> TooShortError."""
    data = _blob(_header(PacketId.CAR_TELEMETRY))
    with pytest.raises(TooShortError):
        parse_packet(data)


def test_parse_or_none_never_raises() -> None:
    """parse_or_none не бросает исключений на мусоре."""
    assert parse_or_none(b"") is None
    assert parse_or_none(b"\xff" * 5) is None
    assert parse_or_none(b"x" * 30) is None
    assert parse_or_none(struct.pack("<HBBBBI", 2026, 25, 1, 0, 1, 1) + b"\x00" * 20) is None


# --- Motion ------------------------------------------------------------------

def test_motion_roundtrip() -> None:
    """Packet 0: известные значения переживают сериализацию и разбор."""
    pkt = P.PacketMotion()
    pkt.m_header = _header(PacketId.MOTION)
    for i in range(22):
        c = pkt.m_carMotionData[i]
        c.m_worldPositionX = 100.5 + i
        c.m_worldPositionY = -50.25
        c.m_worldPositionZ = 12.0
        c.m_worldVelocityX = 70.0
        c.m_gForceLateral = 1.75
        c.m_yaw = 0.5
    parsed = parse_packet(_blob(pkt))
    _check_header(parsed.header, PacketId.MOTION)
    car = parsed.packet.m_carMotionData[4]
    assert car.m_worldPositionX == pytest.approx(104.5)
    assert car.m_gForceLateral == pytest.approx(1.75)


# --- Session -----------------------------------------------------------------

def test_session_roundtrip() -> None:
    """Packet 1: поля сессии совпадают после разбора."""
    pkt = P.PacketSession()
    pkt.m_header = _header(PacketId.SESSION)
    pkt.m_weather = 2
    pkt.m_trackTemperature = 34
    pkt.m_airTemperature = 22
    pkt.m_totalLaps = 52
    pkt.m_trackLength = 5891
    pkt.m_sessionType = 15
    pkt.m_trackId = 7
    pkt.m_sessionTimeLeft = 1800
    pkt.m_numMarshalZones = 3
    pkt.m_marshalZones[0].m_zoneStart = 0.25
    pkt.m_marshalZones[0].m_zoneFlag = 3
    pkt.m_safetyCarStatus = 2
    pkt.m_sector2LapDistanceStart = 1900.0
    pkt.m_sector3LapDistanceStart = 3900.0
    parsed = parse_packet(_blob(pkt))
    s = parsed.packet
    assert s.m_weather == 2
    assert s.m_trackTemperature == 34
    assert s.m_totalLaps == 52
    assert s.m_trackLength == 5891
    assert s.m_sessionType == 15
    assert s.m_trackId == 7
    assert s.m_marshalZones[0].m_zoneStart == pytest.approx(0.25)
    assert s.m_marshalZones[0].m_zoneFlag == 3
    assert s.m_safetyCarStatus == 2


def test_session_marshal_zone_flag_signed() -> None:
    """m_zoneFlag — знаковый i8: 255 из байтов читается как -1."""
    pkt = P.PacketSession()
    pkt.m_header = _header(PacketId.SESSION)
    pkt.m_numMarshalZones = 1
    data = bytearray(_blob(pkt))
    # смещение: 29 (header) + 19 (scalar-поля до массива) = 48; zoneStart f32, затем flag
    flag_offset = 48 + 4
    data[flag_offset] = 0xFF
    parsed = parse_packet(bytes(data))
    assert parsed.packet.m_marshalZones[0].m_zoneFlag == -1


# --- Lap Data ----------------------------------------------------------------

def test_lap_data_roundtrip() -> None:
    """Packet 2: времена, дельты, позиция и статусы."""
    pkt = P.PacketLapData()
    pkt.m_header = _header(PacketId.LAP_DATA)
    lap = pkt.m_lapData[3]
    lap.m_lastLapTimeInMS = 91234
    lap.m_currentLapTimeInMS = 12345
    lap.m_sector1TimeMSPart = 28450
    lap.m_sector1TimeMinutesPart = 0
    lap.m_sector2TimeMSPart = 9999
    lap.m_sector2TimeMinutesPart = 1  # 60000 + 9999 = 69999
    lap.m_deltaToCarInFrontMSPart = 250
    lap.m_lapDistance = 1500.5
    lap.m_totalDistance = 12345.5
    lap.m_carPosition = 2
    lap.m_currentLapNum = 7
    lap.m_pitStatus = 0
    lap.m_sector = 2
    lap.m_speedTrapFastestSpeed = 331.2
    pkt.m_timeTrialPBCarIdx = 255
    pkt.m_timeTrialRivalCarIdx = 255
    parsed = parse_packet(_blob(pkt))
    lap2 = parsed.packet.m_lapData[3]
    assert lap2.m_lastLapTimeInMS == 91234
    assert lap2.m_sector2TimeMinutesPart == 1
    assert sector_ms(lap2.m_sector2TimeMinutesPart, lap2.m_sector2TimeMSPart) == 69999
    assert lap2.m_lapDistance == pytest.approx(1500.5)
    assert lap2.m_currentLapNum == 7
    assert parsed.packet.m_timeTrialPBCarIdx == 255


# --- Event -------------------------------------------------------------------

def test_event_fastest_lap() -> None:
    """Packet 3 / FTLP: vehicleIdx + lapTime."""
    pkt = P.EventPacket()
    pkt.m_header = _header(PacketId.EVENT)
    pkt.m_eventStringCode = b"FTLP"
    details = struct.pack("<Bf", 5, 91.234)
    pkt.m_eventDetails = (P.u8 * 12).from_buffer_copy(details + b"\x00" * 7)
    parsed = parse_packet(_blob(pkt))
    assert parsed.event is not None
    assert parsed.event.code == "FTLP"
    assert parsed.event.name == "Fastest Lap"
    assert parsed.event.details["vehicle_idx"] == 5
    assert parsed.event.details["lap_time_s"] == pytest.approx(91.234)
    assert "91.234" in parsed.event.describe(car_name="VER")


def test_event_flashback_and_sc_and_collision() -> None:
    """Packet 3: FLBK / SCAR / COLL расшифровываются корректно."""
    fb = parse_packet(
        _event_bytes("FLBK", struct.pack("<If", 123456, 300.5))
    )
    assert fb.event.details["flashback_frame"] == 123456
    assert fb.event.details["session_time_s"] == pytest.approx(300.5)

    sc = parse_packet(_event_bytes("SCAR", struct.pack("<BB", 2, 3)))
    assert sc.event.details == {"safety_car_type": 2, "event_type": 3}

    col = parse_packet(_event_bytes("COLL", struct.pack("<BB", 1, 9)))
    assert col.event.details["vehicle1_idx"] == 1
    assert col.event.details["vehicle2_idx"] == 9


def test_event_penalty_and_speed_trap() -> None:
    """Packet 3: PENA (7 байт) и SPTP (12 байт)."""
    pen = parse_packet(
        _event_bytes("PENA", struct.pack("<BBBBBBB", 4, 0, 3, 4, 5, 12, 1))
    )
    assert pen.event.details["penalty_type"] == 4
    assert pen.event.details["lap_num"] == 12
    assert pen.event.details["places_gained"] == 1

    sptp = parse_packet(
        _event_bytes("SPTP", struct.pack("<BfBBBf", 2, 331.9, 1, 1, 2, 332.5))
    )
    assert sptp.event.details["speed_kmh"] == pytest.approx(331.9)
    assert sptp.event.details["fastest_speed_kmh"] == pytest.approx(332.5)


def test_event_no_details() -> None:
    """SSTA/SEND/CHQF/LGOT не имеют деталей."""
    for code in ("SSTA", "SEND", "CHQF", "LGOT", "DRSE", "RDFL"):
        parsed = parse_packet(_event_bytes(code, b""))
        assert parsed.event.details == {}
        assert parsed.event.code == code


def test_decode_event_details_short_blob() -> None:
    """Короткий blob деталей не роняет разбор."""
    assert decode_event_details("FLBK", b"\x01\x02") == {
        "flashback_frame": struct.unpack("<I", b"\x01\x02\x00\x00")[0],
        "session_time_s": 0.0,
    }
    assert decode_event_details("XXXX", b"") == {}


def _event_bytes(code: str, details: bytes) -> bytes:
    pkt = P.EventPacket()
    pkt.m_header = _header(PacketId.EVENT)
    pkt.m_eventStringCode = code.encode("ascii")
    blob = details + b"\x00" * (12 - len(details))
    pkt.m_eventDetails = (P.u8 * 12).from_buffer_copy(blob[:12])
    return _blob(pkt)


# --- Participants ------------------------------------------------------------

def test_participants_roundtrip() -> None:
    """Packet 4: имя (UTF-8, null-terminated) и реквизиты."""
    pkt = P.PacketParticipants()
    pkt.m_header = _header(PacketId.PARTICIPANTS)
    pkt.m_numActiveCars = 2
    p = pkt.m_participants[1]
    p.m_aiControlled = 0
    p.m_driverId = 7
    p.m_teamId = 8
    p.m_raceNumber = 4
    p.m_nationality = 3
    p.m_name = "M. Verstappen".encode("utf-8")
    p.m_yourTelemetry = 1
    p.m_techLevel = 2
    p.m_platform = 1
    p.m_numColours = 2
    p.m_liveryColour[0].red = 255
    parsed = parse_packet(_blob(pkt))
    q = parsed.packet.m_participants[1]
    assert bytes(q.m_name).split(b"\x00")[0].decode() == "M. Verstappen"
    assert q.m_teamId == 8
    assert q.m_raceNumber == 4


# --- Car Telemetry -----------------------------------------------------------

def test_car_telemetry_roundtrip() -> None:
    """Packet 6: скорость, педали, передача, RPM, шины."""
    pkt = P.PacketCarTelemetry()
    pkt.m_header = _header(PacketId.CAR_TELEMETRY)
    t = pkt.m_carTelemetryData[3]
    t.m_speed = 287
    t.m_throttle = 0.75
    t.m_steer = -0.25
    t.m_brake = 0.0
    t.m_gear = 6
    t.m_engineRPM = 11500
    t.m_drs = 1
    t.m_revLightsBitValue = 0b101010101010101
    t.m_brakesTemperature[0] = 480
    t.m_tyresSurfaceTemperature[0] = 92
    t.m_tyresInnerTemperature[0] = 88
    t.m_engineTemperature = 108
    t.m_tyresPressure[0] = 21.5
    pkt.m_suggestedGear = 7
    parsed = parse_packet(_blob(pkt))
    t2 = parsed.packet.m_carTelemetryData[3]
    assert t2.m_speed == 287
    assert t2.m_throttle == pytest.approx(0.75)
    assert t2.m_gear == 6
    assert t2.m_revLightsBitValue == 0b101010101010101
    assert t2.m_brakesTemperature[0] == 480
    assert t2.m_tyresPressure[0] == pytest.approx(21.5)
    assert parsed.packet.m_suggestedGear == 7


# --- Car Status --------------------------------------------------------------

def test_car_status_roundtrip() -> None:
    """Packet 7: топливо, шины, ERS."""
    pkt = P.PacketCarStatus()
    pkt.m_header = _header(PacketId.CAR_STATUS)
    s = pkt.m_carStatusData[3]
    s.m_fuelInTank = 100.0
    s.m_fuelCapacity = 110.0
    s.m_fuelRemainingLaps = 24.5
    s.m_maxRPM = 15000
    s.m_actualTyreCompound = 16
    s.m_visualTyreCompound = 16
    s.m_tyresAgeLaps = 8
    s.m_vehicleFiaFlags = 3
    s.m_ersStoreEnergy = 3_500_000.0
    s.m_ersDeployMode = 2
    s.m_ersDeployedThisLap = 12_000.0
    parsed = parse_packet(_blob(pkt))
    s2 = parsed.packet.m_carStatusData[3]
    assert s2.m_fuelInTank == pytest.approx(100.0)
    assert s2.m_actualTyreCompound == 16
    assert s2.m_vehicleFiaFlags == 3
    assert s2.m_ersStoreEnergy == pytest.approx(3.5e6)


# --- Car Damage --------------------------------------------------------------

def test_car_damage_roundtrip() -> None:
    """Packet 10: износ, повреждения, износ двигателя (46 байт/машину)."""
    pkt = P.PacketCarDamage()
    pkt.m_header = _header(PacketId.CAR_DAMAGE)
    d = pkt.m_carDamageData[3]
    d.m_tyresWear[0] = 22.5
    d.m_tyresDamage[1] = 10
    d.m_brakesDamage[0] = 5
    d.m_tyreBlisters[2] = 3
    d.m_engineICEWear = 17
    d.m_engineBlown = 0
    parsed = parse_packet(_blob(pkt))
    d2 = parsed.packet.m_carDamageData[3]
    assert d2.m_tyresWear[0] == pytest.approx(22.5)
    assert d2.m_tyreBlisters[2] == 3
    assert d2.m_engineICEWear == 17


# --- Session History ---------------------------------------------------------

def test_session_history_roundtrip() -> None:
    """Packet 11: история кругов и стинтов шин."""
    pkt = P.PacketSessionHistory()
    pkt.m_header = _header(PacketId.SESSION_HISTORY)
    pkt.m_carIdx = 3
    pkt.m_numLaps = 2
    pkt.m_numTyreStints = 1
    lap = pkt.m_lapHistoryData[0]
    lap.m_lapTimeInMS = 91234
    lap.m_sector1TimeMSPart = 28000
    lap.m_sector2TimeMSPart = 31000
    lap.m_sector3TimeMSPart = 32234
    lap.m_lapValidBitFlags = 0x0F
    st = pkt.m_tyreStintsHistoryData[0]
    st.m_endLap = 20
    st.m_tyreActualCompound = 16
    st.m_tyreVisualCompound = 16
    parsed = parse_packet(_blob(pkt))
    assert parsed.packet.m_lapHistoryData[0].m_lapTimeInMS == 91234
    assert parsed.packet.m_lapHistoryData[0].m_lapValidBitFlags == 0x0F
    assert parsed.packet.m_tyreStintsHistoryData[0].m_endLap == 20


# --- Tyre Sets / Setups / Final / Lobby / MotionEx / TimeTrial / Positions ---

def test_tyre_sets_roundtrip() -> None:
    """Packet 12: комплекты шин."""
    pkt = P.PacketTyreSets()
    pkt.m_header = _header(PacketId.TYRE_SETS)
    pkt.m_carIdx = 3
    pkt.m_tyreSetData[0].m_actualTyreCompound = 17
    pkt.m_tyreSetData[0].m_wear = 5
    pkt.m_tyreSetData[0].m_lifeSpan = 25
    pkt.m_tyreSetData[0].m_lapDeltaTime = -120
    pkt.m_tyreSetData[0].m_fitted = 1
    pkt.m_fittedIdx = 0
    parsed = parse_packet(_blob(pkt))
    assert parsed.packet.m_tyreSetData[0].m_actualTyreCompound == 17
    assert parsed.packet.m_tyreSetData[0].m_lapDeltaTime == -120
    assert parsed.packet.m_fittedIdx == 0


def test_setup_roundtrip() -> None:
    """Packet 5: сетап."""
    pkt = P.PacketCarSetup()
    pkt.m_header = _header(PacketId.CAR_SETUPS)
    s = pkt.m_carSetups[3]
    s.m_frontWing = 8
    s.m_rearWing = 12
    s.m_frontLeftTyrePressure = 22.1
    s.m_fuelLoad = 100.0
    pkt.m_nextFrontWingValue = 8.5
    parsed = parse_packet(_blob(pkt))
    assert parsed.packet.m_carSetups[3].m_frontWing == 8
    assert parsed.packet.m_carSetups[3].m_frontLeftTyrePressure == pytest.approx(22.1)
    assert parsed.packet.m_nextFrontWingValue == pytest.approx(8.5)


def test_final_classification_roundtrip() -> None:
    """Packet 8: итоговая классификация."""
    pkt = P.PacketFinalClassification()
    pkt.m_header = _header(PacketId.FINAL_CLASSIFICATION)
    pkt.m_numCars = 2
    f = pkt.m_classification[0]
    f.m_position = 1
    f.m_numLaps = 52
    f.m_bestLapTimeInMS = 90123
    f.m_totalRaceTime = 5400.125
    f.m_tyreStintsEndLaps[0] = 26
    parsed = parse_packet(_blob(pkt))
    assert parsed.packet.m_classification[0].m_totalRaceTime == pytest.approx(5400.125)
    assert parsed.packet.m_numCars == 2


def test_lobby_info_roundtrip() -> None:
    """Packet 9: лобби."""
    pkt = P.PacketLobbyInfo()
    pkt.m_header = _header(PacketId.LOBBY_INFO)
    pkt.m_numLobbyPlayers = 1
    pkt.m_lobbyPlayers[0].m_name = "Player One".encode()
    pkt.m_lobbyPlayers[0].m_carNumber = 16
    parsed = parse_packet(_blob(pkt))
    assert bytes(parsed.packet.m_lobbyPlayers[0].m_name).split(b"\x00")[0].decode() == "Player One"


def test_motion_ex_roundtrip() -> None:
    """Packet 13: расширенная физика."""
    pkt = P.PacketMotionEx()
    pkt.m_header = _header(PacketId.MOTION_EX)
    pkt.m_wheelSpeed[0] = 80.5
    pkt.m_wheelCamberGain[3] = -0.02
    pkt.m_frontAeroHeight = 0.045
    parsed = parse_packet(_blob(pkt))
    assert parsed.packet.m_wheelSpeed[0] == pytest.approx(80.5)
    assert parsed.packet.m_frontAeroHeight == pytest.approx(0.045)


def test_time_trial_roundtrip() -> None:
    """Packet 14: time trial."""
    pkt = P.PacketTimeTrial()
    pkt.m_header = _header(PacketId.TIME_TRIAL)
    pkt.m_personalBestDataSet.m_carIdx = 3
    pkt.m_personalBestDataSet.m_lapTimeInMS = 88000
    pkt.m_personalBestDataSet.m_sector1TimeInMS = 27000
    parsed = parse_packet(_blob(pkt))
    assert parsed.packet.m_personalBestDataSet.m_lapTimeInMS == 88000


def test_lap_positions_roundtrip() -> None:
    """Packet 15: позиции по кругам."""
    pkt = P.PacketLapPositions()
    pkt.m_header = _header(PacketId.LAP_POSITIONS)
    pkt.m_numLaps = 3
    pkt.m_lapStart = 0
    # m_positionForVehicleIdx[lap][car]
    pkt.m_positionForVehicleIdx[0][3] = 1
    pkt.m_positionForVehicleIdx[2][3] = 5
    parsed = parse_packet(_blob(pkt))
    assert parsed.packet.m_positionForVehicleIdx[0][3] == 1
    assert parsed.packet.m_positionForVehicleIdx[2][3] == 5


def test_event_packet_type_annotation() -> None:
    """Только у Event-пакета заполнено поле ``event``."""
    plain = parse_packet(_blob(_header(PacketId.SESSION, t=0.0)) + b"\x00" * 724)
    assert plain.event is None
    ev = parse_packet(_event_bytes("CHQF", b""))
    assert isinstance(ev.event, EventInfo)
