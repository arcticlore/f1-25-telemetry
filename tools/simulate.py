#!/usr/bin/env python3
"""Synthetic F1 25 UDP telemetry generator (packet format 2025).

This module is intentionally self-contained: packets are serialized with
``struct.pack`` directly from the spec tables (AGENTS.md section 3) and it does
NOT import ``f1telemetry``. That makes the "simulator -> parser" round-trip
test meaningful.

Examples::

    # local development
    python tools/simulate.py --laps 3 --hz 60

    # verify the network path exactly like the game would use it
    python tools/simulate.py --host 192.168.1.50 --laps 1

    # record a binary fixture without touching the network
    python tools/simulate.py --laps 1 --hz 10 --record-fixture tests/fixtures/s.bin

Fixture file format: a sequence of records ``<H length><datagram>``.
"""

from __future__ import annotations

import argparse
import math
import random
import socket
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# --- Format constants (duplicated on purpose: no f1telemetry import) ---------

PACKET_FORMAT = 2025
GAME_YEAR = 25
MAX_CARS = 22
HEADER_FMT = "<HBBBBBQfIIBB"  # 29 bytes

PLAYER_CAR_INDEX = 0

RIVAL_NAMES = [
    "A. Petrov", "L. Moreau", "K. Tanaka", "M. Oliveira", "S. Novak",
    "J. Andersen", "R. Silva", "T. Kowalski", "D. Fischer", "N. Haddad",
    "P. Ivanov", "C. Dubois", "E. Larsson", "B. Yilmaz", "F. Rossi",
    "G. Mueller", "H. Suzuki", "V. Costa", "W. Jensen", "O. Kozlov",
    "Z. Martin",
]
TEAMS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
NATIONALITIES = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 20, 21, 22]

TYRE_C3, TYRE_C2, TYRE_C1 = 18, 19, 20
VISUAL_SOFT, VISUAL_MED, VISUAL_HARD = 16, 17, 18

N_PROFILE_POINTS = 700
PIT_SPEED_KMH = 80.0
PIT_WINDOW = (0.30, 0.42)

EXPECTED_SIZES = {
    0: 1349, 1: 753, 2: 1285, 3: 45, 4: 1284, 5: 1133, 6: 1352, 7: 1239,
    8: 1042, 9: 954, 10: 1041, 11: 1460, 12: 231, 13: 273, 14: 101, 15: 1131,
}
"""Expected packet sizes, format 2025, 22 cars.

Car Damage is 1041 = 29 + 22 x 46. 46 bytes per car follow directly from the
field list (float[4] + 3 x uint8[4] + 18 x uint8). The value 1129 quoted in
AGENTS.md 3.2 would need 50 bytes per car, i.e. 4 fields that do not exist;
the same PDF gives 1133 = 29 + 24 x 46 for the 2026 Season Pack.
"""

# --- Packet serialization ----------------------------------------------------

def pack_header(
    packet_id: int,
    session_uid: int,
    session_time: float,
    frame: int,
    overall_frame: int,
    player: int = PLAYER_CAR_INDEX,
    secondary: int = 255,
) -> bytes:
    """Build the 29-byte packet header."""
    return struct.pack(
        HEADER_FMT,
        PACKET_FORMAT, GAME_YEAR, 1, 0, 1, packet_id,
        session_uid, session_time, frame, overall_frame, player, secondary,
    )


LAPDATA_FMT = (
    "<II"
    "HB" "HB" "HB" "HB"
    "fff"
    + "B" * 15
    + "HH"
    + "B"
    + "f"
    + "B"
)

CARTELEMETRY_FMT = (
    "<Hfff"
    "Bb"
    "H"
    "BB"
    "H"
    "4H"
    "4B"
    "4B"
    "H"
    "4f"
    "4B"
)

CARSTATUS_FMT = (
    "<BBBBB"
    "fff"
    "HH"
    "BB"
    "H"
    "BBB"
    "b"
    "fff"
    "B"
    "fff"
    "B"
)


def pack_motion(hdr: bytes, cars: list[CarSim]) -> bytes:
    """Packet 0 - Motion (1349 bytes)."""
    out = [hdr]
    for c in cars:
        x, y, z, vx, vy, vz, yaw = c.motion_state()
        out.append(struct.pack(
            "<6f6h6f",
            x, y, z, vx, vy, vz,
            int(32767 * math.cos(yaw)), 0, int(32767 * math.sin(yaw)),
            int(-32767 * math.sin(yaw)), 0, int(32767 * math.cos(yaw)),
            c.g_lat, c.g_lon, c.g_vert,
            yaw, 0.0, 0.0,
        ))
    data = b"".join(out)
    if len(data) != EXPECTED_SIZES[0]:
        raise AssertionError(f"motion packet is {len(data)} bytes, expected 1349")
    return data


def pack_session(
    hdr: bytes,
    *,
    weather: int,
    track_temp: int,
    air_temp: int,
    total_laps: int,
    track_length: int,
    session_type: int,
    track_id: int,
    time_left: int,
    duration: int,
    sc_status: int,
    session_link: int,
) -> bytes:
    """Packet 1 - Session (753 bytes)."""
    out = [hdr]
    out.append(struct.pack(
        "<BbbBHBbBHH",
        weather, track_temp, air_temp, total_laps, track_length,
        session_type, track_id, 0, time_left, duration,
    ))
    out.append(struct.pack("<BBBBBB", int(PIT_SPEED_KMH), 0, 0, 255, 1, 4))
    out.extend(struct.pack("<fb", 0.25, 0) for _ in range(21))
    out.append(struct.pack("<BBB", sc_status, 0, 0))
    out.extend(b"\x00" * 8 for _ in range(64))
    out.append(struct.pack("<BB", 1, 3))
    out.append(struct.pack("<III", 1, 1, session_link))
    out.append(bytes(14))
    out.append(struct.pack("<I", 36000))
    out.append(bytes(33))
    out.append(bytes(12))
    out.append(struct.pack("<ff", track_length / 3.0, 2.0 * track_length / 3.0))
    data = b"".join(out)
    if len(data) != EXPECTED_SIZES[1]:
        raise AssertionError(f"session packet is {len(data)} bytes, expected 753")
    return data


