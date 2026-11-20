"""Эталонный Whisper от OpenAI (пакет openai-whisper, требует PyTorch).

Устанавливается опционально: pip install -U openai-whisper
"""
from __future__ import annotations

import numpy as np

from .base import EngineUnavailable, TranscriptionEngine, TranscriptionResult
from .registry import register_engine


@register_engine
class OpenAIWhisperEngine(TranscriptionEngine):
    name = "openai-whisper"
    label = "Whisper, локально (openai-whisper)"

    def __init__(
        self,
        model_size: str = "small",
        device: str = "auto",
        language: str = "",
        initial_prompt: str = "",
        **_,
    ) -> None:
        super().__init__()
        try:
            import whisper  # noqa: F401  (проверка наличия пакета)
        except ImportError as exc:
            raise EngineUnavailable(
                "Не установлен openai-whisper: pip install -U openai-whisper (потребует PyTorch)"
            ) from exc
        self._model_size = model_size
        self._device_pref = None if device in ("", "auto") else device
        self._model = None
        self.language = (language or "").strip() or None
        self.initial_prompt = (initial_prompt or "").strip() or None

    def _ensure_model(self):
        if self._model is None:
            import torch
            import whisper

            device = self._device_pref or ("cuda" if torch.cuda.is_available() else "cpu")
            self._model = whisper.load_model(self._model_size, device=device)
        return self._model

    def warmup(self) -> None:
        self._ensure_model()

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> TranscriptionResult:
        if audio.size == 0:
            return TranscriptionResult("")
        model = self._ensure_model()
        result = model.transcribe(
            audio,
            language=self.language,
            initial_prompt=self.initial_prompt,
            fp16=False,
        )
        return TranscriptionResult(
            text=(result.get("text") or "").strip(),
            language=result.get("language"),
        )
