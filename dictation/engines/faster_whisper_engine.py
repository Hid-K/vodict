"""Whisper локально через faster-whisper (CTranslate2) — движок по умолчанию.

Быстрый и экономный по памяти вариант: int8 на CPU в разы быстрее
эталонного openai-whisper. Модель скачивается автоматически при первом
запуске (кэш HuggingFace).
"""
from __future__ import annotations

import numpy as np

from .base import EngineUnavailable, TranscriptionEngine, TranscriptionResult
from .registry import register_engine

SUPPORTED_MODELS = [
    "tiny", "base", "small", "medium",
    "large-v2", "large-v3", "large-v3-turbo",
]


@register_engine
class FasterWhisperEngine(TranscriptionEngine):
    name = "faster-whisper"
    label = "Whisper, локально (faster-whisper)"

    def __init__(
        self,
        model_size: str = "small",
        device: str = "auto",        # auto | cpu | cuda
        compute_type: str = "auto",  # auto | int8 | int8_float16 | float16 | float32
        language: str = "",
        beam_size: int = 5,
        initial_prompt: str = "",
        vad_filter: bool = True,
        cpu_threads: int = 0,
        **_,
    ) -> None:
        super().__init__()
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover
            raise EngineUnavailable(
                "Не установлен faster-whisper: pip install faster-whisper"
            ) from exc

        kwargs: dict = {}
        if device and device != "auto":
            kwargs["device"] = device
        if compute_type and compute_type != "auto":
            kwargs["compute_type"] = compute_type
        if cpu_threads:
            kwargs["cpu_threads"] = cpu_threads

        self.language = (language or "").strip() or None
        self.beam_size = beam_size
        self.initial_prompt = (initial_prompt or "").strip() or None
        self.vad_filter = vad_filter

        # Модель уже в кэше HuggingFace -> грузим локально, без сетевых
        # проверок обновлений при каждом создании движка (иначе каждый
        # пересозд идёт в HF API и упирается в лимиты анонимных запросов).
        try:
            self._model = WhisperModel(model_size, local_files_only=True, **kwargs)
        except Exception:
            # Модели нет в кэше (или кэш повреждён) — обычный путь
            # со скачиванием через HuggingFace.
            self._model = WhisperModel(model_size, **kwargs)

        self._model_size = model_size

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> TranscriptionResult:
        if audio.size == 0:
            return TranscriptionResult("")
        segments, info = self._model.transcribe(
            audio,
            language=self.language,
            beam_size=self.beam_size,
            initial_prompt=self.initial_prompt,
            vad_filter=self.vad_filter,
        )
        parts = [seg.text.strip() for seg in segments]
        text = " ".join(p for p in parts if p).strip()
        return TranscriptionResult(
            text=text,
            language=getattr(info, "language", None),
            language_probability=getattr(info, "language_probability", None),
            audio_duration=getattr(info, "duration", None),
        )

    def describe(self) -> str:
        return f"{self.label} — {self._model_size}"
