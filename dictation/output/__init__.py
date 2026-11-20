from .base import TextInserter, activate_target
from .clipboard import ClipboardPasteInserter
from .typing import TypingInserter


def create_inserter(config, focus, support) -> TextInserter:
    """Фабрика по config.insert_method: 'clipboard' | 'typing'."""
    if getattr(config, "insert_method", "clipboard") == "typing":
        return TypingInserter(config, focus, support)
    return ClipboardPasteInserter(config, focus, support)


__all__ = [
    "TextInserter",
    "ClipboardPasteInserter",
    "TypingInserter",
    "create_inserter",
    "activate_target",
]
