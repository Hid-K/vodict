"""DictationController — связывает запись, распознавание и вставку.

КОНТРАКТ «ядро ↔ любой фронтенд» (tkinter, Qt, Web, TUI — всё равно):

Фронтенд МОЖЕТ читать (публичные атрибуты — состояние и данные, не логика):
  controller.state              "idle" | "recording" | "transcribing"
  controller.config             текущий Config
  controller.recording_level()  уровень сигнала записи (0..~1)
  controller.downloads.states() / .cached_models() / .model_size_bytes(m)
  controller.history.read_index(n)
  controller.events             очередь событий (см. таблицу ниже)

Фронтенд ВЫЗЫВАЕТ (публичные методы, потокобезопасно):
  controller.on_button()                 старт/стоп диктовки
  controller.start_recording() / stop_recording()
  controller.pause_hotkey(bool)          пауза глобального хоткея
  controller.apply_settings(Config, save=True)
  controller.request_model_download(repo) -> "cached"|"already"|"started"|"na"
  controller.delete_model(repo) -> int   / downloaded_models()
  controller.collect_diagnostics() -> Path
  controller.shutdown()

Ядро -> фронтенд: очередь ``events``, кортежи (kind, *payload):

  ("state", "idle"|"recording"|"transcribing")
  ("status", текст)                    короткий текст состояния
  ("result", текст)                    распознанный текст
  ("partial", текст)                   live-текст во время записи
                                       (только стриминговые движки)
  ("error", текст)                     ошибка (фронтенд решает, как показать)
  ("refresh",)                         настройки изменились — обновить вид
  ("model_download_started"|"progress"|"done"|"error", модель[, процент])
  ("switch_model", имя)                из трея — сменить модель
  ("show_window"|"open_settings"|"quit") — команды от трея

Единственное место, знающее про конкретный UI, — метод ``run()``
(композиция: окно + трей + mainloop). Любой другой фронтенд реализует
свой аналог ``run()`` поверх тех же вызовов и очереди.

Потоковая модель:
- главный поток: event loop фронтенда (tkinter/Qt/...);
- фокус-поллер: фоновый daemon-поток FocusManager;
- запись: звук пишет колбэк PortAudio (поток sounddevice);
- watchdog записи: контроль max_duration / тишины / нулевого сигнала;
- транскрибации: один daemon-воркер с FIFO-очередью. Нажатие хоткея во
  время транскрибации начинает новую запись сразу, а предыдущий фрагмент
  дозаменяется в фоне — state в этот момент 'recording'.
"""
from __future__ import annotations

import logging
import queue
import sys
import threading
import time

import numpy as np

from .audio.recorder import MicrophoneRecorder, ensure_rate, resolve_device
from .audio import sounds
from .config import Config
from .engines.base import EngineUnavailable, TranscriptionResult
from .engines.registry import create_engine
from .downloads import DownloadManager, KNOWN_MODELS
from .history import HistoryStore
from .hotkeys import HotkeyListener
from .proxy import apply_proxy_env
from .output import create_inserter
from .platform import create_platform_support
from .platform.focus import FocusManager

STATE_IDLE = "idle"
STATE_RECORDING = "recording"
STATE_TRANSCRIBING = "transcribing"

log = logging.getLogger(__name__)

_VALID_MODES = {"push_to_talk", "toggle"}


def _safe_mode(value: str) -> str:
    """Подстраховка от «мусорных» значений в конфиге: неизвестный режим
    всегда означает push-to-talk (а не случайный toggle)."""
    return value if value in _VALID_MODES else "push_to_talk"


