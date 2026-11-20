"""DictationController — связывает запись, распознавание и вставку.

Потоковая модель:
- главный поток: tkinter (UI);
- фокус-поллер: фоновый daemon-поток FocusManager;
- запись: звук пишет колбэк PortAudio (поток sounddevice);
- watchdog записи: контроль max_duration / тишины;
- распознавание и вставка: отдельный daemon-поток на каждый фрагмент.

События для UI публикуются в thread-safe очередь ``events`` и
разбираются главным потоком.
"""
from __future__ import annotations

import queue
import threading
import time

from .audio.recorder import MicrophoneRecorder, ensure_rate
from .audio import sounds
from .config import Config
from .engines.base import EngineUnavailable
from .engines.registry import create_engine
from .hotkeys import HotkeyListener
from .output import create_inserter
from .platform import create_platform_support
from .platform.focus import FocusManager

STATE_IDLE = "idle"
STATE_RECORDING = "recording"
STATE_TRANSCRIBING = "transcribing"

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
        self.state = STATE_IDLE
        self._closed = False

        self.hotkey = HotkeyListener(
            config.hotkey, self._on_hotkey_event, mode=_safe_mode(config.hotkey_mode)
        )
        self.ui = None
        self.tray = None

    # ------------------------------------------------------------------
    # событие для UI: ("state"|"status"|"result"|"error"|"refresh"|
    #                  "show_window"|"open_settings"|"quit", payload...)
    def _post(self, kind: str, *payload) -> None:
        if kind == "error" and self.config.sound_enabled:
            sounds.beep_error()
        self.events.put((kind, *payload))

    def _set_state(self, state: str, message: str | None = None) -> None:
        with self._state_lock:
            self.state = state
        self._post("state", state)
        if message:
            self._post("status", message)

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
            if self.state == STATE_RECORDING:
                self.stop_recording()
            elif self.state == STATE_IDLE:
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
            if self.state == STATE_TRANSCRIBING:
                self._post("status", "Секунду — ещё распознаю предыдущий фрагмент…")
                return
            if self.state != STATE_IDLE:
                return
            try:
                self.recorder.start()
            except Exception as exc:
                self._post("error", f"Не удалось начать запись: {exc}")
                return
            self.state = STATE_RECORDING
            if self.config.sound_enabled:
                sounds.beep_start()
        self._post("state", STATE_RECORDING)
        threading.Thread(target=self._recording_watchdog, daemon=True).start()

    def stop_recording(self) -> None:
        with self._state_lock:
            if self.state != STATE_RECORDING:
                return
            try:
                audio, rate = self.recorder.stop()
            except Exception as exc:
                self._set_state(STATE_IDLE)
                self._post("error", f"Не удалось остановить запись: {exc}")
                return
            self.state = STATE_TRANSCRIBING
            if self.config.sound_enabled:
                sounds.beep_end()
        self._post("state", STATE_TRANSCRIBING)
        threading.Thread(
            target=self._transcribe_and_insert, args=(audio, rate), daemon=True
        ).start()

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

    # ------------------------------------------------------------------
    # распознавание + вставка
    def _transcribe_and_insert(self, audio, rate) -> None:
        try:
            text = self._recognize(audio, rate)
            if not text:
                self._set_state(STATE_IDLE, "Речь не распознана")
                return
            target = self.focus.resolve_target()
            # На время вставки глушим слушатель хоткея: инжектированные
            # Ctrl+V не должны путать состояние комбинации (например,
            # пока пользователь уже держит клавиши для следующей фразы).
            self.hotkey.set_paused(True)
            try:
                self.inserter.insert(text, target)
            except Exception as exc:
                self._set_state(STATE_IDLE)
                self._post("result", text)
                self._post("error", f"Не удалось вставить текст: {exc}")
                return
            finally:
                self.hotkey.set_paused(False)
            if target is None:
                self._set_state(
                    STATE_IDLE, "Целевое окно не определено — текст остался в буфере"
                )
            else:
                self._set_state(STATE_IDLE, "Вставлено ✓")
            self._post("result", text)
        except EngineUnavailable as exc:
            self._set_state(STATE_IDLE)
            self._post("error", str(exc))
        except Exception as exc:
            self._set_state(STATE_IDLE)
            self._post("error", f"Ошибка распознавания: {exc}")

    def _recognize(self, audio, rate) -> str:
        audio16 = ensure_rate(audio, rate)
        engine = self._get_engine()
        result = engine.transcribe(audio16, 16000)
        return (result.text or "").strip()

    def _get_engine(self):
        with self._engine_lock:
            if self.engine is None:
                self._post("status", "Загрузка модели (первый запуск может занять пару минут)…")
                self.engine = create_engine(
                    self.config.engine, **self._engine_options()
                )
            return self.engine

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
    def apply_settings(self, new_config: Config) -> None:
        hotkey_changed = (
            new_config.hotkey != self.config.hotkey
            or new_config.hotkey_mode != self.config.hotkey_mode
        )
        engine_changed = new_config.engine_signature() != self.config.engine_signature()
        mic_changed = (
            new_config.microphone != self.config.microphone
            or new_config.sample_rate != self.config.sample_rate
        )

        self.config = new_config
        try:
            new_config.save()
        except Exception as exc:
            self._post("error", f"Не удалось сохранить конфиг: {exc}")

        if mic_changed:
            self.recorder.device = new_config.microphone or None
            self.recorder.requested_rate = new_config.sample_rate
        self.inserter = create_inserter(new_config, self.focus, self.support)

        if engine_changed:
            with self._engine_lock:
                old_engine, self.engine = self.engine, None
            if old_engine is not None:
                try:
                    old_engine.close()
                except Exception:
                    pass
            self.warmup_engine_async()
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
        self._post("refresh")
        self._post("status", "Настройки сохранены")

    # ------------------------------------------------------------------
    # жизненный цикл
    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
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

    def run(self) -> None:
        from .ui.tray import TrayIcon
        from .ui.window import DictationWindow  # tkinter нужен в главном потоке

        self.ui = DictationWindow(self.config, self, show=False)

        # Трей: если недоступен — покажем обычное окно.
        self.tray = TrayIcon(self)
        tray_ok = self.tray.start()
        show_window = not tray_ok or not self.config.start_in_tray
        if self.config.start_in_tray and not tray_ok:
            print("[tray] Трей недоступен (pip install pystray pillow) — показываю окно")
        if show_window:
            self.ui.show()

        self.focus.start()
        try:
            self.hotkey.start()
        except Exception as exc:
            self._post("error", f"Глобальная горячая клавиша не запустилась: {exc}")
        self.warmup_engine_async()
        try:
            self.ui.run()
        finally:
            if self.tray is not None:
                self.tray.stop()
            self.shutdown()
