# AGENTS.md — F1 25 Telemetry Recorder

Контекст для AI-агента (OpenCode). Читается автоматически в каждой сессии. Держи этот файл в корне репозитория и обновляй, если меняешь архитектуру.

---

## 1. Что это за проект

Десктопное приложение (Windows, Python) которое:

1. Слушает **UDP-телеметрию игры EA SPORTS F1 25** (порт 20777) и парсит бинарные пакеты.
2. **Хранит** данные в SQLite: сессии, участники, круги, посекторные времена, точки телеметрии, события, финальную классификацию, сетапы, комплекты шин.
3. **Отображает телеметрию в реальном времени**: приборная панель + ленты графиков (скорость/газ/тормоз/передача/KERS) на PySide6 + pyqtgraph.
4. Считает **наглядные производные данные**: лучший круг, дельты, теоретический лучший круг по лучшим секторам, деградация шин по стинтам, расход топлива на круг, график позиций, сравнение кругов.

Это **не** веб-приложение и **не** обёртка сайта в Electron. Только нативный Qt.

---

## 2. Стек и жёсткие требования

- Python **3.11+** (разработка/тесты — на 3.13).
- **PySide6** (Qt 6) — GUI. **pyqtgraph** — графики реального времени.
- Ядро парсинга — только stdlib: `socket`, `struct`/`ctypes`, `threading`, `sqlite3`, `json`, `pathlib`.
- Хранилище — **SQLite** (stdlib `sqlite3`), режим **WAL**, батчёвые коммиты.
- Парсинг пакетов — `ctypes.LittleEndianStructure` с `_pack_ = 1` (все структуры packed, little-endian, без паддинга). **Никаких numpy/pandas в рантайме парсера** (pandas допустим только в аналитике/экспорте).
- Зависимости рантайма: `PySide6`, `pyqtgraph`. Дев-зависимости: `pytest`.
- Всё офлайн. Никаких сетевых вызовов наружу, никакой телеметрии «домой», никаких ключей в репозитории.

### Структура репозитория (обязательная)

```
f1-telemetry/
├── AGENTS.md
├── README.md
├── LICENSE                      # MIT
├── pyproject.toml               # пакет f1telemetry, entrypoints
├── requirements.txt
├── .gitignore
├── run.py                       # запуск GUI: python run.py
├── f1telemetry/
│   ├── __init__.py
│   ├── constants.py             # PacketId, enum-таблицы, lookup-словари
│   ├── packets.py               # ctypes-структуры всех 16 пакетов
│   ├── parser.py                # parse_packet(bytes) -> dataclass/dict
│   ├── listener.py              # UDP-приём в отдельном потоке
│   ├── state.py                 # SessionState: склейка пакетов в модель сессии
│   ├── storage.py               # SQLite: схема, writer-поток, запросы
│   ├── recorder.py              # оркестратор: listener -> state -> storage -> сигналы
│   ├── analytics.py             # производные метрики (дельты, стинты, деградация)
│   ├── export.py                # экспорт в CSV/JSON/Parquet(опц.)
│   ├── cli.py                   # headless-режим (запись без GUI)
│   └── ui/
│       ├── __init__.py
│       ├── app.py               # QApplication, тёмная тема
│       ├── main_window.py       # вкладки
│       ├── theme.py
│       └── widgets/
│           ├── live_dash.py     # приборка
│           ├── live_trace.py    # лента графиков (rolling window)
│           ├── lap_table.py     # таблица кругов/секторов в реальном времени
│           ├── session_browser.py
│           ├── lap_compare.py   # оверлей двух кругов + дельта
│           ├── analytics_view.py
│           └── status_bar.py    # соединение, пакеты/с, потеря пакетов
├── tools/
│   └── simulate.py              # генератор синтетических пакетов F1 25 (для тестов без игры)
├── tests/
│   ├── test_packets.py          # размеры структур == таблице
│   ├── test_parser.py           # round-trip: simulate -> parse
│   ├── test_storage.py
│   └── test_analytics.py
└── data/                        # .gitignore, БД и записи не коммитить
```

---

## 3. Формат данных F1 25 (самое важное: НЕ ВЫДУМЫВАТЬ)

Источник: «Data Output from F1® 25 Game» v3 (EA). Формат **2025**, максимум **22 машины**, всё little-endian, всё packed.

> ⚠️ Если у пользователя установлен **2026 Season Pack** — формат другой (24 машины, другой размер части структур, новый packetId 16). Приложение обязано проверить `m_packetFormat` в заголовке: ожидается `2025`. Если пришло `2026` или `2024` — показать баннер «Установите в игре: Настройки → UDP Telemetry → UDP Format = 2025» и не парсить.

### 3.1 Заголовок пакета — 29 байт

