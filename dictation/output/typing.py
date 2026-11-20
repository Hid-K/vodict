"""Вставка эмуляцией набора клавиш (pynput, SendInput/CGEvent).

Медленнее буфера обмена на длинных текстах, зато буфер не трогается.
pynput отправляет Unicode-события, так что кириллица работает независимо
от активной раскладки.
"""
from __future__ import annotations

from pynput.keyboard import Controller

from ..config import Config
from ..platform.base import FocusTarget, PlatformSupport
from ..platform.focus import FocusManager
from .base import TextInserter, activate_target


class TypingInserter(TextInserter):
    name = "typing"

    def __init__(self, config: Config, focus: FocusManager, support: PlatformSupport):
        self.config = config
        self.focus = focus
        self.support = support

    def insert(self, text: str, target: FocusTarget | None) -> None:
        activate_target(self.support, target, self.config.focus_settle_delay)
        Controller().type(text)
