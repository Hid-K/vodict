"""Движок Sherpa-onnx: стриминговый Zipformer (k2-fsa, Apache-2.0).

Современные потоковые трансдьюсер-модели ONNX для множества языков,
включая мультиязычную (арабский, английский, индонезийский, японский,
русский, тайский, вьетнамский, китайский — 2025) и отдельные ru/en.

Модель скачается автоматически при первом выборе движка (~65 МБ,
стриминговый zipformer ru с релизов k2-fsa). Можно указать и свою
распакованную модель: путь к папке — в поле «Модель» настроек.
"""
from __future__ import annotations

import logging
import shutil
import tarfile
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

MODEL_NAME = "sherpa-onnx-streaming-zipformer-ru-2023-11-01"
MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    + MODEL_NAME + ".tar.bz2"
)

from .base import EngineUnavailable, TranscriptionEngine, TranscriptionResult
from .registry import register_engine


@register_engine
class SherpaEngine(TranscriptionEngine):
    name = "sherpa"
    label = "Sherpa-onnx (стриминг, zipformer, CPU)"

    def __init__(self, model_size: str = "", **_):
        super().__init__()
        try:
            import sherpa_onnx  # noqa: PLC0415
        except ImportError as exc:
            raise EngineUnavailable(
                "Пакет sherpa-onnx не установлен. Установите: pip install sherpa-onnx"
            ) from exc

        path = Path((model_size or "").strip())
        if not path.is_dir():
            path = self._ensure_auto_model()

        def pick(pattern: str) -> str:
            exact = path / pattern
            if exact.exists():
                return str(exact)
            matches = sorted(path.glob(pattern.replace(".onnx", "*.onnx")))
            if not matches:
                raise EngineUnavailable(
                    f"В папке модели не найдено {pattern} (папка: {path})"
                )
            # предпочитаем int8 (быстрее и меньше) — сортировка поставит
            # базовый encoder.onnx первым, если он есть
            for m in matches:
                if "int8" in m.name:
                    return str(m)
            return str(matches[0])

        tokens = path / "tokens.txt"
        if not tokens.exists():
            raise EngineUnavailable(f"Не найден tokens.txt (папка: {path})")

        self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            encoder=pick("encoder.onnx"),
            decoder=pick("decoder.onnx"),
            joiner=pick("joiner.onnx"),
            tokens=str(tokens),
            num_threads=2,
        )
        self.actual_device = "cpu (onnx)"
        self.actual_compute_type = "onnx"

    @staticmethod
    def _ensure_auto_model() -> Path:
        """Скачивает и распаковывает стриминговую модель, если её нет."""
        import requests  # noqa: PLC0415

        try:
            from platformdirs import user_data_dir  # noqa: PLC0415

            base = Path(user_data_dir("vodict", appauthor=False))
        except Exception:
            base = Path.home() / "Vodict"
        dest = base / "models" / MODEL_NAME
        if (dest / "tokens.txt").exists():
            return dest

        base.mkdir(parents=True, exist_ok=True)
        archive = base / (MODEL_NAME + ".tar.bz2")
        log.info("Скачивание модели Sherpa: %s", MODEL_URL)
        with requests.get(MODEL_URL, stream=True, timeout=(30, 120)) as r:
            r.raise_for_status()
            with open(archive, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
        log.info("Распаковка модели Sherpa в %s", dest)
        with tarfile.open(archive, "r:bz2") as tf:
            tf.extractall(base)
        extracted = base / MODEL_NAME
        if not (extracted / "tokens.txt").exists():
            raise EngineUnavailable(
                f"После распаковки не найден tokens.txt (папка: {extracted})"
            )
        archive.unlink(missing_ok=True)
        return extracted

    def open_stream(self, sample_rate: int = 16000):
        return _SherpaStreamSession(self._recognizer)

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> TranscriptionResult:
        audio = np.asarray(audio, dtype=np.float32)
        stream = self._recognizer.create_stream()
        stream.accept_waveform(sample_rate, audio)
        while self._recognizer.is_ready(stream):
            self._recognizer.decode(stream)
        text = self._recognizer.get_result(stream).strip()
        return TranscriptionResult(
            text=text,
            audio_duration=len(audio) / float(sample_rate) if sample_rate else None,
        )

    def describe(self) -> str:
        return self.label


class _SherpaStreamSession:
    """Live-сессия Sherpa-onnx: расшифровка накопленного аудио на лету."""

    def __init__(self, recognizer) -> None:
        self._rec = recognizer
        self._stream = recognizer.create_stream()

    def feed(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        audio = np.asarray(audio, dtype=np.float32)
        if audio.size == 0:
            return ""
        self._stream.accept_waveform(sample_rate, audio)
        while self._rec.is_ready(self._stream):
            self._rec.decode(self._stream)
        return self._rec.get_result(self._stream).strip()

    def finalize(self) -> str:
        self._stream.input_finished()
        while self._rec.is_ready(self._stream):
            self._rec.decode(self._stream)
        text = self._rec.get_result(self._stream).strip()
        self._stream = self._rec.create_stream()
        return text
