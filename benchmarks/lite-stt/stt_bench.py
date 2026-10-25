"""Moonshine on the Lite floor hardware: speed, memory and word error rate per model.

Reads a folder of WAVs and a transcripts.txt (file<TAB>text). One model per process run, so
peak RSS belongs to that model. Optional keyterms bias the decoder toward household words (streaming models only).
Run on the Pi in the Moonshine venv:

    python stt_bench.py clips/ --arch TINY [--keyterms Biscuit,Arrival]
"""
from __future__ import annotations

import argparse
import json
import re
import resource
import time
from pathlib import Path

import moonshine_voice as mv
from moonshine_voice.transcriber import Transcriber


def words(s: str) -> list[str]:
    # Hyphens join: BASE writes "to-morrow", "to-day", "to-night", which a parser reads as one word.
    s = re.sub(r"(?<=[a-z])-(?=[a-z])", "", s.lower().replace("%", " percent"))
    return re.sub(r"[^a-z0-9' ]", " ", s).split()


def edits(ref: list[str], hyp: list[str]) -> int:
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
    return d[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clips")
    ap.add_argument("--arch", default="TINY")
    ap.add_argument("--keyterms", default="")
    ap.add_argument("--show", action="store_true", help="print each mistranscription")
    a = ap.parse_args()
    arch = getattr(mv.ModelArch, a.arch)
    path, arch = mv.get_model_for_language("en", arch)
    t0 = time.perf_counter()
    tr = Transcriber(path, model_arch=arch)
    load = time.perf_counter() - t0
    if a.keyterms:
        tr.set_keyterms([k.strip() for k in a.keyterms.split(",")])
    rows = [ln.split("\t") for ln in (Path(a.clips) / "transcripts.txt").read_text().splitlines()]
    tr.transcribe_without_streaming(*mv.load_wav_file(Path(a.clips) / rows[0][0]))  # warm up
    errs = ref_words = exact = 0
    lat, audio = [], 0.0
    for name, text in rows:
        data, rate = mv.load_wav_file(Path(a.clips) / name)
        t = time.perf_counter()
        out = tr.transcribe_without_streaming(data, sample_rate=rate)
        lat.append(time.perf_counter() - t)
        audio += len(data) / rate
        hyp = " ".join(line.text for line in out.lines)
        e = edits(words(text), words(hyp))
        errs, ref_words, exact = errs + e, ref_words + len(words(text)), exact + (e == 0)
        if a.show and e:
            print(f"  {text!r} -> {hyp!r}")
    lat.sort()
    print(json.dumps({"arch": a.arch, "keyterms": bool(a.keyterms), "load_s": round(load, 2),
                      "clips": len(rows), "wer": round(errs / ref_words, 3), "exact": f"{exact}/{len(rows)}",
                      "latency_median_s": round(lat[len(lat) // 2], 3), "latency_max_s": round(lat[-1], 3),
                      "rtf": round(sum(lat) / audio, 3),
                      "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)}))


if __name__ == "__main__":
    main()
