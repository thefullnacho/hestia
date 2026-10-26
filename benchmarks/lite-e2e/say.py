"""KittenTTS as a warm worker: one JSON line in ({"text", "out"}), one line out when the WAV
is written. Runs in the KittenTTS venv so lite.py's own venv never imports it.

KittenTTS prints progress to stdout, so the protocol keeps the real stdout to itself and
everything else goes to stderr. The model loads from the local cache only: the device must
not reach Hugging Face to start (it checks for updates otherwise).
"""
from __future__ import annotations

import json
import os
import sys
import time

os.environ["HF_HUB_OFFLINE"] = "1"
_out = os.fdopen(os.dup(sys.stdout.fileno()), "w")
sys.stdout = sys.stderr

import soundfile as sf
from kittenml import KittenTTS

MODEL = "KittenML/kitten-tts-nano-0.8-int8"
VOICE = "Jasper"


def main():
    model = KittenTTS(MODEL)
    rate = getattr(model, "sample_rate", 24000)
    model.generate("Warm up.", voice=VOICE)
    print(json.dumps({"ready": True}), file=_out, flush=True)
    for line in sys.stdin:
        req = json.loads(line)
        t = time.perf_counter()
        audio = model.generate(req["text"], voice=VOICE)
        sf.write(req["out"], audio, rate)
        print(json.dumps({"secs": round(time.perf_counter() - t, 2),
                          "audio_s": round(len(audio) / rate, 2)}), file=_out, flush=True)


if __name__ == "__main__":
    main()
