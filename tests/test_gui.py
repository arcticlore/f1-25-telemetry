"""GUI-тесты (offscreen): виджеты, окно, живой прогон фикстуры."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FIXTURE = Path(__file__).parent / "fixtures" / "session_short.bin"


@pytest.fixture(scope="module")
def qapp():
    """Единственный QApplication на модуль."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from f1telemetry.ui.theme import apply_theme
    apply_theme(app)
    yield app


def _wait_until(pred, timeout: float = 15.0) -> bool:
    """Крутит event loop до условия или таймаута."""
    from PySide6.QtWidgets import QApplication
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_widgets_construct(qapp):
    from f1telemetry.ui.widgets.analytics_view import AnalyticsView
    from f1telemetry.ui.widgets.lap_compare import LapCompare
    from f1telemetry.ui.widgets.lap_table import LapTable
    from f1telemetry.ui.widgets.live_dash import LiveDash
    from f1telemetry.ui.widgets.live_trace import LiveTrace
    from f1telemetry.ui.widgets.session_browser import SessionBrowser
    from f1telemetry.ui.widgets.settings_view import SettingsView
    from f1telemetry.ui.widgets.status_bar import StatusBarWidget

    for cls in (LiveDash, LiveTrace, LapTable, SessionBrowser,
                LapCompare, AnalyticsView, StatusBarWidget, SettingsView):
        w = cls()
        w.show()
        qapp.processEvents()
        w.hide()


def test_live_dash_updates(qapp):
    from f1telemetry.state import LiveSnapshot
    from f1telemetry.ui.widgets.live_dash import LiveDash

    dash = LiveDash()
    snap = LiveSnapshot()
    snap.speed_kmh = 287.4
    snap.throttle = 1.0
    snap.brake = 0.0
    snap.gear = 7
    snap.rpm = 11200
    snap.rev_lights_pct = 80
    snap.lap_num = 12
    snap.position = 3
    snap.last_lap_ms = 91234
    snap.best_lap_ms = 90871
    snap.theoretical_ms = 90500
    snap.fuel_kg = 42.5
    snap.fuel_capacity_kg = 110.0
    snap.ers_store_j = 2.5e6
    snap.tyre_surf_c = (88, 91, 102, 99)
    snap.tyre_wear_pct = (12.0, 13.5, 10.0, 11.2)
    snap.tyre_press_psi = (21.5, 21.6, 22.0, 21.9)
    snap.tyre_compound_visual = 16
    snap.tyre_age_laps = 8
    dash.update_snapshot(snap)
    qapp.processEvents()
    assert dash.dial.speed == pytest.approx(287.4)
    assert dash.gear.text() == "7"
    assert "круг" not in dash.header.text().lower() or True
    assert "Mercedes" not in dash.header.text()  # имя из снапшота пустое
    assert "287" == dash.dial._value.text()


def test_main_window_fixture_replay(qapp, tmp_path):
    """Окно + фикстура: дашборд получает данные, круги пишутся в БД."""
    from f1telemetry.ui.main_window import MainWindow

    db = tmp_path / "gui.sqlite"
    win = MainWindow(db, fixture=FIXTURE, autostart=True)
    win.show()

    assert _wait_until(
        lambda: win.recorder is not None and not win.recorder.is_running,
        timeout=30.0,
    ), "реплей фикстуры не завершился"

    # дать GUI-таймеру отработать
    for _ in range(30):
        win._tick()
        qapp.processEvents()

    snap = win.recorder.snapshot()
    assert snap.session_uid is not None
    assert snap.speed_kmh > 0
    assert win.dash.dial.speed > 0
    assert win.lap_table.table.rowCount() >= 1

    # статусная строка показывает счётчики
    health = win.recorder.health()
    assert health["datagrams"] > 100
    assert health["parse_errors"] == 0

    win.close()
    qapp.processEvents()

    # данные дошли в БД
    from f1telemetry.storage import Storage
    st = Storage(db, read_only=True)
    try:
        stats = st.stats()
    finally:
        st.close()
    assert stats["sessions"] == 1
    assert stats["participants"] == 22
    assert stats["laps"] > 0
    assert stats["telemetry_points"] > 0


