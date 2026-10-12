# F1 25 Telemetry Recorder

Десктопное приложение (Windows/Linux, Python) для записи и анализа UDP-телеметрии
игры **EA SPORTS F1 25** (формат `2025`, порт `20777`).

- Слушает UDP на `0.0.0.0:20777`, парсит все 16 типов пакетов F1 25.
- Пишет в **SQLite** (WAL): сессии, участников, круги, посекторные времена,
  прореженную телеметрию, детали шин/тормозов, топливо/ERS, события, стинты,
  финальную классификацию, сетапы, комплекты шин, позиции по кругам.
- GUI (PySide6 + pyqtgraph): приборная панель, ленты графиков реального времени,
  таблица кругов, браузер сессий, сравнение кругов (дельта по дистанции),
  аналитика (деградация шин, расход топлива, позиции).
- Headless CLI: запись без GUI, реплей фикстур, статистика, экспорт CSV/JSON.
- Полностью офлайн. Никаких сетевых вызовов наружу.

---

## Установка

```bash
# Python 3.11+ (разработка на 3.13)
uv sync                # создаёт .venv с PySide6, pyqtgraph, pytest
# или без uv:
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

Запуск GUI:

```bash
uv run run.py
# или
uv run python -m f1telemetry.cli record --db data/f1.sqlite --seconds 600
```

---

## Настройка UDP в игре (обязательно!)

В EA SPORTS F1 25: **Настройки → UDP Telemetry → Status = On**

| Параметр | Значение |
|---|---|
| UDP IP | IP этого компьютера (не `127.0.0.1`, если игра на другом ПК) |
| UDP Port | `20777` |
| **UDP Format** | **`2025`** |

> Приложение работает **только** с форматом `2025`. Если игра шлёт формат
> 2024/2026 — в GUI появится жёлтый баннер с инструкцией, парсинг не включается.

### Сеть (Linux)

Приложение слушает `0.0.0.0:20777`. Если игра на другом компьютере:

```bash
sudo firewall-cmd --add-port=20777/udp --permanent
sudo firewall-cmd --reload
```

Проверка, что порт слушается:

```bash
ss -lunp | grep 20777
```

Тест канала без игры (с другого ПК или локально):

```bash
uv run python tools/simulate.py --host 192.168.1.10   # IP этого ПК
```

---

## Использование

### GUI

```bash
uv run run.py                          # живой приём UDP
uv run run.py --db data/f1.sqlite      # свой файл БД
uv run run.py --fixture tests/fixtures/session_short.bin   # реплей без игры
```

Вкладки: **Live** (приборка + графики + круги), **Сессии** (браузер БД),
**Сравнение** (две кривые скорости + дельта), **Аналитика** (динамика по кругам).

### CLI

```bash
# запись N секунд без GUI
uv run python -m f1telemetry.cli record --db data/f1.sqlite --seconds 300

# воспроизвести записанные пакеты в БД
uv run python -m f1telemetry.cli replay --fixture tests/fixtures/session_short.bin --db data/f1.sqlite

# статистика / JSON
uv run python -m f1telemetry.cli stats --db data/f1.sqlite
uv run python -m f1telemetry.cli stats --db data/f1.sqlite --json

# экспорт сессии
uv run python -m f1telemetry.cli export --db data/f1.sqlite --session <uid> --out export/ --format csv

# проверка установки
uv run python -m f1telemetry.cli selftest
QT_QPA_PLATFORM=offscreen uv run python run.py --selftest
```

### Симулятор (без игры)

```bash
uv run python tools/simulate.py --laps 3 --hz 60 --port 20777
uv run python tools/simulate.py --laps 2 --record-fixture tests/fixtures/session_short.bin
```

---

## Тесты

```bash
uv run pytest -q
```

Покрытие: размеры всех 16 ctypes-структур против спецификации, round-trip
симулятор→парсер, storage/analytics/state/recorder, CLI, GUI (offscreen).

---

## Схема БД

12 таблиц: `sessions`, `participants`, `laps`, `telemetry`,
`car_telemetry_detail`, `car_status_samples`, `stints`, `events`,
`final_classification`, `setups`, `tyre_sets`, `lap_positions`.
Подробное описание — в `AGENTS.md`, раздел 5. Высокочастотные таблицы пишутся
только за игрока; `laps`/`participants`/`final_classification`/`lap_positions` —
за всех. Батч-коммит каждые 0.5 с или по 500 записей.

---

## Архитектура

```
UDP (порт 20777) ─► listener.py ─► recorder.py ─► state.py ─► storage.py (SQLite)
                                        │              │
                                        │              └── ring-buffer ─► GUI (QTimer 30 Гц)
                                        └── Qt-сигналы ─► main_window
```

- Ядро (`parser`/`state`/`storage`/`analytics`) не знает про Qt.
- Приём UDP и запись БД — отдельные потоки; GUI общается только через сигналы.
- UDP ненадёжён: пропуски не ошибка, показываются пакеты/с и оценка потери.

---

## Лицензия

MIT — см. `LICENSE`.
