#!/usr/bin/env python3
"""Запуск GUI: ``python run.py`` (или ``uv run run.py``)."""

from __future__ import annotations

import sys

from f1telemetry.ui.app import main

if __name__ == "__main__":
    sys.exit(main())
