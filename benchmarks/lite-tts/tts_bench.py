"""KittenTTS small ONNX models on the Lite floor hardware (Pi 4, 4GB): can the voice fit?

Per model: load time, peak RSS, and for three typical Hestia replies, synthesis seconds and the
real-time factor (synthesis time / audio length; under 1.0 means faster than it speaks). Each
reply is also timed sentence by sentence, since speaking can start after the first sentence.
WAVs are written for a listening check. Run on the Pi:

    python tts_bench.py --out wavs/ [--runs 3]
"""
from __future__ import annotations

import argparse
import json
import re
import resource
import time
from pathlib import Path

MODELS = ["KittenML/kitten-tts-nano-0.8-int8", "KittenML/kitten-tts-nano-0.8-fp32",
          "KittenML/kitten-tts-micro-0.8", "KittenML/kitten-tts-mini-0.8"]
REPLIES = {
    "short": "Done. The kitchen lights are off.",
    "medium": "Rain is likely tomorrow afternoon, about half an inch. Frost isn't expected "
              "this week, so the beds can stay uncovered.",
    "long": "Prune blueberries in late winter while they're dormant. Take out canes older than "
            "six years at the base, and any that are broken or rubbing. Leave six to ten healthy "
            "canes per bush. That's from the Blueberry Pruning Guide, page two.",
}
VOICE = "Jasper"


def peak_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="wavs")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--models", nargs="*", default=MODELS)
    a = ap.parse_args()
    import soundfile as sf
    from kittenml import KittenTTS
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name in a.models:
        t0 = time.perf_counter()
        model = KittenTTS(name)
        load = time.perf_counter() - t0
        rate = getattr(model, "sample_rate", 24000)
        model.generate("Warm up.", voice=VOICE)
        row = {"model": name, "load_s": round(load, 2), "replies": {}}
        for key, text in REPLIES.items():
            times, audio = [], None
            for _ in range(a.runs):
                t = time.perf_counter()
                audio = model.generate(text, voice=VOICE)
                times.append(time.perf_counter() - t)
            secs = len(audio) / rate
            first = re.split(r"(?<=[.!?])\s+", text)[0]
            t = time.perf_counter()
            model.generate(first, voice=VOICE)
            first_s = time.perf_counter() - t
            med = sorted(times)[len(times) // 2]
            row["replies"][key] = {"synth_s": round(med, 2), "audio_s": round(secs, 2),
                                   "rtf": round(med / secs, 2), "first_sentence_s": round(first_s, 2)}
            sf.write(out / f"{name.split('/')[1]}-{key}.wav", audio, rate)
        row["peak_rss_mb"] = round(peak_mb())
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
