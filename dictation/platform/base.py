"""Платформенно-специфичные операции: дескриптор окна и его API."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pynput import keyboard


@dataclass
class FocusTarget:
    """Непрозрачный дескриптор окна/приложения.

    Windows: HWND (int). macOS: NSRunningApplication (pyobjc).
    """

    handle: object
    title: str = ""

    def __eq__(self, other) -> bool:
        if not isinstance(other, FocusTarget):
            return NotImplemented
        return self.handle == other.handle

    def __repr__(self) -> str:  # pragma: no cover
        return f"FocusTarget({type(self.handle).__name__}, title={self.title!r})"


class PlatformSupport(ABC):
    """Что нужно приложению от ОС: узнать активное окно, вернуть фокус,
    знать модификатор вставки."""

    name: str = "abstract"

    @abstractmethod
    def foreground(self) -> FocusTarget | None:
        """Активное окно сейчас или None."""

    @abstractmethod
    def is_own(self, target: FocusTarget) -> bool:
        """Является ли окно нашим собственным процессом."""

    @abstractmethod
    def restore_focus(self, target: FocusTarget) -> bool:
        """Активировать (поднять) указанное окно."""

    @abstractmethod
    def paste_modifier(self) -> keyboard.Key:
        """Модификатор вставки: Ctrl на Windows/Linux, Cmd на macOS."""

    def paste_key(self) -> keyboard.KeyCode:
        """Клавиша 'V' как ВИРТУАЛЬНЫЙ КОД.

        Важно: pynput отправляет обычные символы (tap('v')) как
        UNICODE-события, на которые модификатор Ctrl/Cmd НЕ действует —
        в поле печатается буква «v» вместо вставки. Виртуальный код
        делает Ctrl+V настоящей горячей клавишей.
        """
        return keyboard.KeyCode.from_vk(0x56)  # 'V'

    def send_paste(self) -> None:
        """Нажать и отпустить Ctrl+V / Cmd+V.

        Базовая реализация — через pynput. Платформы могут переопределить
        (Windows использует прямой SendInput со scan-кодами: без них часть
        приложений не может разрешить клавишу и вставка не срабатывает).
        """
        import time

        controller = keyboard.Controller()
        controller.press(self.paste_modifier())
        time.sleep(0.04)
        controller.press(self.paste_key())
        time.sleep(0.04)
        controller.release(self.paste_key())
        time.sleep(0.04)
        controller.release(self.paste_modifier())
        time.sleep(0.1)
