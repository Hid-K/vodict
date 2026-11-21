"""Точка сборки приложения: UI (окно + трей) + сервисы + event loop.

Единственное место во всём проекте, знающее о существовании графического
интерфейса. Заменить фронтенд = написать аналог ``run_app`` поверх
публичного API контроллера и очереди ``controller.events`` (контракт
задокументирован в dictation/app.py). Фоновые сервисы (хоткей, фокус,
прогрев движка) запускаются контроллером напрямую и от UI не зависят.
"""
from __future__ import annotations

from .config import Config
from .ui.tray import TrayIcon
from .ui.window import DictationWindow


def run_app(config: Config, controller) -> None:
    """Создаёт окно и трей, запускает сервисы и event loop.

    По завершении loop'а останавливает трей и контроллер (shutdown).
    """
    controller.ui = DictationWindow(config, controller, show=False)

    # Трей: если недоступен — покажем обычное окно.
    controller.tray = TrayIcon(controller)
    tray_ok = controller.tray.start()
    show_window = not tray_ok or not config.start_in_tray
    if config.start_in_tray and not tray_ok:
        print("[tray] Трей недоступен (pip install pystray pillow) — показываю окно")
    if show_window:
        controller.ui.show()

    controller.start_services()
    try:
        controller.ui.run()  # mainloop фронтенда
    finally:
        if controller.tray is not None:
            controller.tray.stop()
        controller.shutdown()
