"""Synthetic test clips for the speech-recognition bench: each command in commands.txt spoken
by KittenTTS nano int8, cycling through its 8 voices. Clean audio flatters accuracy; real
recordings come later. Run on the Pi in the KittenTTS venv:

    python gen_clips.py commands.txt clips/
"""
import sys
from pathlib import Path

import soundfile as sf
from kittenml import KittenTTS

VOICES = ['Bella', 'Jasper', 'Luna', 'Bruno', 'Rosie', 'Hugo', 'Kiki', 'Leo']
src, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
model = KittenTTS("KittenML/kitten-tts-nano-0.8-int8")
lines = [ln.strip() for ln in src.read_text().splitlines() if ln.strip()]
with open(out / "transcripts.txt", "w") as t:
    for i, text in enumerate(lines):
        name = f"{i:02d}.wav"
        sf.write(out / name, model.generate(text, voice=VOICES[i % len(VOICES)]),
                 getattr(model, "sample_rate", 24000), subtype="PCM_16")
        t.write(f"{name}\t{text}\n")
