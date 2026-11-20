"""Способы вставки распознанного текста в целевое окно."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

from ..platform.base import FocusTarget, PlatformSupport


def activate_target(support: PlatformSupport, target: FocusTarget | None,
                    settle_delay: float) -> None:
    """Вернуть фокус на целевое окно, если фокус сейчас не там."""
    if target is None or support.is_own(target):
        return
    try:
        current = support.foreground()
    except Exception:
        current = None
    if current is not None and current == target:
        return
    if support.restore_focus(target):
        time.sleep(max(0.05, settle_delay))


class TextInserter(ABC):
    """Стратегия вставки текста. Новую реализацию добавить несложно
    (например, вставка через UI Automation / AXUIElement)."""

    @abstractmethod
    def insert(self, text: str, target: FocusTarget | None) -> None:
        """Вставить ``text`` в окно ``target`` (None — текущий фокус)."""
