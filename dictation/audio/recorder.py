"""Микрофон: запись в память как float32 mono + утилита передискретизации."""
from __future__ import annotations

import threading
import time

import numpy as np

try:
    import sounddevice as sd
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Не установлен sounddevice. Установите зависимости: pip install -r requirements.txt"
    ) from exc

TARGET_RATE = 16000


def list_input_devices() -> list[dict]:
    devices = []
    default_index = sd.default.device[0]
    for idx, dev in enumerate(sd.query_devices()):
        if int(dev.get("max_input_channels", 0)) > 0:
            devices.append(
                {
                    "index": idx,
                    "name": str(dev["name"]),
                    "default_samplerate": dev["default_samplerate"],
                    "is_default": idx == default_index,
                }
            )
    return devices


def resolve_device(device) -> int | None:
    """None/"" — по умолчанию; int или числовая строка — индекс из --list-mics;
    иначе — первое устройство, чьё имя содержит строку."""
    if device in (None, ""):
        return None
    if isinstance(device, int):
        return device
    text = str(device).strip()
    if text.isdigit():
        return int(text)
    needle = text.casefold()
    for dev in list_input_devices():
        if needle in dev["name"].casefold():
            return dev["index"]
    return None


class MicrophoneRecorder:
    """start() -> говорим -> stop() возвращает (audio, sample_rate).

    Уровень сигнала (RMS последнего блока) доступен как ``.level`` —
    используется индикатором в интерфейсе и авто-стопом по тишине.
    """

    def __init__(self, sample_rate: int = TARGET_RATE, device=None, blocksize: int = 0):
        self.requested_rate = sample_rate
        self.device = device
        self.blocksize = blocksize
        self._stream: sd.InputStream | None = None
        self._frames: list[np.ndarray] = []
        self._lock = threading.Lock()
        self.level = 0.0
        self.started_at: float | None = None
        self.actual_rate = sample_rate

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._stream is not None:
            raise RuntimeError("Запись уже идёт")
        with self._lock:
            self._frames = []
        self.level = 0.0

        dev_index = resolve_device(self.device)
        params = dict(
            samplerate=self.requested_rate,
            channels=1,
            dtype="float32",
            device=dev_index,
            blocksize=self.blocksize,
            callback=self._callback,
        )
        try:
            stream = sd.InputStream(**params)
            self.actual_rate = self.requested_rate
        except sd.PortAudioError:
            # Часть микрофонов не открывается на 16 кГц — берём дефолтную частоту
            # устройства, а перед записью в движок приводим к 16 кГц (ensure_rate).
            fallback = 48000
            try:
                info = sd.query_devices(dev_index)
                fallback = int(float(info["default_samplerate"]))
            except Exception:
                pass
            params["samplerate"] = fallback
            stream = sd.InputStream(**params)
            self.actual_rate = fallback

        stream.start()
        self._stream = stream
        self.started_at = time.monotonic()

    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        return time.monotonic() - self.started_at

    def stop(self) -> tuple[np.ndarray, int]:
        if self._stream is None:
            raise RuntimeError("Запись не запущена")
        stream, self._stream = self._stream, None
        try:
            stream.stop()
        finally:
            stream.close()
        with self._lock:
            frames, self._frames = self._frames, []
        self.level = 0.0
        self.started_at = None
        if not frames:
            return np.zeros(0, dtype=np.float32), self.actual_rate
        audio = np.concatenate(frames).astype(np.float32)
        return audio, self.actual_rate

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        mono = indata[:, 0]
        with self._lock:
            self._frames.append(mono.copy())
        try:
            self.level = float(np.sqrt(np.mean(np.square(mono))))
        except Exception:
            self.level = 0.0


def ensure_rate(audio: np.ndarray, sample_rate: int, target: int = TARGET_RATE) -> np.ndarray:
    """Линейная передискретизация mono float32 в target Гц (если нужно)."""
    audio = np.asarray(audio, dtype=np.float32)
    if sample_rate == target or audio.size == 0:
        return audio
    duration = audio.shape[0] / float(sample_rate)
    n_target = max(1, int(round(duration * target)))
    x_old = np.linspace(0.0, duration, num=audio.shape[0], endpoint=False)
    x_new = np.linspace(0.0, duration, num=n_target, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)