| Тип | Поле |
|---|---|
|u16|m_packetFormat (2025)|
|u8|m_gameYear (25)|
|u8|m_gameMajorVersion|
|u8|m_gameMinorVersion|
|u8|m_packetVersion|
|u8|m_packetId|
|u64|m_sessionUID|
|f32|m_sessionTime|
|u32|m_frameIdentifier|
|u32|m_overallFrameIdentifier|
|u8|m_playerCarIndex|
|u8|m_secondaryPlayerCarIndex (255 если нет)|

### 3.2 Packet IDs и размеры (формат 2025, 22 машины)

| ID | Пакет | Размер, байт | Частота |
|---|---|---|---|
|0|Motion|1349|как в меню (обычно 20–60 Гц)|
|1|Session|753|2/с|
|2|Lap Data|1285|как в меню|
|3|Event|45|по событию|
|4|Participants|1284|каждые 5 с|
|5|Car Setups|1133|2/с|
|6|Car Telemetry|1352|как в меню|
|7|Car Status|1239|как в меню|
|8|Final Classification|1042|по завершении|
|9|Lobby Info|954|2/с в лобби|
|10|Car Damage|**1129**|10/с|
|11|Session History|1460|20/с, по кругу обходит машины|
|12|Tyre Sets|231|20/с, по машинам|
|13|Motion Ex|273|как в меню|
|14|Time Trial|101|1/с|
|15|Lap Positions|1131|1/с|

**Важно про Car Damage:** в PDF v3 указан размер 1041 байта — это опечатка (размер из F1 24). В F1 25 в структуру добавили `m_tyreBlisters[4]`, поэтому фактический размер **1129** байт: 29 + 22 × 50. Проверь арифметикой своих структур и на живом приёме; при несовпадении длины пакета — не падать, а писать предупреждение в лог и счётчик ошибок.

### 3.3 Размеры подструктур (используй как самопроверку)

| Структура | Байт | Формула проверки |
|---|---|---|
|CarMotionData|60|29 + 22×60 = 1349|
|MarshalZone|5||
|WeatherForecastSample|8|29 + 19 + 21×5 + 3 + 64×8 + 2 + 12 + 3 + 11 + 4 + 33 + 12 + 8 = 753|
|LapData|57|29 + 22×57 + 2 = 1285|
|ParticipantData|57|29 + 1 + 22×57 = 1284|
|CarSetupData|50|29 + 22×50 + 4 = 1133|
|CarTelemetryData|60|29 + 22×60 + 3 = 1352|
|CarStatusData|55|29 + 22×55 = 1239|
|FinalClassificationData|46|29 + 1 + 22×46 = 1042|
|LobbyInfoData|42|29 + 1 + 22×42 = 954|
|CarDamageData|50|29 + 22×50 = 1129|
|LapHistoryData|14|29 + 7 + 100×14 + 8×3 = 1460|
|TyreStintHistoryData|3|(см. выше)|
|TyreSetData|10|29 + 1 + 20×10 + 1 = 231|
|TimeTrialDataSet|24|29 + 3×24 = 101|
|LiveryColour|3|внутри ParticipantData|

В тестах `ctypes.sizeof(...)` каждого пакета обязан совпасть с таблицей 3.2. Это главный тест корректности.

### 3.4 Определения структур (компактная нотация)

Нотация: `u8/i8/u16/i16/u32/i32/f32/d64`, `char[N]`, `T[n]` — массив.