def pack_lap_data(hdr: bytes, cars: list[CarSim]) -> bytes:
    """Packet 2 - Lap Data (1285 bytes)."""
    out = [hdr]
    for c in cars:
        out.append(struct.pack(
            LAPDATA_FMT,
            c.last_lap_ms, int(c.cur_lap_s * 1000),
            c.s1_ms % 60000, c.s1_ms // 60000,
            c.s2_ms % 60000, c.s2_ms // 60000,
            c.delta_front % 60000, 0,
            c.delta_leader % 60000, 0,
            c.lap_distance, c.total_distance, 0.0,
            c.position, c.lap_num, c.pit_status, c.num_pit_stops, c.sector,
            c.lap_invalid,
            0, 0, 0, 0, 0, c.grid_position, c.driver_status, c.result_status,
            0,
            0, 0,
            0,
            c.speed_trap, 0,
        ))
    out.append(struct.pack("<BB", 255, 255))
    data = b"".join(out)
    if len(data) != EXPECTED_SIZES[2]:
        raise AssertionError(f"lap data packet is {len(data)} bytes, expected 1285")
    return data


def pack_event(hdr: bytes, code: str, details: bytes = b"") -> bytes:
    """Packet 3 - Event (45 bytes)."""
    blob = details + b"\x00" * (12 - len(details))
    data = hdr + code.encode("ascii") + blob[:12]
    if len(data) != EXPECTED_SIZES[3]:
        raise AssertionError(f"event packet is {len(data)} bytes, expected 45")
    return data


def pack_participants(hdr: bytes, cars: list[CarSim],
                      num_active: int | None = None) -> bytes:
    """Packet 4 - Participants (1284 bytes).

    Args:
        cars: массив из ровно 22 машин (как в игре).
        num_active: значение ``m_numActiveCars`` (по умолчанию — ``len(cars)``).
    """
    active = len(cars) if num_active is None else int(num_active)
    out = [hdr, struct.pack("<B", active)]
    for c in cars:
        out.append(struct.pack(
            "<BBBBBBB32sBBHBB" + "12B",
            c.ai, c.driver_id, c.network_id, c.team_id, 0,
            c.race_number, c.nationality,
            c.name.encode("utf-8")[:31].ljust(31, b"\x00"),
            1, 1, 2, 1,
            4,
            c.team_id % 256, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        ))
    data = b"".join(out)
    if len(data) != EXPECTED_SIZES[4]:
        raise AssertionError(f"participants packet is {len(data)} bytes, expected 1284")
    return data


def pack_setup(hdr: bytes, cars: list[CarSim]) -> bytes:
    """Packet 5 - Car Setups (1133 bytes)."""
    out = [hdr]
    for _c in cars:
        out.append(struct.pack(
            "<BBBBffffBBBBBBBBBffffBf",
            8, 12, 50, 50, -3.0, -1.5, 0.05, 0.15,
            6, 7, 4, 5, 8, 9, 70, 56, 3,
            21.5, 21.3, 22.0, 21.9,
            7, 100.0,
        ))
    out.append(struct.pack("<f", 8.5))
    data = b"".join(out)
    if len(data) != EXPECTED_SIZES[5]:
        raise AssertionError(f"setup packet is {len(data)} bytes, expected 1133")
    return data


def pack_telemetry(hdr: bytes, cars: list[CarSim], suggested_gear: int) -> bytes:
    """Packet 6 - Car Telemetry (1352 bytes)."""
    out = [hdr]
    for c in cars:
        rev_bits = 0
        for seg in range(15):
            if c.rev_fraction * 15 >= (14 - seg):
                rev_bits |= 1 << seg
        out.append(struct.pack(
            CARTELEMETRY_FMT,
            int(c.speed_kmh), c.throttle, c.steer, c.brake,
            0, c.gear, int(c.rpm), c.drs, int(c.rev_fraction * 100), rev_bits,
            c.brake_temp[0], c.brake_temp[1], c.brake_temp[2], c.brake_temp[3],
            c.tyre_surf[0], c.tyre_surf[1], c.tyre_surf[2], c.tyre_surf[3],
            c.tyre_inner[0], c.tyre_inner[1], c.tyre_inner[2], c.tyre_inner[3],
            int(c.engine_temp),
            c.tyre_press[0], c.tyre_press[1], c.tyre_press[2], c.tyre_press[3],
            c.surface[0], c.surface[1], c.surface[2], c.surface[3],
        ))
    out.append(struct.pack("<BBb", 255, 255, suggested_gear))
    data = b"".join(out)
    if len(data) != EXPECTED_SIZES[6]:
        raise AssertionError(f"telemetry packet is {len(data)} bytes, expected 1352")
    return data


def pack_status(hdr: bytes, cars: list[CarSim]) -> bytes:
    """Packet 7 - Car Status (1239 bytes)."""
    out = [hdr]
    for c in cars:
        out.append(struct.pack(
            CARSTATUS_FMT,
            1, 1, 0, 56, c.pit_limiter,
            c.fuel_kg, 110.0, c.fuel_remaining_laps,
            15000, 4000, 8, c.drs_allowed, 0,
            c.compound_actual, c.compound_visual, c.tyre_age, 0,
            500000.0, 120000.0, c.ers_store, c.ers_mode,
            1000.0, 2000.0, c.ers_deployed,
            0,
        ))
    data = b"".join(out)
    if len(data) != EXPECTED_SIZES[7]:
        raise AssertionError(f"status packet is {len(data)} bytes, expected 1239")
    return data


