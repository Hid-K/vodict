"""Заглушка для прочих платформ (Linux и т.п.) — без возврата фокуса.

Вставка текста всё равно работает: если запись запущена горячей клавишей,
фокус остаётся в целевом окне, и Ctrl+V вставит текст туда.
"""
from __future__ import annotations

from pynput import keyboard

from .base import FocusTarget, PlatformSupport


class StubSupport(PlatformSupport):
    name = "stub"

    def foreground(self) -> FocusTarget | None:
        return None

    def is_own(self, target: FocusTarget) -> bool:
        return False

    def restore_focus(self, target: FocusTarget) -> bool:
        return False

    def paste_modifier(self) -> keyboard.Key:
        return keyboard.Key.ctrl

    def paste_key(self) -> keyboard.KeyCode:
        return keyboard.KeyCode.from_vk(0x56)  # VK_V
