"""Иконка системного трея: приложение живёт в фоне, окно по запросу.

Меню: «Показать окно» (двойной клик по иконке), «Диктовать/Стоп»,
«Настройки…», «Выход». Цвет иконки отражает состояние (зелёный/красный/
оранжевый), всплывающая подсказка — текущий статус.

Действия меню выполняются в потоке pystray: обращения к tkinter
отправляются через очередь событий контроллера, вызов записи
( controller.on_button ) потокобезопасен сам по себе.

Если pystray/Pillow не установлены или платформа не дала создать иконку,
приложение честно работает обычным окном.
"""
from __future__ import annotations

try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:  # pragma: no cover
    TRAY_AVAILABLE = False

_STATE_COLORS = {
    "idle": (46, 125, 50),        # зелёный
    "recording": (198, 40, 40),   # красный
    "transcribing": (239, 108, 0),  # оранжевый
}
_STATE_TITLES = {
    "idle": "Vodict: готов",
    "recording": "Vodict: ● запись…",
    "transcribing": "Vodict: распознаю…",
}


def _icon_image(state: str = "idle"):
    if not TRAY_AVAILABLE:
        return None
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    rgb = _STATE_COLORS.get(state, _STATE_COLORS["idle"])
    draw.ellipse([2, 2, 62, 62], fill=rgb + (255,))
    white = (255, 255, 255, 255)
    # стилизованный микрофон: капсула + дуга + ножка + основание
    draw.rounded_rectangle([24, 12, 40, 34], radius=7, fill=white)
    draw.arc([17, 16, 47, 46], start=15, end=165, fill=white, width=3)
    draw.line([(32, 44), (32, 50)], fill=white, width=3)
    draw.line([(25, 50), (39, 50)], fill=white, width=3)
    return img


class TrayIcon:
    def __init__(self, controller) -> None:
        self.controller = controller
        self._icon = None
        self._started = False

    @property
    def available(self) -> bool:
        return self._started

    # ------------------------------------------------------------------
    def start(self) -> bool:
        """Запускает иконку в отдельном потоке. False — трей недоступен."""
        if not TRAY_AVAILABLE:
            return False
        try:
            self._icon = pystray.Icon(
                "vodict",
                _icon_image("idle"),
                _STATE_TITLES["idle"],
                self._menu(),
            )
            self._icon.run_detached()
            self._started = True
            return True
        except Exception as exc:
            print(f"[tray] Не удалось запустить иконку трея: {exc}")
            self._icon = None
            return False

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
            self._started = False

    # ------------------------------------------------------------------
    def _menu(self):
        return pystray.Menu(
            pystray.MenuItem("Показать окно", self._on_show, default=True),
            pystray.MenuItem("Диктовать / Стоп", self._on_toggle),
            pystray.MenuItem("Настройки…", self._on_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", self._on_quit),
        )

    def _on_show(self, icon=None, item=None) -> None:
        self.controller.events.put(("show_window",))

    def _on_toggle(self, icon=None, item=None) -> None:
        # контроллер сам следит за состоянием; tkinter не трогаем
        self.controller.on_button()

    def _on_settings(self, icon=None, item=None) -> None:
        self.controller.events.put(("open_settings",))

    def _on_quit(self, icon=None, item=None) -> None:
        self.controller.events.put(("quit",))

    # ------------------------------------------------------------------
    # обновления из главного потока (окна)
    def set_state(self, state: str) -> None:
        if not self._started or self._icon is None:
            return
        try:
            self._icon.title = _STATE_TITLES.get(state, _STATE_TITLES["idle"])
            image = _icon_image(state)
            if image is not None:
                self._icon.icon = image
        except Exception:
            pass

    def set_title(self, text: str) -> None:
        if not self._started or self._icon is None:
            return
        try:
            self._icon.title = f"Vodict: {text}"
        except Exception:
            pass