def pack_final(hdr: bytes, cars: list[CarSim]) -> bytes:
    """Packet 8 - Final Classification (1042 bytes)."""
    out = [hdr, struct.pack("<B", len(cars))]
    for c in sorted(cars, key=lambda x: x.position):
        out.append(struct.pack(
            "<BBBBBBBIdBBB" + "24B",
            c.position, max(0, c.lap_num - 1), c.grid_position,
            max(0, 25 - c.position), c.num_pit_stops, 3, 2,
            c.best_lap_ms, c.total_race_time,
            0, 0, len(c.stints),
            *_pad8([s[0] for s in c.stints]),
            *_pad8([s[1] for s in c.stints]),
            *_pad8([s[2] for s in c.stints]),
        ))
    data = b"".join(out)
    if len(data) != EXPECTED_SIZES[8]:
        raise AssertionError(f"final packet is {len(data)} bytes, expected 1042")
    return data


def _pad8(values: list[int]) -> list[int]:
    return (list(values) + [0] * 8)[:8]


def pack_damage(hdr: bytes, cars: list[CarSim]) -> bytes:
    """Packet 10 - Car Damage (1041 bytes = 29 + 22 x 46).

    CarDamageData layout: float tyresWear[4] (16) + u8 tyresDamage[4] +
    u8 brakesDamage[4] + u8 tyreBlisters[4] (12) + 18 x u8 = 46.
    """
    out = [hdr]
    for c in cars:
        out.append(struct.pack(
            "<4f30B",
            c.tyre_wear[0], c.tyre_wear[1], c.tyre_wear[2], c.tyre_wear[3],
            *[min(100, int(w)) for w in c.tyre_wear],
            0, 0, 0, 0,
            0, 0, 0, 0,
            0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
            c.engine_ice_wear, 0, 0, 0, 0,
        ))
    data = b"".join(out)
    if len(data) != EXPECTED_SIZES[10]:
        raise AssertionError(f"damage packet is {len(data)} bytes, expected 1041")
    return data


def pack_lobby(hdr: bytes, cars: list[CarSim]) -> bytes:
    """Packet 9 - Lobby Info (954 bytes)."""
    out = [hdr, struct.pack("<B", len(cars))]
    for c in cars:
        out.append(struct.pack(
            "<BBBB32sBBBHB",
            c.ai, c.team_id, c.nationality, 1,
            c.name.encode("utf-8")[:31].ljust(31, b"\x00"),
            c.race_number, 1, 1, 2, 1,
        ))
    data = b"".join(out)
    if len(data) != EXPECTED_SIZES[9]:
        raise AssertionError(f"lobby packet is {len(data)} bytes, expected 954")
    return data


def pack_session_history(hdr: bytes, car: CarSim) -> bytes:
    """Packet 11 - Session History (1460 bytes)."""
    laps = []
    for rec in car.history[:100]:
        laps.append(struct.pack(
            "<IHBHBHBB",
            rec["lap_ms"],
            rec["s1"] % 60000, rec["s1"] // 60000,
            rec["s2"] % 60000, rec["s2"] // 60000,
            rec["s3"] % 60000, rec["s3"] // 60000,
            rec["flags"],
        ))
    laps.extend(b"\x00" * 14 for _ in range(100 - len(laps)))
    stints = [struct.pack("<BBB", s[2], s[0], s[1]) for s in car.stints[:8]]
    stints.extend(b"\x00\x00\x00" for _ in range(8 - len(stints)))
    return b"".join([
        hdr,
        struct.pack(
            "<BBBBBBB",
            car.idx, min(len(car.history), 100), min(len(car.stints), 8),
            car.best_lap_num, 1, 1, 1,
        ),
        *laps, *stints,
    ])


def pack_tyre_sets(hdr: bytes, car: CarSim) -> bytes:
    """Packet 12 - Tyre Sets (231 bytes)."""
    out = [hdr, struct.pack("<B", car.idx)]
    for i in range(20):
        compound = [TYRE_C3, TYRE_C2, TYRE_C1][i % 3]
        visual = [VISUAL_SOFT, VISUAL_MED, VISUAL_HARD][i % 3]
        out.append(struct.pack(
            "<BBBBBBBhB",
            compound, visual, 0, 1, 0, 25, 20, -120, 1 if i == 0 else 0,
        ))
    out.append(struct.pack("<B", 0))
    data = b"".join(out)
    if len(data) != EXPECTED_SIZES[12]:
        raise AssertionError(f"tyre sets packet is {len(data)} bytes, expected 231")
    return data


MOTION_EX_FMT = "<" + "f" * 61


def pack_motion_ex(hdr: bytes, cars: list[CarSim]) -> bytes:
    """Packet 13 - Motion Ex (273 bytes).

    Format 2025 carries extended physics for the player car only:
    29 + 61 x 4 = 273 (no per-car array).
    """
    del cars
    data = hdr + struct.pack(
        MOTION_EX_FMT,
        0.02, 0.02, 0.02, 0.02,
        0.1, 0.1, 0.1, 0.1,
        1.0, 1.0, 1.0, 1.0,
        80.0, 80.0, 80.0, 80.0,
        0.05, 0.05, 0.04, 0.04,
        0.01, -0.01, 0.0, 0.0,
        1200.0, 1200.0, 1000.0, 1000.0,
        3000.0, 3000.0, 3000.0, 3000.0,
        0.28,
        0.0, 80.0, 0.0,
        0.0, 0.0, 0.05,
        0.0, 0.0, 0.01,
        0.02,
        4000.0, 4000.0, 3800.0, 3800.0,
        0.045, 0.095,
        0.01, 0.01,
        0.0, 0.0,
        -0.02, -0.02, -0.01, -0.01,
        -0.005, -0.005, -0.002, -0.002,
    )
    if len(data) != EXPECTED_SIZES[13]:
        raise AssertionError(f"motion ex packet is {len(data)} bytes, expected 273")
    return data


