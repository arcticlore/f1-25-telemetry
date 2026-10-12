"""Экспорт сессии из SQLite в CSV/JSON.

Только stdlib (``csv``/``json``): экспорт не тянет pandas, чтобы не плодить
зависимости. Файлы пишутся в каталог ``out_dir``:

* ``sessions.csv`` / ``session.json``
* ``participants.csv``
* ``laps.csv``
* ``telemetry.csv``
* ``car_telemetry_detail.csv``
* ``car_status_samples.csv``
* ``stints.csv``
* ``events.csv``
* ``final_classification.csv``
* ``setups.csv``
* ``tyre_sets.csv``
* ``lap_positions.csv``
"""

from __future__ import annotations

import csv
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

from .storage import Storage

log = logging.getLogger(__name__)

__all__ = ["export_session", "TABLE_EXPORTS"]

TABLE_EXPORTS: tuple[str, ...] = (
    "participants",
    "laps",
    "telemetry",
    "car_telemetry_detail",
    "car_status_samples",
    "stints",
    "events",
    "final_classification",
    "setups",
    "tyre_sets",
    "lap_positions",
)


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    cur.row_factory = sqlite3.Row
    return [dict(r) for r in cur.fetchall()]


def export_session(
    db_path: str | Path,
    session_uid: int,
    out_dir: str | Path,
    *,
    fmt: str = "csv",
    tables: Sequence[str] | None = None,
) -> dict[str, Path]:
    """Экспортирует одну сессию.

    Args:
        db_path: путь к SQLite.
        session_uid: UID сессии.
        out_dir: каталог назначения (создаётся).
        fmt: ``csv`` или ``json``.
        tables: список таблиц (по умолчанию все, см. :data:`TABLE_EXPORTS`).

    Returns:
        Словарь ``{имя: путь_к_файлу}`` записанных файлов.
    """
    fmt = fmt.lower()
    if fmt not in ("csv", "json"):
        raise ValueError(f"unsupported format: {fmt!r} (use csv or json)")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    st = Storage(db_path, read_only=True)
    written: dict[str, Path] = {}
    try:
        session = st.get_session(session_uid)
        if session is None:
            raise ValueError(f"session {session_uid} not found in {db_path}")

        names = list(tables) if tables is not None else list(TABLE_EXPORTS)
        data: dict[str, list[dict[str, Any]]] = {
            "sessions": [dict(session)],
        }
        for name in names:
            cur = st.conn.execute(f"SELECT * FROM {name} WHERE session_uid=?", (session_uid,))
            data[name] = _rows(cur)

        if fmt == "json":
            path = out / f"session_{session_uid}.json"
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            written["session"] = path
        else:
            for name, rows in data.items():
                if not rows:
                    continue
                path = out / f"{name}_{session_uid}.csv"
                _write_csv(path, rows)
                written[name] = path
    finally:
        st.close()

    log.info("exported session %s -> %s (%d files)", session_uid, out, len(written))
    return written


def _write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        return
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fields})