class DictationController:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.events: queue.Queue[tuple] = queue.Queue()

        self.support = create_platform_support()
        self.focus = FocusManager(self.support)
        self.recorder = MicrophoneRecorder(
            sample_rate=config.sample_rate,
            device=config.microphone or None,
        )
        self.inserter = create_inserter(config, self.focus, self.support)

        self.engine = None
        self._engine_lock = threading.Lock()
        self._state_lock = threading.RLock()
        # Запись и транскрибация могут сосуществовать: нажатие хоткея во
        # время транскрибации начинает новую запись, а предыдущая фрагмент
        # дозаменяется в фоне. Свойство state вычисляется из флагов.
        self._recording = False
        self._transcribing = False
        self._stream_session = None   # live-сессия движка (или None)
        self._last_partial = ""       # последний partial-текст (дедуп событий)
        self._closed = False

        self.hotkey = HotkeyListener(
            config.hotkey, self._on_hotkey_event, mode=_safe_mode(config.hotkey_mode)
        )
        self.history = (HistoryStore(config.history_dir or None)
                        if config.history_enabled else None)
        self.downloads = DownloadManager(on_event=self._on_download_event)
        self._record_started: float | None = None
        self.ui = None
        self.tray = None
        # Один воркер на все транскрибации (FIFO): движок не используется
        # параллельно, при этом запись новой фразы идёт одновременно.
        self._transcribe_queue: queue.Queue = queue.Queue()
        threading.Thread(target=self._transcribe_worker, daemon=True,
                         name="transcribe").start()

    @property
    def state(self) -> str:
        if self._recording:
            return STATE_RECORDING
        if self._transcribing:
            return STATE_TRANSCRIBING
        return STATE_IDLE

    def _transcription_done(self, message: str | None = None) -> None:
        """Транскрибация завершена (успех или ошибка): снимаем флаг.
        Если уже началась новая запись — state останется 'recording'."""
        self._transcribing = False
        self._post("state", self.state)
        if message:
            self._post("status", message)

    # ------------------------------------------------------------------
    # событие для UI: ("state"|"status"|"result"|"error"|"refresh"|
    #                  "show_window"|"open_settings"|"quit", payload...)
    def _post(self, kind: str, *payload) -> None:
        if kind == "error" and self.config.sound_enabled:
            sounds.beep_error()
        self.events.put((kind, *payload))

    def _transcribe_worker(self) -> None:
        while not self._closed:
            job = self._transcribe_queue.get()
            try:
                if job and job[0] == "stream":
                    _, session, pending = job
                    self._stream_insert(session, pending)
                else:
                    _, audio, rate, pending = job
                    self._transcribe_and_insert(audio, rate, pending)
            except Exception:
                log.exception("Сбой потока транскрибации")
            finally:
                self._transcribing = False
                self._post("state", self.state)

    # ------------------------------------------------------------------
    # live-режим (распознавание на лету)
    def _pump_live(self) -> None:
        """Подкачивает накопившееся аудио в live-сессию стримингового движка."""
        session = self._stream_session
        if session is None:
            return
        try:
            audio = self.recorder.pending_audio()
            if audio.size == 0:
                return
            partial = session.feed(audio, self.recorder.actual_rate or 16000)
            if partial and partial != self._last_partial:
                self._last_partial = partial
                self._post("partial", partial)
        except Exception:
            log.exception("live-распознавание отключено (ошибка)")
            self._stream_session = None

    def _stream_insert(self, session, pending: dict | None) -> None:
        """Завершение live-диктовки: финализация стрима + вставка текста."""
        try:
            t0 = time.perf_counter()
            text = (session.finalize() or "").strip()
            transcribe_seconds = time.perf_counter() - t0
            if not text:
                self._transcription_done("Речь не распознана")
                self._finish_history(pending, status="empty",
                                     transcribe_seconds=transcribe_seconds)
                return

            target = self.focus.resolve_target()
            self.hotkey.set_paused(True)
            try:
                self.inserter.insert(text, target)
            except Exception as exc:
                self._transcription_done()
                self._post("result", text)
                self._post("error", f"Не удалось вставить текст: {exc}")
                self._finish_history(pending, status="insert_error", text=text,
                                     transcribe_seconds=transcribe_seconds,
                                     target_title=self._target_title(target),
                                     error=str(exc))
                return
            finally:
                self.hotkey.set_paused(False)

            if target is None:
                self._transcription_done(
                    "Целевое окно не определено — текст остался в буфере")
            else:
                self._transcription_done("Вставлено ✓")
            log.info("Live: вставлено %d символов", len(text))
            self._post("result", text)
            self._finish_history(pending, status="ok", text=text,
                                 transcribe_seconds=transcribe_seconds,
                                 target_title=self._target_title(target))
        except Exception as exc:
            log.exception("Ошибка live-распознавания")
            self._transcription_done()
            self._post("error", f"Ошибка распознавания: {exc}")
            self._finish_history(pending, status="error", error=str(exc))

    # ------------------------------------------------------------------
    # горячая клавиша / кнопка
    def _on_hotkey_event(self, event: str) -> None:
        if self._closed:
            return
        if event == "press":
            self.start_recording()
        elif event == "release":
            self.stop_recording()
        else:  # toggle
            if self._recording:
                self.stop_recording()
            else:
                # idle или transcribing: новую запись начинаем сразу,
                # предыдущая транскрибация дозаменяется в фоне
                self.start_recording()

    def on_button(self) -> None:
        """Кнопка в окне всегда работает как переключатель."""
        self._on_hotkey_event("toggle")

    # ------------------------------------------------------------------
    # запись
    def start_recording(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            if self._recording:
                return  # запись уже идёт
            # Во время транскрибации новую запись начинаем сразу:
            # предыдущий фрагмент дозаменяется в фоне (очередь транскрибаций).
            try:
                self.recorder.start()
            except Exception as exc:
                log.exception("Не удалось начать запись")
                self._post("error", f"Не удалось начать запись: {exc}")
                return

            # live-режим: стриминговая сессия поверх уже созданного движка
            # (на первый диктовке движка может ещё не быть — обычный батч).
            session = None
            engine = self.engine
            if engine is not None:
                try:
                    session = engine.open_stream(
                        self.recorder.actual_rate or 16000)
                except Exception as exc:
                    log.warning("live-режим недоступен: %s", exc)
            self._stream_session = session

            self._recording = True
            self._record_started = time.time()
            log.info("Запись начата")
            if self.config.sound_enabled:
                sounds.beep_start()
        self._post("state", self.state)
        threading.Thread(target=self._recording_watchdog, daemon=True).start()

    def stop_recording(self) -> None:
        with self._state_lock:
            if not self._recording:
                return
            session = self._stream_session
            self._stream_session = None
            try:
                audio, rate = self.recorder.stop()
            except Exception as exc:
                log.exception("Не удалось остановить запись")
                self._recording = False
                self._post("state", self.state)
                self._post("error", f"Не удалось остановить запись: {exc}")
                return
            log.info("Запись остановлена: %.1f с",
                     len(audio) / float(rate) if rate else 0.0)

            # Цифровой ноль: микрофон не передаёт данных (отключён, замучен
            # или неисправен) — сообщаем явно вместо тихой пустой вставки.
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0
            if peak <= 0.0:
                log.warning("Запись пуста: микрофон не передаёт сигнал")
                self._recording = False
                self._post("state", self.state)
                self._post("error", "С микрофона не поступило звука — проверьте "
                                    "подключение, что он включён и не замучен, "
                                    "и что выбран в Настройках")
                return
            self._recording = False
            self._transcribing = True
            if self.config.sound_enabled:
                sounds.beep_end()
            pending = self._begin_history(audio, rate)
        self._post("state", self.state)
        if session is not None:
            # live-диктовка: текст уже распознан, в воркере — финализация
            self._transcribe_queue.put(("stream", session, pending))
        else:
            self._transcribe_queue.put(("batch", audio, rate, pending))

    def _recording_watchdog(self) -> None:
        low_since: float | None = None
        while not self._closed:
            time.sleep(0.1)
            if self.state != STATE_RECORDING:
                return
            elapsed = self.recorder.elapsed()
            if self.config.max_duration > 0 and elapsed >= self.config.max_duration:
                self._post("status", "Достигнут лимит длительности — останавливаюсь")
                self.stop_recording()
                return
            # Микрофон не подаёт сигнал вообще (отключён/замучен): после
            # 3 секунд строго нулевого уровня — явная ошибка пользователю.
            if elapsed > 3.0 and self.recorder.peak <= 0.0:
                log.warning("Микрофон не подаёт сигнал (peak=0 за %.1f с записи)",
                            elapsed)
                self._post("error", "С микрофона не поступает сигнал — проверьте, "
                                    "что он включён, не замучен и выбран в настройках")
                self.stop_recording()
                return
            # live-режим: подкачиваем аудио в стриминговую сессию движка
            self._pump_live()
            timeout = self.config.silence_timeout
            if timeout > 0:
                if self.recorder.level < self.config.silence_rms_threshold:
                    now = time.monotonic()
                    if low_since is None:
                        low_since = now
                    elif now - low_since >= timeout:
                        self.stop_recording()
                        return
                else:
                    low_since = None

    def request_model_download(self, model: str) -> str:
        """Запустить загрузку модели faster-whisper.

        Возвращает "cached" | "already" | "started" | "na" (для движков
        без скачиваемых моделей).
        """
        if self.config.engine != "faster-whisper":
            return "na"
        return self.downloads.request_download(model)

    def downloaded_models(self) -> list[str]:
        """Скачанные модели, кроме активной (для меню и уведомлений)."""
        if self.config.engine != "faster-whisper":
            return []
        active = self.config.model_size
        return [m for m in KNOWN_MODELS if m != active and self.downloads.is_cached(m)]

    def delete_model(self, model: str) -> int:
        """Удалить модель из кэша. Возвращает освобождённые байты."""
        if model == self.config.model_size:
            raise ValueError(
                f"«{model}» — активная модель. Сначала переключитесь на другую."
            )
        st = self.downloads.state(model)
        if st and st["state"] == "downloading":
            raise ValueError(f"«{model}» сейчас загружается — удаление недоступно.")
        return self.downloads.delete_model(model)

    def _on_download_event(self, kind: str, payload: dict) -> None:
        """События менеджера загрузок -> очередь событий UI."""
        model = payload.get("model", "")
        log.info("Загрузка модели %s: %s %s", model, kind,
                 payload.get("percent") or payload.get("error") or "")
        if kind == "started":
            self._post("model_download_started", model)
        elif kind == "progress":
            self._post("model_download_progress", model, payload.get("percent"))
        elif kind == "done":
            self._post("model_download_done", model)
        elif kind == "error":
            self._post("model_download_error", model,
                       payload.get("error", "неизвестная ошибка"))

    # ------------------------------------------------------------------
    # распознавание + вставка + история
    def _transcribe_and_insert(self, audio, rate, pending: dict | None) -> None:
        target = None
        transcribe_seconds = 0.0
        result = None
        try:
            t0 = time.perf_counter()
            result = self._recognize(audio, rate)
            transcribe_seconds = time.perf_counter() - t0
            text = (result.text or "").strip()

            if not text:
                self._transcription_done("Речь не распознана")
                self._finish_history(pending, status="empty", result=result,
                                     transcribe_seconds=transcribe_seconds)
                return

            target = self.focus.resolve_target()
            # На время вставки глушим слушатель хоткея: инжектированные
            # Ctrl+V не должны путать состояние комбинации (например,
            # пока пользователь уже держит клавиши для следующей фразы).
            self.hotkey.set_paused(True)
            try:
                self.inserter.insert(text, target)
            except Exception as exc:
                self._transcription_done()
                self._post("result", text)
                self._post("error", f"Не удалось вставить текст: {exc}")
                self._finish_history(pending, status="insert_error", text=text,
                                     result=result,
                                     transcribe_seconds=transcribe_seconds,
                                     target_title=self._target_title(target),
                                     error=str(exc))
                return
            finally:
                self.hotkey.set_paused(False)

            if target is None:
                self._transcription_done(
                    "Целевое окно не определено — текст остался в буфере")
            else:
                self._transcription_done("Вставлено ✓")
            log.info("Распознано %d символов за %.1f с (аудио %.1f с)",
                     len(text), transcribe_seconds,
                     float(pending.get("audio_seconds", 0.0)) if pending else 0.0)
            self._post("result", text)
            self._finish_history(pending, status="ok", text=text, result=result,
                                 transcribe_seconds=transcribe_seconds,
                                 target_title=self._target_title(target))
        except EngineUnavailable as exc:
            log.exception("Движок недоступен")
            self._transcription_done()
            self._post("error", str(exc))
            self._finish_history(pending, status="error", result=result,
                                 transcribe_seconds=transcribe_seconds,
                                 error=str(exc))
        except Exception as exc:
            log.exception("Ошибка распознавания")
            self._transcription_done()
            self._post("error", f"Ошибка распознавания: {exc}")
            self._finish_history(pending, status="error", result=result,
                                 transcribe_seconds=transcribe_seconds,
                                 error=str(exc))

    @staticmethod
    def _target_title(target) -> str | None:
        return getattr(target, "title", None) if target is not None else None

    def _begin_history(self, audio, rate) -> dict | None:
        """Сохраняет wav и формирует стартовые метаданные записи истории."""
        if self.history is None:
            return None
        try:
            meta = self.history.begin_entry(audio, rate,
                                            save_audio=self.config.save_audio)
        except Exception as exc:
            print(f"[history] Не удалось сохранить запись: {exc}", file=sys.stderr)
            return None
        model = (self.config.api_model if self.config.engine == "openai-api"
                 else self.config.model_size)
        meta.update({
            "time": time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(self._record_started or time.time()),
            ),
            "microphone": self.config.microphone or "default",
            "engine": self.config.engine,
            "model": model,
            "language_requested": self.config.language,
            # Параметры распознавания: позволяют воспроизвести результат
            "compute_type": self.config.compute_type,
            "device": self.config.device,
            "beam_size": self.config.beam_size,
            "vad_filter": self.config.vad_filter,
            "initial_prompt": self.config.initial_prompt or "",
        })
        return meta

    def _finish_history(self, pending: dict | None, status: str, text: str = "",
                        result: TranscriptionResult | None = None,
                        transcribe_seconds: float = 0.0,
                        target_title: str | None = None,
                        error: str | None = None) -> None:
        if self.history is None or pending is None:
            return
        meta = dict(pending)
        audio_seconds = float(pending.get("audio_seconds", 0.0) or 0.0)
        # Скорость распознавания: сколько секунд аудио обрабатывается
        # за одну секунду реального времени (×2 = вдвое быстрее речи).
        speed = (round(audio_seconds / transcribe_seconds, 1)
                 if transcribe_seconds > 0 else None)
        meta.update({
            "status": status,  # ok | empty | insert_error | error
            "text": text,
            "speed": speed,
            "language_detected": getattr(result, "language", None),
            "language_probability": getattr(result, "language_probability", None),
            "transcribe_seconds": round(transcribe_seconds, 2),
            "target_title": target_title,
            "error": error,
        })
        # Фактическое устройство/точность распознавания (GPU vs CPU):
        # резолвится из "auto" при создании движка.
        engine = self.engine
        meta.update({
            "device_used": getattr(engine, "actual_device", None),
            "compute_type_used": getattr(engine, "actual_compute_type", None),
        })
        try:
            self.history.write_entry(meta)
            log.info("История [%s]: аудио %.1f с, распознавание %.1f с (×%s), "
                     "символов: %d, устройство: %s/%s",
                     status, audio_seconds, transcribe_seconds,
                     speed or 0, len(text),
                     meta["device_used"] or "?", meta["compute_type_used"] or "?")
        except Exception as exc:
            print(f"[history] Не удалось записать историю: {exc}", file=sys.stderr)
            log.exception("Не удалось записать историю")

    def _recognize(self, audio, rate) -> TranscriptionResult:
        audio16 = ensure_rate(audio, rate)
        engine = self._get_engine()
        return engine.transcribe(audio16, 16000)

    def _get_engine(self):
        with self._engine_lock:
            if self.engine is None:
                # Если модель качается менеджером загрузок — ждём её,
                # чтобы не начать параллельную (дублирующую) загрузку.
                if self.config.engine == "faster-whisper":
                    self.downloads.wait_until_cached(self.config.model_size)
                self._post("status", "Загрузка модели (первый запуск может занять пару минут)…")
                self.engine = create_engine(
                    self.config.engine, progress_cb=self._engine_progress,
                    **self._engine_options()
                )
                log.info("Движок создан: %s", self.engine.describe())
            return self.engine

    def _engine_progress(self, pct: int, msg: str) -> None:
        self._post("status", f"Загрузка модели Sherpa: {pct}% ({msg})")

    def _engine_options(self) -> dict:
        c = self.config
        return dict(
            model_size=c.model_size,
            device=c.device,
            compute_type=c.compute_type,
            language=c.language,
            beam_size=c.beam_size,
            initial_prompt=c.initial_prompt,
            vad_filter=c.vad_filter,
            api_key=c.api_key,
            api_base_url=c.api_base_url,
            api_model=c.api_model,
        )

    def warmup_engine_async(self) -> None:
        threading.Thread(target=self._warmup_engine, daemon=True).start()

    def _warmup_engine(self) -> None:
        try:
            engine = self._get_engine()
            engine.warmup()
            self._post("status", f"Модель готова: {engine.describe()}")
        except EngineUnavailable as exc:
            self._post("error", str(exc))
        except Exception as exc:
            self._post("error", f"Не удалось загрузить движок: {exc}")

    # ------------------------------------------------------------------
    # настройки
    def apply_settings(self, new_config: Config, *, save: bool = True) -> None:
        """Применить настройки; ``save=False`` — не писать конфиг на диск."""
        hotkey_changed = (
            new_config.hotkey != self.config.hotkey
            or new_config.hotkey_mode != self.config.hotkey_mode
        )
        engine_changed = new_config.engine_signature() != self.config.engine_signature()
        mic_changed = (
            new_config.microphone != self.config.microphone
            or new_config.sample_rate != self.config.sample_rate
        )
        history_changed = (
            new_config.history_enabled != self.config.history_enabled
            or new_config.history_dir != self.config.history_dir
        )
        proxy_changed = (
            new_config.proxy_enabled != self.config.proxy_enabled
            or new_config.proxy_url != self.config.proxy_url
        )

        self.config = new_config
        log.info("apply: конфиг обновлён")
        if save:
            try:
                new_config.save()
            except Exception as exc:
                self._post("error", f"Не удалось сохранить конфиг: {exc}")

        log.info("apply: прокси-этап")
        if proxy_changed:
            apply_proxy_env(new_config)
            log.info("Прокси применён: %s", new_config.proxy_url or "выключен")

        log.info("apply: этап микрофона")
        if mic_changed:
            self.recorder.device = new_config.microphone or None
            self.recorder.requested_rate = new_config.sample_rate
            problem = self.microphone_problem()
            if problem:
                self._post("status", problem)
        if history_changed:
            self.history = (HistoryStore(new_config.history_dir or None)
                            if new_config.history_enabled else None)
        log.info("apply: inserter")
        self.inserter = create_inserter(new_config, self.focus, self.support)

        log.info("apply: этап движка (warmup уходит в фон)")
        if engine_changed:
            with self._engine_lock:
                old_engine, self.engine = self.engine, None
            if old_engine is not None:
                try:
                    old_engine.close()
                except Exception:
                    pass
            self.warmup_engine_async()
        log.info("apply: этап хоткея")
        if hotkey_changed:
            self.hotkey.stop()
            self.hotkey = HotkeyListener(
                new_config.hotkey, self._on_hotkey_event,
                mode=_safe_mode(new_config.hotkey_mode),
            )
            try:
                self.hotkey.start()
            except Exception as exc:
                self._post("error", f"Горячая клавиша не запустилась: {exc}")
        log.info("apply: готово")
        self._post("refresh")
        self._post("status", "Настройки сохранены")
        log.info("Настройки применены: engine=%s model=%s hotkey=%s",
                 new_config.engine, new_config.model_size, new_config.hotkey)

    # ------------------------------------------------------------------
    # сервисы и жизненный цикл
    def start_services(self) -> None:
        """Фоновые сервисы БЕЗ UI: фокус-поллер, хоткей, прогрев движка.

        Вызывается точкой сборки (bootstrap) до event loop'а; любой
        фронтенд может запустить эти сервисы и управлять контроллером
        через публичный API, вообще не имея графического окна.
        """
        self.focus.start()
        try:
            self.hotkey.start()
        except Exception as exc:
            log.error("Глобальная горячая клавиша не запустилась: %s", exc)
            self._post("error", f"Глобальная горячая клавиша не запустилась: {exc}")
        self.warmup_engine_async()
        # Выбранный микрофон мог быть отключён с прошлого раза: сообщаем
        # сразу (тост), а не при первой попытке диктовки.
        problem = self.microphone_problem()
        if problem:
            self._post("error", problem)

    def microphone_problem(self) -> str | None:
        """Текст проблемы с настроенным микрофоном или None."""
        from .audio.recorder import resolve_device

        device = (self.config.microphone or "").strip()
        if not device:
            return None  # микрофон по умолчанию — проблемы покажем при записи
        if resolve_device(device) is None:
            return (f"Микрофон «{device}» не найден — проверьте подключение "
                    "или выберите другой в Настройках")
        return None

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        log.info("Завершение работы приложения")
        self._recording = False
        self._transcribing = False
        self._stream_session = None
        try:
            self.hotkey.stop()
        except Exception:
            pass
        self.focus.stop()
        with self._state_lock:
            if self.state == STATE_RECORDING:
                try:
                    self.recorder.stop()
                except Exception:
                    pass
        with self._engine_lock:
            engine, self.engine = self.engine, None
        if engine is not None:
            try:
                engine.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # публичный API для фронтендов (фасады над внутренними компонентами)
    def recording_level(self) -> float:
        """Уровень сигнала записи (0..~1) — для индикаторов UI."""
        return float(self.recorder.level)

    def pause_hotkey(self, paused: bool) -> None:
        """Временно заглушить глобальный хоткей (запись комбинации в UI)."""
        self.hotkey.set_paused(bool(paused))

    def model_size_bytes(self, model: str) -> int | None:
        """Размер скачанной модели на диске."""
        return self.downloads.model_size_bytes(model)

    def download_states(self) -> dict[str, dict]:
        """Текущие состояния загрузок моделей."""
        return self.downloads.states()

    def cached_models(self) -> list[str]:
        """Скачанные модели (из известных)."""
        return self.downloads.cached_models()

    # Метод run() и точка сборки приложения переехали в dictation/bootstrap.py:
    # ядро больше не знает о существовании графического интерфейса. Любой
    # фронтенд реализует свой аналог bootstrap.run_app() поверх публичного
    # API этого класса и очереди events.
