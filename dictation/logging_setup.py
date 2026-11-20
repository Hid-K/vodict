"""Настройка логирования приложения.

Лог пишется в ротируемый файл рядом с конфигом
(<config dir>/logs/app.log). При ``--debug`` дублируется в stderr.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from .config import default_config_path


def setup_logging(debug: bool = False) -> Path:
    """Инициализирует логирование. Возвращает путь к файлу лога."""
    log_dir = default_config_path().parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / "app.log"

    level = logging.DEBUG if debug else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(level)

    # Лог-файл не критичен: если его нельзя создать (права, read-only),
    # приложение продолжает работать с логом в stderr.
    file_ok = False
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            logfile, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
        file_ok = True
    except OSError as exc:
        print(f"[log] Лог-файл недоступен ({logfile}): {exc}", file=sys.stderr)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    console.setLevel(logging.DEBUG if (debug or not file_ok) else logging.WARNING)
    root.addHandler(console)

    # Шумные библиотеки — только предупреждения
    for noisy in ("httpx", "httpcore", "urllib3", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logfile
