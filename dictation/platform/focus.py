"""FocusManager: решает, куда вставлять распознанный текст.

Проблема: пользователь нажимает кнопку в нашем окне — фокус уходит
на наше окно, а текст нужен в прежнем. Решение: фоновый поток постоянно
запоминает последнее «чужое» окно; при вставке:

- если фокус сейчас у чужого окна (пользователь вернулся в поле ввода
  во время записи) — вставляем прямо туда;
- иначе — возвращаем фокус на последнее чужое окно и вставляем.
"""
from __future__ import annotations

import threading

from .base import FocusTarget, PlatformSupport


class FocusManager:
    def __init__(self, support: PlatformSupport, poll_interval: float = 0.4) -> None:
        self.support = support
        self.poll_interval = poll_interval
        self._last_foreign: FocusTarget | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="focus-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=1.0)

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.wait(self.poll_interval):
            try:
                current = self.support.foreground()
            except Exception:
                continue
            if current is not None and not self.support.is_own(current):
                self._last_foreign = current

    def resolve_target(self) -> FocusTarget | None:
        """Окно, в которое нужно вставить текст (см. докстринг класса)."""
        try:
            current = self.support.foreground()
        except Exception:
            current = None
        if current is not None and not self.support.is_own(current):
            return current
        return self._last_foreign
