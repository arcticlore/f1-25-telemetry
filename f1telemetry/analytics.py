"""Производные метрики: дельты кругов, деградация шин, топливо, темп.

Только stdlib, без Qt и без numpy. Все функции чистые и тестируемые.

Единицы измерения: миллисекунды (времена), метры (дистанция), км/ч (скорость),
секунды (дельты), мс/круг (деградация), кг (топливо), Дж (ERS).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

__all__ = [
    "DistanceSample",
    "theoretical_best_lap",
    "best_sectors",
    "delta_curve",
    "delta_at",
    "linear_regression",
    "stint_degradation",
    "stint_summary",
    "fuel_per_lap",
    "lap_aggregates",
    "positions_by_lap",
    "lap_time_histogram",
    "mean",
]


# --- Базовые выборки ---------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DistanceSample:
    """Одна точка круга для сравнения по дистанции.

    Attributes:
        distance_m: дистанция от старта круга, метры.
        speed_kmh: скорость, км/ч.
    """

    distance_m: float
    speed_kmh: float


def mean(values: Sequence[float]) -> float:
    """Среднее значение (0.0 для пустой последовательности)."""
    return float(sum(values) / len(values)) if values else 0.0


# --- Лучшие времена ----------------------------------------------------------

def best_sectors(laps: Iterable[dict]) -> dict[str, int]:
    """Лучшие валидные секторы и лучший круг.

    Args:
        laps: словари с ключами ``s1_ms``/``s2_ms``/``s3_ms``/``lap_time_ms``/``valid``.

    Returns:
        ``{"s1": мс, "s2": мс, "s3": мс, "lap": мс, "theoretical": мс}``.
        ``theoretical`` — сумма лучших секторов (теоретический лучший круг).
    """
    s1 = s2 = s3 = lap = 0
    for rec in laps:
        if not rec.get("valid", 1):
            continue
        a = int(rec.get("s1_ms") or 0)
        b = int(rec.get("s2_ms") or 0)
        c = int(rec.get("s3_ms") or 0)
        t = int(rec.get("lap_time_ms") or 0)
        if a > 0:
            s1 = min(s1 or a, a)
        if b > 0:
            s2 = min(s2 or b, b)
        if c > 0:
            s3 = min(s3 or c, c)
        if t > 0:
            lap = min(lap or t, t)
    theoretical = (s1 + s2 + s3) if (s1 and s2 and s3) else 0
    return {"s1": s1, "s2": s2, "s3": s3, "lap": lap, "theoretical": theoretical}


def theoretical_best_lap(laps: Iterable[dict]) -> int | None:
    """Теоретический лучший круг = сумма лучших валидных секторов, мс."""
    best = best_sectors(laps)
    return best["theoretical"] or None


# --- Дельта-время ------------------------------------------------------------

def _interp_speed_at(
    distances: Sequence[float], speeds: Sequence[float], x: float
) -> float:
    """Линейная интерполяция скорости на дистанции ``x`` (км/ч)."""
    n = len(distances)
    if n == 0:
        return 0.0
    if x <= distances[0]:
        return speeds[0]
    if x >= distances[-1]:
        return speeds[-1]
    lo, hi = 0, n - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if distances[mid] <= x:
            lo = mid
        else:
            hi = mid
    x0, x1 = distances[lo], distances[hi]
    if x1 <= x0:
        return speeds[lo]
    f = (x - x0) / (x1 - x0)
    return speeds[lo] + (speeds[hi] - speeds[lo]) * f


def delta_curve(
    reference: Sequence[DistanceSample],
    current: Sequence[DistanceSample],
    *,
    grid_m: float = 5.0,
    min_speed: float = 1.0,
) -> list[tuple[float, float]]:
    """Накопленная дельта текущего круга к эталонному по дистанции.

    Выравнивание по сетке ``grid_m`` (по умолчанию 5 м), скорость — линейной
    интерполяцией, накопление (скорости приводятся к м/с)::

        Δ(x) = Σ (1/v_current − 1/v_reference) · dx        [секунды]

    **Положительная дельта означает, что текущий круг медленнее** эталонного
    (на участке ``dx`` текущий проехал за больший отрезок времени).

    Args:
        reference: точки эталонного круга.
        current: точки текущего круга.
        grid_m: шаг сетки выравнивания, метры.
        min_speed: порог «почти стоп» — исключает деление на ~0.

    Returns:
        Список ``(дистанция_м, накопленная_дельта_с)`` начиная с 0.
    """
    if not reference or not current:
        return []
    d_end = min(
        max(s.distance_m for s in reference),
        max(s.distance_m for s in current),
    )
    if d_end <= 0:
        return []

    ref_d = sorted(s.distance_m for s in reference)
    ref_v = [s.speed_kmh for s in sorted(reference, key=lambda s: s.distance_m)]
    cur_d = sorted(s.distance_m for s in current)
    cur_v = [s.speed_kmh for s in sorted(current, key=lambda s: s.distance_m)]

    out: list[tuple[float, float]] = [(0.0, 0.0)]
    acc = 0.0
    x = 0.0
    while x < d_end:
        dx = min(grid_m, d_end - x)
        xr = x + dx / 2.0
        v_ref = _interp_speed_at(ref_d, ref_v, xr)
        v_cur = _interp_speed_at(cur_d, cur_v, xr)
        if v_ref >= min_speed and v_cur >= min_speed:
            t_ref = dx / (v_ref / 3.6)   # км/ч -> м/с
            t_cur = dx / (v_cur / 3.6)
            acc += t_cur - t_ref
        out.append((x + dx, acc))
        x += dx
    return out


def delta_at(curve: Sequence[tuple[float, float]], distance_m: float) -> float:
    """Дельта на заданной дистанции (междуйдельтовая интерполяция), секунды."""
    if not curve:
        return 0.0
    if distance_m <= curve[0][0]:
        return curve[0][1]
    if distance_m >= curve[-1][0]:
        return curve[-1][1]
    lo, hi = 0, len(curve) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if curve[mid][0] <= distance_m:
            lo = mid
        else:
            hi = mid
    x0, y0 = curve[lo]
    x1, y1 = curve[hi]
    if x1 <= x0:
        return y1
    f = (distance_m - x0) / (x1 - x0)
    return y0 + (y1 - y0) * f


# --- Регрессия / деградация --------------------------------------------------

@dataclass(frozen=True, slots=True)
class Regression:
    """Результат линейной регрессии ``y = slope·x + intercept``."""

    slope: float
    intercept: float
    r2: float
    n: int

    @property
    def valid(self) -> bool:
        """True, если есть хотя бы две точки и есть дисперсия X."""
        return self.n >= 2 and math.isfinite(self.slope)


def linear_regression(xs: Sequence[float], ys: Sequence[float]) -> Regression:
    """Линейная регрессия методом наименьших квадратов.

    Args:
        xs: значения по X (например, возраст шины в кругах).
        ys: значения по Y (время круга, мс).

    Returns:
        :class:`Regression` с наклоном, смещением и R².
    """
    n = len(xs)
    if n != len(ys) or n < 2:
        return Regression(0.0, 0.0, 0.0, n)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return Regression(0.0, my, 0.0, n)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    syy = sum((y - my) ** 2 for y in ys)
    r2 = (sxy ** 2) / (sxx * syy) if syy > 0 else 0.0
    return Regression(slope, intercept, max(0.0, min(1.0, r2)), n)


@dataclass(frozen=True, slots=True)
class StintDegradation:
    """Деградация шин в стинте."""

    stint_index: int
    compound_actual: int
    compound_visual: int
    start_lap: int
    end_lap: int
    laps_count: int
    avg_lap_ms: int
    deg_ms_per_lap: float
    r2: float
    n_points: int


def stint_degradation(
    laps: Sequence[dict],
    *,
    exclude_pit: bool = True,
    min_valid_laps: int = 3,
) -> list[StintDegradation]:
    """Деградация шин по стинтам: линейная регрессия время круга от возраста шины.

    Исключаются: in-lap/out-lap (границы стинта), круги с пит-стопом,
    невалидные круги, круги без возраста шины.

    Args:
        laps: словари кругов с ключами ``lap_num``, ``stint_index``,
            ``tyre_age_laps``, ``lap_time_ms``, ``valid``, ``compound_actual``,
            ``compound_visual`` (и опционально ``is_pit_lap``).
        exclude_pit: исключать пит-круги.
        min_valid_laps: минимум точек для регрессии.

    Returns:
        Список :class:`StintDegradation` по стинтам.
    """
    by_stint: dict[int, list[dict]] = {}
    for rec in laps:
        by_stint.setdefault(int(rec.get("stint_index") or 0), []).append(rec)

    out: list[StintDegradation] = []
    for stint_index in sorted(by_stint):
        group = sorted(by_stint[stint_index], key=lambda r: r.get("lap_num", 0))
        if not group:
            continue
        # границы стинта: первая и последняя запись (in/out lap)
        inner = group[1:-1] if len(group) > 2 else group
        points: list[tuple[float, float]] = []
        for rec in inner:
            if not rec.get("valid", 1):
                continue
            if exclude_pit and rec.get("is_pit_lap"):
                continue
            age = rec.get("tyre_age_laps")
            t = rec.get("lap_time_ms")
            if age is None or not t or int(t) <= 0:
                continue
            points.append((float(age), float(t)))

        times = [float(r["lap_time_ms"]) for r in group if r.get("lap_time_ms")]
        avg = int(sum(times) / len(times)) if times else 0
        first = group[0]
        if len(points) >= min_valid_laps:
            reg = linear_regression([p[0] for p in points], [p[1] for p in points])
            slope, r2, n = reg.slope, reg.r2, reg.n
        else:
            slope, r2, n = 0.0, 0.0, len(points)
        out.append(StintDegradation(
            stint_index=stint_index,
            compound_actual=int(first.get("compound_actual") or 0),
            compound_visual=int(first.get("compound_visual") or 0),
            start_lap=int(first.get("lap_num") or 0),
            end_lap=int(group[-1].get("lap_num") or 0),
            laps_count=len(group),
            avg_lap_ms=avg,
            deg_ms_per_lap=round(slope, 3),
            r2=round(r2, 4),
            n_points=n,
        ))
    return out


def stint_summary(
    laps: Sequence[dict], deg: Sequence[StintDegradation] | None = None
) -> list[dict]:
    """Сводка стинтов: круги, компаунд, средний темп, деградация.

    Args:
        laps: те же словари, что для :func:`stint_degradation`.
        deg: готовый результат :func:`stint_degradation` (если ``None`` — считается).

    Returns:
        Список словарей с ключами ``stint_index``, ``start_lap``, ``end_lap``,
        ``laps``, ``compound_actual``, ``compound_visual``, ``avg_lap_ms``,
        ``deg_ms_per_lap``, ``r2``.
    """
    deg = deg if deg is not None else stint_degradation(laps)
    return [
        {
            "stint_index": d.stint_index,
            "start_lap": d.start_lap,
            "end_lap": d.end_lap,
            "laps": d.laps_count,
            "compound_actual": d.compound_actual,
            "compound_visual": d.compound_visual,
            "avg_lap_ms": d.avg_lap_ms,
            "deg_ms_per_lap": d.deg_ms_per_lap,
            "r2": d.r2,
        }
        for d in deg
    ]


# --- Топливо -----------------------------------------------------------------

def fuel_per_lap(
    samples: Sequence[tuple[int, float]]
) -> list[tuple[int, float]]:
    """Расход топлива на круг по границам кругов.

    Args:
        samples: пары ``(номер_круга, топливо_в_баке_кг)`` — значение на старте
            каждого круга (и, опционально, последнее значение).

    Returns:
        Список ``(номер_круга, расход_кг)`` для кругов, где расход посчитать можно.
    """
    if len(samples) < 2:
        return []
    ordered = sorted(samples, key=lambda s: s[0])
    out: list[tuple[int, float]] = []
    for (lap_a, fuel_a), (lap_b, fuel_b) in zip(ordered, ordered[1:]):
        if lap_b <= lap_a:
            continue
        if lap_b - lap_a > 1:
            continue
        used = fuel_a - fuel_b
        if used >= 0:
            out.append((lap_b, round(used, 3)))
    return out


# --- Агрегаты по телеметрии ---------------------------------------------------

def lap_aggregates(samples: Sequence[dict]) -> dict[str, float]:
    """Агрегаты круга по точкам телеметрии.

    Args:
        samples: словари с ключами ``speed_kmh``, ``throttle``, ``brake``.

    Returns:
        ``{"top_speed_kmh", "avg_throttle", "full_throttle_pct", "brake_time_pct"}``.
        Доли — в процентах (0..100).
    """
    if not samples:
        return {
            "top_speed_kmh": 0.0, "avg_throttle": 0.0,
            "full_throttle_pct": 0.0, "brake_time_pct": 0.0,
        }
    speeds = [float(s.get("speed_kmh") or 0.0) for s in samples]
    throttles = [float(s.get("throttle") or 0.0) for s in samples]
    brakes = [float(s.get("brake") or 0.0) for s in samples]
    n = len(samples)
    return {
        "top_speed_kmh": max(speeds),
        "avg_throttle": mean(throttles),
        "full_throttle_pct": 100.0 * sum(1 for t in throttles if t > 0.95) / n,
        "brake_time_pct": 100.0 * sum(1 for b in brakes if b > 0.05) / n,
    }


# --- Позиции -----------------------------------------------------------------

def positions_by_lap(
    rows: Iterable[tuple[int, int, int]]
) -> dict[int, dict[int, int]]:
    """Группирует ``(номер_круга, индекс_машины, позиция)`` в карту позиций.

    При >50 кругах из игры приходят два пакета Lap Positions с разным
    ``m_lapStart`` — слияние происходит естественным образом, т.к. ключи
    ``(lap, car)`` в таблице уникальны и более поздние записи вытесняют старые.

    Returns:
        ``{номер_круга: {индекс_машины: позиция}}``.
    """
    out: dict[int, dict[int, int]] = {}
    for lap, car, pos in rows:
        if pos <= 0:
            continue
        out.setdefault(int(lap), {})[int(car)] = int(pos)
    return out


# --- Гистограмма -------------------------------------------------------------

def lap_time_histogram(
    times_ms: Sequence[int], *, bins: int = 20, min_ms: int = 1
) -> list[tuple[int, int, int]]:
    """Гистограмма распределения времён кругов.

    Args:
        times_ms: времена кругов, мс.
        bins: число корзин.
        min_ms: отбрасывать значения меньше этого.

    Returns:
        Список корзин ``(с_мс, по_мс, количество)``.
    """
    values = [t for t in times_ms if t >= min_ms]
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi <= lo:
        return [(lo, hi, len(values))]
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        idx = min(bins - 1, int((v - lo) / width))
        counts[idx] += 1
    return [
        (int(lo + i * width), int(lo + (i + 1) * width), counts[i])
        for i in range(bins)
    ]