```
Header (29): u16 packetFormat, u8 gameYear, u8 gameMajorVersion, u8 gameMinorVersion,
  u8 packetVersion, u8 packetId, u64 sessionUID, f32 sessionTime, u32 frameIdentifier,
  u32 overallFrameIdentifier, u8 playerCarIndex, u8 secondaryPlayerCarIndex

CarMotionData (60): f32 worldPositionX, worldPositionY, worldPositionZ,
  f32 worldVelocityX, worldVelocityY, worldVelocityZ,
  i16 worldForwardDirX, Y, Z, i16 worldRightDirX, Y, Z,
  f32 gForceLateral, gForceLongitudinal, gForceVertical, f32 yaw, pitch, roll
  # нормализованные векторы: значение / 32767.0

MarshalZone (5): f32 zoneStart, i8 zoneFlag
WeatherForecastSample (8): u8 sessionType, u8 timeOffset, u8 weather,
  i8 trackTemperature, i8 trackTemperatureChange, i8 airTemperature,
  i8 airTemperatureChange, u8 rainPercentage

Session (753): Header, u8 weather, i8 trackTemperature, i8 airTemperature, u8 totalLaps,
  u16 trackLength, u8 sessionType, i8 trackId, u8 formula, u16 sessionTimeLeft,
  u16 sessionDuration, u8 pitSpeedLimit, u8 gamePaused, u8 isSpectating,
  u8 spectatorCarIndex, u8 sliProNativeSupport, u8 numMarshalZones, MarshalZone[21],
  u8 safetyCarStatus, u8 networkGame, u8 numWeatherForecastSamples,
  WeatherForecastSample[64], u8 forecastAccuracy, u8 aiDifficulty,
  u32 seasonLinkIdentifier, u32 weekendLinkIdentifier, u32 sessionLinkIdentifier,
  u8 pitStopWindowIdealLap, u8 pitStopWindowLatestLap, u8 pitStopRejoinPosition,
  u8 steeringAssist, u8 brakingAssist, u8 gearboxAssist, u8 pitAssist,
  u8 pitReleaseAssist, u8 ERSAssist, u8 DRSAssist, u8 dynamicRacingLine,
  u8 dynamicRacingLineType, u8 gameMode, u8 ruleSet, u32 timeOfDay, u8 sessionLength,
  u8 speedUnitsLeadPlayer, u8 temperatureUnitsLeadPlayer, u8 speedUnitsSecondaryPlayer,
  u8 temperatureUnitsSecondaryPlayer, u8 numSafetyCarPeriods, u8 numVirtualSafetyCarPeriods,
  u8 numRedFlagPeriods, u8 equalCarPerformance, u8 recoveryMode, u8 flashbackLimit,
  u8 surfaceType, u8 lowFuelMode, u8 raceStarts, u8 tyreTemperature, u8 pitLaneTyreSim,
  u8 carDamage, u8 carDamageRate, u8 collisions, u8 collisionsOffForFirstLapOnly,
  u8 mpUnsafePitRelease, u8 mpOffForGriefing, u8 cornerCuttingStringency,
  u8 parcFermeRules, u8 pitStopExperience, u8 safetyCar, u8 safetyCarExperience,
  u8 formationLap, u8 formationLapExperience, u8 redFlags, u8 affectsLicenceLevelSolo,
  u8 affectsLicenceLevelMP, u8 numSessionsInWeekend, u8 weekendStructure[12],
  f32 sector2LapDistanceStart, f32 sector3LapDistanceStart

LapData (57): u32 lastLapTimeInMS, u32 currentLapTimeInMS,
  u16 sector1TimeMSPart, u8 sector1TimeMinutesPart,
  u16 sector2TimeMSPart, u8 sector2TimeMinutesPart,
  u16 deltaToCarInFrontMSPart, u8 deltaToCarInFrontMinutesPart,
  u16 deltaToRaceLeaderMSPart, u8 deltaToRaceLeaderMinutesPart,
  f32 lapDistance, f32 totalDistance, f32 safetyCarDelta, u8 carPosition, u8 currentLapNum,
  u8 pitStatus, u8 numPitStops, u8 sector, u8 currentLapInvalid, u8 penalties,
  u8 totalWarnings, u8 cornerCuttingWarnings, u8 numUnservedDriveThroughPens,
  u8 numUnservedStopGoPens, u8 gridPosition, u8 driverStatus, u8 resultStatus,
  u8 pitLaneTimerActive, u16 pitLaneTimeInLaneInMS, u16 pitStopTimerInMS,
  u8 pitStopShouldServePen, f32 speedTrapFastestSpeed, u8 speedTrapFastestLap

PacketLapData (1285): Header, LapData[22], u8 timeTrialPBCarIdx, u8 timeTrialRivalCarIdx
  # время = minutesPart*60000 + MSPart (мс)

ParticipantData (57): u8 aiControlled, u8 driverId, u8 networkId, u8 teamId, u8 myTeam,
  u8 raceNumber, u8 nationality, char name[32] (UTF-8, null-terminated),
  u8 yourTelemetry, u8 showOnlineNames, u16 techLevel, u8 platform, u8 numColours,
  LiveryColour[4] {u8 red, green, blue}

CarSetupData (50): u8 frontWing, rearWing, onThrottle, offThrottle,
  f32 frontCamber, rearCamber, frontToe, rearToe,
  u8 frontSuspension, rearSuspension, frontAntiRollBar, rearAntiRollBar,
  u8 frontSuspensionHeight, rearSuspensionHeight, u8 brakePressure, brakeBias,
  u8 engineBraking, f32 rearLeftTyrePressure, rearRightTyrePressure,
  frontLeftTyrePressure, frontRightTyrePressure, u8 ballast, f32 fuelLoad
PacketCarSetup (1133): Header, CarSetupData[22], f32 nextFrontWingValue

CarTelemetryData (60): u16 speed (км/ч), f32 throttle (0..1), f32 steer (-1..1), f32 brake (0..1),
  u8 clutch, i8 gear (1-8, N=0, R=-1), u16 engineRPM, u8 drs, u8 revLightsPercent,
  u16 revLightsBitValue, u16 brakesTemperature[4], u8 tyresSurfaceTemperature[4],
  u8 tyresInnerTemperature[4], u16 engineTemperature, f32 tyresPressure[4],
  u8 surfaceType[4]
PacketCarTelemetry (1352): Header, CarTelemetryData[22], u8 mfdPanelIndex,
  u8 mfdPanelIndexSecondaryPlayer, i8 suggestedGear

CarStatusData (55): u8 tractionControl, u8 antiLockBrakes, u8 fuelMix, u8 frontBrakeBias,
  u8 pitLimiterStatus, f32 fuelInTank, f32 fuelCapacity, f32 fuelRemainingLaps,
  u16 maxRPM, u16 idleRPM, u8 maxGears, u8 drsAllowed, u16 drsActivationDistance,
  u8 actualTyreCompound, u8 visualTyreCompound, u8 tyresAgeLaps, i8 vehicleFiaFlags,
  f32 enginePowerICE, f32 enginePowerMGUK, f32 ersStoreEnergy, u8 ersDeployMode,
  f32 ersHarvestedThisLapMGUK, f32 ersHarvestedThisLapMGUH, f32 ersDeployedThisLap,
  u8 networkPaused

FinalClassificationData (46): u8 position, numLaps, gridPosition, points, numPitStops,
  resultStatus, resultReason, u32 bestLapTimeInMS, d64 totalRaceTime (сек),
  u8 penaltiesTime, numPenalties, numTyreStints, u8 tyreStintsActual[8],
  u8 tyreStintsVisual[8], u8 tyreStintsEndLaps[8]

LobbyInfoData (42): u8 aiControlled, teamId, nationality, platform, char name[32],
  u8 carNumber, yourTelemetry, showOnlineNames, u16 techLevel, u8 readyStatus

CarDamageData (50): f32 tyresWear[4], u8 tyresDamage[4], u8 brakesDamage[4],
  u8 tyreBlisters[4], u8 frontLeftWingDamage, frontRightWingDamage, rearWingDamage,
  floorDamage, diffuserDamage, sidepodDamage, drsFault, ersFault, gearBoxDamage,
  engineDamage, engineMGUHWear, engineESWear, engineCEWear, engineICEWear,
  engineMGUKWear, engineTCWear, engineBlown, engineSeized

LapHistoryData (14): u32 lapTimeInMS, u16 sector1TimeMSPart, u8 sector1TimeMinutesPart,
  u16 sector2TimeMSPart, u8 sector2TimeMinutesPart, u16 sector3TimeMSPart,
  u8 sector3TimeMinutesPart, u8 lapValidBitFlags
  # биты: 0x01 круг валиден, 0x02 S1, 0x04 S2, 0x08 S3
TyreStintHistoryData (3): u8 endLap, tyreActualCompound, tyreVisualCompound
SessionHistory (1460): Header, u8 carIdx, u8 numLaps, u8 numTyreStints,
  u8 bestLapTimeLapNum, bestSector1LapNum, bestSector2LapNum, bestSector3LapNum,
  LapHistoryData[100], TyreStintHistoryData[8]

TyreSetData (10): u8 actualTyreCompound, visualTyreCompound, wear, available,
  recommendedSession, lifeSpan, usableLife, i16 lapDeltaTime, u8 fitted
PacketTyreSets (231): Header, u8 carIdx, TyreSetData[20], u8 fittedIdx

MotionEx (273): Header, f32 suspensionPosition[4], suspensionVelocity[4],
  suspensionAcceleration[4], wheelSpeed[4], wheelSlipRatio[4], wheelSlipAngle[4],
  wheelLatForce[4], wheelLongForce[4], f32 heightOfCOGAboveGround,
  localVelocityX/Y/Z, angularVelocityX/Y/Z, angularAccelerationX/Y/Z,
  f32 frontWheelsAngle, f32 wheelVertForce[4], f32 frontAeroHeight, rearAeroHeight,
  frontRollAngle, rearRollAngle, chassisYaw, chassisPitch,
  f32 wheelCamber[4], wheelCamberGain[4]
  # порядок колёс ВО ВСЕХ массивах: 0=RL, 1=RR, 2=FL, 3=FR

TimeTrialDataSet (24): u8 carIdx, u8 teamId, u32 lapTimeInMS, u32 sector1/2/3TimeInMS,
  u8 tractionControl, gearboxAssist, antiLockBrakes, equalCarPerformance, customSetup, valid
PacketTimeTrial (101): Header, playerSessionBestDataSet, personalBestDataSet, rivalDataSet

PacketLapPositions (1131): Header, u8 numLaps, u8 lapStart (0-indexed),
  u8 positionForVehicleIdx[50][22]  # 50 кругов × 22 машины, 0 = нет записи
  # если кругов > 50 — приходят два пакета с разным lapStart, надо слить

Event (45): Header, char eventStringCode[4], EventDataDetails (union, максимум 12 байт)
```

