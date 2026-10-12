"""Скриншот вкладки Engineer (offscreen, без игры).

Подаёт данные симулятора в MainWindow и сохраняет PNG:

    QT_QPA_PLATFORM=offscreen python tools/grab_engineer.py [out.png]
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import simulate as sim  # noqa: E402


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else ROOT / "docs" / "screenshots" / "engineer.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    from f1telemetry.ui.theme import apply_theme
    apply_theme(app)

    from f1telemetry.ui.main_window import MainWindow

    db = Path(tempfile.mkdtemp()) / "shot.sqlite"
    win = MainWindow(db, autostart=False)
    win.resize(1280, 800)
    win.show()
    win.engineer._mute.setChecked(False)  # без звука при генерации
    win._start_recorder("127.0.0.1", 0)

    # Monaco-гонка, короткая трасса — быстрая генерация, правдоподобные числа
    cfg = sim.SimConfig(
        hz=20, laps=20, cars=22, track_length=2000.0, seed=3,
        session_uid=2_600_000_000_000_000_001, track_id=5,
        session_type=15, verbose=False,
    )
    s = sim.Simulator(cfg)
    dgs: list[bytes] = []
    target = s.profile.lap_time_s * 6
    while s.t < target:
        dgs.extend(s.tick())
    win.recorder.feed(dgs)
    win._tick()
    app.processEvents()

    win.tabs.setCurrentWidget(win.engineer)
    app.processEvents()
    pix = win.grab()
    ok = pix.save(str(out))
    win.close()
    if not ok:
        print(f"failed to save {out}", file=sys.stderr)
        return 1
    print(f"saved {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
