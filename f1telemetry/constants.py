"""Enum-таблицы и константы формата F1 25 (UDP format 2025).

Все значения взяты из спецификации «Data Output from F1(R) 25 Game» v3
(см. AGENTS.md, раздел 3). Ничего не выдумывается: неизвестные ID
отображаются как ``#ID`` / ``Unknown (ID)``.
"""

from __future__ import annotations

from enum import IntEnum

# --- Формат пакетов -------------------------------------------------------

EXPECTED_PACKET_FORMAT = 2025
"""Ожидаемый ``m_packetFormat``. Другие форматы (2024, 2026) не парсим."""

MAX_CARS = 22
"""Максимум машин в формате 2025."""

HEADER_SIZE = 29
"""Размер заголовка пакета, байт."""

DEFAULT_UDP_PORT = 20777
DEFAULT_LISTEN_HOST = "0.0.0.0"
"""Слушаем весь интерфейс — игра обычно на другом ПК в LAN."""

RECV_BUFFER_SIZE = 2048
"""Буфер приёма: макс. пакет 1460 байт + запас (AGENTS.md, п.4.8)."""


class PacketId(IntEnum):
    """``m_packetId`` — тип пакета."""

    MOTION = 0
    SESSION = 1
    LAP_DATA = 2
    EVENT = 3
    PARTICIPANTS = 4
    CAR_SETUPS = 5
    CAR_TELEMETRY = 6
    CAR_STATUS = 7
    FINAL_CLASSIFICATION = 8
    LOBBY_INFO = 9
    CAR_DAMAGE = 10
    SESSION_HISTORY = 11
    TYRE_SETS = 12
    MOTION_EX = 13
    TIME_TRIAL = 14
    LAP_POSITIONS = 15


PACKET_NAMES: dict[int, str] = {
    int(PacketId.MOTION): "Motion",
    int(PacketId.SESSION): "Session",
    int(PacketId.LAP_DATA): "Lap Data",
    int(PacketId.EVENT): "Event",
    int(PacketId.PARTICIPANTS): "Participants",
    int(PacketId.CAR_SETUPS): "Car Setups",
    int(PacketId.CAR_TELEMETRY): "Car Telemetry",
    int(PacketId.CAR_STATUS): "Car Status",
    int(PacketId.FINAL_CLASSIFICATION): "Final Classification",
    int(PacketId.LOBBY_INFO): "Lobby Info",
    int(PacketId.CAR_DAMAGE): "Car Damage",
    int(PacketId.SESSION_HISTORY): "Session History",
    int(PacketId.TYRE_SETS): "Tyre Sets",
    int(PacketId.MOTION_EX): "Motion Ex",
    int(PacketId.TIME_TRIAL): "Time Trial",
    int(PacketId.LAP_POSITIONS): "Lap Positions",
}

#: Ожидаемый размер каждого пакета в байтах (формат 2025, 22 машины).
#:
#: Примечание по Car Damage: в AGENTS.md (раздел 3.2) указано 1129
#: (29 + 22×50), однако арифметика перечисленных там же полей даёт 46 байт на
#: машину: 16 (float tyresWear[4]) + 4×3 (u8 массивы) + 18 (u8 поля) = 46,
#: итого 29 + 22×46 = 1041 — ровно то, что указано в официальном PDF EA
#: «Data Output from F1 25» v3. Перекрёстная проверка: в том же PDF для 2026
#: Season Pack (24 машины) размер 1133 = 29 + 24×46. См. «Заметки по
#: реализации» в AGENTS.md.
PACKET_SIZES: dict[int, int] = {
    int(PacketId.MOTION): 1349,
    int(PacketId.SESSION): 753,
    int(PacketId.LAP_DATA): 1285,
    int(PacketId.EVENT): 45,
    int(PacketId.PARTICIPANTS): 1284,
    int(PacketId.CAR_SETUPS): 1133,
    int(PacketId.CAR_TELEMETRY): 1352,
    int(PacketId.CAR_STATUS): 1239,
    int(PacketId.FINAL_CLASSIFICATION): 1042,
    int(PacketId.LOBBY_INFO): 954,
    int(PacketId.CAR_DAMAGE): 1041,
    int(PacketId.SESSION_HISTORY): 1460,
    int(PacketId.TYRE_SETS): 231,
    int(PacketId.MOTION_EX): 273,
    int(PacketId.TIME_TRIAL): 101,
    int(PacketId.LAP_POSITIONS): 1131,
}

#: Альтернативный размер Car Damage из AGENTS.md 3.2 (1129 = 29 + 22×50).
#: Не соответствует ни одной известной ревизии формата; держим только для
#: диагностики, если такое когда-нибудь придёт по сети.
CAR_DAMAGE_SIZE_AGENTS_DOC = 1129