### 3.5 Event: код → структура деталей

| Код | Событие | Детали |
|---|---|---|
|SSTA|Session Started|нет|
|SEND|Session Ended|нет|
|FTLP|Fastest Lap|u8 vehicleIdx, f32 lapTime|
|RTMT|Retirement|u8 vehicleIdx, u8 reason|
|DRSE|DRS enabled|нет|
|DRSD|DRS disabled|u8 reason (0=мокрая трасса,1=SC,2=красный флаг,3=мало кругов)|
|TMPT|Team mate in pits|u8 vehicleIdx|
|CHQF|Chequered flag|нет|
|RCWN|Race Winner|u8 vehicleIdx|
|PENA|Penalty Issued|u8 penaltyType, infringementType, vehicleIdx, otherVehicleIdx, time, lapNum, placesGained|
|SPTP|Speed Trap|u8 vehicleIdx, f32 speed, u8 isOverallFastestInSession, u8 isDriverFastestInSession, u8 fastestVehicleIdxInSession, f32 fastestSpeedInSession|
|STLG|Start lights|u8 numLights|
|LGOT|Lights out|нет|
|DTSV|Drive through served|u8 vehicleIdx|
|SGSV|Stop go served|u8 vehicleIdx, f32 stopTime|
|FLBK|Flashback|u32 flashbackFrameIdentifier, f32 flashbackSessionTime|
|BUTN|Button status|u32 buttonStatus (битовые флаги)|
|RDFL|Red flag|нет|
|OVTK|Overtake|u8 overtakingVehicleIdx, u8 beingOvertakenVehicleIdx|
|SCAR|Safety Car|u8 safetyCarType (0 нет,1 full,2 VSC,3 formation), u8 eventType (0 deployed,1 returning,2 returned,3 resume)|
|COLL|Collision|u8 vehicle1Idx, u8 vehicle2Idx|

