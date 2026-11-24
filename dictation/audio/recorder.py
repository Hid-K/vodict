"""Микрофон: запись в память как float32 mono + утилита передискретизации.

Два бэкенда записи:
- PortAudio (sounddevice) — основной, для всех обычных микрофонов;
- soundcard (WASAPI напрямую) — для устройств, которых PortAudio не
  видит (например, «Remote Audio» в RDP-сессии). Выбирается
  автоматически, если выбранный микрофон не найден в PortAudio.
"""
from __future__ import annotations

import threading
import time

import numpy as np

try:
    import sounddevice as sd
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        f"Не установлен sounddevice ({exc}). "
        "Установите зависимости: pip install -r requirements.txt"
    ) from exc

TARGET_RATE = 16000


def _hostapi_name(hostapi_index) -> str:
    try:
        return str(sd.query_hostapis(int(hostapi_index))["name"])
    except Exception:
        return ""


def list_input_devices(dedupe: bool = True, devices=None) -> list[dict]:
    """Входные устройства.

    dedupe=True (по умолчанию, для UI): PortAudio перечисляет одно и то же
    физическое устройство несколько раз — по одному экземпляру на каждый
    Host API (MME, DirectSound, WASAPI, WDM-KS), плюс добавляет
    «Microsoft Sound Mapper». Здесь дубли схлопываются, mapper убирается,
    предпочитается WASAPI (нативный стек, низкие задержки). В конец списка
    добавляются микрофоны, которых PortAudio не видит, но видит soundcard
    (WASAPI напрямую) — например, «Remote Audio» в RDP-сессии.

    dedupe=False (для диагностики): полный список как его видит PortAudio.
    """
    default_index = sd.default.device[0]
    rows = []
    for idx, dev in enumerate(sd.query_devices()):
        if int(dev.get("max_input_channels", 0)) <= 0:
            continue
        name = str(dev["name"])
        rows.append({
            "index": idx,
            "name": name,
            "channels": int(dev.get("max_input_channels", 0)),
            "default_samplerate": int(float(dev.get("default_samplerate", 0))),
            "is_default": idx == default_index,
            "hostapi": _hostapi_name(dev.get("hostapi")),
        })
    if not dedupe:
        return rows

    clean = [r for r in rows
             if not r["name"].casefold().startswith("microsoft sound mapper")]
    wasapi = [r for r in clean if "wasapi" in r["hostapi"].casefold()]
    picked = wasapi or clean

    unique = []
    seen = set()
    for r in picked:
        key = r["name"].casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)

    # Микрофоны, которых нет у PortAudio (напр. RDP «Remote Audio»)
    try:
        import soundcard as sc  # noqa: PLC0415

        have = {r["name"].casefold() for r in unique}
        for m in sc.all_microphones():
            nm = str(m.name)
            if nm.casefold() in have or int(getattr(m, "channels", 0) or 0) < 1:
                continue
            unique.append({
                "index": None,
                "name": nm,
                "default_samplerate": 48000,
                "is_default": False,
                "hostapi": "WASAPI (soundcard)",
                "backend": "soundcard",
            })
            have.add(nm.casefold())
    except Exception:
        pass  # soundcard недоступен — обычный список продолжает работать

    return unique


def resolve_device(device, devices=None) -> int | None:
    """None/"" — по умолчанию; int или числовая строка — индекс из --list-mics;
    иначе — первое устройство, чьё имя содержит строку (искать по всем
    Host API, чтобы «старые» имена из конфига продолжали находиться)."""
    if device in (None, ""):
        return None
    if isinstance(device, int):
        return device
    text = str(device).strip()
    if text.isdigit():
        return int(text)
    needle = text.casefold()
    rows = devices if devices is not None else list_input_devices(dedupe=False)
    fallback = None
    for r in rows:
        if needle not in r["name"].casefold():
            continue
        # одно имя может быть и у выхода, и у входа (напр. «Remote Audio»):
        # для записи нужен экземпляр с входными каналами
        if int(r.get("channels", 0)) > 0:
            return r["index"]
        if fallback is None:
            fallback = r["index"]
    return fallback


