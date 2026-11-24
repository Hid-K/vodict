"""Движок T-one: потоковое распознавание русской речи (t-tech, Apache-2.0).

T-one — 71M Conformer CTC от Т-Технологий, специализирован на русском
языке, работает на CPU через ONNX Runtime без torch и поддерживает
настоящий стриминг (аудио идёт чанками по 300 мс @ 8 кГц).

Этот движок интегрирует офлайн-режим пайплайна: фрагмент диктовки
распознаётся целиком — пайплайн внутри сам режет аудио на чанки и
находит границы фраз (вставляются с переносами строк).

Зависимости инференса (ставятся отдельно от faster-whisper):
    pip install git+https://github.com/voicekit-team/T-one
    pip install onnxruntime pyctcdecode kenlm

Если kenlm не собирается (нужен компилятор), движок автоматически
переключается на greedy-декодер без потери работоспособности.
"""
from __future__ import annotations

import numpy as np

from ..audio.recorder import ensure_rate
from .base import EngineUnavailable, TranscriptionEngine, TranscriptionResult
from .registry import register_engine

TARGET_RATE = 8000  # T-one обучена на телефонном аудио 8 кГц


@register_engine
class ToneEngine(TranscriptionEngine):
    name = "tone"
    label = "T-one (русский, стриминг, CPU)"

    def __init__(self, **_):
        super().__init__()
        try:
            from tone import StreamingCTCPipeline  # noqa: PLC0415
        except ImportError as exc:
            raise EngineUnavailable(
                "Пакет tone не установлен. Установите зависимости T-one:\n"
                "  pip install git+https://github.com/voicekit-team/T-one\n"
                "  pip install onnxruntime pyctcdecode kenlm"
            ) from exc

        # kenlm требует компилятора на некоторых системах: при его
        # отсутствии деградируем на greedy-декодер без потери работы.
        try:
            self._pipeline = StreamingCTCPipeline.from_hugging_face()
            self._decoder = "beam-search + KenLM"
        except Exception:
            try:
                from tone.decoder import DecoderType  # noqa: PLC0415

                self._pipeline = StreamingCTCPipeline.from_hugging_face(
                    decoder_type=DecoderType.GREEDY
                )
                self._decoder = "greedy"
            except Exception as exc:
                raise EngineUnavailable(
                    f"Не удалось загрузить T-one: {exc}"
                ) from exc

        # Фактические параметры для истории и логов (см. _finish_history)
        self.actual_device = "cpu (onnx)"
        self.actual_compute_type = "ctc-int32"

    def open_stream(self, sample_rate: int = 16000):
        return _ToneStreamSession(self._pipeline)

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> TranscriptionResult:
        duration = len(audio) / float(sample_rate) if sample_rate else None

        # 1) Приводим к 8 кГц (родная частота T-one).
        audio8 = ensure_rate(audio, sample_rate, TARGET_RATE)
        # 2) float [-1, 1] -> int32 со значениями int16-диапазона — формат
        #    входа пайплайна (как в tone.demo.read_audio).
        pcm = np.clip(audio8 * 32768.0, -32768, 32767).astype(np.int32)

        phrases = self._pipeline.forward_offline(pcm)
        text = "\n".join(p.text for p in phrases if p.text).strip()

        return TranscriptionResult(
            text=text,
            language="ru" if text else None,
            language_probability=1.0 if text else None,
            audio_duration=duration,
        )

    def describe(self) -> str:
        return f"{self.label} — {self._decoder}"


class _ToneStreamSession:
    """Live-сессия T-one: накопление 8к-int32 буфера и чанк-обработка."""

    CHUNK = 2400  # 300 мс @ 8 кГц

    def __init__(self, pipeline) -> None:
        self._pipeline = pipeline
        self._buf = np.zeros(0, dtype=np.int32)
        self._state = None
        self._phrases: list[str] = []

    def feed(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        audio8 = ensure_rate(np.asarray(audio, dtype=np.float32), sample_rate, 8000)
        if audio8.size == 0:
            return ""
        pcm = np.clip(audio8 * 32768.0, -32768, 32767).astype(np.int32)
        self._buf = np.concatenate([self._buf, pcm])
        new: list[str] = []
        while self._buf.size >= self.CHUNK:
            chunk = self._buf[:self.CHUNK]
            self._buf = self._buf[self.CHUNK:]
            phrases, self._state = self._pipeline.forward(chunk, self._state)
            for p in phrases:
                self._phrases.append(p.text)
                new.append(p.text)
        return "\n".join(new).strip()

    def finalize(self) -> str:
        # добираем хвост меньше чанка
        if self._buf.size:
            chunk = np.zeros(self.CHUNK, dtype=np.int32)
            chunk[:self._buf.size] = self._buf
            self._buf = np.zeros(0, dtype=np.int32)
            phrases, self._state = self._pipeline.forward(chunk, self._state)
            for p in phrases:
                self._phrases.append(p.text)
        phrases, _ = self._pipeline.finalize(self._state)
        for p in phrases:
            self._phrases.append(p.text)
        return "\n".join(self._phrases).strip()
