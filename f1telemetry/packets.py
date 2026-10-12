"""ctypes-структуры UDP-пакетов F1 25 (формат 2025, 22 машины).

Все структуры — ``ctypes.LittleEndianStructure`` с ``_pack_ = 1``:
little-endian, без внутреннего паддинга. Размер каждой структуры проверяется
тестом ``tests/test_packets.py`` против таблицы 3.2 AGENTS.md.

Порядок колёс во всех массивах: 0 = RL, 1 = RR, 2 = FL, 3 = FR.
"""

from __future__ import annotations

import ctypes as C

from .constants import HEADER_SIZE, MAX_CARS

__all__ = [
    "Header",
    "CarMotionData",
    "MarshalZone",
    "WeatherForecastSample",
    "PacketSession",
    "LapData",
    "PacketLapData",
    "LiveryColour",
    "ParticipantData",
    "PacketParticipants",
    "CarSetupData",
    "PacketCarSetup",
    "CarTelemetryData",
    "PacketCarTelemetry",
    "CarStatusData",
    "PacketCarStatus",
    "FinalClassificationData",
    "PacketFinalClassification",
    "LobbyInfoData",
    "PacketLobbyInfo",
    "CarDamageData",
    "PacketCarDamage",
    "LapHistoryData",
    "TyreStintHistoryData",
    "PacketSessionHistory",
    "TyreSetData",
    "PacketTyreSets",
    "PacketMotionEx",
    "TimeTrialDataSet",
    "PacketTimeTrial",
    "PacketLapPositions",
    "EventPacket",
    "PACKET_STRUCTS",
    "MAX_CARS",
    "HEADER_SIZE",
]

u8 = C.c_uint8
i8 = C.c_int8
u16 = C.c_uint16
i16 = C.c_int16
u32 = C.c_uint32
i32 = C.c_int32
f32 = C.c_float
d64 = C.c_double
u64 = C.c_uint64


class _Base(C.LittleEndianStructure):
    _pack_ = 1
    _fields_: list = []


# --- Заголовок (29 байт) ---------------------------------------------------

class Header(_Base):
    """Заголовок любого пакета, 29 байт."""

    _fields_ = [
        ("m_packetFormat", u16),
        ("m_gameYear", u8),
        ("m_gameMajorVersion", u8),
        ("m_gameMinorVersion", u8),
        ("m_packetVersion", u8),
        ("m_packetId", u8),
        ("m_sessionUID", u64),
        ("m_sessionTime", f32),
        ("m_frameIdentifier", u32),
        ("m_overallFrameIdentifier", u32),
        ("m_playerCarIndex", u8),
        ("m_secondaryPlayerCarIndex", u8),
    ]


# --- 0: Motion (1349) ------------------------------------------------------

class CarMotionData(_Base):
    """Движение одной машины, 60 байт."""

    _fields_ = [
        ("m_worldPositionX", f32),  # м
        ("m_worldPositionY", f32),  # м
        ("m_worldPositionZ", f32),  # м
        ("m_worldVelocityX", f32),  # м/с
        ("m_worldVelocityY", f32),  # м/с
        ("m_worldVelocityZ", f32),  # м/с
        ("m_worldForwardDirX", i16),
        ("m_worldForwardDirY", i16),
        ("m_worldForwardDirZ", i16),
        ("m_worldRightDirX", i16),
        ("m_worldRightDirY", i16),
        ("m_worldRightDirZ", i16),
        ("m_gForceLateral", f32),  # g
        ("m_gForceLongitudinal", f32),  # g
        ("m_gForceVertical", f32),  # g
        ("m_yaw", f32),  # радианы
        ("m_pitch", f32),
        ("m_roll", f32),
    ]


class PacketMotion(_Base):
    """Packet 0 — Motion, 1349 байт."""

    _fields_ = [
        ("m_header", Header),
        ("m_carMotionData", CarMotionData * MAX_CARS),
    ]


# --- 1: Session (753) ------------------------------------------------------