def packet_name(packet_id: int) -> str:
    """Человекочитаемое имя пакета (``Unknown (ID)`` для неизвестных)."""
    return PACKET_NAMES.get(packet_id, f"Unknown ({packet_id})")


# --- Session type ---------------------------------------------------------

SESSION_TYPES: dict[int, str] = {
    0: "Unknown",
    1: "P1",
    2: "P2",
    3: "P3",
    4: "Short Practice",
    5: "Q1",
    6: "Q2",
    7: "Q3",
    8: "Short Qualifying",
    9: "One-Shot Qualifying",
    10: "Sprint Shootout 1",
    11: "Sprint Shootout 2",
    12: "Sprint Shootout 3",
    13: "Short Sprint Shootout",
    14: "One-Shot Sprint Shootout",
    15: "Race",
    16: "Race 2",
    17: "Race 3",
    18: "Time Trial",
}

# --- Track IDs ------------------------------------------------------------

TRACKS: dict[int, str] = {
    0: "Melbourne",
    2: "Shanghai",
    3: "Sakhir (Bahrain)",
    4: "Catalunya",
    5: "Monaco",
    6: "Montreal",
    7: "Silverstone",
    9: "Hungaroring",
    10: "Spa",
    11: "Monza",
    12: "Singapore",
    13: "Suzuka",
    14: "Abu Dhabi",
    15: "Texas (COTA)",
    16: "Brazil (Interlagos)",
    17: "Austria (Red Bull Ring)",
    19: "Mexico",
    20: "Baku",
    26: "Zandvoort",
    27: "Imola",
    29: "Jeddah",
    30: "Miami",
    31: "Las Vegas",
    32: "Losail",
    39: "Silverstone Reverse",
    40: "Austria Reverse",
    41: "Zandvoort Reverse",
}

# --- Team IDs -------------------------------------------------------------

_TEAMS_2025 = {
    0: "Mercedes",
    1: "Ferrari",
    2: "Red Bull Racing",
    3: "Williams",
    4: "Aston Martin",
    5: "Alpine",
    6: "RB",
    7: "Haas",
    8: "McLaren",
    9: "Sauber",
}

_TEAMS_OTHER = {
    41: "F1 Generic",
    104: "F1 Custom Team",
    129: "Konnersport",
    142: "APXGP",
    154: "APXGP",
    155: "Konnersport '24",
}

#: Команды F2 (158–168) и их версии «'24» (185–194).
for _i in range(158, 169):
    _TEAMS_OTHER[_i] = f"F2 team #{_i}"
for _i in range(185, 195):
    _TEAMS_OTHER[_i] = f"F2 team #{_i} ('24)"

TEAMS: dict[int, str] = {**_TEAMS_2025, **_TEAMS_OTHER}
del _i, _TEAMS_2025, _TEAMS_OTHER

# --- Tyres ----------------------------------------------------------------

#: ``m_actualTyreCompound`` (F1 25).
TYRE_COMPOUND_ACTUAL: dict[int, str] = {
    7: "Intermediate",
    8: "Wet",
    16: "C5",
    17: "C4",
    18: "C3",
    19: "C2",
    20: "C1",
    21: "C0",
    22: "C6",
    # F1 Classic
    9: "Dry (Classic)",
    10: "Wet (Classic)",
    # F2
    11: "Super Soft (F2)",
    12: "Soft (F2)",
    13: "Medium (F2)",
    14: "Hard (F2)",
    15: "Wet (F2)",
}

#: ``m_visualTyreCompound``.
TYRE_COMPOUND_VISUAL: dict[int, str] = {
    7: "Intermediate",
    8: "Wet",
    15: "Wet (F2 '20)",
    16: "Soft",
    17: "Medium",
    18: "Hard",
    19: "Super Soft (F2 '20)",
    20: "Soft (F2 '20)",
    21: "Medium (F2 '20)",
    22: "Hard (F2 '20)",
}

#: Короткая метка для GUI.
TYRE_COMPOUND_SHORT: dict[int, str] = {
    7: "INT",
    8: "WET",
    16: "S",
    17: "M",
    18: "H",
    19: "C2",
    20: "C1",
    21: "C0",
    22: "C6",
}

# --- Weather / status -----------------------------------------------------

WEATHER: dict[int, str] = {
    0: "Clear",
    1: "Light cloud",
    2: "Overcast",
    3: "Light rain",
    4: "Heavy rain",
    5: "Storm",
}

SAFETY_CAR_STATUS: dict[int, str] = {
    0: "None",
    1: "Full",
    2: "Virtual (VSC)",
    3: "Formation lap",
}

