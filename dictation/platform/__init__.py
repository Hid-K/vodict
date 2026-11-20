"""Фабрика платформенной поддержки."""
from __future__ import annotations

import sys

from .base import FocusTarget, PlatformSupport

__all__ = ["create_platform_support", "FocusTarget", "PlatformSupport"]


def create_platform_support() -> PlatformSupport:
    if sys.platform.startswith("win"):
        from .windows import WindowsSupport
        return WindowsSupport()
    if sys.platform == "darwin":
        from .macos import MacOSSupport
        return MacOSSupport()
    from .stub import StubSupport
    return StubSupport()
