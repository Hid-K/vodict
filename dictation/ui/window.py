"""Окно управления и окно настроек на tkinter.

Обновления из рабочих потоков приходят через очередь событий
(controller.events) и разбираются в главном потоке — так tkinter
остаётся потокобезопасным.

Настройки сгруппированы по вкладкам и покрывают все поля Config:
движок и модель, горячая клавиша (можно записать с клавиатуры), способ
вставки, микрофон (выбор из списка + проверка записи), параметры облака
и окна.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import messagebox, ttk

from ..audio import sounds
from ..audio.recorder import MicrophoneRecorder, list_input_devices
from ..config import Config, default_config_path
from ..engines.registry import available_engines
from ..history import default_history_dir
from ..hotkeys import key_token
from ..notifications import notify_model_downloaded, notify_system

MODEL_SIZES = ["tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo"]
LANGS = [("(автоопределение)", ""), ("Русский", "ru"), ("English", "en"),
         ("Deutsch", "de"), ("Français", "fr"), ("Español", "es"), ("Italiano", "it"),
         ("Українська", "uk"), ("Қазақша", "kk")]
MODES = [("PTT — удерживать и говорить", "push_to_talk"),
         ("Switch — нажал: старт, ещё раз: стоп", "toggle")]
INSERTS = [("Буфер обмена (Ctrl+V / ⌘V)", "clipboard"),
           ("Эмуляция набора клавиш", "typing")]
DEVICES = [("auto", "auto"), ("CPU", "cpu"), ("CUDA (NVIDIA)", "cuda")]
COMPUTES = [("auto", "auto"), ("int8 — быстро на CPU", "int8"),
            ("int8_float16", "int8_float16"), ("float16 — GPU", "float16"),
            ("float32 — точно", "float32")]
BEAMS = [("1 — быстрее", "1"), ("2", "2"), ("3", "3"),
         ("5 — по умолчанию", "5"), ("8 — точнее", "8")]
DEFAULT_MIC = "(по умолчанию)"

_STATE_TEXT = {
    "idle": "Готов к диктовке",
    "recording": "● Идёт запись — говорите…",
    "transcribing": "Распознавание…",
}
_STATE_COLOR = {"idle": "#2e7d32", "recording": "#c62828", "transcribing": "#ef6c00"}

_PRETTY_KEYS = {
    "ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "cmd": "Cmd/Win",
    "space": "Space", "enter": "Enter", "esc": "Esc", "tab": "Tab",
    "up": "↑", "down": "↓", "left": "←", "right": "→", "caps_lock": "Caps Lock",
    "page_up": "Page Up", "page_down": "Page Down", "backspace": "Backspace",
    "delete": "Del", "home": "Home", "end": "End", "insert": "Ins",
}


def pretty_hotkey(spec: str) -> str:
    parts = []
    for raw in spec.split("+"):
        token = raw.strip()
        if not token:
            continue
        low = token.lower()
        if low in _PRETTY_KEYS:
            parts.append(_PRETTY_KEYS[low])
        elif len(token) == 1:
            parts.append(token.upper())
        else:
            parts.append(token.capitalize())
    return " + ".join(parts)


def open_in_explorer(path: Path) -> None:
    """Открыть папку в проводнике/Finder."""
    path = Path(path)
    if not path.exists():
        path = path.parent
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class DictationWindow:
    POLL_MS = 80

    def __init__(self, config: Config, controller, show: bool = True) -> None:
        self.controller = controller
        self.root = tk.Tk()
        self.root.title("Vodict")
        self.root.resizable(False, False)
        if config.always_on_top:
            self.root.attributes("-topmost", True)
        self._build()
        self._apply_state(controller.state)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._closing = False
        if not show:
            self.root.withdraw()  # работа в фоне: только иконка в трее
        self.root.after(self.POLL_MS, self._poll)

    # ------------------------------------------------------------------
    def show(self) -> None:
        """Показать окно (из трея / при старте)."""
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except tk.TclError:
            pass
        if self.controller.config.always_on_top:
            self.root.attributes("-topmost", True)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        frm = ttk.Frame(self.root, padding=14)
        frm.pack(fill="both", expand=True)

        top = ttk.Frame(frm)
        top.pack(fill="x")
        self.status_dot = tk.Canvas(top, width=14, height=14, highlightthickness=0)
        self.status_dot.pack(side="left")
        self.status_var = tk.StringVar(value="Запуск…")
        ttk.Label(top, textvariable=self.status_var, font=("", 11, "bold")).pack(
            side="left", padx=6
        )

        self.level_canvas = tk.Canvas(frm, height=8, highlightthickness=0,
                                      background="#ececec")
        self.level_canvas.pack(fill="x", pady=(8, 2))

        self.button_var = tk.StringVar(value="🎤 Диктовать")
        ttk.Button(frm, textvariable=self.button_var, command=self.controller.on_button
                   ).pack(fill="x", ipady=9, pady=10)

        self.result_var = tk.StringVar(value="")
        ttk.Label(frm, textvariable=self.result_var, wraplength=370,
                  foreground="#555555", justify="left").pack(fill="x")

        self.hint_var = tk.StringVar(value=self._hint_text())
        ttk.Label(frm, textvariable=self.hint_var, foreground="#777777",
                  justify="left").pack(fill="x", pady=(8, 0))

        bottom = ttk.Frame(frm)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text="⚙ Настройки", command=self._open_settings).pack(side="left")
        ttk.Button(bottom, text="Микрофоны", command=self._show_mics).pack(
            side="left", padx=8
        )
        ttk.Button(bottom, text="🩺", width=3,
                   command=self._collect_diagnostics).pack(side="left")
        ttk.Button(bottom, text="📜 История", command=self._show_history).pack(side="right")

    # ------------------------------------------------------------------
    def _hint_text(self) -> str:
        c = self.controller.config
        engines = available_engines()
        cls = engines.get(c.engine)
        engine_label = cls.label if cls else c.engine
        mode = "PTT" if c.hotkey_mode == "push_to_talk" else "Switch"
        insert = "Ctrl+V" if c.insert_method == "clipboard" else "эмуляция клавиш"
        mic = c.microphone or "по умолчанию"
        return (
            f"{engine_label} • {c.model_size} • микрофон: {mic}\n"
            f"Горячая клавиша: {pretty_hotkey(c.hotkey)} ({mode}) • вставка: {insert}"
        )

    def _apply_state(self, state: str) -> None:
        self._draw_dot(_STATE_COLOR.get(state, "#999999"))
        self.status_var.set(_STATE_TEXT.get(state, state))
        self.button_var.set("⏹ Остановить" if state == "recording" else "🎤 Диктовать")
        tray = self.controller.tray
        if tray is not None:
            tray.set_state(state)

    def _draw_dot(self, color: str) -> None:
        canvas = self.status_dot
        canvas.delete("all")
        canvas.create_oval(2, 2, 12, 12, fill=color, outline="")

    def _draw_level(self) -> None:
        canvas = self.level_canvas
        width = int(canvas.winfo_width())
        height = int(canvas.winfo_height())
        canvas.delete("all")
        if self.controller.state == "recording" and width > 10:
            level = min(1.0, self.controller.recording_level() * 6)
            canvas.create_rectangle(0, 0, max(2, int(width * level)), height,
                                    fill="#c62828", width=0)

    # ------------------------------------------------------------------
    # цикл событий
    def _poll(self) -> None:
        if self._closing:
            return
        try:
            try:
                while True:
                    self._handle(self.controller.events.get_nowait())
            except queue.Empty:
                pass
            self._draw_level()
        except tk.TclError:
            return  # окно уже уничтожается — не планируем следующий тик
        self.root.after(self.POLL_MS, self._poll)

    def _handle(self, event: tuple) -> None:
        kind, payload = event[0], event[1] if len(event) > 1 else None
        if kind == "state":
            self._apply_state(payload)
        elif kind == "status":
            self.status_var.set(payload)
            if self.controller.tray is not None:
                self.controller.tray.set_title(payload)
        elif kind == "result":
            text = payload or ""
            tail = "…" if len(text) > 120 else ""
            self.result_var.set(f"Вставлено: {text[:120]}{tail}")
        elif kind == "error":
            self.status_var.set("Ошибка")
            self._notify_or_dialog("Ошибка диктовки", payload)
        elif kind == "refresh":
            self.hint_var.set(self._hint_text())
            try:
                self.root.attributes(
                    "-topmost", bool(self.controller.config.always_on_top)
                )
            except tk.TclError:
                pass
        elif kind == "show_window":
            self.show()
        elif kind == "open_settings":
            self.show()
            self._open_settings()
        elif kind == "quit":
            self._quit()
        elif kind == "switch_model":
            self._switch_model(payload)
        elif kind == "model_download_started":
            self.status_var.set(f"Загрузка модели {payload}…")
        elif kind == "model_download_progress":
            model = event[1]
            pct = event[2] if len(event) > 2 else None
            self.status_var.set(f"Загрузка модели {model}: "
                                + (f"{pct:.0f}%" if pct is not None else "подготовка…"))
        elif kind == "model_download_done":
            model = event[1]
            self.status_var.set(f"Модель {model} загружена ✓")
            # Windows: тост с кнопкой «Переключиться»; macOS: нативное
            # уведомление. Переключение идёт через очередь событий, чтобы
            # apply_settings выполнялся в главном потоке.
            shown = notify_model_downloaded(
                model,
                on_switch=lambda m=model: self.controller.events.put(
                    ("switch_model", m)
                ),
            )
            if not shown:
                tray = self.controller.tray
                if tray is not None and tray.notify(
                        "Модель загружена ✓",
                        f"{model} готова. Переключиться: меню иконки "
                        "в трее → «Переключить модель», или в Настройках."):
                    return
                messagebox.showinfo("Модель загружена",
                                    f"«{model}» готова к использованию.",
                                    parent=self.root)
        elif kind == "model_download_error":
            model = event[1]
            err = event[2] if len(event) > 2 else ""
            self.status_var.set(f"Ошибка загрузки модели {model}")
            self._notify_or_dialog("Ошибка загрузки модели", f"{model}: {err}")

    # ------------------------------------------------------------------
    def _on_close(self) -> None:
        # Крестик сворачивает в трей; полный выход — через меню трея.
        tray = self.controller.tray
        if tray is not None and tray.available:
            self.root.withdraw()
            self.status_var.set("Свёрнуто в трей")
        else:
            self._quit()

    def _quit(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.controller.shutdown()
        tray = self.controller.tray
        if tray is not None:
            tray.stop()
        self.root.destroy()

    def _switch_model(self, model: str) -> None:
        """Переключиться на загруженную модель (трей/уведомление)."""
        self.controller.apply_settings(
            replace(self.controller.config, model_size=model)
        )

    def _notify_or_dialog(self, title: str, message: str) -> None:
        # Сначала macOS-/osascript-уведомление, затем трей, затем окно.
        if notify_system(title, message):
            return
        tray = self.controller.tray
        if tray is not None and tray.notify(title, message):
            return
        messagebox.showerror(title, message, parent=self.root)

    def run(self) -> None:
        self.root.mainloop()

    # ------------------------------------------------------------------
    def _open_settings(self) -> None:
        SettingsDialog(self.root, self.controller.config, self.controller)

    def _show_history(self) -> None:
        HistoryDialog(self.root, self.controller)

    def _collect_diagnostics(self) -> None:
        from ..diagnostics import collect

        try:
            path = collect(self.controller.config)
        except Exception as exc:
            messagebox.showerror("Диагностика", f"Не удалось собрать: {exc}",
                                 parent=self.root)
            return
        if messagebox.askyesno(
                "Диагностика",
                f"Файл собран:\n{path}\n\nОтправьте его тому, кто помогает "
                "с отладкой. Открыть папку?",
                parent=self.root):
            open_in_explorer(path.parent)

    def _show_mics(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Микрофоны")
        dialog.transient(self.root)
        try:
            dialog.grab_set()
        except tk.TclError:
            pass
        text = tk.Text(dialog, width=70, height=14, wrap="none")
        text.pack(padx=10, pady=(10, 4))
        try:
            devices = list_input_devices()
        except Exception as exc:
            devices = []
            text.insert("1.0", f"Не удалось получить список: {exc}")
        for dev in devices:
            default = "  (по умолчанию)" if dev["is_default"] else ""
            text.insert("end", f"[{dev['index']}] {dev['name']} — "
                               f"{int(dev['default_samplerate'])} Гц{default}\n")
        text.configure(state="disabled")
        ttk.Label(
            dialog,
            text="Полный список также доступен во вкладке «Аудио» настроек.",
            foreground="#777777",
        ).pack(padx=10, pady=(0, 10))


# ======================================================================
# Диалог истории диктовок
# ======================================================================
class HistoryDialog(tk.Toplevel):
    def __init__(self, master, controller) -> None:
        super().__init__(master)
        self.title("История диктовок")
        self.geometry("880x420")
        self.transient(master)
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.controller = controller

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        columns = ("time", "sec", "speed", "model", "qual", "beam",
                   "lang", "chars", "status", "text")
        headers = {"time": "Время", "sec": "Сек", "speed": "Скорость",
                   "model": "Модель", "qual": "Кач", "beam": "Beam",
                   "lang": "Яз", "chars": "Симв.",
                   "status": "Статус", "text": "Текст"}
        widths = {"time": 145, "sec": 50, "speed": 65, "model": 150,
                  "qual": 55, "beam": 40, "lang": 35, "chars": 45,
                  "status": 70, "text": 230}
        self.tree = ttk.Treeview(frm, columns=columns, show="headings", height=16)
        for col in columns:
            self.tree.heading(col, text=headers[col])
            self.tree.column(col, width=widths[col],
                             stretch=(col == "text"), anchor="w")
        vsb = ttk.Scrollbar(frm, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        frm.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)

        bottom = ttk.Frame(frm)
        bottom.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(bottom, text="⟳ Обновить", command=self._refresh).pack(side="left")
        ttk.Button(bottom, text="📂 Открыть папку",
                   command=lambda: open_in_explorer(self._history_dir())).pack(
            side="left", padx=8)
        ttk.Label(bottom, text=f"Хранение: {self._history_dir()}",
                  foreground="#888888").pack(side="left", padx=(16, 0))

        self._refresh()

    def _history_dir(self):
        history = self.controller.history
        return history.root if history is not None else default_history_dir()

    def _refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        history = self.controller.history
        if history is None:
            self.tree.insert("", "end",
                             values=("—", "", "", "", "", "", "", "", "",
                                     "история отключена в настройках"))
            return
        status_ru = {"ok": "✓", "empty": "пусто", "insert_error": "ошибка вст.",
                     "error": "ошибка"}
        for r in history.read_index(100):
            status = status_ru.get(r.get("status", ""), r.get("status", ""))
            speed = r.get("speed")
            self.tree.insert("", "end", values=(
                r.get("time", ""),
                f"{float(r.get('seconds', 0)):.1f}",
                f"×{speed}" if speed else "—",
                r.get("model", ""),
                r.get("compute_type", ""),
                r.get("beam_size", ""),
                r.get("language", ""),
                r.get("chars", 0),
                status,
                r.get("preview", ""),
            ))


# ======================================================================
# Запись комбинации клавиш с клавиатуры
# ======================================================================
class HotkeyCapture:
    """Слушает клавиатуру и собирает комбинацию: держите модификаторы
    (Ctrl/Shift/Alt/Cmd) и нажмите обычную клавишу — она завершит запись.
    Если нужна комбинация только из модификаторов (как ctrl+alt) —
    просто нажмите «Стоп», когда строка покажет нужное."""

    MODS = ("ctrl", "shift", "alt", "cmd")

    def __init__(self) -> None:
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self._mods: set[str] = set()
        from pynput import keyboard

        self._listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        try:
            self._listener.stop()
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _fmt(self, last: str | None = None) -> str:
        parts = [m for m in self.MODS if m in self._mods]
        if last:
            parts.append(last)
        return "+".join(parts)

    def _on_press(self, key) -> None:
        token = key_token(key)
        if token is None:
            return
        if token in self.MODS:
            if token not in self._mods:
                self._mods.add(token)
                self.events.put(("update", self._fmt()))
        else:
            self.events.put(("final", self._fmt(token)))

    def _on_release(self, key) -> None:
        token = key_token(key)
        if token in self.MODS:
            self._mods.discard(token)
            self.events.put(("update", self._fmt()))


# ======================================================================
# Окно настроек
# ======================================================================
class SettingsDialog(tk.Toplevel):
    def __init__(self, master, config: Config, controller) -> None:
        super().__init__(master)
        self.title("Настройки диктовки")
        self.resizable(False, False)
        self.transient(master)
        self.controller = controller
        self._config = config
        self._capturing: HotkeyCapture | None = None
        self._capture_deadline = 0.0
        self._mic_testing = False

        engines = available_engines()
        self._engine_by_label = {cls.label: name for name, cls in engines.items()}

        self._build()
        self._load(config)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._active_model = config.model_size
        self._last_labels = self._model_labels()
        self._del_map: dict[str, str] = {}
        self._last_del_labels: list[str] = []
        self.after(60, self._acquire_grab)
        self.after(300, self._poll_downloads)

    def _acquire_grab(self) -> None:
        """Захват ввода после того, как окно реально показалось
        (grab_set на невидимом окне вызывает TclError)."""
        if not self.winfo_exists():
            return
        try:
            self.grab_set()
        except tk.TclError:
            self.after(60, self._acquire_grab)

    # ------------------------------------------------------------------
    # каркас: вкладки и строки «подпись → виджет»
    def _build(self) -> None:
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        self.nb = ttk.Notebook(outer)
        self.nb.pack(fill="both", expand=True)
        self.tab_speech = self._tab("Распознавание")
        self.tab_hotkey = self._tab("Горячая клавиша")
        self.tab_insert = self._tab("Вставка")
        self.tab_audio = self._tab("Аудио")
        self.tab_cloud = self._tab("Облако (OpenAI)")
        self.tab_window = self._tab("Звук и окно")
        self._build_speech()
        self._build_hotkey()
        self._build_insert()
        self._build_audio()
        self._build_cloud()
        self._build_window()

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="По умолчанию", command=self._reset_defaults).pack(
            side="left"
        )
        ttk.Button(buttons, text="Отмена", command=self._cancel).pack(side="right")
        ttk.Button(buttons, text="💾 Сохранить", command=self._save).pack(
            side="right", padx=(0, 8)
        )

    def _tab(self, title: str) -> ttk.Frame:
        frame = ttk.Frame(self.nb, padding=(12, 10))
        self.nb.add(frame, text=title)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(100, weight=1)
        return frame

    def _row(self, parent: ttk.Frame, label: str) -> ttk.Frame:
        row = parent.grid_size()[1]
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="nw", pady=3)
        holder = ttk.Frame(parent)
        holder.grid(row=row, column=1, sticky="ew", pady=3, padx=(10, 0))
        return holder

    def _note(self, parent: ttk.Frame, text: str) -> None:
        row = parent.grid_size()[1]
        ttk.Label(parent, text=text, foreground="#888888",
                  wraplength=380, justify="left").grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )

    # ------------------------------------------------------------------
    def _build_speech(self) -> None:
        f = self.tab_speech
        holder = self._row(f, "Движок")
        self.engine_var = tk.StringVar()
        ttk.Combobox(holder, textvariable=self.engine_var, state="readonly",
                     values=list(self._engine_by_label), width=36).pack(fill="x")

        holder = self._row(f, "Модель Whisper")
        self.model_var = tk.StringVar()
        self.model_cb = ttk.Combobox(holder, textvariable=self.model_var,
                                     values=self._model_labels(), width=36)
        self.model_cb.pack(fill="x")
        self.model_cb.bind("<<ComboboxSelected>>", self._on_model_selected)

        # Прогресс загрузки новой модели (скрыт, пока ничего не качается)
        self.dl_box = ttk.Frame(holder)
        self.dl_label = tk.Label(self.dl_box, text="", fg="#1e88e5")
        self.dl_bar = ttk.Progressbar(self.dl_box, maximum=100)
        self.dl_label.pack(anchor="w", pady=(4, 0))
        self.dl_bar.pack(fill="x", pady=(2, 0))

        # Удаление скачанных моделей (освобождение места)
        holder = self._row(f, "Удаление моделей")
        self.del_model_var = tk.StringVar()
        self.del_cb = ttk.Combobox(holder, textvariable=self.del_model_var,
                                   state="readonly", width=32)
        self.del_cb.pack(side="left", fill="x", expand=True)
        ttk.Button(holder, text="🗑 Удалить",
                   command=self._delete_model).pack(side="left", padx=(8, 0))

        holder = self._row(f, "Прокси для HuggingFace")
        self.proxy_enabled_var = tk.BooleanVar()
        ttk.Checkbutton(holder, text="Использовать прокси (SOCKS5/HTTP)",
                        variable=self.proxy_enabled_var).pack(anchor="w")
        self.proxy_url_var = tk.StringVar()
        ttk.Entry(holder, textvariable=self.proxy_url_var, width=36).pack(
            anchor="w", pady=(2, 0))
        ttk.Label(holder, text="пример: socks5://127.0.0.1:1080 или "
                  "socks5://user:pass@host:1080. Применяется после "
                  "перезапуска приложения.",
                  foreground="#888888", wraplength=380,
                  justify="left").pack(anchor="w", pady=(2, 0))

        holder = self._row(f, "Язык речи")
        self.lang_var = tk.StringVar()
        ttk.Combobox(holder, textvariable=self.lang_var, state="readonly",
                     values=[lbl for lbl, _ in LANGS], width=36).pack(fill="x")

        holder = self._row(f, "Устройство вычислений")
        self.device_var = tk.StringVar()
        ttk.Combobox(holder, textvariable=self.device_var, state="readonly",
                     values=[lbl for lbl, _ in DEVICES], width=36).pack(fill="x")

        holder = self._row(f, "Тип вычислений")
        self.compute_var = tk.StringVar()
        ttk.Combobox(holder, textvariable=self.compute_var, state="readonly",
                     values=[lbl for lbl, _ in COMPUTES], width=36).pack(fill="x")

        holder = self._row(f, "Beam size (точность/скорость)")
        self.beam_var = tk.StringVar()
        ttk.Combobox(holder, textvariable=self.beam_var, state="readonly",
                     values=[lbl for lbl, _ in BEAMS], width=24).pack(side="left")

        holder = self._row(f, "")
        self.vad_var = tk.BooleanVar()
        ttk.Checkbutton(holder, text="Отсекать тишину и шум (VAD)",
                        variable=self.vad_var).pack(anchor="w")

        holder = self._row(f, "Подсказка модели\n(стиль, глоссарий)")
        self.prompt_var = tk.StringVar()
        ttk.Entry(holder, textvariable=self.prompt_var, width=38).pack(fill="x")

        self._note(f, "Модель скачивается автоматически при первом использовании "
                      "размера. «small» — разумный баланс; «large-v3» — максимум "
                      "качества (медленнее). Выберите ещё не загруженную модель — "
                      "начнётся загрузка с прогрессом.")

    # ------------------------------------------------------------------
    # статусы моделей faster-whisper: кэш / загрузка с прогрессом
    def _downloads_active(self):
        if self.controller.config.engine != "faster-whisper":
            return None
        return self.controller.downloads

    def _model_suffix(self, model: str, mgr) -> str:
        st = mgr.state(model)
        if st and st["state"] == "downloading":
            p = st.get("progress")
            return f" ⬇ {p:.0f}%" if p is not None else " ⬇ …"
        if st and st["state"] == "error":
            return " ⚠ ошибка"
        if mgr.is_cached(model):
            return ""
        return " ⤓ не загружена"

    def _model_labels(self) -> list[str]:
        mgr = self._downloads_active()
        if mgr is None:
            return list(MODEL_SIZES)
        return [m + self._model_suffix(m, mgr) for m in MODEL_SIZES]

    @staticmethod
    def _model_from_label(label: str) -> str:
        """«large-v3 ⬇ 45%» / «small (460 МБ)» -> имя модели."""
        cut = len(label)
        for marker in (" ⤓", " ⬇", " ✓", " ⚠", " ("):
            i = label.find(marker)
            if 0 < i < cut:
                cut = i
        return label[:cut].strip()

    def _on_model_selected(self, event=None) -> None:
        model = self._model_from_label(self.model_var.get())
        mgr = self._downloads_active()
        if mgr is None:
            return
        st = mgr.state(model)
        if st and st["state"] == "downloading":
            p = st.get("progress")
            pct = f"{p:.0f}% " if p is not None else ""
            messagebox.showinfo(
                "Загрузка модели",
                f"«{model}» уже загружается ({pct}— подождите завершения, "
                "затем её можно будет выбрать).",
                parent=self,
            )
            self.model_var.set(self._active_model)
            return
        result = self.controller.request_model_download(model)
        if result == "started":
            # Модель недоступна, пока качается — возвращаем активную,
            # прогресс виден ниже; по завершении предложим переключиться.
            self.model_var.set(self._active_model)
            self.model_cb["values"] = self._model_labels()
            messagebox.showinfo(
                "Загрузка модели",
                f"Начал загрузку «{model}». Прогресс — под списком моделей; "
                "по завершении появится предложение переключиться на неё.",
                parent=self,
            )
        # result == "cached" — обычный выбор; применится кнопкой «Сохранить»

    def _poll_downloads(self) -> None:
        if not self.winfo_exists():
            return
        try:
            mgr = self._downloads_active()
            downloading = None
            if mgr is not None:
                for m, st in mgr.states().items():
                    if st.get("state") == "downloading":
                        downloading = (m, st.get("progress"))
                        break
            if downloading is not None:
                m, p = downloading
                if not self.dl_box.winfo_ismapped():
                    self.dl_box.pack(fill="x", pady=(4, 0))
                self.dl_label.config(
                    text=f"Загрузка «{m}»: "
                         + (f"{p:.0f}%" if p is not None else "подготовка…")
                )
                if p is not None:
                    self.dl_bar["value"] = p
            elif self.dl_box.winfo_ismapped():
                self.dl_box.pack_forget()
            labels = self._model_labels()
            if labels != self._last_labels:
                self._last_labels = labels
                self.model_cb["values"] = labels
            self._refresh_delete_list()
        except tk.TclError:
            return
        self.after(300, self._poll_downloads)

    def _refresh_delete_list(self) -> None:
        mgr = self.controller.downloads
        if mgr is None:
            return
        self._del_map = {}
        labels = []
        for m in mgr.cached_models():
            size = mgr.model_size_bytes(m)
            label = f"{m} ({size / 1e6:.0f} МБ)" if size else m
            self._del_map[label] = m
            labels.append(label)
        if labels != self._last_del_labels:
            self._last_del_labels = labels
            self.del_cb["values"] = labels
            if self.del_model_var.get() not in labels:
                self.del_model_var.set("")

    def _delete_model(self) -> None:
        label = self.del_model_var.get().strip()
        if not label:
            messagebox.showinfo("Удаление моделей",
                                "Выберите модель из списка.", parent=self)
            return
        model = self._del_map.get(label, self._model_from_label(label))
        if model == self.controller.config.model_size:
            messagebox.showwarning(
                "Удаление моделей",
                f"«{model}» — активная модель. Сначала переключитесь на другую.",
                parent=self,
            )
            return
        size = self.controller.downloads.model_size_bytes(model)
        size_text = f"\nОсвободит ~{size / 1e6:.0f} МБ." if size else ""
        if not messagebox.askyesno(
                "Удаление моделей", f"Удалить «{model}» из кэша?{size_text}",
                parent=self):
            return
        try:
            freed = self.controller.delete_model(model)
        except Exception as exc:
            messagebox.showerror("Удаление моделей", str(exc), parent=self)
            return
        self._refresh_delete_list()
        messagebox.showinfo(
            "Удаление моделей",
            f"«{model}» удалена. Освобождено {freed / 1e6:.0f} МБ.",
            parent=self,
        )

    # ------------------------------------------------------------------
    def _build_hotkey(self) -> None:
        f = self.tab_hotkey
        holder = self._row(f, "Комбинация")
        self.hotkey_var = tk.StringVar()
        self.hotkey_entry = ttk.Entry(holder, textvariable=self.hotkey_var, width=24)
        self.hotkey_entry.pack(side="left")
        self.capture_btn_var = tk.StringVar(value="✎ Записать…")
        ttk.Button(holder, textvariable=self.capture_btn_var,
                   command=self._toggle_capture).pack(side="left", padx=(8, 0))

        holder = self._row(f, "Режим")
        self.mode_var = tk.StringVar()
        ttk.Combobox(holder, textvariable=self.mode_var, state="readonly",
                     values=[lbl for lbl, _ in MODES], width=36).pack(fill="x")

        self._note(f, "«Записать…»: держите модификаторы и нажмите обычную клавишу — "
                      "она завершит запись. Для комбинации только из модификаторов "
                      "(напр. Ctrl+Alt) нажмите «Стоп», когда строка покажет нужное.\n\n"
                      "На раскладках с AltGr (немецкая и т.п.) избегайте ctrl+alt — "
                      "возьмите ctrl+shift или клавишу F1–F12.")

    def _build_insert(self) -> None:
        f = self.tab_insert
        holder = self._row(f, "Способ вставки")
        self.insert_var = tk.StringVar()
        ttk.Combobox(holder, textvariable=self.insert_var, state="readonly",
                     values=[lbl for lbl, _ in INSERTS], width=36).pack(fill="x")

        holder = self._row(f, "")
        self.restore_var = tk.BooleanVar()
        ttk.Checkbutton(holder, text="Восстанавливать прежний буфер обмена",
                        variable=self.restore_var).pack(anchor="w")

        holder = self._row(f, "Пауза перед восстановлением\nбуфера, с")
        self.clipdelay_var = tk.StringVar()
        ttk.Entry(holder, textvariable=self.clipdelay_var, width=10).pack(side="left")

        holder = self._row(f, "Пауза после активации\nокна, с")
        self.focusdelay_var = tk.StringVar()
        ttk.Entry(holder, textvariable=self.focusdelay_var, width=10).pack(side="left")

        self._note(f, "Буфер: быстро и надёжно, но прежний текстовый буфер "
                      "восстанавливается, а нетекстовое содержимое (картинки) "
                      "теряется. «Эмуляция набора» буфер не трогает, но длинные "
                      "тексты печатает медленнее.")

    # ------------------------------------------------------------------
    def _build_audio(self) -> None:
        f = self.tab_audio
        holder = self._row(f, "Микрофон")
        self.mic_var = tk.StringVar()
        self.mic_combo = ttk.Combobox(holder, textvariable=self.mic_var, width=34)
        self.mic_combo.pack(fill="x")
        btns = ttk.Frame(holder)
        btns.pack(fill="x", pady=(4, 0))
        ttk.Button(btns, text="⟳ Обновить список", command=self._refresh_mics).pack(
            side="left"
        )
        self.mictest_btn_var = tk.StringVar(value="Проверить (2 с)")
        ttk.Button(btns, textvariable=self.mictest_btn_var,
                   command=self._test_mic).pack(side="left", padx=(8, 0))
        self.mictest_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.mictest_var, foreground="#555555",
                  wraplength=380, justify="left").grid(
            row=f.grid_size()[1], column=0, columnspan=2, sticky="ew"
        )

        holder = self._row(f, "Частота записи, Гц")
        self.rate_var = tk.StringVar()
        ttk.Entry(holder, textvariable=self.rate_var, width=10).pack(side="left")

        holder = self._row(f, "Лимит записи, с (0 — нет)")
        self.maxdur_var = tk.StringVar()
        ttk.Entry(holder, textvariable=self.maxdur_var, width=10).pack(side="left")

        holder = self._row(f, "Авто-стоп тишины, с (0 — выкл)")
        self.silence_var = tk.StringVar()
        ttk.Entry(holder, textvariable=self.silence_var, width=10).pack(side="left")

        holder = self._row(f, "Порог тишины (RMS)")
        self.threshold_var = tk.StringVar()
        ttk.Entry(holder, textvariable=self.threshold_var, width=10).pack(side="left")

        self._note(f, "Если микрофон не открывается на выбранной частоте, "
                      "приложение автоматически возьмёт частоту устройства "
                      "и приведёт звук к 16 кГц перед распознаванием.")
        self._refresh_mics()

    def _build_cloud(self) -> None:
        f = self.tab_cloud
        holder = self._row(f, "API-ключ")
        self.apikey_var = tk.StringVar()
        ttk.Entry(holder, textvariable=self.apikey_var, show="•",
                  width=38).pack(fill="x")

        holder = self._row(f, "Base URL (необязательно)")
        self.apibase_var = tk.StringVar()
        ttk.Entry(holder, textvariable=self.apibase_var, width=38).pack(fill="x")

        holder = self._row(f, "Модель API")
        self.apimodel_var = tk.StringVar()
        ttk.Entry(holder, textvariable=self.apimodel_var, width=38).pack(fill="x")

        self._note(f, "Используется движком «OpenAI Whisper API (облако)». Ключ "
                      "также можно задать переменной окружения OPENAI_API_KEY. "
                      "Base URL — для совместимых сервисов.")

    def _build_window(self) -> None:
        f = self.tab_window
        holder = self._row(f, "Звуковые сигналы")
        self.sound_var = tk.BooleanVar()
        ttk.Checkbutton(holder, text="Сигнал начала и окончания записи",
                        variable=self.sound_var).pack(anchor="w")
        btns = ttk.Frame(holder)
        btns.pack(anchor="w", pady=(4, 0))
        ttk.Button(btns, text="▸ Прослушать начало",
                   command=sounds.beep_start).pack(side="left")
        ttk.Button(btns, text="▸ Прослушать конец",
                   command=sounds.beep_end).pack(side="left", padx=(8, 0))

        holder = self._row(f, "История диктовок")
        self.history_var = tk.BooleanVar()
        ttk.Checkbutton(holder, text="Сохранять тексты и метаданные",
                        variable=self.history_var).pack(anchor="w")
        self.save_audio_var = tk.BooleanVar()
        ttk.Checkbutton(holder, text="Сохранять аудиозаписи (wav)",
                        variable=self.save_audio_var).pack(anchor="w")
        self.historydir_var = tk.StringVar()
        ttk.Entry(holder, textvariable=self.historydir_var, width=36).pack(
            anchor="w", pady=(2, 0)
        )
        ttk.Label(holder, text=f"пусто = {default_history_dir()}",
                  foreground="#888888").pack(anchor="w")

        holder = self._row(f, "Режим работы")
        self.tray_var = tk.BooleanVar()
        ttk.Checkbutton(holder, text="Запускать свёрнутым в трей (работа в фоне)",
                        variable=self.tray_var).pack(anchor="w")

        holder = self._row(f, "")
        self.topmost_var = tk.BooleanVar()
        ttk.Checkbutton(holder, text="Держать окно поверх всех",
                        variable=self.topmost_var).pack(anchor="w")

        holder = self._row(f, "Файл настроек")
        self.cfgpath_var = tk.StringVar(value=str(default_config_path()))
        ttk.Label(holder, textvariable=self.cfgpath_var, foreground="#777777",
                  wraplength=300, justify="left").pack(fill="x")
        ttk.Button(holder, text="Открыть папку",
                   command=self._open_config_folder).pack(anchor="w", pady=(4, 0))

        self._note(f, "Когда окно свёрнуто в трей, диктовка по-прежнему работает "
                      "по горячей клавише; цвет иконки трея показывает состояние. "
                      "Выход из программы — меню иконки трея → «Выход».")

    # ------------------------------------------------------------------
    # заполнение полей из конфига
    def _load(self, cfg: Config) -> None:
        engines = available_engines()
        cls = engines.get(cfg.engine)
        self.engine_var.set(cls.label if cls else cfg.engine)
        self.model_var.set(cfg.model_size)
        self.lang_var.set(next((lbl for lbl, val in LANGS if val == cfg.language),
                               LANGS[0][0]))
        self.device_var.set(next((lbl for lbl, val in DEVICES if val == cfg.device),
                                 "auto"))
        self.compute_var.set(next((lbl for lbl, val in COMPUTES
                                   if val == cfg.compute_type), "auto"))
        self.beam_var.set(next((lbl for lbl, val in BEAMS
                                if val == str(cfg.beam_size)),
                               "5 — по умолчанию"))
        self.vad_var.set(cfg.vad_filter)
        self.prompt_var.set(cfg.initial_prompt)

        self.hotkey_var.set(cfg.hotkey)
        self.mode_var.set(next((lbl for lbl, val in MODES if val == cfg.hotkey_mode),
                               MODES[0][0]))

        self.insert_var.set(next((lbl for lbl, val in INSERTS
                                  if val == cfg.insert_method), INSERTS[0][0]))
        self.restore_var.set(cfg.restore_clipboard)
        self.clipdelay_var.set(str(cfg.clipboard_restore_delay))
        self.focusdelay_var.set(str(cfg.focus_settle_delay))

        self.mic_var.set(cfg.microphone or DEFAULT_MIC)
        self.rate_var.set(str(cfg.sample_rate))
        self.maxdur_var.set(str(cfg.max_duration))
        self.silence_var.set(str(cfg.silence_timeout))
        self.threshold_var.set(str(cfg.silence_rms_threshold))

        self.apikey_var.set(cfg.api_key)
        self.apibase_var.set(cfg.api_base_url)
        self.apimodel_var.set(cfg.api_model)
        self.sound_var.set(cfg.sound_enabled)
        self.tray_var.set(cfg.start_in_tray)
        self.history_var.set(cfg.history_enabled)
        self.save_audio_var.set(cfg.save_audio)
        self.historydir_var.set(cfg.history_dir)
        self.proxy_enabled_var.set(cfg.proxy_enabled)
        self.proxy_url_var.set(cfg.proxy_url)
        self.topmost_var.set(cfg.always_on_top)

    def _reset_defaults(self) -> None:
        if self._capturing is not None:
            self._finish_capture(keep=False)
        self._load(Config())
        self.mictest_var.set("")

    # ------------------------------------------------------------------
    # микрофон: список и проверка
    def _refresh_mics(self) -> None:
        current = self.mic_var.get()
        try:
            devices = [d["name"] for d in list_input_devices()]
        except Exception:
            devices = []
        values = [DEFAULT_MIC] + devices
        self.mic_combo["values"] = values
        if current and current not in values:
            self.mic_var.set(current)  # сохраняем ручной ввод/подстроку

    def _mic_device_value(self):
        value = self.mic_var.get().strip()
        if not value or value == DEFAULT_MIC:
            return None
        return value

    def _test_mic(self) -> None:
        if self._mic_testing:
            return
        if self.controller.state == "recording":
            self.mictest_var.set("Сейчас идёт диктовка — проверка недоступна.")
            return
        device = self._mic_device_value()
        recorder = MicrophoneRecorder(device=device)
        try:
            recorder.start()
        except Exception as exc:
            self.mictest_var.set(f"Не удалось открыть микрофон: {exc}")
            return
        self._mic_testing = True
        self.mictest_btn_var.set("● Говорите…")
        self.mictest_var.set("Запись 2 секунд — скажите что-нибудь…")

        def worker():
            peak = 0.0
            deadline = time.time() + 2.0
            while time.time() < deadline:
                peak = max(peak, recorder.level)
                time.sleep(0.05)
            try:
                audio, rate = recorder.stop()
            except Exception as exc:
                audio, rate = None, 0
                peak_err = str(exc)
            else:
                peak_err = ""
            self.after(0, lambda: self._mic_test_done(peak, audio, rate, peak_err))

        threading.Thread(target=worker, daemon=True).start()

    def _mic_test_done(self, peak: float, audio, rate: int, error: str) -> None:
        self._mic_testing = False
        self.mictest_btn_var.set("Проверить (2 с)")
        if error:
            self.mictest_var.set(f"Ошибка записи: {error}")
            return
        duration = (len(audio) / rate) if audio is not None and rate else 0.0
        if peak < 0.001:
            verdict = "тишина — проверьте устройство/разрешения"
        elif peak < 0.01:
            verdict = "очень тихо — говорите ближе к микрофону"
        else:
            verdict = "уровень хороший, можно диктовать"
        self.mictest_var.set(
            f"Пик уровня: {peak:.3f} • {duration:.1f} с — {verdict}"
        )

    # ------------------------------------------------------------------
    # запись комбинации
    def _toggle_capture(self) -> None:
        if self._capturing is None:
            self.controller.pause_hotkey(True)  # не начать диктовку случайно
            self._capturing = HotkeyCapture()
            self._capturing.start()
            self._capture_deadline = time.time() + 12.0
            self.capture_btn_var.set("⏹ Стоп")
            self.hotkey_entry.state(["disabled"])
            self.hotkey_var.set("Нажмите комбинацию…")
            self.after(40, self._poll_capture)
        else:
            self._finish_capture(keep=True)

    def _poll_capture(self) -> None:
        capture = self._capturing
        if capture is None:
            return
        try:
            while True:
                kind, combo = capture.events.get_nowait()
                if kind == "update":
                    if combo:
                        self.hotkey_var.set(combo)
                else:  # final
                    self.hotkey_var.set(combo or self._config.hotkey)
                    self._finish_capture(keep=bool(combo))
                    return
        except queue.Empty:
            pass
        if time.time() > self._capture_deadline:
            self._finish_capture(keep=True)
            return
        self.after(40, self._poll_capture)

    def _finish_capture(self, keep: bool) -> None:
        capture, self._capturing = self._capturing, None
        if capture is not None:
            capture.stop()
        self.controller.hotkey.set_paused(False)
        self.capture_btn_var.set("✎ Записать…")
        try:
            self.hotkey_entry.state(["!disabled"])
        except tk.TclError:
            pass
        value = self.hotkey_var.get().strip()
        if not keep or not value or value == "Нажмите комбинацию…":
            self.hotkey_var.set(self._config.hotkey)

    # ------------------------------------------------------------------
    # служебное
    def _open_config_folder(self) -> None:
        open_in_explorer(default_config_path())

    def _cancel(self) -> None:
        if self._capturing is not None:
            self._finish_capture(keep=False)
        self.destroy()

    # ------------------------------------------------------------------
    # сохранение
    def _num(self, getter, name: str, kind=float, minv=None, maxv=None):
        raw = getter().strip()
        try:
            value = kind(raw)
        except ValueError:
            raise ValueError(f"«{name}» должно быть числом (получено: {raw!r})")
        if minv is not None and value < minv:
            raise ValueError(f"«{name}»: минимум {minv}")
        if maxv is not None and value > maxv:
            raise ValueError(f"«{name}»: максимум {maxv}")
        return value

    def _save(self) -> None:
        if self._capturing is not None:
            self._finish_capture(keep=True)  # завершить захват, снять паузу хоткея
        engine_name = self._engine_by_label.get(
            self.engine_var.get(), self.engine_var.get().strip()
        )
        model_name = self._model_from_label(self.model_var.get())
        mgr = self.controller.downloads
        if engine_name == "faster-whisper" and mgr is not None:
            st = mgr.state(model_name)
            if st and st["state"] == "downloading":
                p = st.get("progress")
                pct = f"{p:.0f}% " if p is not None else ""
                messagebox.showwarning(
                    "Загрузка модели",
                    f"«{model_name}» ещё загружается ({pct}— дождитесь "
                    "завершения, затем сохраните настройки снова.",
                    parent=self,
                )
                return
        try:
            updates = dict(
                engine=engine_name,
                model_size=model_name or "small",
                language=self._rev(LANGS, self.lang_var.get(), ""),
                device=self._rev(DEVICES, self.device_var.get(), "auto"),
                compute_type=self._rev(COMPUTES, self.compute_var.get(), "auto"),
                beam_size=int(self._rev(BEAMS, self.beam_var.get(), "5")),
                vad_filter=bool(self.vad_var.get()),
                initial_prompt=self.prompt_var.get().strip(),
                hotkey=self.hotkey_var.get().strip() or "ctrl+alt",
                hotkey_mode=self._rev(MODES, self.mode_var.get(), "push_to_talk"),
                insert_method=self._rev(INSERTS, self.insert_var.get(), "clipboard"),
                restore_clipboard=bool(self.restore_var.get()),
                clipboard_restore_delay=self._num(self.clipdelay_var.get,
                                                  "Пауза восстановления", float, 0.0),
                focus_settle_delay=self._num(self.focusdelay_var.get,
                                             "Пауза активации", float, 0.0),
                microphone=(self._mic_device_value() or ""),
                sample_rate=self._num(self.rate_var.get, "Частота", int, 8000, 192000),
                max_duration=self._num(self.maxdur_var.get, "Лимит записи", float, 0.0),
                silence_timeout=self._num(self.silence_var.get,
                                          "Авто-стоп тишины", float, 0.0),
                silence_rms_threshold=self._num(self.threshold_var.get,
                                                "Порог тишины", float, 0.0, 1.0),
                api_key=self.apikey_var.get().strip(),
                api_base_url=self.apibase_var.get().strip(),
                api_model=self.apimodel_var.get().strip() or "whisper-1",
                sound_enabled=bool(self.sound_var.get()),
                start_in_tray=bool(self.tray_var.get()),
                history_enabled=bool(self.history_var.get()),
                save_audio=bool(self.save_audio_var.get()),
                history_dir=self.historydir_var.get().strip(),
                proxy_enabled=bool(self.proxy_enabled_var.get()),
                proxy_url=self.proxy_url_var.get().strip(),
                always_on_top=bool(self.topmost_var.get()),
            )
        except ValueError as exc:
            messagebox.showwarning("Настройки", str(exc), parent=self)
            return

        self.controller.apply_settings(replace(self._config, **updates))
        self.destroy()

    @staticmethod
    def _rev(pairs, label: str, default):
        return next((val for lbl, val in pairs if lbl == label), default)
