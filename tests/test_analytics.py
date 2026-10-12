"""Тесты производных метрик: дельты, деградация, топливо, агрегаты."""

from __future__ import annotations

import pytest

from f1telemetry.analytics import (
    DistanceSample,
    best_sectors,
    delta_at,
    delta_curve,
    fuel_per_lap,
    lap_aggregates,
    lap_time_histogram,
    linear_regression,
    mean,
    positions_by_lap,
    stint_degradation,
    stint_summary,
    theoretical_best_lap,
)


def test_mean():
    assert mean([]) == 0.0
    assert mean([1.0, 2.0, 3.0]) == 2.0


def test_best_sectors_and_theoretical():
    laps = [
        {"s1_ms": 31000, "s2_ms": 30500, "s3_ms": 30200, "lap_time_ms": 91700, "valid": 1},
        {"s1_ms": 30800, "s2_ms": 30700, "s3_ms": 30000, "lap_time_ms": 91500, "valid": 1},
        {"s1_ms": 30100, "s2_ms": 30400, "s3_ms": 30900, "lap_time_ms": 91400, "valid": 0},
    ]
    best = best_sectors(laps)
    assert best["s1"] == 30800   # из валидных кругов
    assert best["s2"] == 30500
    assert best["s3"] == 30000
    assert best["lap"] == 91500
    assert best["theoretical"] == 30800 + 30500 + 30000
    assert theoretical_best_lap(laps) == 91300
    assert theoretical_best_lap([]) is None


def test_theoretical_requires_all_sectors():
    laps = [{"s1_ms": 30000, "s2_ms": 30000, "s3_ms": 0, "lap_time_ms": 60000, "valid": 1}]
    assert theoretical_best_lap(laps) is None


def test_linear_regression_exact():
    reg = linear_regression([0, 1, 2, 3], [90000, 90080, 90160, 90240])
    assert reg.slope == pytest.approx(80.0)
    assert reg.intercept == pytest.approx(90000.0)
    assert reg.r2 == pytest.approx(1.0)
    assert reg.n == 4
    assert reg.valid


def test_linear_regression_degenerate():
    assert not linear_regression([1], [5.0]).valid
    assert not linear_regression([], []).valid
    reg = linear_regression([2, 2, 2], [1.0, 2.0, 3.0])
    assert reg.slope == 0.0


def _laps_for_stint():
    """Сентетический стинт: 40 мс/круг деградации + шум 0."""
    out = []
    for lap, age in enumerate([0, 1, 2, 3, 4, 5], start=1):
        out.append({
            "lap_num": lap,
            "stint_index": 0,
            "tyre_age_laps": age,
            "lap_time_ms": 90000 + 40 * age,
            "valid": 1,
            "compound_actual": 18,
            "compound_visual": 17,
            "is_pit_lap": lap == 1,   # out lap
        })
    return out


def test_stint_degradation():
    deg = stint_degradation(_laps_for_stint())
    assert len(deg) == 1
    d = deg[0]
    assert d.stint_index == 0
    assert d.start_lap == 1 and d.end_lap == 6
    assert d.laps_count == 6
    assert d.compound_actual == 18
    assert d.avg_lap_ms == pytest.approx(90000 + 40 * 2.5)
    # out-lap исключён, осталось 4 точки (возраст 1..4) — линейно
    assert d.n_points == 4
    assert d.deg_ms_per_lap == pytest.approx(40.0)
    assert d.r2 == pytest.approx(1.0)