def pack_time_trial(hdr: bytes, cars: list[CarSim]) -> bytes:
    """Packet 14 - Time Trial (101 bytes)."""
    best = min(
        (c for c in cars if c.best_lap_ms),
        key=lambda c: c.best_lap_ms,
        default=cars[PLAYER_CAR_INDEX],
    )

    def _set(car: CarSim) -> bytes:
        return struct.pack(
            "<BBIIII6B",
            car.idx, car.team_id,
            car.best_lap_ms, car.s1_ms, car.s2_ms, car.s3_ms,
            1, 0, 1, 1, 0, 1,
        )

    data = b"".join([hdr, _set(best), _set(best), _set(cars[PLAYER_CAR_INDEX])])
    if len(data) != EXPECTED_SIZES[14]:
        raise AssertionError(f"time trial packet is {len(data)} bytes, expected 101")
    return data


def pack_lap_positions(
    hdr: bytes, cars: list[CarSim], history: list[list[int]]
) -> bytes:
    """Packet 15 - Lap Positions (1131 bytes)."""
    del cars
    grid = [[0] * MAX_CARS for _ in range(50)]
    for lap_idx, positions in enumerate(history[:50]):
        for car_idx, pos in enumerate(positions[:MAX_CARS]):
            grid[lap_idx][car_idx] = pos
    flat = bytes(p for row in grid for p in row)
    data = hdr + struct.pack("<BB", min(len(history), 50), 0) + flat
    if len(data) != EXPECTED_SIZES[15]:
        raise AssertionError(f"lap positions packet is {len(data)} bytes, expected 1131")
    return data


# --- Lap profile -------------------------------------------------------------

@dataclass
class Profile:
    """Speed profile of one lap: 700 samples along the distance."""

    distance: list[float]
    speed_kmh: list[float]
    time_s: list[float]
    lap_time_s: float
    s1_s: float
    s2_s: float
    s3_s: float


def build_profile(
    track_length: float,
    corners: list[tuple[float, float, float]],
    top_speed: float,
    accel: float = 11.0,
    brake: float = 42.0,
) -> Profile:
    """Build a realistic lap profile (acceleration, braking, apexes).

    Args:
        track_length: lap length in metres.
        corners: list of ``(centre_fraction, min_speed_kmh, half_width_fraction)``.
        top_speed: top speed in km/h.
        accel: acceleration in m/s^2.
        brake: deceleration in m/s^2.

    Returns:
        The computed :class:`Profile`.
    """
    n = N_PROFILE_POINTS
    ds = track_length / n
    v_lim = [float(top_speed)] * n
    for center, min_v, half_w in corners:
        for i in range(n):
            frac = i / n
            d = abs(frac - center)
            d = min(d, 1.0 - d)
            if d <= half_w:
                t = (d / half_w) if half_w > 0 else 0.0
                shape = math.sin(max(0.0, t) * math.pi / 2.0) ** 0.6
                v_lim[i] = min(v_lim[i], min_v + (top_speed - min_v) * shape)

    v = list(v_lim)
    for _ in range(2):
        for i in range(n - 1, -1, -1):
            j = (i + 1) % n
            v[i] = min(v[i], math.sqrt((v[j] / 3.6) ** 2 + 2 * brake * ds) * 3.6)
    for _ in range(2):
        for i in range(n):
            j = (i - 1) % n
            v[i] = min(v[i], math.sqrt((v[j] / 3.6) ** 2 + 2 * accel * ds) * 3.6)

    times = [0.0]
    for i in range(n):
        times.append(times[-1] + ds / max(1.0, v[i] / 3.6))
    third = n // 3
    return Profile(
        distance=[i * ds for i in range(n)],
        speed_kmh=v,
        time_s=times[:n],
        lap_time_s=times[n],
        s1_s=times[third],
        s2_s=times[2 * third] - times[third],
        s3_s=times[n] - times[2 * third],
    )


def default_corners() -> list[tuple[float, float, float]]:
    """Generic corner set for the synthetic track."""
    return [
        (0.03, 95, 0.025),
        (0.11, 180, 0.02),
        (0.19, 85, 0.03),
        (0.28, 210, 0.02),
        (0.36, 140, 0.02),
        (0.44, 78, 0.03),
        (0.52, 195, 0.02),
        (0.61, 120, 0.02),
        (0.70, 240, 0.015),
        (0.79, 90, 0.03),
        (0.88, 160, 0.02),
        (0.95, 200, 0.02),
    ]


GEAR_BREAKS = [(0, 1), (95, 2), (135, 3), (175, 4), (215, 5), (255, 6), (295, 7), (325, 8)]
GEAR_RANGES = {
    1: (40, 100), 2: (90, 140), 3: (130, 180), 4: (170, 220),
    5: (210, 260), 6: (250, 300), 7: (290, 330), 8: (320, 360),
}


def gear_for_speed(speed: float) -> int:
    """Gear selection from speed in km/h."""
    g = 1
    for limit, gear in GEAR_BREAKS:
        if speed >= limit:
            g = gear
    return g


def rpm_for(speed: float, gear: int) -> float:
    """Engine RPM for a speed/gear pair."""
    lo, hi = GEAR_RANGES[gear]
    frac = (speed - lo) / max(1.0, hi - lo)
    return max(4000.0, min(15000.0, 7500.0 + frac * 7500.0))


# --- One simulated car -------------------------------------------------------

