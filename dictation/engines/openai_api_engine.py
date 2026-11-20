"""Облачный Whisper через OpenAI API (или любой совместимый endpoint).

Пример удалённого движка: тяжёлая модель не нужна, аудио уходит по сети.
Требуется api_key в настройках или переменная окружения OPENAI_API_KEY.
"""
from __future__ import annotations

import io
import os
import wave

import numpy as np

from .base import EngineUnavailable, TranscriptionEngine, TranscriptionResult
from .registry import register_engine


@register_engine
class OpenAIApiEngine(TranscriptionEngine):
    name = "openai-api"
    label = "OpenAI Whisper API (облако)"

    def __init__(
        self,
        api_key: str = "",
        api_base_url: str = "",
        api_model: str = "whisper-1",
        language: str = "",
        initial_prompt: str = "",
        **_,
    ) -> None:
        super().__init__()
        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise EngineUnavailable(
                "Нет API-ключа: заполните «API-ключ» в настройках "
                "или задайте переменную окружения OPENAI_API_KEY"
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise EngineUnavailable(
                "Не установлен пакет openai: pip install openai"
            ) from exc

        kwargs = {"api_key": key}
        if api_base_url:
            kwargs["base_url"] = api_base_url
        self._client = OpenAI(**kwargs)
        self.model = api_model
        self.language = (language or "").strip() or None
        self.prompt = (initial_prompt or "").strip() or None

    @staticmethod
    def _to_wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
        buf = io.BytesIO()
        pcm = np.clip(audio, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype("<i2")
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm.tobytes())
        return buf.getvalue()

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> TranscriptionResult:
        if audio.size == 0:
            return TranscriptionResult("")
        wav = self._to_wav_bytes(audio, sample_rate)
        kwargs: dict = {
            "model": self.model,
            "file": ("speech.wav", wav, "audio/wav"),
        }
        if self.language:
            kwargs["language"] = self.language
        if self.prompt:
            kwargs["prompt"] = self.prompt
        resp = self._client.audio.transcriptions.create(**kwargs)
        text = getattr(resp, "text", "") or ""
        return TranscriptionResult(text=text.strip(), language=self.language)