### 3.6 Enum-таблицы (для человекочитаемого вывода)

**Session type:** 0 Unknown, 1 P1, 2 P2, 3 P3, 4 Short Practice, 5 Q1, 6 Q2, 7 Q3, 8 Short Qualifying, 9 One-Shot Qualifying, 10 Sprint Shootout 1, 11 Sprint Shootout 2, 12 Sprint Shootout 3, 13 Short Sprint Shootout, 14 One-Shot Sprint Shootout, 15 Race, 16 Race 2, 17 Race 3, 18 Time Trial

**Track ID:** 0 Melbourne, 2 Shanghai, 3 Sakhir (Bahrain), 4 Catalunya, 5 Monaco, 6 Montreal, 7 Silverstone, 9 Hungaroring, 10 Spa, 11 Monza, 12 Singapore, 13 Suzuka, 14 Abu Dhabi, 15 Texas (COTA), 16 Brazil (Interlagos), 17 Austria (Red Bull Ring), 19 Mexico, 20 Baku, 26 Zandvoort, 27 Imola, 29 Jeddah, 30 Miami, 31 Las Vegas, 32 Losail, 39 Silverstone Reverse, 40 Austria Reverse, 41 Zandvoort Reverse

**Team ID:** 0 Mercedes, 1 Ferrari, 2 Red Bull Racing, 3 Williams, 4 Aston Martin, 5 Alpine, 6 RB, 7 Haas, 8 McLaren, 9 Sauber, 41 F1 Generic, 104 F1 Custom Team, 129 Konnersport, 142/154 APXGP, 155 Konnersport '24, 158–168 команды F2, 185–194 «'24» версии команд F1