@dataclass
class CarSim:
    """State of one synthetic car."""

    idx: int
    name: str
    team_id: int
    race_number: int
    nationality: int
    ai: int
    network_id: int
    driver_id: int
    pace: float
    track_length: float
    active: bool = True   # False = не входит в m_numActiveCars (stands in garage)

    lap_num: int = 1
    lap_start_t: float = 0.0
    position: int = 1
    grid_position: int = 1
    lap_distance: float = 0.0
    total_distance: float = 0.0
    sector: int = 1
    driver_status: int = 4
    result_status: int = 2
    last_lap_ms: int = 0
    cur_lap_s: float = 0.0
    best_lap_ms: int = 0
    best_lap_num: int = 0
    s1_ms: int = 0
    s2_ms: int = 0
    s3_ms: int = 0
    delta_front: int = 0
    delta_leader: int = 0
    lap_invalid: int = 0
    pit_status: int = 0
    pit_limiter: int = 0
    num_pit_stops: int = 0
    speed_trap: float = 0.0
    total_race_time: float = 0.0

    speed_kmh: float = 0.0
    throttle: float = 0.0
    brake: float = 0.0
    steer: float = 0.0
    gear: int = 1
    rpm: float = 4000.0
    rev_fraction: float = 0.0
    drs: int = 0
    drs_allowed: int = 0
    g_lat: float = 0.0
    g_lon: float = 0.0
    g_vert: float = 0.0
    engine_temp: float = 105.0
    engine_ice_wear: int = 5

    compound_actual: int = TYRE_C3
    compound_visual: int = VISUAL_SOFT
    tyre_age: int = 0
    fuel_kg: float = 100.0
    fuel_remaining_laps: float = 30.0
    ers_store: float = 4.0e6
    ers_mode: int = 1
    ers_deployed: float = 0.0
    stints: list[tuple[int, int, int]] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)

    tyre_wear: list[float] = field(default_factory=lambda: [0.0] * 4)
    tyre_surf: list[int] = field(default_factory=lambda: [85] * 4)
    tyre_inner: list[int] = field(default_factory=lambda: [82] * 4)
    tyre_press: list[float] = field(default_factory=lambda: [21.7] * 4)
    brake_temp: list[int] = field(default_factory=lambda: [420] * 4)
    surface: list[int] = field(default_factory=lambda: [0] * 4)

    in_pit_window: bool = False
    s1_recorded: float | None = None
    s2_recorded: float | None = None

    def pace_eff(self) -> float:
        """Effective pace: requested pace scaled by tyre degradation."""
        return self.pace * (1.0 - 0.0009 * max(0, self.tyre_age - 1))

    def motion_state(self) -> tuple[float, float, float, float, float, float, float]:
        """Position/velocity/yaw for the Motion packet (synthetic oval)."""
        frac = (self.lap_distance / self.track_length) % 1.0 if self.track_length else 0.0
        ang = frac * 2 * math.pi
        r = self.track_length / (2 * math.pi)
        v = self.speed_kmh / 3.6
        yaw = ang + math.pi / 2
        return (
            r * math.cos(ang), r * math.sin(ang), 0.0,
            -v * math.sin(ang), v * math.cos(ang), 0.0,
            yaw,
        )


# --- Simulator ---------------------------------------------------------------

@dataclass
class SimConfig:
    """Simulation parameters.

    Attributes:
        cars: число активных машин (``m_numActiveCars``). Пакеты всегда
            собираются на 22 машины, как в игре; неактивные стоят в боксе.
        secondary_car: индекс второго игрока для splitscreen (255 = нет второго).
    """

    host: str = "127.0.0.1"
    port: int = 20777
    hz: float = 60.0
    laps: int = 3
    cars: int = 22
    secondary_car: int = 255
    session_type: int = 15
    track_id: int = 7
    track_length: float = 5000.0
    seed: int = 1
    session_uid: int = 0
    pit_laps: tuple[int, ...] = ()
    safety_car_lap: int | None = None
    fixture: Path | None = None
    loop_forever: bool = False
    verbose: bool = True


