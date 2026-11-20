"""Короткие звуковые сигналы начала/окончания записи.

Тоны генерируются на лету (numpy) и проигрываются неблокирующе через
sounddevice — отдельный выходной поток, запись с микрофона не мешает.
Отсутствие аудиовыхода не считается ошибкой.
"""
from __future__ import annotations

import numpy as np

try:
    import sounddevice as sd
except ImportError:  # pragma: no cover
    sd = None

_RATE = 44100


def _tone(freq: float, duration: float, volume: float) -> np.ndarray:
    n = int(_RATE * duration)
    t = np.linspace(0.0, duration, n, endpoint=False)
    wave = np.sin(2.0 * np.pi * freq * t)
    fade = max(1, int(_RATE * 0.01))  # сглаживание краёв, чтобы не щёлкало
    env = np.ones(n, dtype=np.float32)
    env[:fade] = np.linspace(0.0, 1.0, fade)
    env[-fade:] = np.linspace(1.0, 0.0, fade)
    return (wave * env * volume).astype(np.float32)


def _play(freq: float, duration: float, volume: float = 0.22) -> None:
    if sd is None:
        return
    try:
        sd.play(_tone(freq, duration, volume), _RATE)
    except Exception:
        pass


def beep_start() -> None:
    """Восходящий тон — запись началась."""
    _play(660.0, 0.08)


def beep_end() -> None:
    """Нисходящий тон — запись окончена."""
    _play(440.0, 0.11)


def beep_error() -> None:
    """Низкий длинный тон — ошибка."""
    _play(220.0, 0.20, 0.25)
