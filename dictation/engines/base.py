"""Базовые контракты движков распознавания речи (STT).

Как добавить свой движок (пример — Vosk, Google Speech, локальная LLM):

    # dictation/engines/vosk_engine.py
    from .base import TranscriptionEngine, TranscriptionResult, EngineUnavailable
    from .registry import register_engine

    @register_engine
    class VoskEngine(TranscriptionEngine):
        name = "vosk"                 # попадёт в config.engine и в UI
        label = "Vosk (офлайн)"

        def __init__(self, model_path="vosk-model-small-ru-0.22", **_):
            super().__init__()
            try:
                from vosk import Model
            except ImportError as exc:
                raise EngineUnavailable("pip install vosk") from exc
            self._model = Model(model_path)

        def transcribe(self, audio, sample_rate=16000):
            ...
            return TranscriptionResult(text="...")

Больше ничего менять не нужно: модуль достаточно импортировать в
``dictation/engines/__init__.py`` — движок автоматически появится в
настройках интерфейса и в CLI.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass
class TranscriptionResult:
    """Результат распознавания одного фрагмента аудио."""

    text: str
    language: str | None = None
    language_probability: float | None = None
    audio_duration: float | None = None


class EngineUnavailable(RuntimeError):
    """Движок нельзя создать: не установлен пакет, нет API-ключа и т.п."""


class TranscriptionEngine(ABC):
    """Интерфейс движка распознавания речи.

    Вход: ``audio`` — numpy float32, mono, диапазон [-1, 1], частота
    ``sample_rate`` Гц (контроллер всегда передаёт 16 кГц).
    Выход: ``TranscriptionResult``.

    Жёсткие зависимости движка (torch, vosk, openai...) импортируются в
    ``__init__`` и оборачиваются в ``EngineUnavailable`` с понятным
    сообщением — приложение при этом продолжает работать с другими движками.
    """

    name: str = "abstract"   # идентификатор для config.engine / CLI / UI
    label: str = "Абстрактный движок"

    def __init__(self, **options) -> None:
        self.options = dict(options)

    @abstractmethod
    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> TranscriptionResult:
        raise NotImplementedError

    def warmup(self) -> None:
        """Необязательный прогрев: загрузка модели, тестовый запрос."""

    def close(self) -> None:
        """Освобождение ресурсов (вызывается при выходе / смене настроек)."""

    def describe(self) -> str:
        return self.label

    def open_stream(self, sample_rate: int = 16000):
        """Стриминговая сессия распознавания «на лету» (опционально).

        Возвращает объект с методами:
          feed(audio16k_float32, sample_rate) -> str  # промежуточный текст
          finalize() -> str                            # полный текст фрагмента
        или None, если движок не поддерживает live-режим — тогда контроллер
        использует обычный батчевый путь (transcribe после остановки записи).
        """
        return None
