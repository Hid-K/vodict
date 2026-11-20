"""Глобальная горячая клавиша (pynput) с семантикой push-to-talk / toggle.

Синтаксис комбинации: "ctrl+alt", "ctrl+shift+f9", "cmd+space".
Алиасы: control->ctrl, option->alt, win/super/meta/command->cmd.

Важно: колбэк pynput выполняется в потоке низкоуровневого хука Windows.
Долгая работа в этом потоке (открытие микрофона и т.п.) приводит к таймауту
хука, и Windows перестаёт доставлять события — отпускание клавиш теряется.
Поэтому обработчик события выполняется в отдельном worker-потоке, а хук
мгновенно кладёт событие в очередь.
"""
from __future__ import annotations

import queue
import re
import threading
from typing import Callable

from pynput import keyboard

_ALIASES = {
    "control": "ctrl",
    "command": "cmd",
    "super": "cmd",
    "win": "cmd",
    "windows": "cmd",
    "meta": "cmd",
    "option": "alt",
    "escape": "esc",
    "return": "enter",
    "del": "delete",
    "pgup": "page_up",
    "pgdn": "page_down",
}

_TOKEN_RE = re.compile(r"^[a-z0-9_]+$")


def _normalize_token(token: str) -> str:
    token = _ALIASES.get(token.strip().lower(), token.strip().lower())
    if not token or not _TOKEN_RE.match(token):
        raise ValueError(f"Некорректная клавиша в комбинации: {token!r}")
    return token


def parse_combo(spec: str) -> frozenset[str]:
    """Разбирает строку комбинации в множество канонических токенов."""
    parts = {t for t in (_normalize_token(x) for x in spec.split("+")) if t}
    if not parts:
        raise ValueError("Пустая комбинация клавиш")
    return frozenset(parts)


def key_token(key) -> str | None:
    """Событие pynput -> канонический токен ('ctrl', 'a', 'f5'). None — нечего учесть."""
    if isinstance(key, keyboard.Key):
        name = key.name
        for base in ("ctrl", "shift", "alt", "cmd"):
            if name.startswith(base):
                return base
        return _normalize_token(name)
    char = getattr(key, "char", None)
    if char:
        return char.lower()
    return None  # dead keys / IME-события без символа


class HotkeyListener:
    """Слушает глобальную комбинацию.

    push_to_talk: on_event("press") при полном зажатии, on_event("release")
                  когда комбинация разорвана.
    toggle:       on_event("toggle") при каждом новом полном зажатии.
    """

    def __init__(self, combo_spec: str, on_event: Callable[[str], None],
                 mode: str = "push_to_talk") -> None:
        self.combo = parse_combo(combo_spec)
        self.mode = mode
        self.on_event = on_event
        self._paused = False
        self._pressed: set[str] = set()
        self._active = False
        self._events: queue.Queue[str] = queue.Queue()
        self._worker = threading.Thread(
            target=self._pump, name="hotkey-worker", daemon=True
        )
        self._listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )

    def start(self) -> None:
        self._worker.start()
        self._listener.start()

    def stop(self) -> None:
        try:
            self._listener.stop()
        except Exception:
            pass

    def set_paused(self, paused: bool) -> None:
        """Временно игнорировать события (например, пока пользователь
        записывает новую комбинацию в настройках или идёт вставка текста).
        Слушатель не пересоздаётся; события, попавшие в очередь во время
        паузы, отбрасываются."""
        self._paused = bool(paused)
        if paused:
            self._pressed.clear()
            self._active = False

    # ------------------------------------------------------------------
    def _satisfied(self) -> bool:
        return self.combo.issubset(self._pressed)

    def _on_press(self, key) -> None:
        if self._paused:
            return
        token = key_token(key)
        if token is None:
            return
        fresh = token not in self._pressed
        self._pressed.add(token)
        if not fresh or not self._satisfied():
            return  # автоповтор клавиши или неполная комбинация
        if self.mode == "push_to_talk":
            if not self._active:
                self._active = True
                self._emit("press")
        else:
            self._emit("toggle")

    def _on_release(self, key) -> None:
        if self._paused:
            return
        token = key_token(key)
        if token is None:
            return
        self._pressed.discard(token)
        if self.mode == "push_to_talk" and self._active and not self._satisfied():
            self._active = False
            self._emit("release")

    def _emit(self, event: str) -> None:
        # Только положить в очередь: поток хука не должен блокироваться.
        self._events.put(event)

    def _pump(self) -> None:
        while True:
            event = self._events.get()
            if self._paused:
                continue  # синтетические события во время паузы отбрасываем
            try:
                self.on_event(event)
            except Exception as exc:  # никогда не роняем worker-поток
                print(f"[hotkey] Ошибка обработчика: {exc}")
