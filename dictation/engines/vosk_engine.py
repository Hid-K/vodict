"""Движок Vosk: офлайн-стриминговое распознавание, 20+ языков (Apache-2.0).

Vosk — Kaldi-based ASR: лёгкий, быстрый на CPU, модели по языкам с
https://alphacephei.com/vosk/models (ru, en-us, de, es, fr, ...).

Русская модель (~45 МБ) скачается автоматически при первом выборе движка.
Своя модель: путь к распакованной папке — в поле «Модель» настроек.
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)

import numpy as np

from .base import EngineUnavailable, TranscriptionEngine, TranscriptionResult
from .registry import register_engine


@register_engine
class VoskEngine(TranscriptionEngine):
    name = "vosk"
    label = "Vosk (20+ языков, стриминг, CPU)"

    MODEL_NAME = "vosk-model-small-ru-0.22"
    MODEL_URL = "https://alphacephei.com/vosk/models/" + MODEL_NAME + ".zip"

    def __init__(self, model_size: str = "", progress_cb=None, **_):
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
            path = str(self._ensure_auto_model(progress_cb))
        if not Path(path).is_dir():
            raise EngineUnavailable(f"Папка модели Vosk не найдена: {path}")

        self._model = Model(path)
        self.actual_device = "cpu (kaldi)"
        self.actual_compute_type = "kaldi"

    @classmethod
    def _ensure_auto_model(cls, progress_cb=None) -> Path:
        """Скачивает и распаковывает русскую модель Vosk, если её нет."""
        import requests  # noqa: PLC0415

        try:
            from platformdirs import user_data_dir  # noqa: PLC0415

            base = Path(user_data_dir("vodict", appauthor=False))
        except Exception:
            base = Path.home() / "Vodict"
        dest = base / "models" / cls.MODEL_NAME
        if (dest / "conf").exists():
            return dest

        log.info("Скачивание модели Vosk: %s", cls.MODEL_URL)
        base.mkdir(parents=True, exist_ok=True)
        archive = base / (cls.MODEL_NAME + ".zip")
        with requests.get(cls.MODEL_URL, stream=True, timeout=(30, 120)) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            done = 0
            with open(archive, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 18):
                    fh.write(chunk)
                    done += len(chunk)
                    if progress_cb and total:
                        progress_cb(min(99, done * 100 // total), "vosk-model")
        log.info("Распаковка модели Vosk в %s", dest)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(base)
        archive.unlink(missing_ok=True)
        if not dest.is_dir():
            raise EngineUnavailable(
                f"После распаковки не найдена папка модели: {dest}"
            )
        if progress_cb:
            progress_cb(100, "готово")
        return dest

    def open_stream(self, sample_rate: int = 16000):
        from vosk import KaldiRecognizer

        return _VoskStreamSession(KaldiRecognizer(self._model, sample_rate))

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


class _VoskStreamSession:
    """Live-сессия Vosk: PartialResult во время записи, FinalResult в конце."""

    def __init__(self, recognizer) -> None:
        self._rec = recognizer

    def feed(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
               * 32767).astype(np.int16).tobytes()
        self._rec.AcceptWaveform(pcm)
        partial = json.loads(self._rec.PartialResult()).get("partial", "")
        return partial.strip()

    def finalize(self) -> str:
        result = json.loads(self._rec.FinalResult()).get("text", "")
        return result.strip()
