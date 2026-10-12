"""Звуковые алерты инженера: WAV генерируется в кэш, играется QSoundEffect.

Всё best-effort: нет мультимедиа-плагина или QApplication — просто
не играет, приложение не падает. Бинарных ассетов в репозитории нет —
синусоидные бипы создаются stdlib `wave` при первом использовании.
"""

from __future__ import annotations

import logging
import math
import os
import struct
import wave
from pathlib import Path

log = logging.getLogger(__name__)

# имя → (частота Гц, длительность с, повторов)
BEEP_SPECS: dict[str, tuple[float, float, int]] = {
    "tmpt": (660.0, 0.18, 2),     # соперник/напарник в питe
    "rain": (440.0, 0.45, 1),     # дождь по прогнозу
    "tyres": (880.0, 0.12, 3),    # шины на пределе
    "fuel": (520.0, 0.25, 2),     # топливо на исходе
    "drs_on": (780.0, 0.10, 1),   # DRSE — DRS разрешён
    "drs_off": (520.0, 0.08, 1),  # DRSD — DRS отключён
    "sc": (400.0, 0.4, 2),        # SCAR — safety car
}

_RATE = 22050
_VOLUME = 0.7


def alerts_cache_dir() -> Path:
    """Каталог для сгенерированных WAV (XDG_CACHE_HOME)."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    d = Path(base) / "f1-telemetry" / "alerts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_wav(name: str) -> Path | None:
    """Создаёт (если нужно) WAV для алерта ``name``; None при ошибке."""
    spec = BEEP_SPECS.get(name)
    if spec is None:
        return None
    freq, dur, reps = spec
    path = alerts_cache_dir() / f"{name}.wav"
    if path.exists() and path.stat().st_size > 44:
        return path
    try:
        tmp = path.with_suffix(".tmp")
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(_RATE)
            frames = bytearray()
            n = max(1, int(_RATE * dur))
            for _ in range(reps):
                for i in range(n):
                    env = 1.0 - i / n
                    val = int(12000 * env * math.sin(2 * math.pi * freq * i / _RATE))
                    frames += struct.pack("<h", val)
                frames += b"\x00\x00" * int(_RATE * 0.08)
            w.writeframes(bytes(frames))
        tmp.replace(path)
        return path
    except OSError:
        log.debug("не удалось создать wav для %s", name, exc_info=True)
        return None


class AlertSounds:
    """Проигрывает именованные алерты; глушится mute-флажком GUI."""

    def __init__(self) -> None:
        self._effects: dict[str, object] = {}
        self._broken = False

    @property
    def available(self) -> bool:
        return not self._broken

    def play(self, name: str) -> bool:
        """Играет алерт ``name``; False — тихо (нет плагина/ассета/имени)."""
        if self._broken or name not in BEEP_SPECS:
            return False
        try:
            eff = self._effects.get(name)
            if eff is None:
                from PySide6.QtCore import QUrl
                from PySide6.QtMultimedia import QSoundEffect

                wav = ensure_wav(name)
                if wav is None:
                    return False
                eff = QSoundEffect()
                eff.setSource(QUrl.fromLocalFile(str(wav)))
                eff.setVolume(_VOLUME)
                self._effects[name] = eff
            eff.play()  # type: ignore[attr-defined]
            return True
        except Exception:  # noqa: BLE001 — аудио не должно ронять запись
            log.debug("звуковой алерт %s недоступен", name, exc_info=True)
            self._broken = True
            return False