class MarshalZone(_Base):
    """Зона маршалов, 5 байт."""

    _fields_ = [("m_zoneStart", f32), ("m_zoneFlag", i8)]


class WeatherForecastSample(_Base):
    """Прогноз погоды, 8 байт."""

    _fields_ = [
        ("m_sessionType", u8),
        ("m_timeOffset", u8),  # секунды от старта сессии
        ("m_weather", u8),
        ("m_trackTemperature", i8),  # °C
        ("m_trackTemperatureChange", i8),
        ("m_airTemperature", i8),  # °C
        ("m_airTemperatureChange", i8),
        ("m_rainPercentage", u8),  # %
    ]


class PacketSession(_Base):
    """Packet 1 — Session, 753 байта."""

    _fields_ = [
        ("m_header", Header),
        ("m_weather", u8),
        ("m_trackTemperature", i8),  # °C
        ("m_airTemperature", i8),  # °C
        ("m_totalLaps", u8),
        ("m_trackLength", u16),  # м
        ("m_sessionType", u8),
        ("m_trackId", i8),
        ("m_formula", u8),
        ("m_sessionTimeLeft", u16),  # сек
        ("m_sessionDuration", u16),  # сек
        ("m_pitSpeedLimit", u8),  # км/ч
        ("m_gamePaused", u8),
        ("m_isSpectating", u8),
        ("m_spectatorCarIndex", u8),
        ("m_sliProNativeSupport", u8),
        ("m_numMarshalZones", u8),
        ("m_marshalZones", MarshalZone * 21),
        ("m_safetyCarStatus", u8),
        ("m_networkGame", u8),
        ("m_numWeatherForecastSamples", u8),
        ("m_weatherForecastSamples", WeatherForecastSample * 64),
        ("m_forecastAccuracy", u8),
        ("m_aiDifficulty", u8),
        ("m_seasonLinkIdentifier", u32),
        ("m_weekendLinkIdentifier", u32),
        ("m_sessionLinkIdentifier", u32),
        ("m_pitStopWindowIdealLap", u8),
        ("m_pitStopWindowLatestLap", u8),
        ("m_pitStopRejoinPosition", u8),
        ("m_steeringAssist", u8),
        ("m_brakingAssist", u8),
        ("m_gearboxAssist", u8),
        ("m_pitAssist", u8),
        ("m_pitReleaseAssist", u8),
        ("m_ERSAssist", u8),
        ("m_DRSAssist", u8),
        ("m_dynamicRacingLine", u8),
        ("m_dynamicRacingLineType", u8),
        ("m_gameMode", u8),
        ("m_ruleSet", u8),
        ("m_timeOfDay", u32),
        ("m_sessionLength", u8),
        ("m_speedUnitsLeadPlayer", u8),
        ("m_temperatureUnitsLeadPlayer", u8),
        ("m_speedUnitsSecondaryPlayer", u8),
        ("m_temperatureUnitsSecondaryPlayer", u8),
        ("m_numSafetyCarPeriods", u8),
        ("m_numVirtualSafetyCarPeriods", u8),
        ("m_numRedFlagPeriods", u8),
        ("m_equalCarPerformance", u8),
        ("m_recoveryMode", u8),
        ("m_flashbackLimit", u8),
        ("m_surfaceType", u8),
        ("m_lowFuelMode", u8),
        ("m_raceStarts", u8),
        ("m_tyreTemperature", u8),
        ("m_pitLaneTyreSim", u8),
        ("m_carDamage", u8),
        ("m_carDamageRate", u8),
        ("m_collisions", u8),
        ("m_collisionsOffForFirstLapOnly", u8),
        ("m_mpUnsafePitRelease", u8),
        ("m_mpOffForGriefing", u8),
        ("m_cornerCuttingStringency", u8),
        ("m_parcFermeRules", u8),
        ("m_pitStopExperience", u8),
        ("m_safetyCar", u8),
        ("m_safetyCarExperience", u8),
        ("m_formationLap", u8),
        ("m_formationLapExperience", u8),
        ("m_redFlags", u8),
        ("m_affectsLicenceLevelSolo", u8),
        ("m_affectsLicenceLevelMP", u8),
        ("m_numSessionsInWeekend", u8),
        ("m_weekendStructure", u8 * 12),
        ("m_sector2LapDistanceStart", f32),  # м
        ("m_sector3LapDistanceStart", f32),  # м
    ]