def test_stint_degradation_multiple_stints():
    laps = _laps_for_stint()
    st2 = [
        {"lap_num": 7, "stint_index": 1, "tyre_age_laps": 0, "lap_time_ms": 89500,
         "valid": 1, "compound_actual": 17, "compound_visual": 16, "is_pit_lap": True},
        {"lap_num": 8, "stint_index": 1, "tyre_age_laps": 1, "lap_time_ms": 89600,
         "valid": 1, "compound_actual": 17, "compound_visual": 16},
        {"lap_num": 9, "stint_index": 1, "tyre_age_laps": 2, "lap_time_ms": 89700,
         "valid": 1, "compound_actual": 17, "compound_visual": 16},
        {"lap_num": 10, "stint_index": 1, "tyre_age_laps": 3, "lap_time_ms": 89800,
         "valid": 1, "compound_actual": 17, "compound_visual": 16},
        {"lap_num": 11, "stint_index": 1, "tyre_age_laps": 4, "lap_time_ms": 89900,
         "valid": 1, "compound_actual": 17, "compound_visual": 16},
        {"lap_num": 12, "stint_index": 1, "tyre_age_laps": 5, "lap_time_ms": 90000,
         "valid": 1, "compound_actual": 17, "compound_visual": 16},
    ]
    deg = stint_degradation(laps + st2)
    assert [d.stint_index for d in deg] == [0, 1]
    assert deg[1].deg_ms_per_lap == pytest.approx(100.0)

    summary = stint_summary(laps + st2, deg)
    assert summary[0]["laps"] == 6
    assert summary[1]["compound_visual"] == 16


def test_stint_degradation_too_few_points():
    laps = [{"lap_num": 1, "stint_index": 0, "tyre_age_laps": 0,
             "lap_time_ms": 90000, "valid": 1, "compound_actual": 18,
             "compound_visual": 17}]
    deg = stint_degradation(laps)
    assert deg[0].deg_ms_per_lap == 0.0
    assert deg[0].n_points == 1


def test_fuel_per_lap():
    samples = [(1, 100.0), (2, 98.5), (3, 97.0), (4, 95.6), (7, 80.0)]
    out = fuel_per_lap(samples)
    assert out == [(2, 1.5), (3, 1.5), (4, 1.4)]
    assert fuel_per_lap([]) == []
    assert fuel_per_lap([(1, 50.0)]) == []


def test_fuel_per_lap_ignores_increases():
    # флешбэк/пересчёт не должны давать отрицательный расход
    out = fuel_per_lap([(1, 100.0), (2, 105.0), (3, 103.0)])
    assert out == [(3, 2.0)]


def test_lap_aggregates():
    samples = [
        {"speed_kmh": 100.0, "throttle": 0.0, "brake": 1.0},
        {"speed_kmh": 320.0, "throttle": 1.0, "brake": 0.0},
        {"speed_kmh": 200.0, "throttle": 0.5, "brake": 0.0},
        {"speed_kmh": 50.0, "throttle": 1.0, "brake": 0.0},
    ]
    agg = lap_aggregates(samples)
    assert agg["top_speed_kmh"] == 320.0
    assert agg["avg_throttle"] == pytest.approx(0.625)
    assert agg["full_throttle_pct"] == pytest.approx(50.0)   # 2 из 4
    assert agg["brake_time_pct"] == pytest.approx(25.0)      # 1 из 4

    empty = lap_aggregates([])
    assert empty["top_speed_kmh"] == 0.0


def test_positions_by_lap():
    rows = [
        (1, 0, 3), (1, 1, 1), (1, 2, 2),
        (2, 0, 2), (2, 1, 1), (2, 2, 3),
        (3, 0, 0),  # 0 = нет записи — пропускаем
    ]
    pos = positions_by_lap(rows)
    assert pos[1] == {0: 3, 1: 1, 2: 2}
    assert pos[2][0] == 2
    assert 3 not in pos


def test_positions_by_lap_merges_two_packets():
    """Больше 50 кругов: два пакета с разным lapStart сливаются в одну карту."""
    packet_a = [(lap, 0, ((lap - 1) % 10) + 1) for lap in range(1, 26)]
    packet_b = [(lap, 0, ((lap - 1) % 10) + 1) for lap in range(26, 51)]
    pos = positions_by_lap(packet_a + packet_b)
    assert len(pos) == 50
    assert pos[50][0] == 10
    assert pos[26][0] == 6


