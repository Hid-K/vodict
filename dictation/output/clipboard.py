"""Вставка через буфер обмена: скопировать текст и эмулировать Ctrl+V / Cmd+V.

Плюсы: мгновенно, корректно для любого Unicode, не зависит от раскладки.
Минус: временно перезаписывает буфер (прежнее содержимое восстанавливается,
если restore_clipboard=True; нетекстовое содержимое буфера теряется).
"""
from __future__ import annotations

import time

from ..config import Config
from ..platform.base import FocusTarget, PlatformSupport
from ..platform.focus import FocusManager
from .base import TextInserter, activate_target


class ClipboardPasteInserter(TextInserter):
    name = "clipboard"

    def __init__(self, config: Config, focus: FocusManager, support: PlatformSupport):
        self.config = config
        self.focus = focus
        self.support = support

    def insert(self, text: str, target: FocusTarget | None) -> None:
        import pyperclip

        backup: str | None = None
        try:
            backup = pyperclip.paste()
        except Exception:
            backup = None

        pyperclip.copy(text)
        activate_target(self.support, target, self.config.focus_settle_delay)

        # Инжекция Ctrl+V / Cmd+V способом платформы (на Windows — прямой
        # SendInput со scan-кодами, иначе часть приложений вставку игнорирует).
        self.support.send_paste()
        time.sleep(0.15)

        if self.config.restore_clipboard and backup:
            # Даём целевому приложению время «прочитать» буфер.
            time.sleep(max(0.0, self.config.clipboard_restore_delay - 0.15))
            try:
                pyperclip.copy(backup)
            except Exception:
                pass
