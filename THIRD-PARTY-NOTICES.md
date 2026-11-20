# Сторонние компоненты и лицензии

Код этого проекта распространяется по лицензии **MIT** (см. `LICENSE`).
Приложение использует следующие сторонние компоненты. Все они — свободное
программное обеспечение с пермиссивными лицензиями либо LGPL/MPL (см.
примечания в конце).

## Компоненты рантайма

| Компонент | Лицензия | Назначение |
|---|---|---|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | MIT | распознавание речи (CTranslate2) |
| [OpenAI Whisper (модели)](https://github.com/openai/whisper) | MIT | модели, из которых собраны скачиваемые веса |
| Модели Systran/mobiuslabs ([HF](https://huggingface.co/Systran)) | MIT | конвертированные веса (small, large-v3, turbo, …) |
| [ctranslate2](https://github.com/OpenNMT/CTranslate2) | MIT | рантайм инференса |
| [onnxruntime](https://github.com/microsoft/onnxruntime) | MIT | VAD-модель silero |
| [PyAV (av)](https://github.com/PyAV-Org/PyAV) | BSD-3-Clause | декодирование аудио |
| [tokenizers](https://github.com/huggingface/tokenizers) | Apache-2.0 | токенизация |
| [numpy](https://numpy.org) | BSD-3-Clause | численные операции |
| [sounddevice](https://python-sounddevice.readthedocs.io) | MIT (+ PortAudio, MIT-подобная) | запись с микрофона |
| [pynput](https://pypi.org/project/pynput/) | LGPL-3.0 | глобальная горячая клавиша, инжекция клавиш |
| [pystray](https://pypi.org/project/pystray/) | LGPL-3.0 | иконка системного трея |
| [pyperclip](https://github.com/asweigart/pyperclip) | BSD-3-Clause | буфер обмена |
| [platformdirs](https://github.com/platformdirs/platformdirs) | MIT | расположение конфига/кэша |
| [Pillow](https://python-pillow.org) | MIT-CMU | иконка трея |
| [windows-toasts](https://github.com/DatGuy1/windows-toasts) | Apache-2.0 | тосты Windows с кнопками |
| [pyobjc-framework-Cocoa](https://github.com/ronaldoussoren/pyobjc) | MIT | macOS: трей, фокус окон |
| [winrt-runtime и winrt-*](https://github.com/pywinrt/pywinrt) | MIT | WinRT-проекции для тостов |
| [huggingface_hub](https://github.com/huggingface/huggingface_hub) | Apache-2.0 | загрузка моделей |
| [tqdm](https://github.com/tqdm/tqdm) | MPL-2.0 / MIT | прогресс загрузки |
| [hf_xet](https://github.com/huggingface/xet-core) | Apache-2.0 | ускоренная загрузка HF |
| filelock, fsspec, flatbuffers | MIT / BSD / Apache-2.0 | зависимости перечисленных |

Полные тексты лицензий включены в дистрибутивы пакетов и доступны по
указанным ссылкам.

## Примечания

- **LGPL-3.0 (pynput, pystray)**: библиотеки используются «как есть» из
  публичного репозитория PyPI; их исходный код остаётся доступен, лицензии
  прилагаются. LGPL разрешает использование библиотеки в проектах с любой
  лицензией при соблюдении этих условий.
- **MPL-2.0 (tqdm)**: коперайт действует на уровне файлов библиотеки;
  использование в составе данного проекта совместимо с лицензией MIT.
- **Модели Whisper**: выпущены OpenAI под лицензией MIT, конверсии SYSTRAN
  и mobiuslabs наследуют лицензию (проверено: `license: mit` в карточках
  моделей HuggingFace). Использование моделей подчиняется также
  [политике использования OpenAI](https://github.com/openai/whisper/blob/main/model-card.md).
- **Сборка**: PyInstaller (GPL-2.0 со специальным исключением — собранные
  исполняемые файлы не подпадают под GPL) и Inno Setup (свободный
  компилятор инсталляторов) используются только на этапе сборки и не
  накладывают обязательств на распространяемые файлы.
