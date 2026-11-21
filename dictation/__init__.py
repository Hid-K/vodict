"""Voice dictation: press a key — speak — text appears in the focused input field.

Architecture overview:
- ``dictation.engines`` — pluggable speech-to-text engines (Whisper family,
  cloud APIs, ...). New engines are added by subclassing ``TranscriptionEngine``.
- ``dictation.audio``      — microphone capture and sample-rate utilities.
- ``dictation.output``     — how recognized text lands in the target app
  (clipboard+paste or keystroke emulation).
- ``dictation.platform``   — per-OS focus tracking / window activation
  (Windows, macOS).
- ``dictation.ui``         — tkinter control window.
- ``dictation.app``        — DictationController wiring everything together.
"""

__version__ = "0.2.0"