class Simulator:
    """Generator and sender of a synthetic F1 25 UDP stream."""

    def __init__(self, cfg: SimConfig) -> None:
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self.frame = 0
        self.t = 0.0
        self.profile = build_profile(cfg.track_length, default_corners(), 332.0)
        self.session_link = 1000 + (cfg.seed % 1000)
        self.sc_status = 0
        self.best_lap_ms = 0
        self.best_car: CarSim | None = None
        self.lap_positions_history: list[list[int]] = []
        self.packets_sent = 0
        self.pending_events: list[bytes] = []
        self.sock: socket.socket | None = None
        self.fixture_fh = None
        self.cars: list[CarSim] = []
        self._make_cars()
        self.active_cars = max(1, min(cfg.cars, MAX_CARS))

    def _make_cars(self) -> None:
        """Создаёт всегда MAX_CARS машин; активные — первые ``cfg.cars``."""
        active = max(1, min(self.cfg.cars, MAX_CARS))
        for i in range(MAX_CARS):
            pace = 1.0 - self.rng.uniform(0.0, 0.03) - i * 0.001
            car = CarSim(
                idx=i,
                name="Player One" if i == PLAYER_CAR_INDEX
                else RIVAL_NAMES[(i - 1) % len(RIVAL_NAMES)],
                team_id=TEAMS[i % len(TEAMS)],
                race_number=(4 if i == PLAYER_CAR_INDEX else 10 + i * 3) % 100,
                nationality=NATIONALITIES[i % len(NATIONALITIES)],
                ai=0 if i == PLAYER_CAR_INDEX else 1,
                network_id=i,
                driver_id=i,
                pace=pace,
                track_length=self.cfg.track_length,
                active=i < active,
            )
            if not car.active:
                car.result_status = 1   # inactive
                car.driver_status = 0   # in garage
            car.stints.append((TYRE_C3, VISUAL_SOFT, 0))
            self.cars.append(car)

    def _speed_at(self, car: CarSim, distance: float) -> tuple[float, float, float]:
        """Speed, throttle and brake at a point of the lap (pit lane included)."""
        prof = self.profile
        n = len(prof.speed_kmh)
        frac = (distance / self.cfg.track_length) % 1.0
        idx = min(n - 1, int(frac * n))
        nxt = (idx + 1) % n
        ds = self.cfg.track_length / n
        a = ((prof.speed_kmh[nxt] / 3.6) ** 2 - (prof.speed_kmh[idx] / 3.6) ** 2) / (2 * ds)
        throttle = max(0.0, min(1.0, a / 11.0))
        brake = max(0.0, min(1.0, -a / 42.0))
        speed = prof.speed_kmh[idx] * car.pace_eff()
        if car.in_pit_window:
            speed = min(speed, PIT_SPEED_KMH)
            throttle = 0.0
            brake = 0.05
        return speed, throttle, brake

    def _is_pit_lap(self, car: CarSim) -> bool:
        return car.idx == PLAYER_CAR_INDEX and car.lap_num in self.cfg.pit_laps

    def tick(self) -> list[bytes]:
        """Return the datagrams that should be sent on this tick."""
        cfg = self.cfg
        dt = 1.0 / cfg.hz
        self.t += dt
        self.frame += 1
        hz = cfg.hz
        n_prof = len(self.profile.speed_kmh)

        for car in self.cars:
            if not car.active:
                # неактивная машина стоит в боксе: без физики и кругов
                car.speed_kmh = 0.0
                car.throttle = 0.0
                car.brake = 0.0
                car.gear = 0
                car.rpm = 800
                car.rev_fraction = 0.0
                car.steer = 0.0
                car.drs = 0
                car.drs_allowed = 0
                car.g_lat = car.g_lon = car.g_vert = 0.0
                car.result_status = 1
                car.driver_status = 0
                car.total_distance = 0.0
                continue
            car.pit_status = 0
            car.in_pit_window = False
            car.pit_limiter = 0
            if self._is_pit_lap(car):
                lo, hi = (x * cfg.track_length for x in PIT_WINDOW)
                car.in_pit_window = lo <= car.lap_distance < hi
                if car.in_pit_window:
                    car.pit_status = 2
                    car.pit_limiter = 1

            prev_distance = car.lap_distance
            speed, throttle, brake = self._speed_at(car, car.lap_distance)
            car.lap_distance += (speed / 3.6) * dt
            car.cur_lap_s = self.t - car.lap_start_t

            third = cfg.track_length / 3.0
            if prev_distance < third <= car.lap_distance:
                car.s1_recorded = car.cur_lap_s
            if prev_distance < 2 * third <= car.lap_distance:
                car.s2_recorded = car.cur_lap_s

            if car.lap_distance >= cfg.track_length:
                self._complete_lap(car)
                car.lap_distance -= cfg.track_length
                speed, throttle, brake = self._speed_at(car, car.lap_distance)

            car.total_distance = (car.lap_num - 1) * cfg.track_length + car.lap_distance
            frac = (car.lap_distance / cfg.track_length) % 1.0
            third_f = 1.0 / 3.0
            car.sector = 1 if frac < third_f else (2 if frac < 2 * third_f else 3)

            idx = min(n_prof - 1, int(frac * n_prof))
            prev_speed = self.profile.speed_kmh[idx] * car.pace_eff()

            car.speed_kmh = speed
            car.throttle = throttle
            car.brake = brake
            car.gear = gear_for_speed(speed)
            car.rpm = rpm_for(speed, car.gear)
            car.rev_fraction = max(0.0, min(1.0, (car.rpm - 8000.0) / 7000.0))
            car.steer = max(-1.0, min(1.0, (speed / 330.0) * math.sin(car.lap_distance / 200.0)))
            car.g_lon = max(-4.5, min(4.5, ((speed - prev_speed) / 3.6) * hz / 9.81))
            car.g_lat = max(-4.0, min(4.0, (speed / 330.0) * 2.2 * math.sin(car.lap_distance / 90.0)))
            car.drs_allowed = 1 if car.lap_num >= 2 else 0
            car.drs = 1 if (throttle > 0.9 and speed > 250 and car.drs_allowed) else 0
            car.speed_trap = max(car.speed_trap, speed)
            car.engine_temp = 100.0 + 12.0 * throttle
            for w in range(4):
                car.tyre_wear[w] = min(100.0, car.tyre_age * 1.6 + frac * 1.6 + w * 0.1)
            heat = 80.0 + 18.0 * throttle - 6.0 * brake
            car.tyre_surf = [int(max(55, min(130, heat + d))) for d in (2, 1, -2, -3)]
            car.tyre_inner = [int(max(55, min(120, heat - 4 + d))) for d in (1, 0, -2, -2)]
            car.surface = [1 if (car.lap_distance + k * 13.0) % 180.0 < 4.0 else 0 for k in range(4)]

        ordered = sorted(self.cars, key=lambda c: c.total_distance, reverse=True)
        for pos, car in enumerate(ordered, start=1):
            car.position = pos
        for i, car in enumerate(ordered):
            v_ms = max(1.0, car.speed_kmh / 3.6)
            car.delta_leader = int((ordered[0].total_distance - car.total_distance) / v_ms * 1000)
            car.delta_front = (
                int((ordered[i + 1].total_distance - car.total_distance) / v_ms * 1000)
                if i + 1 < len(ordered) else 0
            )

        player = self.cars[PLAYER_CAR_INDEX]
        out: list[bytes] = []
        if self.frame == 1 or self.frame % max(1, int(round(hz / 2))) == 0:
            out.append(self._session_packet())
            out.append(pack_setup(self._hdr(5), self.cars))
        if self.frame == 1 or self.frame % max(1, int(round(hz / 5))) == 0:
            out.append(pack_participants(
                self._hdr(4), self.cars, num_active=self.active_cars))
            out.append(pack_lobby(self._hdr(9), self.cars))
        if self.frame == 1 or self.frame % max(1, int(round(hz / 10))) == 0:
            out.append(pack_damage(self._hdr(10), self.cars))

        out.append(pack_motion(self._hdr(0), self.cars))
        out.append(pack_motion_ex(self._hdr(13), self.cars))
        out.append(pack_lap_data(self._hdr(2), self.cars))
        out.append(pack_telemetry(self._hdr(6), self.cars, suggested_gear=player.gear + 1))
        out.append(pack_status(self._hdr(7), self.cars))

        if self.frame % max(1, int(hz)) == 0:
            out.append(pack_session_history(self._hdr(11), player))
            out.append(pack_tyre_sets(self._hdr(12), player))
            out.append(pack_lap_positions(self._hdr(15), self.cars, self.lap_positions_history))
            out.append(pack_time_trial(self._hdr(14), self.cars))

        out.extend(self._timed_events())
        if self.pending_events:
            out.extend(self.pending_events)
            self.pending_events = []
        return out

    def _hdr(self, packet_id: int) -> bytes:
        return pack_header(
            packet_id, self.cfg.session_uid, self.t, self.frame, self.frame * 3,
            secondary=self.cfg.secondary_car,
        )

    def _session_packet(self) -> bytes:
        cfg = self.cfg
        duration = int(self.profile.lap_time_s * cfg.laps * 1.5) + 60
        return pack_session(
            self._hdr(1),
            weather=2,
            track_temp=38,
            air_temp=24,
            total_laps=cfg.laps if cfg.session_type in (15, 16, 17) else 0,
            track_length=int(cfg.track_length),
            session_type=cfg.session_type,
            track_id=cfg.track_id,
            time_left=max(0, duration - int(self.t)),
            duration=duration,
            sc_status=self.sc_status,
            session_link=self.session_link,
        )

    def _timed_events(self) -> list[bytes]:
        cfg = self.cfg
        dt = 1.0 / cfg.hz
        events: list[bytes] = []

        def near(sec: float) -> bool:
            return abs(self.t - sec) < dt / 2

        if near(0.5):
            events.append(pack_event(self._hdr(3), "SSTA"))
        for i, sec in enumerate((1.0, 1.5, 2.0, 2.5, 3.0), start=1):
            if near(sec):
                events.append(pack_event(self._hdr(3), "STLG", struct.pack("<B", i)))
        if near(3.5):
            events.append(pack_event(self._hdr(3), "LGOT"))
        if cfg.safety_car_lap is not None and self.sc_status == 0:
            if any(c.lap_num == cfg.safety_car_lap for c in self.cars):
                self.sc_status = 1
                events.append(pack_event(self._hdr(3), "SCAR", struct.pack("<BB", 1, 0)))
        return events

    def _complete_lap(self, car: CarSim) -> None:
        lap_s = max(0.001, car.cur_lap_s)
        s1 = car.s1_recorded if car.s1_recorded is not None else lap_s / 3
        s2 = (car.s2_recorded - s1) if car.s2_recorded is not None else lap_s / 3
        s3 = lap_s - (car.s2_recorded if car.s2_recorded is not None else 2 * lap_s / 3)
        lap_ms = max(1, int(lap_s * 1000))
        s1_ms = int(s1 * 1000)
        s2_ms = int(s2 * 1000)
        s3_ms = max(0, lap_ms - s1_ms - s2_ms)
        car.s1_ms, car.s2_ms, car.s3_ms = s1_ms, s2_ms, s3_ms
        car.last_lap_ms = lap_ms
        car.history.append({
            "lap": car.lap_num, "lap_ms": lap_ms,
            "s1": s1_ms, "s2": s2_ms, "s3": s3_ms, "flags": 0x0F,
        })
        if car.best_lap_ms == 0 or lap_ms < car.best_lap_ms:
            car.best_lap_ms = lap_ms
            car.best_lap_num = car.lap_num
        if self.best_lap_ms == 0 or lap_ms < self.best_lap_ms:
            self.best_lap_ms = lap_ms
            self.best_car = car
            self.pending_events.append(pack_event(
                self._hdr(3), "FTLP", struct.pack("<Bf", car.idx, lap_ms / 1000.0)
            ))
        car.total_race_time += lap_s
        self.lap_positions_history.append([c.position for c in self.cars])

        car.lap_num += 1
        car.lap_start_t = self.t
        car.cur_lap_s = 0.0
        car.s1_recorded = None
        car.s2_recorded = None
        car.speed_trap = 0.0
        car.tyre_age += 1
        car.fuel_kg = max(0.0, car.fuel_kg - 1.4)
        car.fuel_remaining_laps = car.fuel_kg / 1.4
        car.ers_deployed = 0.0

        if car.idx == PLAYER_CAR_INDEX and car.lap_num in self.cfg.pit_laps:
            car.num_pit_stops += 1
            car.tyre_age = 0
            car.tyre_wear = [0.0] * 4
            if len(car.stints) == 1:
                car.compound_actual, car.compound_visual = TYRE_C2, VISUAL_MED
                car.stints.append((TYRE_C2, VISUAL_MED, car.lap_num - 1))
            elif len(car.stints) == 2:
                car.compound_actual, car.compound_visual = TYRE_C1, VISUAL_HARD
                car.stints.append((TYRE_C1, VISUAL_HARD, car.lap_num - 1))

    def open(self) -> None:
        """Open the UDP socket and/or the fixture file."""
        if self.cfg.fixture is not None:
            self.cfg.fixture.parent.mkdir(parents=True, exist_ok=True)
            self.fixture_fh = self.cfg.fixture.open("wb")
        else:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)

    def send(self, datagrams: list[bytes]) -> None:
        """Send datagrams over UDP and/or append them to the fixture file."""
        if self.fixture_fh is not None:
            for d in datagrams:
                self.fixture_fh.write(struct.pack("<H", len(d)))
                self.fixture_fh.write(d)
        if self.sock is not None:
            addr = (self.cfg.host, self.cfg.port)
            for d in datagrams:
                self.sock.sendto(d, addr)
        self.packets_sent += len(datagrams)

    def close(self) -> None:
        """Close the socket and the fixture file."""
        if self.fixture_fh is not None:
            self.fixture_fh.close()
            self.fixture_fh = None
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def run(self, seconds: float | None = None) -> None:
        """Main loop: generate and send packets."""
        cfg = self.cfg
        period = 1.0 / cfg.hz
        if seconds is None:
            seconds = self.profile.lap_time_s * cfg.laps + 15.0
        start_wall = time.perf_counter()
        self.open()
        try:
            while self.t < seconds:
                self.send(self.tick())
                if self.sock is not None and not cfg.loop_forever:
                    delay = (start_wall + self.t + period) - time.perf_counter()
                    if delay > 0:
                        time.sleep(delay)
        except KeyboardInterrupt:
            pass
        finally:
            if self.fixture_fh is not None or self.sock is not None:
                self.send([
                    pack_event(self._hdr(3), "CHQF"),
                    pack_final(self._hdr(8), self.cars),
                    pack_event(self._hdr(3), "SEND"),
                ])
            self.close()
        if self.cfg.verbose:
            print(
                f"simulator: sent {self.packets_sent} packets in {self.t:.1f} s "
                f"(laps={self.cfg.laps}, cars={len(self.cars)}, "
                f"base lap {self.profile.lap_time_s:.2f} s)"
            )


