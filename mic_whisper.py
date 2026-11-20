import threading

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from pynput import keyboard

SAMPLE_RATE = 16000
BLOCK_SIZE = 1024

# Multilingual model; "small.en" would be English-only.
model = WhisperModel("small", device="cpu", compute_type="int8")

space_held = threading.Event()
quit_requested = threading.Event()


def on_press(key):
    if key == keyboard.Key.space:
        space_held.set()
    elif key == keyboard.Key.esc:
        quit_requested.set()
        return False


def on_release(key):
    if key == keyboard.Key.space:
        space_held.clear()


listener = keyboard.Listener(on_press=on_press, on_release=on_release)
listener.start()

print("Hold Space to record; release to transcribe. Press Esc to quit.")

try:
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=BLOCK_SIZE,
    ) as stream:
        while not quit_requested.is_set():
            if not space_held.wait(timeout=0.1):
                continue

            print("Recording...")
            chunks = []

            while space_held.is_set() and not quit_requested.is_set():
                audio, overflowed = stream.read(BLOCK_SIZE)
                chunks.append(audio.copy())

                if overflowed:
                    print("Warning: some audio may have been dropped.")

            if chunks:
                audio = np.concatenate(chunks, axis=0).flatten()

                segments, info = model.transcribe(
                    audio,
                    initial_prompt=(
                        "Русская речь с английскими словами. "
                        "Пишите английские слова латиницей."
                    ),
                )

                text = " ".join(segment.text.strip() for segment in segments)
                if text:
                    print(f"[Detected language: {info.language}] {text}")

except KeyboardInterrupt:
    pass
except Exception as error:
    print(f"Error: {error}")
finally:
    listener.stop()
    print("\nStopped.")
