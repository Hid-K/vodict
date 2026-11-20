"""Реестр движков + фабрика. Классы попадают сюда через @register_engine."""
from __future__ import annotations

from typing import Type

from .base import EngineUnavailable, TranscriptionEngine

_REGISTRY: dict[str, Type[TranscriptionEngine]] = {}


def register_engine(cls: Type[TranscriptionEngine]) -> Type[TranscriptionEngine]:
    """Декоратор: регистрирует класс движка по атрибуту ``name``."""
    if not getattr(cls, "name", None):
        raise ValueError(f"{cls.__name__} должен определить атрибут name")
    if cls.name in _REGISTRY:
        raise ValueError(f"Движок с именем {cls.name!r} уже зарегистрирован")
    _REGISTRY[cls.name] = cls
    return cls


def available_engines() -> dict[str, Type[TranscriptionEngine]]:
    return dict(_REGISTRY)


def create_engine(name: str, **options) -> TranscriptionEngine:
    try:
        cls = _REGISTRY[name]
    except KeyError:
        raise EngineUnavailable(
            f"Неизвестный движок {name!r}. Доступны: {', '.join(sorted(_REGISTRY))}"
        ) from None
    return cls(**options)