def verify_sizes(verbose: bool = True) -> list[str]:
    """Build one packet of every type and compare sizes with the spec.

    Returns:
        A list of error strings (empty when everything matches).
    """
    sim = Simulator(SimConfig(cars=22, laps=1, hz=60, seed=1, verbose=False))
    for car in sim.cars:
        car.best_lap_ms = 91000
        car.history.append({
            "lap": 1, "lap_ms": 91000,
            "s1": 30000, "s2": 31000, "s3": 30000, "flags": 0x0F,
        })
    packets = {
        0: pack_motion(sim._hdr(0), sim.cars),
        1: sim._session_packet(),
        2: pack_lap_data(sim._hdr(2), sim.cars),
        3: pack_event(sim._hdr(3), "SSTA"),
        4: pack_participants(sim._hdr(4), sim.cars),
        5: pack_setup(sim._hdr(5), sim.cars),
        6: pack_telemetry(sim._hdr(6), sim.cars, 5),
        7: pack_status(sim._hdr(7), sim.cars),
        8: pack_final(sim._hdr(8), sim.cars),
        9: pack_lobby(sim._hdr(9), sim.cars),
        10: pack_damage(sim._hdr(10), sim.cars),
        11: pack_session_history(sim._hdr(11), sim.cars[0]),
        12: pack_tyre_sets(sim._hdr(12), sim.cars[0]),
        13: pack_motion_ex(sim._hdr(13), sim.cars),
        14: pack_time_trial(sim._hdr(14), sim.cars),
        15: pack_lap_positions(sim._hdr(15), sim.cars, [[1] * 22]),
    }
    errors = [
        f"packet {pid}: got {len(data)}, expected {exp}"
        for pid, data in sorted(packets.items())
        if len(data) != EXPECTED_SIZES[pid]
    ]
    if verbose:
        if errors:
            print("size check FAILED:")
            for e in errors:
                print(" ", e)
        else:
            print("size check OK: all 16 packets match the F1 25 spec")
    return errors