class MicrophoneRecorder:
    """start() -> говорим -> stop() возвращает (audio, sample_rate).

    Уровень сигнала (RMS последнего блока) доступен как ``.level`` —
    используется индикатором в интерфейсе и авто-стопом по тишине.

    Бэкенд выбирается в start(): если выбранный по имени микрофон не найден
    в PortAudio — пробуем soundcard (WASAPI); при успехе все публичные
    методы делегируются внутреннему рекордеру.
    """

    def __init__(self, sample_rate: int = TARGET_RATE, device=None, blocksize: int = 0):
        self.requested_rate = sample_rate
        self.device = device
        self.blocksize = blocksize
        self._stream: sd.InputStream | None = None
        self._frames: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._consumed = 0  # сколько сэмплов уже отдано в live-сессию
        self._level = 0.0
        self._peak = 0.0  # максимальный уровень за сессию записи
        self.started_at: float | None = None
        self._actual_rate = sample_rate
        self._impl = None  # _SoundcardRecorder, если выбран альтернативный бэкенд

    # --- делегирование к альтернативному бэкенду --------------------
    @property
    def level(self) -> float:
        return self._impl.level if self._impl is not None else self._level

    @level.setter
    def level(self, value: float) -> None:
        if self._impl is not None:
            self._impl.level = value
        else:
            self._level = value

    @property
    def peak(self) -> float:
        return self._impl.peak if self._impl is not None else self._peak

    @peak.setter
    def peak(self, value: float) -> None:
        if self._impl is not None:
            self._impl.peak = value
        else:
            self._peak = value

    @property
    def actual_rate(self) -> int:
        return self._impl.actual_rate if self._impl is not None else self._actual_rate

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._stream is not None or self._impl is not None:
            raise RuntimeError("Запись уже идёт")

        dev_index = resolve_device(self.device)
        dev_name = str(self.device or "").strip().casefold()
        # «Remote Audio» (перенаправленный микрофон RDP) роняет PortAudio
        # при открытии — сразу идём через soundcard (WASAPI напрямую).
        force_soundcard = "remote audio" in dev_name
        if self.device and (dev_index is None or force_soundcard):
            # Не найден в PortAudio (или RDP-устройство) — soundcard.
            impl = _SoundcardRecorder.try_create(self.requested_rate, self.device)
            if impl is None:
                # Выбранный ранее микрофон отключён или переименован —
                # говорим явно, вместо тихой пустой записи.
                raise RuntimeError(
                    f"Микрофон «{self.device}» не найден — проверьте подключение "
                    "или выберите другой в настройках"
                )
            self._impl = impl
            impl.start()
            return

        with self._lock:
            self._frames = []
        self._level = 0.0
        self._peak = 0.0
        self._consumed = 0

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
            self._actual_rate = self.requested_rate
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
            self._actual_rate = fallback

        stream.start()
        self._stream = stream
        self.started_at = time.monotonic()

    def elapsed(self) -> float:
        if self._impl is not None:
            return self._impl.elapsed()
        if self.started_at is None:
            return 0.0
        return time.monotonic() - self.started_at

    def active(self) -> bool:
        """Жив ли поток записи (False — устройство пропало)."""
        if self._impl is not None:
            return self._impl.active()
        try:
            return bool(self._stream is not None and self._stream.active)
        except Exception:
            return True

    def pending_audio(self) -> np.ndarray:
        """Новые сэмплы с момента последнего вызова (float32 @ recorded rate).

        Используется live-режимом: финальный stop() по-прежнему возвращает
        весь фрагмент целиком — здесь только «хвост» для стриминговой
        сессии, поэтому внутренний буфер не очищается.
        """
        if self._impl is not None:
            return self._impl.pending_audio()
        with self._lock:
            total = sum(len(f) for f in self._frames)
            new = max(0, total - self._consumed)
            if new == 0:
                return np.zeros(0, dtype=np.float32)
            self._consumed = total
            tail: list[np.ndarray] = []
            remaining = new
            for f in reversed(self._frames):
                take = min(len(f), remaining)
                tail.append(f[-take:])
                remaining -= take
                if remaining <= 0:
                    break
        return np.concatenate(list(reversed(tail))).astype(np.float32)

    def stop(self) -> tuple[np.ndarray, int]:
        if self._impl is not None:
            impl, self._impl = self._impl, None
            self.started_at = None
            return impl.stop()
        if self._stream is None:
            raise RuntimeError("Запись не запущена")
        stream, self._stream = self._stream, None
        try:
            stream.stop()
        finally:
            stream.close()
        with self._lock:
            frames, self._frames = self._frames, []
        self._level = 0.0
        self.started_at = None
        if not frames:
            return np.zeros(0, dtype=np.float32), self._actual_rate
        audio = np.concatenate(frames).astype(np.float32)
        return audio, self._actual_rate

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        mono = indata[:, 0]
        with self._lock:
            self._frames.append(mono.copy())
        try:
            self._level = float(np.sqrt(np.mean(np.square(mono))))
            if self._level > self._peak:
                self._peak = self._level
        except Exception:
            self._level = 0.0


