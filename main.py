#!/usr/bin/env python3
"""Запуск приложения: python main.py (или python -m dictation)."""
import sys

from dictation.cli import main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        # В windowed-сборке stderr отсутствует: без этого блока любая
        # фатальная ошибка — молчаливый exit 1 без единого следа.
        try:
            import traceback

            from dictation.config import default_config_path

            crash = default_config_path().parent / "logs" / "crash.log"
            crash.parent.mkdir(parents=True, exist_ok=True)
            with open(crash, "a", encoding="utf-8") as f:
                traceback.print_exc(file=f)
        except Exception:
            pass
        raise