# --- CLI ---------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    """Command line arguments."""
    p = argparse.ArgumentParser(description="F1 25 synthetic UDP telemetry generator")
    p.add_argument("--host", default="127.0.0.1",
                   help="destination host (LAN address of the receiver)")
    p.add_argument("--port", type=int, default=20777)
    p.add_argument("--hz", type=float, default=60.0, help="packet rate (20/30/60)")
    p.add_argument("--laps", type=int, default=3)
    p.add_argument("--cars", type=int, default=22,
                   help="активных машин (m_numActiveCars); пакеты всегда на 22")
    p.add_argument("--secondary-car", type=int, default=255,
                   help="индекс второго игрока (splitscreen, 255 = нет второго)")
    p.add_argument("--session-type", type=int, default=15)
    p.add_argument("--track-id", type=int, default=7)
    p.add_argument("--track-length", type=float, default=5000.0, help="metres")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--session-uid", type=int, default=None)
    p.add_argument("--pit-laps", default="", help="comma separated pit stop laps")
    p.add_argument("--safety-car-lap", type=int, default=None)
    p.add_argument("--loop", action="store_true", help="do not stop on the timer")
    p.add_argument("--record-fixture", type=Path, default=None)
    p.add_argument("--verify-sizes", action="store_true",
                   help="check sizes of all 16 packets and exit")
    p.add_argument("--quiet", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_arg_parser().parse_args(argv)
    if args.verify_sizes:
        return 1 if verify_sizes(verbose=not args.quiet) else 0

    pit_laps = tuple(int(x) for x in str(args.pit_laps).split(",") if x.strip())
    cfg = SimConfig(
        host=args.host,
        port=args.port,
        hz=args.hz,
        laps=args.laps,
        cars=args.cars,
        secondary_car=args.secondary_car,
        session_type=args.session_type,
        track_id=args.track_id,
        track_length=args.track_length,
        seed=args.seed,
        session_uid=args.session_uid if args.session_uid is not None
        else 1_700_000_000_000_000_000 + args.seed,
        pit_laps=pit_laps,
        safety_car_lap=args.safety_car_lap,
        fixture=args.record_fixture,
        loop_forever=args.loop,
        verbose=not args.quiet,
    )
    sim = Simulator(cfg)
    if not args.quiet:
        errors = verify_sizes(verbose=False)
        if errors:
            print("internal size check failed:", *errors, sep="\n  ", file=sys.stderr)
            return 2
    seconds = sim.profile.lap_time_s * cfg.laps + 10.0 if cfg.fixture else None
    sim.run(seconds=seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
