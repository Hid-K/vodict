"""macOS: активное приложение через NSWorkspace (pyobjc).

Замечание: для эмуляции клавиатуры и глобальных хоткеев macOS требует
права Accessibility (Системные настройки → Конфиденциальность и
безопасность → Универсальный доступ) для терминала/Python.
"""
from __future__ import annotations

import os
import time

from pynput import keyboard

from .base import FocusTarget, PlatformSupport

try:
    from AppKit import NSApplicationActivateIgnoringOtherApps, NSWorkspace
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Для macOS нужен pyobjc-framework-Cocoa: pip install pyobjc-framework-Cocoa"
    ) from exc


class MacOSSupport(PlatformSupport):
    name = "macos"

    def foreground(self) -> FocusTarget | None:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        return FocusTarget(handle=app, title=str(app.localizedName()))

    def is_own(self, target: FocusTarget) -> bool:
        try:
            return int(target.handle.processIdentifier()) == os.getpid()
        except Exception:
            return False

    def restore_focus(self, target: FocusTarget) -> bool:
        app = target.handle
        try:
            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            time.sleep(0.15)
            return True
        except Exception:
            return False

    def paste_modifier(self) -> keyboard.Key:
        return keyboard.Key.cmd

    def paste_key(self) -> keyboard.KeyCode:
        return keyboard.KeyCode.from_vk(0x09)  # kVK_ANSI_V
