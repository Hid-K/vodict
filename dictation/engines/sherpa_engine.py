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
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

MODEL_REPO = "csukuangfj/sherpa-onnx-streaming-zipformer-small-ru-vosk-int8-2025-08-16"

from .base import EngineUnavailable, TranscriptionEngine, TranscriptionResult
from .registry import register_engine


def _log_tqdm_class():
    """tqdm, который раз в 2 сек пишет прогресс загрузки в лог."""
    try:
        from tqdm import tqdm  # noqa: PLC0415
    except Exception:
        return None

    class _LogTqdm(tqdm):
        _last_log = 0.0

        def display(self, msg=None, pos=None):
            import time  # noqa: PLC0415

            now = time.monotonic()
            if now - type(self)._last_log >= 2.0:
                type(self)._last_log = now
                log.info("Модель Sherpa: %s", self.format_msg(msg))
            super().display(msg, pos)

    return _LogTqdm


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
        if not ((model_size or "").strip() and path.is_dir()
                and (path / "tokens.txt").exists()):
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

    def _decode(self, stream) -> None:
        """Декодирование потока: API sherpa-onnx 1.x и 2.x различаются."""
        if hasattr(self._recognizer, "decode_streams"):
            self._recognizer.decode_streams([stream])
        else:
            while self._recognizer.is_ready(stream):
                self._recognizer.decode(stream)

    @staticmethod
    def _result_text(recognizer, stream) -> str:
        r = getattr(stream, "result", None)
        if r is not None and hasattr(r, "text"):
            return r.text
        return recognizer.get_result(stream)

    @staticmethod
    def _ensure_auto_model() -> Path:
        """Скачивает и распаковывает стриминговую модель, если её нет."""
        from huggingface_hub import snapshot_download  # noqa: PLC0415

        try:
            from platformdirs import user_data_dir  # noqa: PLC0415

            base = Path(user_data_dir("vodict", appauthor=False))
        except Exception:
            base = Path.home() / "Vodict"
        dest = base / "models" / MODEL_REPO
        if (dest / "tokens.txt").exists():
            return dest

        log.info("Скачивание модели Sherpa: %s (huggingface_hub)", MODEL_REPO)
        snapshot_download(repo_id=MODEL_REPO, local_dir=dest,
                          tqdm_class=_log_tqdm_class())
        if not (dest / "tokens.txt").exists():
            raise EngineUnavailable(
                f"После загрузки не найден tokens.txt (папка: {dest})"
            )
        return dest

    def open_stream(self, sample_rate: int = 16000):
        return _SherpaStreamSession(self, self._recognizer)

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> TranscriptionResult:
        audio = np.asarray(audio, dtype=np.float32)
        stream = self._recognizer.create_stream()
        stream.accept_waveform(sample_rate, audio)
        self._decode(stream)
        text = self._result_text(self._recognizer, stream).strip()
        return TranscriptionResult(
            text=text,
            audio_duration=len(audio) / float(sample_rate) if sample_rate else None,
        )

    def describe(self) -> str:
        return self.label


class _SherpaStreamSession:
    """Live-сессия Sherpa-onnx: расшифровка накопленного аудио на лету."""

    PAD = 24000  # 1.5 с тишины: sherpa падает на аудио короче ~1 с

    def __init__(self, engine, recognizer) -> None:
        self._engine = engine
        self._rec = recognizer
        self._stream = recognizer.create_stream()
        self._left_padded = False

    def _decode_stream(self, stream) -> None:
        self._engine._decode(stream)

    def _result_text(self, recognizer, stream) -> str:
        return self._engine._result_text(recognizer, stream)

    def feed(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        audio = np.asarray(audio, dtype=np.float32)
        if audio.size == 0:
            return ""
        if not self._left_padded:
            audio = np.pad(audio, (self.PAD, 0))
            self._left_padded = True
        self._stream.accept_waveform(sample_rate, audio)
        self._decode_stream(self._stream)
        return self._result_text(self._rec, self._stream).strip()

    def finalize(self) -> str:
        self._stream.accept_waveform(16000, np.zeros(self.PAD, dtype=np.float32))
        self._stream.input_finished()
        self._decode_stream(self._stream)
        text = self._result_text(self._rec, self._stream).strip()
        self._stream = self._rec.create_stream()
        return text
