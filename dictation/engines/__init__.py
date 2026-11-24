"""Пакет движков распознавания.

Импорт модуля движка здесь регистрирует его в реестре — больше нигде
регистрацию делать не нужно. Чтобы добавить новый движок: создайте файл
по образцу ``vosk_engine`` из докстринга ``base.py`` и добавьте строку
импорта ниже.
"""
from .registry import available_engines, create_engine, register_engine

from . import faster_whisper_engine  # noqa: F401
from . import whisper_ref_engine     # noqa: F401
from . import openai_api_engine      # noqa: F401
from . import vosk_engine            # noqa: F401
from . import sherpa_engine          # noqa: F401

__all__ = ["available_engines", "create_engine", "register_engine"]