FLAGS: dict[int, str] = {
    -1: "Unknown",
    0: "None",
    1: "Green",
    2: "Blue",
    3: "Yellow",
}

SURFACE_TYPES: dict[int, str] = {
    0: "Tarmac",
    1: "Rumble strip",
    2: "Concrete",
    3: "Rock",
    4: "Gravel",
    5: "Mud",
    6: "Sand",
    7: "Grass",
    8: "Water",
    9: "Cobblestone",
    10: "Metal",
    11: "Ridged",
}

PIT_STATUS: dict[int, str] = {0: "None", 1: "Pitting", 2: "In pit area"}

DRIVER_STATUS: dict[int, str] = {
    0: "In garage",
    1: "Flying lap",
    2: "In lap",
    3: "Out lap",
    4: "On track",
}

RESULT_STATUS: dict[int, str] = {
    0: "Invalid",
    1: "Inactive",
    2: "Active",
    3: "Finished",
    4: "DNF",
    5: "DSQ",
    6: "Not classified",
    7: "Retired",
}

RESULT_REASON: dict[int, str] = {
    0: "Invalid",
    1: "Retired",
    2: "Finished",
    3: "Terminal damage",
    4: "Inactive",
    5: "Not enough laps",
    6: "Black flag",
    7: "Red flag",
    8: "Mechanical failure",
    9: "Session skipped",
    10: "Session simulated",
}

DRS_STATUS: dict[int, str] = {0: "Off", 1: "On"}

ERS_DEPLOY_MODE: dict[int, str] = {
    0: "None",
    1: "Medium",
    2: "Hotlap",
    3: "Overtake",
}

# --- Events ---------------------------------------------------------------

#: Коды событий (``eventStringCode``) -> название.
EVENT_CODES: dict[str, str] = {
    "SSTA": "Session Started",
    "SEND": "Session Ended",
    "FTLP": "Fastest Lap",
    "RTMT": "Retirement",
    "DRSE": "DRS enabled",
    "DRSD": "DRS disabled",
    "TMPT": "Team mate in pits",
    "CHQF": "Chequered flag",
    "RCWN": "Race Winner",
    "PENA": "Penalty Issued",
    "SPTP": "Speed Trap",
    "STLG": "Start lights",
    "LGOT": "Lights out",
    "DTSV": "Drive through served",
    "SGSV": "Stop go served",
    "FLBK": "Flashback",
    "BUTN": "Button status",
    "RDFL": "Red flag",
    "OVTK": "Overtake",
    "SCAR": "Safety Car",
    "COLL": "Collision",
}

#: Причина отключения DRS.
DRSD_REASONS: dict[int, str] = {
    0: "Wet track",
    1: "Safety car",
    2: "Red flag",
    3: "Fewer laps remaining",
}

SAFETY_CAR_EVENT_TYPES: dict[int, str] = {
    0: "Deployed",
    1: "Returning",
    2: "Returned",
    3: "Resume",
}

# --- Penalties ------------------------------------------------------------

PENALTY_TYPES: dict[int, str] = {
    0: "Drive through",
    1: "Stop go",
    2: "Grid penalty",
    3: "Penalty reminder",
    4: "Time penalty",
    5: "Warning",
    6: "Disqualified",
    7: "Removed from formation lap",
    8: "Parked too long",
    9: "Tyre regulations",
    10: "Lap invalidated",
    11: "Lap invalidated",
    12: "Lap invalidated",
    13: "Lap invalidated",
    14: "Lap invalidated",
    15: "Lap invalidated",
    16: "Retired",
    17: "Black flag timer",
}

#: ``m_infringementType`` (0..46). Подтверждены только названия, приведённые
#: в AGENTS.md (раздел 3.6); для остальных используется fallback ``#ID``,
#: пока не будет переписан полный список из PDF EA.
INFRINGEMENT_TYPES: dict[int, str] = {
    0: "Blocking slow driving",
    46: "Unserved drive through penalty",
}

#: Биты ``m_lapValidBitFlags`` (Session History).
LAP_VALID_BIT = 0x01
SECTOR1_VALID_BIT = 0x02
SECTOR2_VALID_BIT = 0x04
SECTOR3_VALID_BIT = 0x08


def name_of(table: dict[int, str], key: int, prefix: str = "#") -> str:
    """Возвращает человекочитаемое имя из таблицы или ``#ID``.

    Args:
        table: словарь ``id -> название``.
        key: искомый ID.
        prefix: префикс для неизвестных значений.
    """
    return table.get(key, f"{prefix}{key}")


def safe_name(table: dict[int, str], key: int) -> str:
    """Возвращает имя из таблицы или ``Unknown (ID)`` для неизвестных."""
    return table.get(key, f"Unknown ({key})")
