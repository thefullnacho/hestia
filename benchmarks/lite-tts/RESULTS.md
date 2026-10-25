# KittenTTS on the Lite floor hardware

Pi 4 Model B, 4GB, no heatsink, ONNX Runtime 1.31, voice "Jasper", 2026-10-24. `tts_bench.py`,
median of 3 runs per reply, each model started under 55C. Real-time factor (RTF) is synthesis
time over audio length; under 1.0 speaks faster than real time. "First sentence" is the time
before speech could start if replies are spoken sentence by sentence.

| Model | Size | RTF (short / medium / long) | First sentence | Load | Peak RSS |
|---|---|---|---|---|---|
| nano int8 | 25 MB | 0.63 / 0.58 / 0.59 | 0.95-2.69s | 9.6s | 256 MB |
| nano fp32 | 56 MB | 0.61 / 0.57 / 0.57 | 0.91-2.65s | 8.4s | 275 MB |
| micro | 41 MB | 0.69 / 0.60 / 0.61 | 0.70-2.62s | 9.8s | 282 MB |
| mini | 80 MB | 1.50 / 1.39 / 1.40 | 1.85-5.49s | 9.6s | 412 MB |

- nano and micro fit: faster than real time, under 300 MB, and a short confirmation ("Done. The
  kitchen lights are off.") can start speaking in under a second. mini is slower than it speaks.
- None of the small ONNX models clone voices (8 preset voices); zero-shot cloning is KittenTTS 2,
  1.7B parameters and about 6 GB of RAM, so not this box.
- Heat: every run took the bare board from 54C to 70-76C within a minute and set the
  soft-temperature-limit flag (throttled=0x80000). Results matched across a hot first pass and a
  cooled rerun, so the numbers stand, but the enclosure needs a heatsink or airflow.
- Install without torch: `pip install --no-deps kittenml` then `onnxruntime espeakng_loader
  phonemizer soundfile numpy huggingface_hub` (torch is only for KittenTTS 2).
- Budget so far on 3.5 GB usable: router (EmbeddingGemma) + caller (FunctionGemma 270M) + TTS
  under 300 MB leaves room for speech recognition, still unmeasured.
- Licence: code Apache-2.0; models are licensed separately, read before shipping anything.