# --- 2: Lap Data (1285) ----------------------------------------------------

class LapData(_Base):
    """Данные круга одной машины, 57 байт."""

    _fields_ = [
        ("m_lastLapTimeInMS", u32),  # мс
        ("m_currentLapTimeInMS", u32),  # мс
        ("m_sector1TimeMSPart", u16),  # мс
        ("m_sector1TimeMinutesPart", u8),  # минуты
        ("m_sector2TimeMSPart", u16),  # мс
        ("m_sector2TimeMinutesPart", u8),  # минуты
        ("m_deltaToCarInFrontMSPart", u16),  # мс
        ("m_deltaToCarInFrontMinutesPart", u8),
        ("m_deltaToRaceLeaderMSPart", u16),  # мс
        ("m_deltaToRaceLeaderMinutesPart", u8),
        ("m_lapDistance", f32),  # м
        ("m_totalDistance", f32),  # м
        ("m_safetyCarDelta", f32),  # сек
        ("m_carPosition", u8),
        ("m_currentLapNum", u8),
        ("m_pitStatus", u8),
        ("m_numPitStops", u8),
        ("m_sector", u8),
        ("m_currentLapInvalid", u8),
        ("m_penalties", u8),
        ("m_totalWarnings", u8),
        ("m_cornerCuttingWarnings", u8),
        ("m_numUnservedDriveThroughPens", u8),
        ("m_numUnservedStopGoPens", u8),
        ("m_gridPosition", u8),
        ("m_driverStatus", u8),
        ("m_resultStatus", u8),
        ("m_pitLaneTimerActive", u8),
        ("m_pitLaneTimeInLaneInMS", u16),  # мс
        ("m_pitStopTimerInMS", u16),  # мс
        ("m_pitStopShouldServePen", u8),
        ("m_speedTrapFastestSpeed", f32),  # км/ч
        ("m_speedTrapFastestLap", u8),
    ]


class PacketLapData(_Base):
    """Packet 2 — Lap Data, 1285 байт."""

    _fields_ = [
        ("m_header", Header),
        ("m_lapData", LapData * MAX_CARS),
        ("m_timeTrialPBCarIdx", u8),
        ("m_timeTrialRivalCarIdx", u8),
    ]


# --- 3: Event (45) ---------------------------------------------------------

class EventPacket(_Base):
    """Packet 3 — Event, 45 байт.

    Детали события — union размером 12 байт; разбор по ``eventStringCode``
    выполняется в :mod:`f1telemetry.parser`.
    """

    _fields_ = [
        ("m_header", Header),
        ("m_eventStringCode", C.c_char * 4),
        ("m_eventDetails", u8 * 12),
    ]


EVENT_DETAILS_SIZE = 12
"""Размер union ``EventDataDetails``, байт."""


# --- 4: Participants (1284) ------------------------------------------------

class LiveryColour(_Base):
    """Цвет ливреи, 3 байта."""

    _fields_ = [("red", u8), ("green", u8), ("blue", u8)]


class ParticipantData(_Base):
    """Участник сессии, 57 байт."""

    _fields_ = [
        ("m_aiControlled", u8),
        ("m_driverId", u8),
        ("m_networkId", u8),
        ("m_teamId", u8),
        ("m_myTeam", u8),
        ("m_raceNumber", u8),
        ("m_nationality", u8),
        ("m_name", C.c_char * 32),  # UTF-8, null-terminated
        ("m_yourTelemetry", u8),
        ("m_showOnlineNames", u8),
        ("m_techLevel", u16),
        ("m_platform", u8),
        ("m_numColours", u8),
        ("m_liveryColour", LiveryColour * 4),
    ]


