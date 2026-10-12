"""XDG-пути, JSON-конфиг и служебные утилиты приложения.

Всё работает из любого cwd:

- БД:    ``$XDG_DATA_HOME/f1-telemetry/f1.sqlite`` (обычно ``~/.local/share/...``)
- Конфиг: ``$XDG_CONFIG_HOME/f1-telemetry/config.json`` (обычно ``~/.config/...``)
- Логи:  ``$XDG_STATE_HOME/f1-telemetry/logs/app.log`` (обычно ``~/.local/state/...``)

Windows: те же переменные, если заданы; иначе ``%USERPROFILE%``-варианты
через :mod:`pathlib` (``Path.home()``). Все каталоги создаются при старте
(:func:`ensure_dirs`), пути можно переопределить флагами CLI (``--db`` и т.п.).
"""

from __future__ import annotations

import json
import logging
import os
import socket
from dataclasses import asdict, dataclass
from pathlib import Path

from .constants import DEFAULT_LISTEN_HOST, DEFAULT_UDP_PORT

APP_NAME = "f1-telemetry"

_log = logging.getLogger(__name__)

__all__ = [
    "APP_NAME",
    "AppConfig",
    "config_file",
    "data_dir",
    "ensure_dirs",
    "lan_addresses",
    "load_config",
    "log_dir",
    "save_config",
    "setup_logging",
    "xdg_config_home",
    "xdg_data_home",
    "xdg_state_home",
]


# --- XDG-каталоги ------------------------------------------------------------

def xdg_data_home() -> Path:
    """Корень данных пользователя (``$XDG_DATA_HOME`` или ``~/.local/share``)."""
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local/share"))


def xdg_config_home() -> Path:
    """Корень конфигов (``$XDG_CONFIG_HOME`` или ``~/.config``)."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))


def xdg_state_home() -> Path:
    """Корень состояния (``$XDG_STATE_HOME`` или ``~/.local/state``)."""
    return Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local/state"))


def data_dir() -> Path:
    """``<data-home>/f1-telemetry`` — тут лежит БД по умолчанию."""
    return xdg_data_home() / APP_NAME


def config_dir() -> Path:
    """``<config-home>/f1-telemetry`` — тут лежит config.json."""
    return xdg_config_home() / APP_NAME


def state_dir() -> Path:
    """``<state-home>/f1-telemetry`` — тут лежат логи."""
    return xdg_state_home() / APP_NAME


def default_db_path() -> Path:
    """Путь к SQLite-базе по умолчанию."""
    return data_dir() / "f1.sqlite"


def log_dir() -> Path:
    """Каталог файловых логов."""
    return state_dir() / "logs"


def config_file() -> Path:
    """Путь к JSON-конфигу."""
    return config_dir() / "config.json"


def ensure_dirs() -> None:
    """Создаёт каталоги данных/конфига/логов (best-effort, вызывается при старте)."""
    for d in (data_dir(), config_dir(), log_dir()):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:  # noqa: BLE001 — не роняем старт из-за прав
            _log.warning("не удалось создать каталог %s", d)


# --- Конфиг ------------------------------------------------------------------

@dataclass
class AppConfig:
    """Настройки приложения (сохраняются в config.json, применяются сразу)."""

    host: str = DEFAULT_LISTEN_HOST
    """Адрес прослушивания UDP (0.0.0.0 — все интерфейсы)."""
    port: int = DEFAULT_UDP_PORT
    """UDP-порт игры (по умолчанию 20777)."""
    db_path: str = ""
    """Путь к БД; пустая строка = :func:`default_db_path` (XDG)."""
    autostart: bool = True
    """Автостарт записи при запуске GUI."""

    def resolved_db(self) -> Path:
        """Путь к БД: пользовательский или XDG-дефолт."""
        if self.db_path:
            return Path(self.db_path).expanduser()
        return default_db_path()


def load_config(path: Path | None = None) -> AppConfig:
    """Читает config.json; при отсутствии/ошибке — дефолты.

    Args:
        path: явный путь (по умолчанию — :func:`config_file`).

    Returns:
        :class:`AppConfig` (никогда не бросает).
    """
    p = Path(path) if path is not None else config_file()
    cfg = AppConfig()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return cfg
    if not isinstance(raw, dict):
        return cfg
    if isinstance(raw.get("host"), str) and raw["host"]:
        cfg.host = raw["host"]
    if isinstance(raw.get("port"), int) and 0 < raw["port"] < 65536:
        cfg.port = raw["port"]
    if isinstance(raw.get("db_path"), str):
        cfg.db_path = raw["db_path"]
    if isinstance(raw.get("autostart"), bool):
        cfg.autostart = raw["autostart"]
    return cfg


def save_config(cfg: AppConfig, path: Path | None = None) -> Path:
    """Сохраняет конфиг (создаёт каталог). Возвращает фактический путь."""
    p = Path(path) if path is not None else config_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(asdict(cfg), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return p


# --- Логирование -------------------------------------------------------------

_file_handler: logging.FileHandler | None = None


def setup_logging(verbose: bool = False) -> None:
    """Консоль + файл в ``log_dir()/app.log`` (один FileHandler на процесс).

    Уровень: ``$F1TELEMETRY_LOG`` или DEBUG при ``verbose``, иначе INFO.
    """
    global _file_handler

    level_name = os.environ.get("F1TELEMETRY_LOG", "").upper()
    level = getattr(logging, level_name, None) or (
        logging.DEBUG if verbose else logging.INFO
    )
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    if not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    ):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    if _file_handler is None:
        try:
            d = log_dir()
            d.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(d / "app.log", encoding="utf-8")
            fh.setFormatter(fmt)
            root.addHandler(fh)
            _file_handler = fh
        except OSError:  # noqa: BLE001 — без файлового лога тоже работаем
            _log.debug("файловый лог недоступен", exc_info=True)


# --- Сеть --------------------------------------------------------------------

def lan_addresses() -> list[str]:
    """Локальные IPv4-адреса машины (для ввода в настройках игры).

    Сначала «UDP-трюк» (основной исходящий интерфейр), затем все адреса
    hostname; дубликаты убираются. Список может быть пустым (оффлайн).
    """
    addrs: list[str] = []

    def _add(ip: str) -> None:
        if ip and ip not in addrs and not ip.startswith("127."):
            addrs.append(ip)

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("223.223.223.223", 53))  # пакет не уходит, нужен только локальный IP
        _add(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            _add(info[4][0])
    except OSError:
        pass
    return addrs