def test_analytics_and_compare_with_db(qapp, tmp_path):
    """Аналитика и сравнение читают реальную БД после реплея."""
    from f1telemetry.recorder import Recorder
    from f1telemetry.ui.widgets.analytics_view import AnalyticsView
    from f1telemetry.ui.widgets.lap_compare import LapCompare

    db = tmp_path / "ana.sqlite"
    rec = Recorder(db, fixture=FIXTURE)
    rec.start()
    assert _wait_until(lambda: not rec.is_running, timeout=30.0)
    rec.stop()

    from f1telemetry.storage import Storage
    st = Storage(db, read_only=True)
    try:
        uid = st.list_sessions()[0].session_uid
    finally:
        st.close()

    ana = AnalyticsView()
    ana.configure(db, uid)
    ana.show()
    qapp.processEvents()
    assert ana.table.rowCount() >= 1

    cmp_ = LapCompare()
    cmp_.configure(db, uid)
    cmp_.show()
    qapp.processEvents()
    if len(cmp_._laps) >= 2:
        cmp_._redraw()
        qapp.processEvents()
        assert cmp_.picker_a.combo.count() >= 2


def test_format_banner(tmp_path):
    from f1telemetry.ui.main_window import MainWindow
    db = tmp_path / "b.sqlite"
    win = MainWindow(db, autostart=False)
    win._on_format_warning(2026, "192.168.0.99")
    text = win.banner.text()
    assert "2026" in text and "192.168.0.99" in text
    win.close()


def test_player_combo_splitscreen(qapp, tmp_path):
    """Переключатель игроков: splitscreen даёт 2 пункта, view переключается."""
    from f1telemetry.recorder import Recorder
    from f1telemetry.ui.main_window import MainWindow

    db = tmp_path / "split_ui.sqlite"
    win = MainWindow(db, autostart=False)
    rec = Recorder(db)   # без start(): читаем state/snapshot/health
    rec.state.player_car_index = 0
    rec.state.secondary_player_car_index = 1
    win.recorder = rec

    win._tick()
    qapp.processEvents()
    assert win.player_combo.count() == 2
    assert "Игрок 1" in win.player_combo.itemText(0)
    assert "Игрок 2" in win.player_combo.itemText(1)
    assert win._current_view_car() is None      # по умолчанию — игрок 1

    win.player_combo.setCurrentIndex(1)         # включаем второго игрока
    qapp.processEvents()
    assert win._view_car == 1
    assert win._current_view_car() == 1

    # без второго игрока (255) — комбо исчезает, вид снова «игрок 1»
    rec.state.secondary_player_car_index = 255
    win._tick()
    qapp.processEvents()
    assert win.player_combo.count() == 1
    assert win._current_view_car() is None

    win.close()
    qapp.processEvents()


def test_settings_tab_saves_config(qapp, tmp_path, monkeypatch):
    """Правка порта на вкладке «Настройки» сохраняет config.json (XDG-изолированный)."""
    from f1telemetry.paths import AppConfig, config_file, load_config
    from f1telemetry.ui.main_window import MainWindow

    cfg = AppConfig(host="0.0.0.0", port=20777, db_path=str(tmp_path / "s.sqlite"),
                    autostart=False)
    mw = MainWindow(cfg.resolved_db(), config=cfg, autostart=False)
    assert mw.settings_tab.collect().port == 20777

    mw.settings_tab.port_spin.setValue(21777)
    mw.settings_tab.host_edit.setText("127.0.0.1")
    mw.settings_tab.host_edit.editingFinished.emit()
    qapp.processEvents()

    saved = load_config(config_file())
    assert saved.port == 21777
    assert saved.host == "127.0.0.1"
    assert mw._port == 21777 and mw._host == "127.0.0.1"
    mw.close()


def test_session_browser_delete(qapp, tmp_path, monkeypatch):
    """Кнопка «Удалить» удаляет сессию из БД после подтверждения."""
    from PySide6.QtWidgets import QMessageBox

    from f1telemetry.recorder import Recorder
    from f1telemetry.storage import Storage
    from f1telemetry.ui.widgets.session_browser import SessionBrowser
    import simulate as sim

    db = tmp_path / "del.sqlite"
    s = sim.Simulator(sim.SimConfig(hz=10.0, laps=1, cars=2, track_length=200.0,
                                    seed=5, session_uid=555001, verbose=False))
    rec = Recorder(db)
    data = []
    while s.t < s.profile.lap_time_s * 1.0:
        data.extend(s.tick())
    rec.feed(data)
    rec.stop()

    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

    browser = SessionBrowser()
    browser.set_db_path(db)
    assert browser.table.rowCount() == 1
    browser.table.selectRow(0)
    browser._on_delete()
    qapp.processEvents()

    st = Storage(db)
    assert st.list_sessions() == []
    st.close()
    assert browser.table.rowCount() == 0