class PacketParticipants(_Base):
    """Packet 4 — Participants, 1284 байта."""

    _fields_ = [
        ("m_header", Header),
        ("m_numActiveCars", u8),
        ("m_participants", ParticipantData * MAX_CARS),
    ]


# --- 5: Car Setups (1133) --------------------------------------------------

class CarSetupData(_Base):
    """Сетап одной машины, 50 байт."""

    _fields_ = [
        ("m_frontWing", u8),
        ("m_rearWing", u8),
        ("m_onThrottle", u8),  # %
        ("m_offThrottle", u8),  # %
        ("m_frontCamber", f32),  # градусы
        ("m_rearCamber", f32),
        ("m_frontToe", f32),
        ("m_rearToe", f32),
        ("m_frontSuspension", u8),
        ("m_rearSuspension", u8),
        ("m_frontAntiRollBar", u8),
        ("m_rearAntiRollBar", u8),
        ("m_frontSuspensionHeight", u8),
        ("m_rearSuspensionHeight", u8),
        ("m_brakePressure", u8),  # %
        ("m_brakeBias", u8),  # %
        ("m_engineBraking", u8),
        ("m_rearLeftTyrePressure", f32),  # PSI
        ("m_rearRightTyrePressure", f32),
        ("m_frontLeftTyrePressure", f32),
        ("m_frontRightTyrePressure", f32),
        ("m_ballast", u8),  # кг
        ("m_fuelLoad", f32),  # кг
    ]


class PacketCarSetup(_Base):
    """Packet 5 — Car Setups, 1133 байта."""

    _fields_ = [
        ("m_header", Header),
        ("m_carSetups", CarSetupData * MAX_CARS),
        ("m_nextFrontWingValue", f32),
    ]


# --- 6: Car Telemetry (1352) -----------------------------------------------

class CarTelemetryData(_Base):
    """Телеметрия одной машины, 60 байт."""

    _fields_ = [
        ("m_speed", u16),  # км/ч
        ("m_throttle", f32),  # 0..1
        ("m_steer", f32),  # -1..1
        ("m_brake", f32),  # 0..1
        ("m_clutch", u8),  # 0..100
        ("m_gear", i8),  # 1..8, 0 = N, -1 = R
        ("m_engineRPM", u16),
        ("m_drs", u8),
        ("m_revLightsPercent", u8),  # %
        ("m_revLightsBitValue", u16),  # битовая маска 15 сегментов
        ("m_brakesTemperature", u16 * 4),  # °C
        ("m_tyresSurfaceTemperature", u8 * 4),  # °C
        ("m_tyresInnerTemperature", u8 * 4),  # °C
        ("m_engineTemperature", u16),  # °C
        ("m_tyresPressure", f32 * 4),  # PSI
        ("m_surfaceType", u8 * 4),
    ]


class PacketCarTelemetry(_Base):
    """Packet 6 — Car Telemetry, 1352 байта."""

    _fields_ = [
        ("m_header", Header),
        ("m_carTelemetryData", CarTelemetryData * MAX_CARS),
        ("m_mfdPanelIndex", u8),
        ("m_mfdPanelIndexSecondaryPlayer", u8),
        ("m_suggestedGear", i8),
    ]


# --- 7: Car Status (1239) --------------------------------------------------

