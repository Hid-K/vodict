"""Движок Vosk: офлайн-стриминговое распознавание, 20+ языков (Apache-2.0).

Vosk — Kaldi-based ASR: лёгкий, быстрый на CPU, модели по языкам с
https://alphacephei.com/vosk/models (ru, en-us, de, es, fr, ...).

Настройка: скачайте модель для нужного языка, распакуйте и укажите путь
к распакованной папке в поле «Модель» настроек (или --model).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .base import EngineUnavailable, TranscriptionEngine, TranscriptionResult
from .registry import register_engine


@register_engine
class VoskEngine(TranscriptionEngine):
    name = "vosk"
    label = "Vosk (20+ языков, стриминг, CPU)"

    def __init__(self, model_size: str = "", **_):
        super().__init__()
        try:
            from vosk import Model, SetLogLevel  # noqa: PLC0415
        except ImportError as exc:
            raise EngineUnavailable(
                "Пакет vosk не установлен. Установите: pip install vosk"
            ) from exc

        SetLogLevel(-1)
        path = (model_size or "").strip()
        if not path:
            raise EngineUnavailable(
                "Для Vosk укажите путь к папке модели в поле «Модель»: "
                "скачайте модель для нужного языка с "
                "https://alphacephei.com/vosk/models и распакуйте "
                "(например, vosk-model-small-ru-0.22)"
            )
        if not Path(path).is_dir():
            raise EngineUnavailable(f"Папка модели Vosk не найдена: {path}")

        self._model = Model(path)
        self.actual_device = "cpu (kaldi)"
        self.actual_compute_type = "kaldi"

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> TranscriptionResult:
        from vosk import KaldiRecognizer  # локальный импорт: быстрый старт

        pcm = np.clip(audio, -1.0, 1.0)
        pcm = (pcm * 32767).astype(np.int16).tobytes()

        rec = KaldiRecognizer(self._model, sample_rate)
        chunk = sample_rate * 2  # 1 секунда PCM16 = 2 байта * частота
        for i in range(0, len(pcm), chunk):
            rec.AcceptWaveform(pcm[i:i + chunk])

        result = json.loads(rec.FinalResult())
        text = (result.get("text") or "").strip()
        return TranscriptionResult(
            text=text,
            audio_duration=len(audio) / float(sample_rate) if sample_rate else None,
        )

    def describe(self) -> str:
        return f"{self.label} — {self._model.ModelPath()}" if hasattr(
            self._model, "ModelPath") else self.label
