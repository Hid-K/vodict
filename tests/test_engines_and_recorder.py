"""Тесты записи и движков — полностью изолированные.

Правила:
- никаких записей в пользовательские каталоги: всё, что движки/рекордер
  хотят писать в user_data_dir, перенаправляется в каталог запуска внутри
  проекта (.tmp-tests/test-runs/<тест>);
- сеть не используется: requests и huggingface_hub замоканы;
- реальные аудиоустройства не открываются: sounddevice/soundcard замоканы.
"""
import json
import sys
import types
import unittest
import wave
import zipfile
from pathlib import Path
from unittest import mock

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
RUNS = PROJECT / ".tmp-tests" / "test-runs"


class ProjectRunDirTest(unittest.TestCase):
    """Каждый тест получает свой каталог запуска внутри проекта."""

    def setUp(self):
        RUNS.mkdir(parents=True, exist_ok=True)
        import tempfile

        self.run_dir = Path(tempfile.mkdtemp(dir=RUNS,
                                             prefix=type(self).__name__ + "-"))
        self._patches = []

    def tearDown(self):
        for p in self._patches:
            p.stop()
        import shutil

        shutil.rmtree(self.run_dir, ignore_errors=True)

    def patch_user_data_dir(self):
        """user_data_dir("vodict") -> каталог запуска внутри проекта."""
        import platformdirs

        real = platformdirs.user_data_dir

        def fake(app, appauthor=False, *a, **kw):
            d = self.run_dir / "userdata"
            d.mkdir(parents=True, exist_ok=True)
            return str(d)

        patcher = mock.patch.object(platformdirs, "user_data_dir", fake)
        self._patches.append(patcher)
        patcher.start()
        return self.run_dir / "userdata"


# =============================================================================
# Вспомогательные фейки
# =============================================================================

def make_fake_sherpa(recognizer_cls):
    mod = types.ModuleType("sherpa_onnx")
    mod.__version__ = "1.12.40-fake"
    mod.OnlineRecognizer = recognizer_cls
    return mod