class CarStatusData(_Base):
    """Статус одной машины, 55 байт."""

    _fields_ = [
        ("m_tractionControl", u8),
        ("m_antiLockBrakes", u8),
        ("m_fuelMix", u8),
        ("m_frontBrakeBias", u8),  # %
        ("m_pitLimiterStatus", u8),
        ("m_fuelInTank", f32),  # кг
        ("m_fuelCapacity", f32),  # кг
        ("m_fuelRemainingLaps", f32),  # кругов
        ("m_maxRPM", u16),
        ("m_idleRPM", u16),
        ("m_maxGears", u8),
        ("m_drsAllowed", u8),
        ("m_drsActivationDistance", u16),  # м
        ("m_actualTyreCompound", u8),
        ("m_visualTyreCompound", u8),
        ("m_tyresAgeLaps", u8),
        ("m_vehicleFiaFlags", i8),
        ("m_enginePowerICE", f32),  # Вт
        ("m_enginePowerMGUK", f32),  # Вт
        ("m_ersStoreEnergy", f32),  # Дж
        ("m_ersDeployMode", u8),
        ("m_ersHarvestedThisLapMGUK", f32),  # Дж
        ("m_ersHarvestedThisLapMGUH", f32),  # Дж
        ("m_ersDeployedThisLap", f32),  # Дж
        ("m_networkPaused", u8),
    ]


class PacketCarStatus(_Base):
    """Packet 7 — Car Status, 1239 байт."""

    _fields_ = [
        ("m_header", Header),
        ("m_carStatusData", CarStatusData * MAX_CARS),
    ]


# --- 8: Final Classification (1042) ----------------------------------------

class FinalClassificationData(_Base):
    """Финальная классификация одной машины, 46 байт."""

    _fields_ = [
        ("m_position", u8),
        ("m_numLaps", u8),
        ("m_gridPosition", u8),
        ("m_points", u8),
        ("m_numPitStops", u8),
        ("m_resultStatus", u8),
        ("m_resultReason", u8),
        ("m_bestLapTimeInMS", u32),  # мс
        ("m_totalRaceTime", d64),  # сек
        ("m_penaltiesTime", u8),  # сек
        ("m_numPenalties", u8),
        ("m_numTyreStints", u8),
        ("m_tyreStintsActual", u8 * 8),
        ("m_tyreStintsVisual", u8 * 8),
        ("m_tyreStintsEndLaps", u8 * 8),
    ]


class PacketFinalClassification(_Base):
    """Packet 8 — Final Classification, 1042 байта."""

    _fields_ = [
        ("m_header", Header),
        ("m_numCars", u8),
        ("m_classification", FinalClassificationData * MAX_CARS),
    ]


# --- 9: Lobby Info (954) ---------------------------------------------------

class LobbyInfoData(_Base):
    """Информация о машине в лобби, 42 байта."""

    _fields_ = [
        ("m_aiControlled", u8),
        ("m_teamId", u8),
        ("m_nationality", u8),
        ("m_platform", u8),
        ("m_name", C.c_char * 32),
        ("m_carNumber", u8),
        ("m_yourTelemetry", u8),
        ("m_showOnlineNames", u8),
        ("m_techLevel", u16),
        ("m_readyStatus", u8),
    ]


class PacketLobbyInfo(_Base):
    """Packet 9 — Lobby Info, 954 байта."""

    _fields_ = [
        ("m_header", Header),
        ("m_numLobbyPlayers", u8),
        ("m_lobbyPlayers", LobbyInfoData * MAX_CARS),
    ]


# --- 10: Car Damage (1041) -------------------------------------------------
#
# Поля — ровно те, что перечислены в AGENTS.md (раздел 3.4) и в официальном
# PDF EA «Data Output from F1 25» v3:
#   float m_tyresWear[4]                     = 16
#   uint8 m_tyresDamage[4]                   =  4
#   uint8 m_brakesDamage[4]                  =  4
#   uint8 m_tyreBlisters[4]                  =  4
#   18 × uint8 (кроме перечисленных выше)    = 18
#                                              ---
#   CarDamageData                            = 46
#   Packet = 29 + 22 × 46                    = 1041
#
# Перекрёстная проверка арифметикой: в PDF v3 для 2026 Season Pack (24 машины)
# тот же пакет имеет размер 1133 = 29 + 24 × 46, что подтверждает 46 байт на
# машину. Значение 1129 (29 + 22 × 50) потребовало бы 4 несуществующих байта
# на машину, а 1041 в PDF — не опечатка. См. «Заметки по реализации» в
# AGENTS.md.

