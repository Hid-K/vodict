"""Модульные тесты: конфиг и разбор горячих клавиш. Запуск:
    python -m unittest discover -s tests -v
"""
import json
import tempfile
import unittest
from contextlib import contextmanager
from importlib.util import find_spec
from pathlib import Path

from dictation.config import Config

_TMP_BASE = Path(__file__).resolve().parent.parent / ".tmp-tests"


def _fs_writable() -> bool:
    """Проба записи тем же способом, каким пишет тест (временный подкаталог +
    файл внутри): в песочницах может быть запрещён любой такой запись-паттерн —
    тогда файловые тесты пропускаются (на реальной машине работают)."""
    try:
        _TMP_BASE.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=_TMP_BASE) as tmp:
            (Path(tmp) / ".probe").write_text("x", encoding="utf-8")
        return True
    except OSError:
        return False


_FS_OK = _fs_writable()


@contextmanager
def tmp_dir():
    """Временный каталог внутри проекта: в некоторых песочницах системная
    %TEMP% недоступна на запись для дочерних процессов."""
    base = _TMP_BASE
    base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as tmp:
        yield tmp


_SKIP_FS = unittest.skipUnless(_FS_OK, "нет прав на запись временных файлов (песочница)")


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = Config()
        self.assertEqual(cfg.engine, "faster-whisper")
        self.assertEqual(cfg.hotkey, "ctrl+alt")
        self.assertEqual(cfg.insert_method, "clipboard")

    @_SKIP_FS
    def test_roundtrip(self):
        with tmp_dir() as tmp:
            path = Path(tmp) / "config.json"
            cfg = Config(model_size="medium", language="ru", silence_timeout=2.5)
            cfg.save(path)
            loaded = Config.load(path)
            self.assertEqual(loaded.model_size, "medium")
            self.assertEqual(loaded.language, "ru")
            self.assertEqual(loaded.silence_timeout, 2.5)

    @_SKIP_FS
    def test_unknown_keys_ignored(self):
        with tmp_dir() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"engine": "openai-api", "wat": 1}),
                            encoding="utf-8")
            cfg = Config.load(path)
            self.assertEqual(cfg.engine, "openai-api")

    def test_engine_signature_detects_change(self):
        a = Config(model_size="small").engine_signature()
        b = Config(model_size="medium").engine_signature()
        self.assertNotEqual(a, b)

    def test_label_values_from_old_gui_are_sanitized(self):
        # Регрессия: старый диалог настроек сохранял подписи вместо значений,
        # из-за чего PTT работал как toggle.
        from dictation.config import _sanitize_enum

        cfg = Config(hotkey_mode="PTT — удерживать и говорить",
                     insert_method="Буфер обмена (Ctrl+V / ⌘V)")
        _sanitize_enum(cfg)
        self.assertEqual(cfg.hotkey_mode, "push_to_talk")
        self.assertEqual(cfg.insert_method, "clipboard")

    def test_label_values_switch_variant_sanitized(self):
        from dictation.config import _sanitize_enum

        cfg = Config(hotkey_mode="Switch — нажал: старт, ещё раз: стоп",
                     insert_method="Эмуляция набора клавиш")
        _sanitize_enum(cfg)
        self.assertEqual(cfg.hotkey_mode, "toggle")
        self.assertEqual(cfg.insert_method, "typing")

    def test_ui_constant_pairs_map_to_canonical_values(self):
        # Все пары в диалоге настроек обязаны быть (подпись, значение).
        from dictation.ui import window as ui

        mode_values = {val for _, val in ui.MODES}
        self.assertEqual(mode_values, {"push_to_talk", "toggle"})
        insert_values = {val for _, val in ui.INSERTS}
        self.assertEqual(insert_values, {"clipboard", "typing"})
        lang_values = {val for _, val in ui.LANGS}
        self.assertIn("ru", lang_values)
        device_values = {val for _, val in ui.DEVICES}
        self.assertEqual(device_values, {"auto", "cpu", "cuda"})


@_SKIP_FS
class HistoryStoreTests(unittest.TestCase):
    def test_roundtrip_and_index(self):
        import numpy as np

        from dictation.history import HistoryStore

        with tmp_dir() as tmp:
            store = HistoryStore(Path(tmp) / "Vodict")
            audio = (np.sin(np.linspace(0, 100, 1600)).astype(np.float32) * 0.1)
            meta = store.begin_entry(audio, 16000, save_audio=True)
            self.assertTrue((Path(tmp) / "Vodict" / meta["audio_file"]).exists())
            meta.update({
                "time": "2026-01-01 12:00:00",
                "microphone": "default",
                "engine": "faster-whisper",
                "model": "small",
                "language_requested": "ru",
                "status": "ok",
                "text": "привет мир",
            })
            store.write_entry(meta)
            store.write_entry({**meta, "id": meta["id"] + "_2",
                               "audio_file": None, "status": "empty", "text": ""})

            rows = store.read_index(10)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["id"], meta["id"] + "_2")  # свежие сверху
            ok_row = rows[1]
            self.assertEqual(ok_row["status"], "ok")
            self.assertEqual(ok_row["chars"], len("привет мир"))
            self.assertEqual(ok_row["preview"], "привет мир")
            self.assertTrue(ok_row["has_audio"])
            self.assertTrue(ok_row["model"].startswith("faster-whisper:"))
            # полный JSON записи тоже на месте
            entry_json = (Path(tmp) / "Vodict" / "entries"
                          / f"{meta['id']}.json")
            self.assertTrue(entry_json.exists())

    def test_read_index_empty(self):
        from dictation.history import HistoryStore

        with tmp_dir() as tmp:
            store = HistoryStore(Path(tmp))
            self.assertEqual(store.read_index(10), [])


@unittest.skipIf(find_spec("pynput") is None, "pynput не установлен")
class HotkeyTests(unittest.TestCase):
    def test_parse_combo(self):
        from dictation.hotkeys import parse_combo

        self.assertEqual(parse_combo("Ctrl + Alt"), frozenset({"ctrl", "alt"}))
        self.assertEqual(parse_combo("win+space"), frozenset({"cmd", "space"}))
        self.assertEqual(parse_combo("control+option+F5"), frozenset({"ctrl", "alt", "f5"}))

    def test_parse_rejects_empty(self):
        from dictation.hotkeys import parse_combo

        with self.assertRaises(ValueError):
            parse_combo("   ")

    def test_key_token(self):
        from pynput import keyboard

        from dictation.hotkeys import key_token

        self.assertEqual(key_token(keyboard.Key.ctrl), "ctrl")
        self.assertEqual(key_token(keyboard.Key.cmd), "cmd")
        self.assertEqual(key_token(keyboard.Key.alt_l), "alt")
        self.assertEqual(key_token(keyboard.KeyCode.from_char("A")), "a")

    def test_registry_lists_builtin_engines(self):
        from dictation.engines.registry import available_engines

        names = set(available_engines())
        self.assertLessEqual({"faster-whisper", "openai-whisper", "openai-api"}, names)


if __name__ == "__main__":
    unittest.main()
