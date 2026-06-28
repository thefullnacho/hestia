"""Phase 3 voice spike — prove the end-to-end loop on hardware we already own.

    RODE USB mic (card 5) --arecord--> faster-whisper (4060 Ti) --> brain /v1 --> piper TTS --> wav

No HA, no satellite, no purchase: just the standalone loop so we can judge STT accuracy,
the brain's answer to spoken phrasing, TTS intelligibility, and end-to-end latency BEFORE
buying a Voice-PE satellite. The box has no analog speakers (HDMI-only out), so the reply
is written to a wav to listen to off-box.

Usage (default = auto-stop: speak naturally, it ends when you go quiet):
    cd brain/voice
    CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=1 uv run python spike.py
Fixed-duration fallback, or transcribe an existing clip:
    uv run python spike.py --seconds 6
    uv run python spike.py --wav /tmp/clip.wav
"""
from __future__ import annotations

import argparse
import math
import struct
import subprocess
import sys
import time
import wave

import httpx

BRAIN = "http://127.0.0.1:8730/v1/chat/completions"  # local brain; point at your brain host if remote
RODE = "plughw:5,0"          # card 5 = RODE NT-USB (see `arecord -l`)
WHISPER_MODEL = "small.en"   # bump to medium.en if accuracy is short; small.en is ~real-time on the Ti
VOICE = "en_US-lessac-medium"
REC_WAV = "/tmp/voice_in.wav"
OUT_WAV = "/tmp/voice_out.wav"

RATE = 16000
FRAME_MS = 30
FRAME_BYTES = int(RATE * FRAME_MS / 1000) * 2  # 16-bit mono samples


def _rms(frame: bytes) -> float:
    n = len(frame) // 2
    if n == 0:
        return 0.0
    return math.sqrt(sum(s * s for s in struct.unpack("<" + "h" * n, frame)) / n)


def _write_wav(pcm: bytes) -> str:
    with wave.open(REC_WAV, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return REC_WAV


def record(seconds: int) -> str:
    """Fixed-duration capture (kept as a fallback)."""
    print(f"● recording {seconds}s from the RODE — speak now...", flush=True)
    subprocess.run(
        ["arecord", "-D", RODE, "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-d", str(seconds), REC_WAV],
        check=True, capture_output=True,
    )
    return REC_WAV


def record_auto(silence_hang: float = 1.5, start_timeout: float = 8.0, max_record: float = 20.0) -> str:
    """Capture until you stop talking: stream raw PCM from arecord, end after `silence_hang`
    seconds below an auto-calibrated noise floor. No fixed duration, no extra deps."""
    proc = subprocess.Popen(
        ["arecord", "-D", RODE, "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw", "-q"],
        stdout=subprocess.PIPE,
    )
    frames: list[bytes] = []
    try:
        # Calibrate the noise floor from the first ~0.3s of room tone.
        cal = b""
        while len(cal) < FRAME_BYTES * 10:
            block = proc.stdout.read(FRAME_BYTES * 10 - len(cal))
            if not block:
                break
            cal += block
        floor = _rms(cal)
        threshold = max(floor * 3.0, 300.0)
        frames.append(cal)
        print(f"● listening — speak now (auto-stops after {silence_hang}s of silence; "
              f"floor {floor:.0f}, threshold {threshold:.0f})...", flush=True)

        started = False
        silent = 0.0
        t0 = time.time()
        while True:
            chunk = proc.stdout.read(FRAME_BYTES)
            if not chunk:
                break
            frames.append(chunk)
            level = _rms(chunk)
            elapsed = time.time() - t0
            if not started:
                if level > threshold:
                    started = True
                elif elapsed > start_timeout:
                    break  # nobody spoke
            else:
                silent = silent + FRAME_MS / 1000 if level < threshold else 0.0
                if silent >= silence_hang:
                    break
            if elapsed > max_record:
                break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except Exception:  # noqa: BLE001
            proc.kill()
    return _write_wav(b"".join(frames))


def transcribe(wav: str) -> tuple[str, float]:
    from faster_whisper import WhisperModel
    t = time.time()
    model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16")
    segments, _ = model.transcribe(wav, beam_size=5, language="en")
    text = " ".join(s.text.strip() for s in segments).strip()
    return text, time.time() - t


def ask_brain(text: str) -> tuple[str, float]:
    t = time.time()
    r = httpx.post(BRAIN, json={"model": "qwen3:14b",
                                "messages": [{"role": "user", "content": text}]}, timeout=120)
    r.raise_for_status()
    reply = r.json()["choices"][0]["message"]["content"]
    return reply, time.time() - t


def speak(text: str) -> float:
    t = time.time()
    subprocess.run(["python", "-m", "piper", "-m", VOICE, "--data-dir", "voices", "-f", OUT_WAV],
                   input=text.encode(), check=True, capture_output=True)
    return time.time() - t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, help="fixed-duration capture instead of auto-stop")
    ap.add_argument("--wav", help="transcribe this existing wav instead of recording")
    args = ap.parse_args()

    if args.wav:
        wav = args.wav
    elif args.seconds:
        wav = record(args.seconds)
    else:
        wav = record_auto()

    heard, t_stt = transcribe(wav)
    print(f"\n  heard   ({t_stt:.1f}s STT): {heard!r}")
    if not heard:
        print("  (nothing transcribed — silence, or speak louder/closer)")
        sys.exit(1)

    reply, t_brain = ask_brain(heard)
    print(f"  brain   ({t_brain:.1f}s): {reply}")

    t_tts = speak(reply)
    print(f"  spoke   ({t_tts:.1f}s TTS) -> {OUT_WAV}")
    print(f"\n  end-to-end: {t_stt + t_brain + t_tts:.1f}s")


if __name__ == "__main__":
    main()