class CarDamageData(_Base):
    """Повреждения одной машины, 46 байт."""

    _fields_ = [
        ("m_tyresWear", f32 * 4),  # %
        ("m_tyresDamage", u8 * 4),  # %
        ("m_brakesDamage", u8 * 4),  # %
        ("m_tyreBlisters", u8 * 4),  # %
        ("m_frontLeftWingDamage", u8),
        ("m_frontRightWingDamage", u8),
        ("m_rearWingDamage", u8),
        ("m_floorDamage", u8),
        ("m_diffuserDamage", u8),
        ("m_sidepodDamage", u8),
        ("m_drsFault", u8),
        ("m_ersFault", u8),
        ("m_gearBoxDamage", u8),
        ("m_engineDamage", u8),
        ("m_engineMGUHWear", u8),
        ("m_engineESWear", u8),
        ("m_engineCEWear", u8),
        ("m_engineICEWear", u8),
        ("m_engineMGUKWear", u8),
        ("m_engineTCWear", u8),
        ("m_engineBlown", u8),
        ("m_engineSeized", u8),
    ]


class PacketCarDamage(_Base):
    """Packet 10 — Car Damage, 1041 байт."""

    _fields_ = [
        ("m_header", Header),
        ("m_carDamageData", CarDamageData * MAX_CARS),
    ]


# --- 11: Session History (1460) --------------------------------------------

class LapHistoryData(_Base):
    """История одного круга, 14 байт."""

    _fields_ = [
        ("m_lapTimeInMS", u32),  # мс
        ("m_sector1TimeMSPart", u16),
        ("m_sector1TimeMinutesPart", u8),
        ("m_sector2TimeMSPart", u16),
        ("m_sector2TimeMinutesPart", u8),
        ("m_sector3TimeMSPart", u16),
        ("m_sector3TimeMinutesPart", u8),
        ("m_lapValidBitFlags", u8),
    ]


class TyreStintHistoryData(_Base):
    """Стир истории шин, 3 байта."""

    _fields_ = [
        ("m_endLap", u8),
        ("m_tyreActualCompound", u8),
        ("m_tyreVisualCompound", u8),
    ]


class PacketSessionHistory(_Base):
    """Packet 11 — Session History, 1460 байт."""

    _fields_ = [
        ("m_header", Header),
        ("m_carIdx", u8),
        ("m_numLaps", u8),
        ("m_numTyreStints", u8),
        ("m_bestLapTimeLapNum", u8),
        ("m_bestSector1LapNum", u8),
        ("m_bestSector2LapNum", u8),
        ("m_bestSector3LapNum", u8),
        ("m_lapHistoryData", LapHistoryData * 100),
        ("m_tyreStintsHistoryData", TyreStintHistoryData * 8),
    ]


# --- 12: Tyre Sets (231) ---------------------------------------------------

class TyreSetData(_Base):
    """Комплект шин, 10 байт."""

    _fields_ = [
        ("m_actualTyreCompound", u8),
        ("m_visualTyreCompound", u8),
        ("m_wear", u8),  # %
        ("m_available", u8),
        ("m_recommendedSession", u8),
        ("m_lifeSpan", u8),  # кругов
        ("m_usableLife", u8),  # кругов
        ("m_lapDeltaTime", i16),  # мс
        ("m_fitted", u8),
    ]


class PacketTyreSets(_Base):
    """Packet 12 — Tyre Sets, 231 байт."""

    _fields_ = [
        ("m_header", Header),
        ("m_carIdx", u8),
        ("m_tyreSetData", TyreSetData * 20),
        ("m_fittedIdx", u8),
    ]


# --- 13: Motion Ex (273) ---------------------------------------------------