**Шины — actual compound:** 16=C5, 17=C4, 18=C3, 19=C2, 20=C1, 21=C0, 22=C6, 7=Inter, 8=Wet (F1 Classiс: 9 dry, 10 wet; F2: 11 super soft … 15 wet)
**Шины — visual compound:** 16=Soft, 17=Medium, 18=Hard, 7=Inter, 8=Wet (F2 '20: 19 super soft, 20 soft, 21 medium, 22 hard, 15 wet)

**Weather:** 0 clear, 1 light cloud, 2 overcast, 3 light rain, 4 heavy rain, 5 storm
**Safety car status:** 0 нет, 1 full, 2 virtual (VSC), 3 formation lap
**Flag (vehicleFiaFlags / marshal zone):** -1 unknown, 0 none, 1 green, 2 blue, 3 yellow
**Surface type:** 0 Tarmac, 1 Rumble strip, 2 Concrete, 3 Rock, 4 Gravel, 5 Mud, 6 Sand, 7 Grass, 8 Water, 9 Cobblestone, 10 Metal, 11 Ridged
**Pit status:** 0 none, 1 pitting, 2 in pit area
**Driver status:** 0 in garage, 1 flying lap, 2 in lap, 3 out lap, 4 on track
**Result status:** 0 invalid, 1 inactive, 2 active, 3 finished, 4 DNF, 5 DSQ, 6 not classified, 7 retired
**Result reason:** 0 invalid, 1 retired, 2 finished, 3 terminal damage, 4 inactive, 5 not enough laps, 6 black flag, 7 red flag, 8 mechanical failure, 9 session skipped, 10 session simulated
**DRS:** 0 off, 1 on; drsAllowed: 0 не разрешён, 1 разрешён
**ERS deploy mode:** 0 none, 1 medium, 2 hotlap, 3 overtake
**Penalty types:** 0 Drive through, 1 Stop Go, 2 Grid penalty, 3 Penalty reminder, 4 Time penalty, 5 Warning, 6 Disqualified, 7 Removed from formation lap, 8 Parked too long, 9 Tyre regulations, 10–15 инвалидация кругов, 16 Retired, 17 Black flag timer
**Infringement types:** 0..46 — полный список в файле `f1telemetry/constants.py`, завести словарь (0 Blocking slow driving … 46 Unserved drive through penalty). Если точного названия не помнишь — используй `#ID`, не выдумывай.

---

## 4. Архитектурные правила

1. **Ядро не знает про Qt.** `parser/state/storage/analytics` обязаны импортироваться и работать в headless-режиме. GUI — только подписчик на события.
2. **Потоки:** приём UDP — отдельный поток (`daemon=True`); запись в SQLite — отдельный поток-писатель с очередью; GUI — главный поток. Из фоновых потоков в GUI передавать данные **только через Qt-сигналы** (никаких прямых вызовов виджетов из потоков).
3. **UI-обновление:** QTimer ~30–60 Гц, читает последние срезы из ring-буфера, а не по каждому входящему пакету.
4. **Ring-буферы:** телеметрия в памяти — deque на ~120 с (по умолчанию). Полный круг пишется в БД всегда (прореживание: 20 Гц по умолчанию, настраивается).
5. **Устойчивость к потерям:** UDP не гарантирует доставку — все поля считать «последним известным», пропуски не считать ошибкой. Считать и показывать пакеты/с и примерную потерю по `m_overallFrameIdentifier`.
6. **Смена сессии** (`m_sessionUID` изменился) → закрыть текущую сессию в БД, начать новую запись.
7. **Flashback** (событие `FLBK`): во времени игра откатилась назад — помечать затронутые срезы в БД флагом `flashback=1`, не удалять.
8. **Никаких падений на мусоре из сокета:** пакеты неверной длины/формата — в счётчик ошибок, в лог, и жить дальше. Размер буфера приёма — минимум 2048 байт (макс. пакет 1460 + запас).
9. **Логи:** stdlib `logging`, файл `data/logs/app.log` + консоль, уровень через переменную окружения.
10. **Типизация:** аннотации типов везде, `from __future__ import annotations`.

---

## 5. Схема БД (SQLite, WAL)

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS sessions (
  session_uid INTEGER PRIMARY KEY,
  packet_format INTEGER, game_year INTEGER, game_version TEXT,
  track_id INTEGER, track_name TEXT,
  session_type INTEGER, session_type_name TEXT,
  weather INTEGER, total_laps INTEGER, track_length_m INTEGER,
  season_link_id INTEGER, weekend_link_id INTEGER, session_link_id INTEGER,
  identical_session INTEGER,          -- из sessionLinkIdentifier: это уже виденная сессия
  started_at_utc TEXT, ended_at_utc TEXT,
  player_car_index INTEGER, num_cars INTEGER,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS participants (
  session_uid INTEGER, car_index INTEGER,
  name TEXT, driver_id INTEGER, team_id INTEGER, team_name TEXT,
  race_number INTEGER, nationality INTEGER, ai_controlled INTEGER,
  platform INTEGER, telemetry_public INTEGER,
  PRIMARY KEY (session_uid, car_index)
);

CREATE TABLE IF NOT EXISTS laps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_uid INTEGER, car_index INTEGER, lap_num INTEGER,
  lap_time_ms INTEGER, sector1_ms INTEGER, sector2_ms INTEGER, sector3_ms INTEGER,
  valid INTEGER, s1_valid INTEGER, s2_valid INTEGER, s3_valid INTEGER,
  position INTEGER, stint_index INTEGER,
  compound_actual INTEGER, compound_visual INTEGER, tyre_age_laps INTEGER,
  fuel_at_start REAL, ers_deployed_j REAL,
  top_speed_kmh REAL, avg_throttle REAL, brake_time_pct REAL, full_throttle_pct REAL,
  UNIQUE (session_uid, car_index, lap_num) ON CONFLICT REPLACE
);

CREATE TABLE IF NOT EXISTS telemetry (        -- точки телеметрии (прорежено)
  session_uid INTEGER, car_index INTEGER, lap_num INTEGER, sample_idx INTEGER,
  session_time REAL, lap_distance_m REAL,
  speed_kmh REAL, throttle REAL, brake REAL, steer REAL, gear INTEGER, rpm INTEGER,
  drs INTEGER, ers_mode INTEGER,
  pos_x REAL, pos_y REAL, pos_z REAL,
  world_vel_x REAL, world_vel_y REAL, world_vel_z REAL,
  g_lat REAL, g_lon REAL, g_vert REAL,
  yaw REAL, pitch REAL, roll REAL,
  PRIMARY KEY (session_uid, car_index, lap_num, sample_idx)
);
CREATE INDEX IF NOT EXISTS idx_telemetry_lap ON telemetry(session_uid, car_index, lap_num, lap_distance_m);

CREATE TABLE IF NOT EXISTS car_telemetry_detail (   -- низкочастотные детали (10 Гц)
  session_uid INTEGER, car_index INTEGER, lap_num INTEGER, sample_idx INTEGER,
  brake_temp_rl REAL, brake_temp_rr REAL, brake_temp_fl REAL, brake_temp_fr REAL,
  tyre_surf_rl REAL, tyre_surf_rr REAL, tyre_surf_fl REAL, tyre_surf_fr REAL,
  tyre_inner_rl REAL, tyre_inner_rr REAL, tyre_inner_fl REAL, tyre_inner_fr REAL,
  tyre_press_rl REAL, tyre_press_rr REAL, tyre_press_fl REAL, tyre_press_fr REAL,
  tyre_wear_rl REAL, tyre_wear_rr REAL, tyre_wear_fl REAL, tyre_wear_fr REAL,
  tyre_dmg_rl REAL, tyre_dmg_rr REAL, tyre_dmg_fl REAL, tyre_dmg_fr REAL,
  surface_rl INTEGER, surface_rr INTEGER, surface_fl INTEGER, surface_fr INTEGER,
  engine_temp REAL,
  PRIMARY KEY (session_uid, car_index, lap_num, sample_idx)
);

CREATE TABLE IF NOT EXISTS car_status_samples (
  session_uid INTEGER, car_index INTEGER, lap_num INTEGER, sample_idx INTEGER,
  fuel_in_tank REAL, fuel_remaining_laps REAL, ers_store_j REAL,
  ers_deployed_lap_j REAL, ers_harvested_lap_j REAL,
  engine_power_ice_w REAL, engine_power_mguk_w REAL,
  PRIMARY KEY (session_uid, car_index, lap_num, sample_idx)
);

CREATE TABLE IF NOT EXISTS stints (
  session_uid INTEGER, car_index INTEGER, stint_index INTEGER,
  end_lap INTEGER, start_lap INTEGER,
  compound_actual INTEGER, compound_visual INTEGER,
  laps_count INTEGER, avg_lap_ms INTEGER, deg_ms_per_lap REAL,
  PRIMARY KEY (session_uid, car_index, stint_index)
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_uid INTEGER, session_time REAL, code TEXT,
  car_index INTEGER, other_car_index INTEGER, payload_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_uid, session_time);

CREATE TABLE IF NOT EXISTS final_classification (
  session_uid INTEGER, car_index INTEGER, position INTEGER, num_laps INTEGER,
  grid_position INTEGER, points INTEGER, num_pit_stops INTEGER,
  result_status INTEGER, result_reason INTEGER, best_lap_ms INTEGER,
  total_race_time REAL, penalties_time INTEGER, num_penalties INTEGER,
  stints_actual_json TEXT, stints_visual_json TEXT, stints_end_laps_json TEXT,
  PRIMARY KEY (session_uid, car_index)
);

CREATE TABLE IF NOT EXISTS setups (
  session_uid INTEGER, car_index INTEGER, captured_at TEXT, setup_json TEXT,
  PRIMARY KEY (session_uid, car_index)
);

CREATE TABLE IF NOT EXISTS tyre_sets (
  session_uid INTEGER, car_index INTEGER, set_index INTEGER,
  compound_actual INTEGER, compound_visual INTEGER, wear REAL, available INTEGER,
  recommended_session INTEGER, life_span INTEGER, usable_life INTEGER,
  lap_delta_ms INTEGER, fitted INTEGER, updated_at TEXT,
  PRIMARY KEY (session_uid, car_index, set_index)
);

CREATE TABLE IF NOT EXISTS lap_positions (
  session_uid INTEGER, lap_num INTEGER, car_index INTEGER, position INTEGER,
  PRIMARY KEY (session_uid, lap_num, car_index)
);
```

Правила записи: писать **только за игрока** в высокочастотные таблицы; за всех — в `laps`, `participants`, `final_classification`, `lap_positions`. Батч-коммит каждые 0.5–1 с или по 500 записей.

---

## 6. Команды для разработки

```bash
python -m pytest -q                     # тесты
python tools/simulate.py --laps 3 --hz 60 --port 20777   # синтетический поток данных
python run.py                           # GUI
python -m f1telemetry.cli record --db data/f1.sqlite --seconds 30   # запись без GUI
python -m f1telemetry.cli export --db data/f1.sqlite --session <uid> --out export/ --format csv
```

`tools/simulate.py` — обязателен: без него ни тесты, ни GUI не проверить, если игра не запущена. Генерировать пакеты **независимым** кодом (`struct.pack` по полям согласно разделу 3, без импорта `f1telemetry.packets`) — это делает тест round-trip валидным. Симулятор должен уметь: число кругов, частоту, число машин, «идеальный круг» с реалистичным профилем скорость/газ/тормоз/передача, 2–3 стинта, пит-стоп, события (старт, быстрый круг, финиш), и режим `--record-fixture tests/fixtures/*.bin` для записи бинарных пакетов в файл.

```bash
python tools/simulate.py --laps 3 --record-fixture tests/fixtures/session_3laps.bin
```

---

## 7. Стиль и правила для агента

- Не выдумывать структуры пакетов, ID, названия трасс/команд: только раздел 3 этого файла. Если данных нет — `#ID` / `Unknown (ID)`.
- Каждый новый модуль сопровождать тестом.
- Публичные функции — docstring; в docstring для полей указывать единицы измерения (км/ч, мс, метры, Джоули).
- Не добавлять тяжёлые зависимости без спроса.
- Не коммитить: `data/`, `*.sqlite*`, `*.bin`, `*.log`, `__pycache__/`, `.venv/`, `export/`.
- Коммиты — Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
- Никогда не заливать в публичный репозиторий что-либо, кроме кода и документации (игровые ассеты, логи, БД, никнеймы соперников — не публиковать).

## 8. Definition of Done (краткая версия, полная — в ТЗ)

- [ ] `python -m pytest -q` — зелёный, включая тест размеров всех 16 пакетов.
- [ ] `python tools/simulate.py` + `python -m f1telemetry.cli record` пишут непустые `laps`/`telemetry` в SQLite.
- [ ] `python run.py` показывает живую приборку и графики на данных симулятора.
- [ ] README с настройкой UDP в игре, скриншотом и описанием схемы БД.
- [ ] Код залит на GitHub.

---

## 9. Заметки по реализации (отклонения от раздела 3 — НЕ редактировать раздел 3 задним числом)

1. **Car Damage = 1041 байт, а не 1129.** В PDF v3 «Data Output from F1® 25»
   указан размер 1041 (29 + 22×46) — это корректный размер F1 25 (структура из
   F1 24: 4×f32 + 30×u8 на машину). Значение 1129 в разделе 3.2 выше — ошибка
   предыдущей редакции (два лишних u8 на машину). `PACKET_SIZES[CarDamage]=1041`;
   в `constants.py` лежит константа `CAR_DAMAGE_SIZE_AGENTS_DOC = 1129` для
   сверки. Пакеты неверной длины не роняют приём: счётчик ошибок + лог.

2. **Таблицы шин разнесены на три**: `TYRE_COMPOUND_ACTUAL`, `TYRE_COMPOUND_VISUAL`
   и короткие метки `TYRE_COMPOUND_SHORT` (Soft/Medium/Hard/Inter/Wet) для UI.

3. **`INFRINGEMENT_TYPES` — только концы диапазона** (0 и 46) по правилу
   «не выдумывать». Остальные ID отображаются как `#N`.

4. **`SESSION_TYPES` / `TRACKS` / `TEAMS`** — имена таблиц (не `*_NAMES`).

5. **Session UID — u64.** Qt-сигнал `Signal(int)` — 32-битный: в GUI мост
   использует `Signal(object)` для `session_changed`.

6. **ctypes-порядок размерностей**: `u8 * MAX_CARS * 50` соответствует C
   `uint8_t[50][22]` (в Python это [22][50]). Ошибка приводила к мусору в
   PacketLapPositions.

7. **`SessionState.recent_laps`** — deque последних завершённых кругов
   (LapRecord) для наполнения таблицы кругов в GUI; GUI читает через
   `take_recent_laps()`.

8. **`requirements.txt` не ведётся** — источник истины `pyproject.toml` +
   закоммиченный `uv.lock`.

9. **Фикстура `tests/fixtures/session_short.bin`** — Silverstone, гонка, 22
   машины, 2 круга по короткой трассе (400 м), 10 Гц. Генерируется командой:
   `python tools/simulate.py --laps 2 --hz 10 --cars 22 --track-length 400
   --record-fixture tests/fixtures/session_short.bin`
