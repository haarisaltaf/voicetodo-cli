"""Voice recorder.

Three backends, picked at runtime in this order:

  1. **sounddevice + soundfile**: pure-Python, identical behaviour on macOS
     and Linux. Requires PortAudio (apt install libportaudio2 / brew install
     portaudio). This is the cleanest option.

  2. **sox** (`rec` command): single binary, available on most Linux distros
     and via brew on macOS.

  3. **ffmpeg**: as a last resort. Uses avfoundation on macOS / alsa on
     Linux. Most installs already have ffmpeg.

Records mono 16-kHz WAV — small, and that's exactly what Whisper resamples
to anyway, so encoding/decoding overhead is minimal.

Press Enter to stop. (We deliberately avoid raw-mode stdin so Ctrl-C still
works as expected.)
"""

from __future__ import annotations

import os
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional


SAMPLE_RATE = 16_000
CHANNELS = 1


class RecorderError(RuntimeError):
    pass


@dataclass
class RecordResult:
    path: Path
    duration: float
    backend: str


def detect_backend() -> str:
    """Return the name of the backend that will be used. Useful for status
    output before recording starts."""
    if _have_sounddevice():
        return "sounddevice"
    if shutil.which("rec"):
        return "sox"
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    return "none"


def record_until_enter(out_path: Path) -> RecordResult:
    """Record to {out_path}, stopping when the user presses Enter (or
    Ctrl-C). Returns the resulting file and its duration in seconds."""
    backend = detect_backend()
    if backend == "sounddevice":
        return _record_sounddevice(out_path)
    if backend == "sox":
        return _record_subprocess(
            out_path,
            ["rec", "-q", "-r", str(SAMPLE_RATE), "-c", str(CHANNELS),
             "-b", "16", str(out_path)],
            backend="sox",
        )
    if backend == "ffmpeg":
        # Pick the right input device per platform.
        if sys.platform == "darwin":
            input_args = ["-f", "avfoundation", "-i", ":0"]
        elif sys.platform.startswith("linux"):
            input_args = ["-f", "alsa", "-i", "default"]
        else:
            raise RecorderError(
                f"ffmpeg backend doesn't know how to capture audio on "
                f"{sys.platform}. Install sounddevice or sox."
            )
        return _record_subprocess(
            out_path,
            ["ffmpeg", "-loglevel", "error", "-y", *input_args,
             "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE),
             str(out_path)],
            backend="ffmpeg",
        )
    raise RecorderError(
        "No audio backend found. Install one of:\n"
        "  pip install sounddevice soundfile     (recommended)\n"
        "  apt install sox    /  brew install sox\n"
        "  apt install ffmpeg /  brew install ffmpeg"
    )


# ----------------------------------------------------------------- backends

def _have_sounddevice() -> bool:
    try:
        import sounddevice  # noqa: F401
        import soundfile    # noqa: F401
        return True
    except (ImportError, OSError):
        # OSError covers "PortAudio shared lib missing".
        return False


def _record_sounddevice(out_path: Path) -> RecordResult:
    import numpy as np
    import sounddevice as sd
    import soundfile as sf

    chunks: list = []
    stop = threading.Event()

    def callback(indata, frames, time_info, status):  # noqa: ARG001
        if status:
            # Don't print, just keep going — overruns are common on shared CPU
            pass
        chunks.append(indata.copy())

    print(f"recording — press Enter to stop  ({SAMPLE_RATE} Hz mono)")
    started = time.monotonic()
    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE, channels=CHANNELS,
            dtype="int16", callback=callback,
        ):
            try:
                _wait_for_enter()
            except KeyboardInterrupt:
                pass
    except sd.PortAudioError as e:
        raise RecorderError(f"PortAudio error: {e}") from e

    elapsed = time.monotonic() - started
    if not chunks:
        raise RecorderError("No audio captured.")

    audio = np.concatenate(chunks, axis=0)
    sf.write(str(out_path), audio, SAMPLE_RATE, subtype="PCM_16")
    return RecordResult(path=out_path, duration=elapsed, backend="sounddevice")


def _record_subprocess(
    out_path: Path, argv: list[str], *, backend: str
) -> RecordResult:
    print(f"recording with {backend} — press Enter to stop")
    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    try:
        _wait_for_enter_while_running(proc)
    except KeyboardInterrupt:
        pass

    # Send SIGINT first (sox/ffmpeg both flush their output cleanly on it).
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=5)

    elapsed = time.monotonic() - started
    if proc.returncode not in (0, -signal.SIGINT, 130, 255):
        # ffmpeg in particular sometimes returns 255 after SIGINT — that's fine.
        err = (proc.stderr.read() or b"").decode("utf-8", errors="replace")
        if err.strip():
            print(err.strip(), file=sys.stderr)
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RecorderError("Recorder produced no audio.")
    return RecordResult(path=out_path, duration=elapsed, backend=backend)


# ----------------------------------------------------------------- input wait

def _wait_for_enter() -> None:
    """Block until the user presses Enter on stdin, without putting the
    terminal into raw mode. Ctrl-C still raises KeyboardInterrupt."""
    try:
        sys.stdin.readline()
    except KeyboardInterrupt:
        raise


def _wait_for_enter_while_running(proc: subprocess.Popen) -> None:
    """Same as above but bails out early if the subprocess dies on its own."""
    if not sys.stdin.isatty():
        # Non-interactive: just wait for the subprocess.
        proc.wait()
        return
    while True:
        if proc.poll() is not None:
            return
        rlist, _, _ = select.select([sys.stdin], [], [], 0.25)
        if rlist:
            sys.stdin.readline()
            return
