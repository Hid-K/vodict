"""Системные уведомления с платформенной спецификой.

Windows: нативные тосты через windows-toasts (WinRT). Кнопка
«Переключиться на <модель>» прямо на тосте — нажатие приходит событием
в ``on_switch`` (из потока WinRT, колбэк обязан сам маршалить в UI).

macOS: нативное уведомление через ``osascript`` (без кнопок — кнопки на
уведомлениях macOS требуют подписанного .app-бандла; переключение модели
доступно через меню иконки в трее или Настройки).

На прочих платформах/при недоступности возвращает False — вызывающий код
откатывается к уведомлению через иконку трея.
"""
from __future__ import annotations

import subprocess
import sys


def _macos_notify(title: str, message: str) -> bool:
    if sys.platform != "darwin":
        return False

    def esc(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    script = (f'display notification "{esc(message)}" '
              f'with title "{esc(title)}"')
    try:
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def notify_system(title: str, message: str) -> bool:
    """Простое системное уведомление без кнопок (macOS: osascript)."""
    return _macos_notify(title, message)


def notify_model_downloaded(model: str, on_switch) -> bool:
    """Уведомление «модель загружена».

    Windows: тост с кнопкой «Переключиться на <model>»; ``on_switch()``
    вызывается при нажатии кнопки. macOS: нативное уведомление без кнопки.
    Возвращает True, если системное уведомление показано.
    """
    title = "Модель загружена ✓"
    message = f"{model} готова к использованию"

    if sys.platform.startswith("win"):
        try:
            return _windows_toast_with_switch(model, on_switch, title, message)
        except Exception:
            return False
    if _macos_notify(title, message):
        return True
    return False


def _windows_toast_with_switch(model: str, on_switch, title: str, message: str) -> bool:
    from windows_toasts import (  # installed on Windows only
        InteractableWindowsToaster,
        Toast,
        ToastButton,
        ToastDuration,
    )

    def on_activated(event_args) -> None:
        arguments = getattr(event_args, "arguments", None) or ""
        if arguments.startswith("switch_model:"):
            on_switch()
        # "close" и прочие аргументы просто закрывают тост

    toaster = InteractableWindowsToaster("Vodict — голосовая диктовка")
    toast = Toast(
        text_fields=[title, message],
        duration=ToastDuration.Long,
        on_activated=on_activated,
        actions=[
            ToastButton(f"Переключиться на {model}", f"switch_model:{model}"),
            ToastButton("Позже", "close"),
        ],
    )
    toaster.show_toast(toast)
    return True