def test_lap_time_histogram():
    times = [90000, 91000, 92000, 93000, 90000]
    hist = lap_time_histogram(times, bins=4)
    assert sum(c for _, _, c in hist) == 5
    assert hist[0][0] == 90000
    assert hist[-1][1] == 93000
    assert lap_time_histogram([]) == []
    assert lap_time_histogram([90000, 90000]) == [(90000, 90000, 2)]


# --- Дельта -------------------------------------------------------------------

def _circle_samples(speed_kmh: float, length_m: float = 1000.0, step_m: float = 10.0):
    """Идеальный круг с постоянной скоростью — для аналитической проверки."""
    return [
        DistanceSample(d, speed_kmh)
        for d in [i * step_m for i in range(int(length_m / step_m) + 1)]
    ]


def test_delta_zero_for_identical_laps():
    ref = _circle_samples(216.0)   # 216 км/ч = 60 м/с
    cur = _circle_samples(216.0)
    curve = delta_curve(ref, cur, grid_m=10.0)
    assert curve[0] == (0.0, 0.0)
    assert curve[-1][0] == pytest.approx(1000.0)
    assert curve[-1][1] == pytest.approx(0.0, abs=1e-9)
    assert all(abs(d) < 1e-9 for _, d in curve)


def test_delta_positive_when_current_slower():
    """Аналитическая проверка: круг 1000 м, 60 м/с против 50 м/с.

    Δ = T_cur − T_ref = 1000/50 − 1000/60 = 20 − 16.667 = 3.333 с.
    """
    ref = _circle_samples(216.0)   # 60 м/с
    cur = _circle_samples(180.0)   # 50 м/с
    curve = delta_curve(ref, cur, grid_m=10.0)
    total = curve[-1][1]
    assert total == pytest.approx(1000 / 50 - 1000 / 60, rel=1e-4)
    assert total > 0            # положительная = текущий круг медленнее
    # монотонность на круге с постоянной скоростью
    deltas = [d for _, d in curve]
    assert deltas == sorted(deltas)


def test_delta_negative_when_current_faster():
    ref = _circle_samples(180.0)
    cur = _circle_samples(216.0)
    curve = delta_curve(ref, cur, grid_m=10.0)
    assert curve[-1][1] < 0
    assert curve[-1][1] == pytest.approx(1000 / 60 - 1000 / 50, rel=1e-4)


def test_delta_at_interpolation():
    curve = [(0.0, 0.0), (100.0, 0.4), (200.0, 1.0)]
    assert delta_at(curve, 0.0) == 0.0
    assert delta_at(curve, 50.0) == pytest.approx(0.2)
    assert delta_at(curve, 150.0) == pytest.approx(0.7)
    assert delta_at(curve, 999.0) == 1.0
    assert delta_at([], 100.0) == 0.0


def test_delta_curve_empty_inputs():
    assert delta_curve([], []) == []
    assert delta_curve(_circle_samples(200.0), []) == []


def test_delta_curve_handles_variable_speed():
    """Реалистичный круг: торможение в шикане на 500 м даёт заметную дельту."""
    def profile(base, slow_at, slow_speed):
        out = []
        for i in range(101):
            d = i * 10.0
            v = slow_speed if slow_at <= d <= slow_at + 100 else base
            out.append(DistanceSample(d, v))
        return out

    ref = profile(280.0, 500.0, 120.0)
    cur = profile(280.0, 500.0, 100.0)   # медленнее в шикане
    curve = delta_curve(ref, cur, grid_m=10.0)
    assert curve[-1][1] > 0.0
    # вся дельта должна приходиться на участок шиканы
    assert delta_at(curve, 400.0) == pytest.approx(0.0, abs=1e-6)
    assert delta_at(curve, 1000.0) > 0.0


def test_delta_requires_positive_length():
    assert delta_curve([DistanceSample(0.0, 200.0)], [DistanceSample(0.0, 200.0)]) == []
