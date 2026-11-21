"""Conformance-тест: весь функционал бэкенда доступен БЕЗ tkinter.

Доказательство заменяемости UI:
1) субпроцессный тест — свежий интерпретатор импортирует бэкенд
   (контроллер, движки, историю, загрузки) и проверяет, что tkinter
   и dictation.ui не появились;
2) полный цикл диктовки и весь публичный API прогоняются на контроллере
   с заглушенным UI — любой фронтенд может повторить ровно эти вызовы.
"""
import shutil
import subprocess
import sys
import time
import unittest
import uuid
from dataclasses import replace
from pathlib import Path

from dictation.config import Config

_ROOT = Path(__file__).resolve().parent.parent


class _StubResult:
    text = "тест диктовки"
    language = "ru"
    language_probability = 0.99


class _StubEngine:
    def transcribe(self, audio, sample_rate=16000):
        return _StubResult()

    def describe(self):
        return "stub"

    def close(self):
        pass


class _StubInserter:
    def __init__(self):
        self.inserted = []

    def insert(self, text, target):
        self.inserted.append(text)


class _StubRecorder:
    requested_rate = 16000
    level = 0.0

    def start(self):
        pass

    def elapsed(self) -> float:
        return 0.0

    def stop(self):
        import numpy as np
        audio = (np.sin(np.linspace(0, 90, 1600)) * 0.3).astype(np.float32)
        return audio, 16000


class BackendImportIsolationTests(unittest.TestCase):
    """Импорт бэкенда не тянет tkinter/dictation.ui — проверка в субпроцессе."""

    def test_backend_import_does_not_pull_ui(self):
        code = (
            "import sys\n"
            "import dictation.app, dictation.downloads, dictation.history, "
            "dictation.diagnostics\n"
            "assert 'tkinter' not in sys.modules, 'tkinter imported'\n"
            "assert 'dictation.ui' not in sys.modules, 'dictation.ui imported'\n"
            "print('ok')\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(_ROOT),
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class HeadlessConformanceTests(unittest.TestCase):
    """Полный цикл диктовки и весь публичный API на контроллере без UI."""

    def setUp(self):
        from dictation.app import DictationController

        base = _ROOT / ".tmp-tests" / ("vd-" + uuid.uuid4().hex[:8])
        base.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, base, True)

        cfg = Config(history_dir=str(base / "Vodict"), sound_enabled=False,
                     start_in_tray=False)
        self.c = DictationController(cfg)
        self.addCleanup(self.c.shutdown)
        self.c.hotkey.stop()  # глобальный хук в тестах не нужен
        self.c._recognize = lambda audio, rate: _StubResult()
        self.stub = _StubInserter()
        self.c.inserter = self.stub
        self.c.recorder = _StubRecorder()
        self._drain()

    def _drain(self):
        events = []
        while True:
            try:
                events.append(self.c.events.get_nowait())
            except Exception:
                break
        return events

    def wait_idle(self, timeout=5.0):
        t0 = time.monotonic()
        while self.c.state != "idle" and time.monotonic() - t0 < timeout:
            time.sleep(0.02)

    def test_public_api_surface(self):
        from dictation.app import DictationController as C

        for attr in ("on_button", "start_recording", "stop_recording",
                     "apply_settings", "request_model_download",
                     "delete_model", "downloaded_models", "model_size_bytes",
                     "download_states", "cached_models", "pause_hotkey",
                     "recording_level", "start_services", "shutdown",
                     "events", "state", "config", "history"):
            self.assertTrue(hasattr(C, attr) or hasattr(self.c, attr), attr)

    def test_dictation_flow_without_tkinter(self):
        self.c.start_recording()
        self.assertEqual(self.c.state, "recording")
        time.sleep(0.15)
        self.c.stop_recording()
        t0 = time.monotonic()
        while self.c.state != "idle" and time.monotonic() - t0 < 5:
            time.sleep(0.02)
        self.assertEqual(self.c.state, "idle")
        self.assertEqual(self.stub.inserted, [_StubResult.text])
        kinds = [e[0] for e in self._drain()]
        self.assertIn("result", kinds)
        self.assertIn("state", kinds)

    def test_settings_apply_in_memory(self):
        # НЕ триггерим пересоздание движка (иначе тест потянет модель)
        new_cfg = replace(self.c.config, hotkey="ctrl+shift+f9",
                          history_enabled=False)
        self.c.apply_settings(new_cfg, save=False)
        self.assertEqual(self.c.config.hotkey, "ctrl+shift+f9")
        self.assertEqual(self.c.config.history_enabled, False)
        self.c.apply_settings(replace(self.c.config), save=False)

    def test_model_facade(self):
        self.c.downloaded_models()
        self.assertIsNone(self.c.model_size_bytes("этой-модели-нет"))
        self.c.download_states()
        self.c.cached_models()

    def test_pause_facade(self):
        self.c.pause_hotkey(True)
        self.assertTrue(self.c.hotkey._paused)
        self.c.pause_hotkey(False)
        self.assertFalse(self.c.hotkey._paused)

    def test_press_during_transcription_starts_new_recording(self):
        """Хоткей во время транскрибации: новая запись сразу, старая
        транскрибация дозаменяется в фоне и обе вставляются."""
        self.c.start_recording()
        self.c.stop_recording()
        # предыдущий фрагмент ещё «транскрибируется» — начинаем новую запись
        self.c.start_recording()
        self.assertEqual(self.c.state, "recording")
        self.c.stop_recording()
        self.wait_idle()
        self.assertEqual(self.c.state, "idle")
        self.assertEqual(len(self.stub.inserted), 2)

    def test_shutdown_is_clean(self):
        self.c.shutdown()
        self.assertTrue(self.c._closed)


if __name__ == "__main__":
    unittest.main()
