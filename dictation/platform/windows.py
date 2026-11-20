"""Windows: активное окно через WinAPI (ctypes, без внешних зависимостей)."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

from pynput import keyboard

from .base import FocusTarget, PlatformSupport

SW_RESTORE = 9
VK_MENU = 0x12        # ALT
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
INPUT_KEYBOARD = 1
MAPVK_VK_TO_VSC = 0
VK_CONTROL = 0x11
VK_V = 0x56
SC_CONTROL = 0x1D     # scan-код левого Ctrl
SC_V = 0x2F           # scan-код V


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG),
        ("dy", wt.LONG),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wt.DWORD),
        ("wParamL", wt.WORD),
        ("wParamH", wt.WORD),
    ]


class _INPUT(ctypes.Structure):
    # В union обязательно должен быть MOUSEINPUT — он самый большой и
    # определяет размер структуры; несовпадение размера -> SendInput отказ.
    class _U(ctypes.Union):
        _fields_ = [
            ("mi", _MOUSEINPUT),
            ("ki", _KEYBDINPUT),
            ("hi", _HARDWAREINPUT),
        ]

    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


class WindowsSupport(PlatformSupport):
    name = "windows"

    def __init__(self) -> None:
        self._user32 = ctypes.windll.user32

    # ------------------------------------------------------------------
    def _window_title(self, hwnd: int) -> str:
        length = self._user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    def _pid(self, hwnd: int) -> int:
        pid = wt.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    # ------------------------------------------------------------------
    def foreground(self) -> FocusTarget | None:
        hwnd = self._user32.GetForegroundWindow()
        if not hwnd:
            return None
        return FocusTarget(handle=hwnd, title=self._window_title(hwnd))

    def is_own(self, target: FocusTarget) -> bool:
        try:
            return self._pid(target.handle) == os.getpid()
        except Exception:
            return False

    def restore_focus(self, target: FocusTarget) -> bool:
        hwnd = target.handle
        if not self._user32.IsWindow(hwnd):
            return False
        if self._user32.IsIconic(hwnd):  # окно свёрнуто — разворачиваем
            self._user32.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(0.05)
        if self._user32.SetForegroundWindow(hwnd):
            return True
        # Если система отказала — трюк с коротким нажатием ALT:
        # он разрешает стороннему процессу перехватить фокус ввода.
        self._user32.keybd_event(VK_MENU, 0, 0, 0)
        self._user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.05)
        return bool(self._user32.SetForegroundWindow(hwnd))

    def paste_modifier(self) -> keyboard.Key:
        return keyboard.Key.ctrl

    def paste_key(self) -> keyboard.KeyCode:
        return keyboard.KeyCode.from_vk(0x56)  # VK_V

    # ------------------------------------------------------------------
    # Прямой SendInput со scan-кодами: pynput шлёт клавиши без wScan,
    # и часть приложений (например, tkinter) не может разрешить клавишу —
    # вставка не срабатывает. С scan-кодом инжекция неотличима от физической.

    def _send_key_scan(self, scan: int, up: bool = False) -> None:
        """Scancode-only событие: именно так делают pyautogui/AutoHotkey —
        tk и другие фреймворки разрешают клавишу по scan-коду."""
        inp = _INPUT()
        inp.type = INPUT_KEYBOARD
        inp.ki.wScan = scan
        inp.ki.dwFlags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
        sent = self._user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        if sent != 1:
            print(f"[windows] SendInput отклонён (sent={sent})", file=sys.stderr)

    def send_paste(self) -> None:
        time.sleep(0.03)
        self._send_key_scan(SC_CONTROL)
        time.sleep(0.04)
        self._send_key_scan(SC_V)
        time.sleep(0.04)
        self._send_key_scan(SC_V, up=True)
        time.sleep(0.04)
        self._send_key_scan(SC_CONTROL, up=True)
        time.sleep(0.1)
