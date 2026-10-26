"""Relabel Ollama's EmbeddingGemma file so llama.cpp loads it. Every weight byte is unchanged.

Ollama stores EmbeddingGemma as architecture `gemma3` with its two projection layers named
`dense.0` and `dense.1`. llama.cpp loads it as `gemma-embedding`, with `dense_2` / `dense_3` and
their sizes in the metadata (what its own converter writes from the Hugging Face checkpoint,
which is gated behind a license click). Same model, different labels, so the router's cached
example vectors from Ollama stay valid; embed_probe.py checks that.

    PYTHONPATH=~/llama.cpp/gguf-py python retag_embeddinggemma.py ollama-blob out.gguf [--q8]

--q8 also quantizes the 16-bit matrices to Q8_0 (norms stay F32), as llama-quantize would: about
half the memory, and faster on a CPU with no 16-bit math (the Pi 4). The vectors move slightly,
so the router's examples have to be embedded again with the new file.
"""
from __future__ import annotations

import sys

import gguf
import numpy as np
from gguf.quants import quantize

OLD, NEW = "gemma3", "gemma-embedding"
RENAME = {"dense.0.weight": "dense_2.weight", "dense.1.weight": "dense_3.weight"}


def to_q8(t):
    """BF16 matrix -> (Q8_0 bytes, type); anything else is left as it is."""
    if t.tensor_type != gguf.GGMLQuantizationType.BF16 or len(t.shape) != 2:
        return t.data, t.tensor_type
    # the reader hands BF16 over as raw bytes (rows x 2*cols); BF16 is the top half of an F32
    f32 = (np.asarray(t.data).view(np.uint16).astype(np.uint32) << 16).view(np.float32)
    return quantize(f32, gguf.GGMLQuantizationType.Q8_0), gguf.GGMLQuantizationType.Q8_0


def main():
    q8 = "--q8" in sys.argv
    reader = gguf.GGUFReader(sys.argv[1])
    writer = gguf.GGUFWriter(sys.argv[2], arch=NEW)
    for field in reader.fields.values():
        if field.name == gguf.Keys.General.ARCHITECTURE or field.name.startswith("GGUF."):
            continue
        val_type = field.types[0]
        sub_type = field.types[-1] if val_type == gguf.GGUFValueType.ARRAY else None
        name = NEW + field.name[len(OLD):] if field.name.startswith(OLD + ".") else field.name
        writer.add_key_value(name, field.contents(), val_type, sub_type=sub_type)

    tensors = {t.name: t for t in reader.tensors}
    d2, d3 = tensors["dense.0.weight"], tensors["dense.1.weight"]
    # GGUF shapes are (in, out): dense_2 maps 768 -> 3072, dense_3 maps 3072 -> 768
    writer.add_dense_features_dims("dense_2", int(d2.shape[0]), int(d2.shape[1]))
    writer.add_dense_features_dims("dense_3", int(d3.shape[0]), int(d3.shape[1]))

    if q8:
        writer.add_file_type(gguf.LlamaFileType.MOSTLY_Q8_0)
    out = [(t, *(to_q8(t) if q8 else (t.data, t.tensor_type))) for t in reader.tensors]
    for t, data, kind in out:
        writer.add_tensor_info(RENAME.get(t.name, t.name), data.shape, data.dtype, data.nbytes, kind)
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_ti_data_to_file()
    for _, data, _ in out:
        writer.write_tensor_data(data, tensor_endianess=reader.endianess)
    writer.close()
    print(f"{len(tensors)} tensors -> {sys.argv[2]} as {NEW}")


if __name__ == "__main__":
    main()