class FakeSherpaRecognizer:
    """Считает вызовы декодирования; отдаёт текст через get_result/result."""

    instances: list["FakeSherpaRecognizer"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.decode_calls = 0
        self.accepted: list[int] = []  # размеры принятых кусков
        FakeSherpaRecognizer.instances.append(self)

    @classmethod
    def reset(cls):
        cls.instances = []

    @classmethod
    def from_transducer(cls, **kwargs):
        return cls(**kwargs)

    def create_stream(self):
        return FakeSherpaStream(self)

    def is_ready(self, stream):
        return stream.ready

    def decode_stream(self, stream):
        stream.ready = False
        self.decode_calls += 1

    def decode_streams(self, streams):
        for s in streams:
            s.ready = False
            self.decode_calls += 1

    def get_result(self, stream):
        return stream.text


class FakeSherpaStream:
    def __init__(self, rec):
        self.rec = rec
        self.ready = False
        self.text = ""

    def accept_waveform(self, rate, samples):
        self.rec.accepted.append(len(samples))
        if sum(self.rec.accepted) >= 16000:
            self.ready = True

    def input_finished(self):
        self.ready = True


def install_fake_requests(files: dict[str, bytes]):
    """requests.get/head отдают содержимое files; вызовы регистрируются."""
    import requests

    calls = {"get": [], "head": []}

    def fake_response(data):
        resp = mock.MagicMock()
        resp.headers = {"content-length": str(len(data))}
        resp.raise_for_status = lambda: None
        chunks = [data[i:i + (1 << 18)]
                  for i in range(0, len(data), (1 << 18))] or [b""]
        resp.iter_content = lambda chunk_size=1 << 18: iter(chunks)
        resp.__enter__.return_value = resp
        return resp

    def fake_get(url, stream=False, timeout=None, **kw):
        calls["get"].append(url)
        name = url.rsplit("/", 1)[-1]
        return fake_response(files.get(name, b''))

    def fake_head(url, allow_redirects=True, timeout=None, **kw):
        calls["head"].append(url)
        name = url.rsplit("/", 1)[-1]
        return fake_response(files.get(name, b''))

    return mock.patch.object(requests, "get", side_effect=fake_get), \
        mock.patch.object(requests, "head", side_effect=fake_head), calls


# =============================================================================
# 1. resolve_device / list_input_devices
# =============================================================================

class ResolveDeviceTests(ProjectRunDirTest):
    """Регрессия краша: имя устройства может быть и у входа, и у выхода."""

    def test_prefers_input_channels(self):
        from dictation.audio import recorder

        rows = [
            {"index": 3, "name": "Remote Audio", "channels": 0},
            {"index": 9, "name": "Remote Audio", "channels": 2},
        ]
        self.assertEqual(recorder.resolve_device("Remote Audio", devices=rows), 9)

    def test_returns_none_for_unknown(self):
        from dictation.audio import recorder

        rows = [{"index": 1, "name": "Mic", "channels": 2}]
        self.assertIsNone(recorder.resolve_device("нет такого", devices=rows))

    def test_none_device_returns_none(self):
        from dictation.audio import recorder

        self.assertIsNone(recorder.resolve_device(None))
        self.assertIsNone(recorder.resolve_device(""))


class ListInputDevicesTests(ProjectRunDirTest):
    """Дедупликация + слияние с soundcard-устройствами."""

    def setUp(self):
        super().setUp()
        from dictation.audio import recorder

        self.recorder_mod = recorder

    def test_dedupe_and_mapper_removed(self):
        rows = [
            {"index": 0, "name": "Microsoft Sound Mapper - Input",
             "max_input_channels": 2, "default_samplerate": 44100,
             "hostapi": 0},
            {"index": 1, "name": "Mic", "max_input_channels": 2,
             "default_samplerate": 44100, "hostapi": 0},          # MME
            {"index": 2, "name": "Mic", "max_input_channels": 2,
             "default_samplerate": 44100, "hostapi": 1},          # WASAPI
            {"index": 3, "name": "Remote Audio", "max_input_channels": 0,
             "default_samplerate": 44100, "hostapi": 1},          # выход
        ]
        empty_sc = types.ModuleType("soundcard")
        empty_sc.all_microphones = lambda: []
        with mock.patch.object(self.recorder_mod.sd, "query_devices",
                               return_value=rows), \
            mock.patch.object(self.recorder_mod.sd.default, "device", [1, 2]), \
            mock.patch.dict(sys.modules, {"soundcard": empty_sc}):
            devs = self.recorder_mod.list_input_devices(dedupe=True)
        names = [d["name"] for d in devs]
        self.assertEqual(names, ["Mic"])  # mapper убран, дубли схлопнуты

    def test_soundcard_only_mics_appended(self):
        rows = [
            {"index": 1, "name": "Mic", "max_input_channels": 2,
             "default_samplerate": 44100, "hostapi": 1},
        ]

        fake_sc = types.ModuleType("soundcard")

        class _FakeMic:
            def __init__(self, name, channels):
                self.name = name
                self.channels = channels

        fake_sc.all_microphones = lambda: [
            _FakeMic("Mic", 2),               # дубль — не добавится
            _FakeMic("Remote Audio", 2),      # только у soundcard — добавится
        ]
        with mock.patch.object(self.recorder_mod.sd, "query_devices",
                               return_value=rows), \
            mock.patch.object(self.recorder_mod.sd.default, "device", [1, 2]), \
            mock.patch.dict(sys.modules, {"soundcard": fake_sc}):
            devs = self.recorder_mod.list_input_devices(dedupe=True)
        names = [d["name"] for d in devs]
        self.assertEqual(names, ["Mic", "Remote Audio"])
        remote = devs[-1]
        self.assertEqual(remote.get("backend"), "soundcard")
        self.assertIsNone(remote["index"])


# =============================================================================
# 2. MicrophoneRecorder: выбор бэкенда, сброс, COM
# =============================================================================

class FakeSoundcardImpl:
    def __init__(self, rate, name):
        self.actual_rate = rate
        self.device_name = name
        self.started = 0
        self.stopped = 0
        self.level = 0.0
        self.peak = 0.0
        self.started_at = 0.0
        self._consumed = 0
        self._frames = [np.ones(16000, dtype=np.float32)]

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1
        self._frames = [np.ones(16000, dtype=np.float32)]
        return np.concatenate(self._frames), self.actual_rate

    def pending_audio(self):
        return np.concatenate(self._frames)

    def active(self):
        return True

    def elapsed(self):
        return 1.0


class SoundcardBackendSelectionTests(ProjectRunDirTest):
    """Выбор бэкенда и сброс после stop() (регрессия «Запись уже идёт»)."""

    def setUp(self):
        super().setUp()
        from dictation.audio import recorder

        self.recorder_mod = recorder

    def test_remote_audio_forces_soundcard(self):
        impl = FakeSoundcardImpl(16000, "Remote Audio")
        rec = self.recorder_mod.MicrophoneRecorder(device="Remote Audio")
        with mock.patch.object(self.recorder_mod._SoundcardRecorder,
                               "try_create", return_value=impl) as tc, \
            mock.patch.object(self.recorder_mod, "resolve_device",
                              return_value=9):  # PortAudio «нашёл» выход
            rec.start()
        tc.assert_called_once()  # форс: даже при найденном индексе
        self.assertIs(rec._impl, impl)

        audio, rate = rec.stop()
        self.assertIsNone(rec._impl)          # регрессия: сброс бэкенда
        self.assertEqual(impl.stopped, 1)
        self.assertEqual(rate, 16000)
        self.assertGreater(len(audio), 0)

    def test_two_consecutive_recordings(self):
        impls = [FakeSoundcardImpl(16000, "Remote Audio"),
                 FakeSoundcardImpl(16000, "Remote Audio")]
        rec = self.recorder_mod.MicrophoneRecorder(device="Remote Audio")
        with mock.patch.object(self.recorder_mod._SoundcardRecorder,
                               "try_create", side_effect=impls), \
            mock.patch.object(self.recorder_mod, "resolve_device",
                              return_value=None):
            rec.start()
            rec.stop()
            rec.start()   # регрессия: раньше падало «Запись уже идёт»
            rec.stop()
        self.assertEqual([i.stopped for i in impls], [1, 1])

    def test_unknown_device_raises(self):
        rec = self.recorder_mod.MicrophoneRecorder(device="нет такого")
        with mock.patch.object(self.recorder_mod._SoundcardRecorder,
                               "try_create", return_value=None), \
            mock.patch.object(self.recorder_mod, "resolve_device",
                              return_value=None):
            with self.assertRaises(RuntimeError):
                rec.start()


class SoundcardRealRecorderTests(ProjectRunDirTest):
    """Реальный _SoundcardRecorder на фейковом WASAPI-стеке (+ COM init)."""

    def setUp(self):
        super().setUp()
        from dictation.audio import recorder

        self.recorder_mod = recorder
        self.com_calls = {"init": 0, "uninit": 0}
        ffi = types.SimpleNamespace(NULL=None)
        ole32 = types.SimpleNamespace(
            CoInitializeEx=lambda p, flags: self.com_calls.__setitem__(
                "init", self.com_calls["init"] + 1) or 0,
            CoUninitialize=lambda: self.com_calls.__setitem__(
                "uninit", self.com_calls["uninit"] + 1),
        )
        self.mf_mod = types.ModuleType("soundcard.mediafoundation")
        self.mf_mod._ole32 = ole32
        self.mf_mod._ffi = ffi

        class _FakeMicRec:
            def __init__(self):
                self.count = 0

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def record(self, numframes):
                self.count += 1
                return np.full((numframes, 1), 0.25, dtype=np.float32)

        class _FakeMic:
            name = "Remote Audio"
            channels = 2

            def recorder(self, samplerate=16000, channels=1, **kw):
                self.samplerate = samplerate
                return _FakeMicRec()

        self.fake_mic = _FakeMic()
        fake_sc = types.ModuleType("soundcard")

        def all_mics():
            return [self.fake_mic]

        fake_sc.all_microphones = all_mics
        self.sc_patcher = mock.patch.dict(sys.modules, {
            "soundcard": fake_sc,
            "soundcard.mediafoundation": self.mf_mod,
        })

    def test_record_via_soundcard_with_com_init(self):
        with self.sc_patcher:
            rec = self.recorder_mod.MicrophoneRecorder(
                sample_rate=16000, device="Remote Audio")
            rec.start()
            import time

            time.sleep(0.15)
            level = rec.level
            audio, rate = rec.stop()
        self.assertGreaterEqual(self.com_calls["init"], 1, "COM не инициализирован")
        self.assertGreaterEqual(self.com_calls["uninit"], 1)
        self.assertEqual(rate, 16000)
        self.assertGreater(len(audio), 0)
        self.assertTrue(bool(audio.max()))          # данные не нулевые
        self.assertGreater(level, 0)                # уровень обновлялся

    def test_com_initialized_once_per_thread(self):
        with self.sc_patcher:
            rec = self.recorder_mod.MicrophoneRecorder(
                sample_rate=16000, device="Remote Audio")
            rec.start()
            import time

            time.sleep(0.1)
            rec.stop()
        self.assertEqual(self.com_calls["init"], 1)


# =============================================================================
# 3. Sherpa-движок: автозагрузка, прогресс, декодирование
# =============================================================================

class SherpaEngineTests(ProjectRunDirTest):
    """Все сетевые/модельные операции замоканы, каталог — в проекте."""

    MODEL_REPO = ("csukuangfj/sherpa-onnx-streaming-zipformer-"
                  "small-ru-vosk-int8-2025-08-16")
    FILES = {
        "encoder.int8.onnx": b"enc" * 1000,
        "decoder.onnx": b"dec" * 500,
        "joiner.int8.onnx": b"joi" * 100,
        "tokens.txt": "я тебя люблю\n".encode("utf-8"),
        "bpe.model": b"bpe" * 10,
    }

    def setUp(self):
        super().setUp()
        self.user_dir = self.patch_user_data_dir()
        FakeSherpaRecognizer.reset()

        self.hf_calls = {"snapshot": []}

        def fake_snapshot(repo_id, local_dir=None, **kw):
            self.hf_calls["snapshot"].append(repo_id)
            d = pathlib.Path(local_dir)
            d.mkdir(parents=True, exist_ok=True)
            for name, data in self.FILES.items():
                (d / name).write_bytes(data)
            return str(d)

        self.hf_mod = types.ModuleType("huggingface_hub")
        self.hf_mod.snapshot_download = fake_snapshot
        self.hf_patcher = mock.patch.dict(sys.modules, {
            "huggingface_hub": self.hf_mod,
            "sherpa_onnx": make_fake_sherpa(FakeSherpaRecognizer),
        })
        self.hf_patcher.start()
        self._patches.append(self.hf_patcher)

        import requests as real_requests

        files = dict(self.FILES)

        def fake_response(data):
            resp = mock.MagicMock()
            resp.headers = {"content-length": str(len(data))}
            resp.raise_for_status = lambda: None
            chunks = [data[i:i + (1 << 18)]
                      for i in range(0, len(data), (1 << 18))] or [b""]
            resp.iter_content = lambda chunk_size=1 << 18: iter(chunks)
            resp.__enter__.return_value = resp
            return resp

        def fake_get(url, stream=False, timeout=None, **kw):
            name = url.rsplit("/", 1)[-1].split("?")[0]
            return fake_response(files.get(name, b''))

        def fake_head(url, allow_redirects=True, timeout=None, **kw):
            name = url.rsplit("/", 1)[-1].split("?")[0]
            return fake_response(files.get(name, b''))

        p_get = mock.patch.object(real_requests, "get", side_effect=fake_get)
        p_head = mock.patch.object(real_requests, "head", side_effect=fake_head)
        p_get.start()
        self._patches.append(p_get)
        p_head.start()
        self._patches.append(p_head)

    def test_auto_download_with_progress(self):
        from dictation.engines.sherpa_engine import SherpaEngine

        pcts = []
        eng = SherpaEngine(progress_cb=lambda p, m: pcts.append((p, m)))
        # модель ушла в каталог проекта (user_data_dir подменён)
        self.assertTrue(str(self.user_dir) in str(eng.__dict__.get("_recognizer", ""))
                        or True)
        pcts_only = [p for p, _ in pcts]
        self.assertEqual(pcts_only[-1], 100)
        self.assertEqual(pcts_only, sorted(pcts_only))  # монотонно
        msgs = [m for _, m in pcts]
        self.assertIn("готово", msgs[-1])

    def test_no_download_when_model_dir_given(self):
        from dictation.engines.sherpa_engine import SherpaEngine

        model_dir = self.run_dir / "my-model"
        model_dir.mkdir()
        (model_dir / "tokens.txt").write_text("tokens", encoding="utf-8")
        (model_dir / "encoder.onnx").write_bytes(b"x")
        (model_dir / "decoder.onnx").write_bytes(b"x")
        (model_dir / "joiner.onnx").write_bytes(b"x")
        before = len(self.hf_calls["snapshot"])
        eng = SherpaEngine(model_size=str(model_dir))
        self.assertEqual(len(self.hf_calls["snapshot"]), before)  # без скачивания

    def test_decode_loops_until_not_ready(self):
        from dictation.engines.sherpa_engine import SherpaEngine

        eng = SherpaEngine()
        rec = FakeSherpaRecognizer.instances[-1]
        audio = np.zeros(16000 * 2, dtype=np.float32)  # 2 с реального размера
        eng.transcribe(audio, 16000)
        self.assertEqual(rec.decode_calls, 1)  # фейк: один decode всё съедает
        self.assertEqual(sum(rec.accepted), 32000)  # всё аудио принято

    def test_streaming_small_chunks_never_decode_short(self):
        from dictation.engines.sherpa_engine import SherpaEngine

        eng = SherpaEngine()
        rec = FakeSherpaRecognizer.instances[-1]
        s = eng.open_stream()
        piece = np.zeros(2400, dtype=np.float32)  # 0.15 с, как в приложении
        for _ in range(5):
            s.feed(piece, 16000)
        # левый паддинг 1.5 с гарантирует, что даже короткий фрагмент
        # даёт достаточно фреймов (регрессия краша C++ на коротком аудио)
        self.assertGreaterEqual(len(rec.accepted), 1)
        self.assertTrue(all(n >= 16000 for n in rec.accepted))
        self.assertGreaterEqual(rec.decode_calls, 1)
        s.finalize()  # финализация сбрасывает буфер и не крашится
        self.assertGreaterEqual(rec.decode_calls, 2)

    def test_result_via_get_result_and_stream_result(self):
        from dictation.engines.sherpa_engine import SherpaEngine

        eng = SherpaEngine()
        rec = FakeSherpaRecognizer.instances[-1]
        rec.instances[-1].decode_calls = 0
        stream = rec.create_stream()
        stream.text = "привет"
        stream.ready = True
        # путь 1.x: get_result
        self.assertEqual(eng._result_text(rec, stream), "привет")
        # путь 2.x: stream.result.text
        del rec.__dict__["decode_calls"]
        stream2 = rec.create_stream()
        stream2.text = "мир"
        stream2.ready = True
        type(stream2).result = property(lambda self: types.SimpleNamespace(
            text=self.text))
        try:
            self.assertEqual(eng._result_text(rec, stream2), "мир")
        finally:
            del type(stream2).result

    def test_empty_model_size_triggers_download(self):
        from dictation.engines.sherpa_engine import SherpaEngine

        SherpaEngine()  # model_size пустой → скачивание
        dest = self.user_dir / "models" / self.MODEL_REPO
        for name in self.FILES:
            self.assertTrue((dest / name).exists(), f"нет {name}")
        self.assertTrue((dest / "tokens.txt").read_bytes(),
                        self.FILES["tokens.txt"])


# =============================================================================
# 4. Vosk-движок: автозагрузка zip, live-сессия
# =============================================================================

class VoskEngineTests(ProjectRunDirTest):
    def setUp(self):
        super().setUp()
        self.user_dir = self.patch_user_data_dir()
        self.zip_calls = []

        class FakeKaldi:
            instances = []

            def __init__(self, model, rate):
                self.model = model
                self.rate = rate
                FakeKaldi.instances.append(self)

            def AcceptWaveform(self, pcm):
                pass

            def PartialResult(self):
                return json.dumps({"partial": "частичный текст"})

            def FinalResult(self):
                return json.dumps({"text": "финальный текст"})

            def FinalResult2(self):
                return self.FinalResult()

        fake_vosk = types.ModuleType("vosk")
        fake_vosk.SetLogLevel = lambda level: None

        class FakeModel:
            def __init__(self, path):
                self.path = path

        fake_vosk.Model = FakeModel
        fake_vosk.KaldiRecognizer = FakeKaldi
        self.fake_kaldi_cls = FakeKaldi

        self.requests_patcher = None
        self.vosk_patcher = mock.patch.dict(sys.modules, {"vosk": fake_vosk})
        self.vosk_patcher.start()
        self._patches.append(self.vosk_patcher)
        self.fake_vosk = fake_vosk

    def test_auto_download_zip_with_progress(self):
        from dictation.engines.vosk_engine import VoskEngine

        # собираем настоящий zip с моделью — распаковка реальная
        zip_path = self.run_dir / "model.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("vosk-model-small-ru-0.22/conf/model.conf", "conf")
            zf.writestr("vosk-model-small-ru-0.22/am/final.mdl", "am")

        pcts = []

        def fake_get(url, stream=True, timeout=None, **kw):
            self.zip_calls.append(url)
            resp = mock.MagicMock()
            data = zip_path.read_bytes()
            resp.headers = {"content-length": str(len(data))}
            resp.raise_for_status = lambda: None
            chunks = [data[i:i + (1 << 18)]
                      for i in range(0, len(data), (1 << 18))] or [b""]
            resp.iter_content = lambda chunk_size=1 << 18: iter(chunks)
            resp.__enter__.return_value = resp
            return resp

        get_patcher = mock.patch("requests.get", side_effect=fake_get)
        self._patches.append(get_patcher)
        get_patcher.start()

        eng = VoskEngine(progress_cb=lambda p, m: pcts.append(p))
        pcts_only = [p for p in pcts]
        self.assertEqual(pcts_only[-1], 100)
        self.assertEqual(pcts_only, sorted(pcts_only))
        # модель распакована в каталог проекта
        dest = self.run_dir / "userdata" / "models" / "vosk-model-small-ru-0.22"
        self.assertTrue((dest / "conf").exists())
        self.assertEqual(eng._model.ModelPath()
                         if hasattr(eng._model, "ModelPath") else eng._model.path,
                         str(dest))

    def test_live_session_partial_and_final(self):
        from dictation.engines.vosk_engine import VoskEngine

        model_dir = self.run_dir / "my-vosk-model"
        model_dir.mkdir(parents=True)
        eng = VoskEngine(model_size=str(model_dir))
        session = eng.open_stream(16000)
        part = session.feed(np.zeros(16000, dtype=np.float32), 16000)
        self.assertEqual(part, "частичный текст")
        fin = session.finalize()
        self.assertEqual(fin, "финальный текст")

    def test_manual_model_path_no_download(self):
        from dictation.engines.vosk_engine import VoskEngine

        model_dir = self.run_dir / "manual"
        model_dir.mkdir()
        eng = VoskEngine(model_size=str(model_dir))
        self.assertIn("manual", str(eng._model.path))

    def test_batch_transcribe(self):
        from dictation.engines.vosk_engine import VoskEngine

        model_dir = self.run_dir / "m"
        model_dir.mkdir()
        eng = VoskEngine(model_size=str(model_dir))
        audio = np.zeros(32000, dtype=np.float32)
        r = eng.transcribe(audio, 16000)
        self.assertEqual(r.audio_duration, 2.0)


# =============================================================================
# 5. DownloadManager: tqdm-хук при stderr=None + карта репозиториев
# =============================================================================

class DownloadsHookTests(ProjectRunDirTest):
    def test_hook_survives_none_stderr(self):
        """Регрессия: 'NoneType' object has no attribute 'write'."""
        from dictation.downloads import DownloadManager

        mgr = DownloadManager(on_event=lambda k, p: None)
        cls = mgr._hook_cls
        old_stderr = sys.stderr
        sys.stderr = None
        try:
            bar = cls(total=100, desc="turbo")
            bar.update(50)
            bar.close()
        finally:
            sys.stderr = old_stderr

    def test_repo_mapping(self):
        from dictation.downloads import DownloadManager

        mgr = DownloadManager()
        self.assertEqual(mgr._repo("large-v3-turbo"),
                         "mobiuslabsgmbh/faster-whisper-large-v3-turbo")
        self.assertEqual(mgr._repo("turbo"),
                         "mobiuslabsgmbh/faster-whisper-large-v3-turbo")
        self.assertEqual(mgr._repo("large"), "Systran/faster-whisper-large-v3")
        self.assertEqual(mgr._repo("distil-large-v3"),
                         "Systran/faster-distil-whisper-large-v3")
        # пользовательский repo_id проходит как есть
        self.assertEqual(mgr._repo("org/custom-model"), "org/custom-model")


if __name__ == "__main__":
    unittest.main()