class PacketMotionEx(_Base):
    """Packet 13 — Motion Ex, 273 байта."""

    _fields_ = [
        ("m_header", Header),
        ("m_suspensionPosition", f32 * 4),  # м
        ("m_suspensionVelocity", f32 * 4),  # м/с
        ("m_suspensionAcceleration", f32 * 4),  # м/с^2
        ("m_wheelSpeed", f32 * 4),  # м/с
        ("m_wheelSlipRatio", f32 * 4),
        ("m_wheelSlipAngle", f32 * 4),  # радианы
        ("m_wheelLatForce", f32 * 4),  # Н
        ("m_wheelLongForce", f32 * 4),  # Н
        ("m_heightOfCOGAboveGround", f32),  # м
        ("m_localVelocityX", f32),  # м/с
        ("m_localVelocityY", f32),
        ("m_localVelocityZ", f32),
        ("m_angularVelocityX", f32),  # рад/с
        ("m_angularVelocityY", f32),
        ("m_angularVelocityZ", f32),
        ("m_angularAccelerationX", f32),  # рад/с^2
        ("m_angularAccelerationY", f32),
        ("m_angularAccelerationZ", f32),
        ("m_frontWheelsAngle", f32),  # радианы
        ("m_wheelVertForce", f32 * 4),  # Н
        ("m_frontAeroHeight", f32),  # м
        ("m_rearAeroHeight", f32),
        ("m_frontRollAngle", f32),  # радианы
        ("m_rearRollAngle", f32),
        ("m_chassisYaw", f32),
        ("m_chassisPitch", f32),
        ("m_wheelCamber", f32 * 4),  # радианы
        ("m_wheelCamberGain", f32 * 4),
    ]


# --- 14: Time Trial (101) --------------------------------------------------

class TimeTrialDataSet(_Base):
    """Набор данных Time Trial, 24 байта."""

    _fields_ = [
        ("m_carIdx", u8),
        ("m_teamId", u8),
        ("m_lapTimeInMS", u32),  # мс
        ("m_sector1TimeInMS", u32),  # мс
        ("m_sector2TimeInMS", u32),  # мс
        ("m_sector3TimeInMS", u32),  # мс
        ("m_tractionControl", u8),
        ("m_gearboxAssist", u8),
        ("m_antiLockBrakes", u8),
        ("m_equalCarPerformance", u8),
        ("m_customSetup", u8),
        ("m_valid", u8),
    ]


class PacketTimeTrial(_Base):
    """Packet 14 — Time Trial, 101 байт."""

    _fields_ = [
        ("m_header", Header),
        ("m_playerSessionBestDataSet", TimeTrialDataSet),
        ("m_personalBestDataSet", TimeTrialDataSet),
        ("m_rivalDataSet", TimeTrialDataSet),
    ]


# --- 15: Lap Positions (1131) ----------------------------------------------

class PacketLapPositions(_Base):
    """Packet 15 — Lap Positions, 1131 байт.

    ``m_positionForVehicleIdx[lap][car]``: 0 — нет записи.
    При >50 кругов приходят два пакета с разным ``m_lapStart``.

    В ctypes порядок размерностей читается справа налево, поэтому
    ``u8 * MAX_CARS * 50`` == C ``uint8_t[50][22]`` (50 строк по 22 машины).
    """

    _fields_ = [
        ("m_header", Header),
        ("m_numLaps", u8),
        ("m_lapStart", u8),
        ("m_positionForVehicleIdx", u8 * MAX_CARS * 50),
    ]


# --- Реестр ----------------------------------------------------------------

PACKET_STRUCTS: dict[int, type] = {
    0: PacketMotion,
    1: PacketSession,
    2: PacketLapData,
    3: EventPacket,
    4: PacketParticipants,
    5: PacketCarSetup,
    6: PacketCarTelemetry,
    7: PacketCarStatus,
    8: PacketFinalClassification,
    9: PacketLobbyInfo,
    10: PacketCarDamage,
    11: PacketSessionHistory,
    12: PacketTyreSets,
    13: PacketMotionEx,
    14: PacketTimeTrial,
    15: PacketLapPositions,
}