class _SoundcardRecorder:
    """Запись через soundcard (WASAPI напрямую).

    Для устройств, которых PortAudio не перечисляет как входные —
    например «Remote Audio» (перенаправленный микрофон RDP-сессии).
    Интерфейс совпадает с MicrophoneRecorder.
    """

    def __init__(self, sample_rate: int, device_name: str):
        self.requested_rate = sample_rate
        self.device_name = device_name
        self._frames: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._consumed = 0
        self.level = 0.0
        self.peak = 0.0
        self.started_at: float | None = None
        self.actual_rate = sample_rate
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._mic = None  # объект микрофона soundcard (заполняется в try_create)

    @classmethod
    def try_create(cls, sample_rate: int, device_name: str) -> "_SoundcardRecorder | None":
        """Возвращает рекордер, если имя найдено среди микрофонов soundcard."""
        try:
            import soundcard as sc  # noqa: PLC0415

            needle = str(device_name).casefold()
            for m in sc.all_microphones():
                if needle in str(m.name).casefold() and int(m.channels) >= 1:
                    rec = cls(sample_rate, str(m.name))
                    rec._mic = m   # найденный объект — без повторного поиска
                    return rec
        except Exception:
            pass
        return None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Запись уже идёт")
        with self._lock:
            self._frames = []
        self.level = 0.0
        self.peak = 0.0
        self._consumed = 0
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="vodict-soundcard-rec")
        self._thread.start()
        self.started_at = time.monotonic()

    def _loop(self) -> None:
        # WASAPI требует инициализации COM в каждом потоке —
        # используем cffi-биндинг самого soundcard.
        import soundcard.mediafoundation as _mf  # noqa: PLC0415

        hr = _mf._ole32.CoInitializeEx(_mf._ffi.NULL, 0x0)  # MTA
        com_initialized = hr in (0, 1)  # S_OK / S_FALSE
        import soundcard as sc  # noqa: PLC0415

        mic = getattr(self, "_mic", None)
        if mic is None:
            for m in sc.all_microphones():
                if self.device_name.casefold() in str(m.name).casefold():
                    mic = m
                    break
        if mic is None:
            raise RuntimeError(f"Микрофон {self.device_name!r} не найден")
        rate = self.requested_rate
        try:
            rec_ctx = mic.recorder(samplerate=rate, channels=1)
            self.actual_rate = rate
        except Exception:
            rate = 48000
            self.actual_rate = rate
            rec_ctx = mic.recorder(samplerate=rate, channels=1)
        with rec_ctx as rec:
            while not self._stop_evt.is_set():
                data = rec.record(numframes=1024)
                mono = data[:, 0].astype(np.float32)
                with self._lock:
                    self._frames.append(mono)
                try:
                    self.level = float(np.sqrt(np.mean(np.square(mono))))
                    if self.level > self.peak:
                        self.peak = self.level
                except Exception:
                    self.level = 0.0
        if com_initialized:
            try:
                _mf._ole32.CoUninitialize()
            except Exception:
                pass

    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        return time.monotonic() - self.started_at

    def active(self) -> bool:
        return bool(self._thread is not None and self._thread.is_alive())

    def pending_audio(self) -> np.ndarray:
        with self._lock:
            total = sum(len(f) for f in self._frames)
            new = max(0, total - self._consumed)
            if new == 0:
                return np.zeros(0, dtype=np.float32)
            self._consumed = total
            tail: list[np.ndarray] = []
            remaining = new
            for f in reversed(self._frames):
                take = min(len(f), remaining)
                tail.append(f[-take:])
                remaining -= take
                if remaining <= 0:
                    break
        return np.concatenate(list(reversed(tail))).astype(np.float32)

    def stop(self) -> tuple[np.ndarray, int]:
        if self._thread is None:
            raise RuntimeError("Запись не запущена")
        self._stop_evt.set()
        self._thread.join(timeout=5)
        self._thread = None
        with self._lock:
            frames, self._frames = self._frames, []
        self.level = 0.0
        self.started_at = None
        if not frames:
            return np.zeros(0, dtype=np.float32), self.actual_rate
        audio = np.concatenate(frames).astype(np.float32)
        return audio, self.actual_rate


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
