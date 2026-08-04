"""The status tool's contract: it must NEVER raise (a probe failing IS the signal, and the
agent loop expects a string back), it must advertise a well-formed schema, and `snapshot()`
must always return the dict shape the future /status web endpoint relies on — even fully
offline, where every service simply reports down. Network is not stubbed: the assertions
hold whether the stack is up or unreachable."""
from __future__ import annotations

import tools
from tools import status


def test_status_registered_and_advertised():
    assert "status" in [s["function"]["name"] for s in tools.SCHEMAS]


def test_status_dispatch_returns_string():
    out = tools.dispatch("status", {})
    assert isinstance(out, str) and out.startswith("Hestia status:")


def test_status_unknown_section_is_a_string_not_a_raise():
    assert tools.dispatch("status", {"section": "bogus"}) == "Error: unknown section 'bogus'."


def test_snapshot_shape():
    snap = status.snapshot()
    assert set(snap) >= {"services", "brain", "gpus", "system", "downloads"}
    assert isinstance(snap["services"], list)
    assert {"ollama_up", "model", "resident"} <= set(snap["brain"])
    # swap_pct is always present — it's the meltdown early-warning signal we never omit.
    assert "swap_pct" in snap["system"]


# ----- VRAM attribution ------------------------------------------------------
# Totals alone hide orphans: "4060 Ti: 5.2/16 GB" looked healthy for the eleven days two
# stray benchmark containers sat on the cards (2026-07-23 -> 2026-08-03). These pin the
# attribution that makes such a holder visible.

def test_snapshot_carries_gpu_processes():
    assert "gpu_processes" in status.snapshot()


def test_expected_holders_are_recognised_by_cmdline_not_exe(monkeypatch):
    """The voice services run as `.../.venv/bin/python`, so matching nvidia-smi's `name`
    would mark Chatterbox unexpected. Identification must come from /proc cmdline."""
    monkeypatch.setattr(status, "_proc_cmdline",
                        lambda pid: "/home/alex/.../.venv/bin/python -m wyoming_chatterbox --device cuda")
    monkeypatch.setattr(status, "_in_container", lambda pid: False)
    monkeypatch.setattr(status.subprocess, "run", _fake_smi(
        "2255, 3638, GPU-aaa, /home/alex/.venv/bin/python"))
    procs = status._gpu_processes({"GPU-aaa": 1})
    assert len(procs) == 1
    assert procs[0]["expected"] is True
    assert procs[0]["label"] == "wyoming_chatterbox"
    assert procs[0]["gpu"] == 1


def test_orphaned_container_is_flagged(monkeypatch):
    """The real 2026-07-23 signature: a containerised uvicorn nobody asked for."""
    monkeypatch.setattr(status, "_proc_cmdline",
                        lambda pid: "/app/.venv/bin/python3 -m uvicorn api.src.main:app --port 8880")
    monkeypatch.setattr(status, "_in_container", lambda pid: True)
    monkeypatch.setattr(status.subprocess, "run", _fake_smi(
        "1576200, 848, GPU-bbb, /app/.venv/bin/python3"))
    p = status._gpu_processes({"GPU-bbb": 1})[0]
    assert p["expected"] is False
    assert p["container"] is True
    assert p["mem_mb"] == 848
    assert status._unexpected_vram([p]) == [p]


def test_unexpected_holder_reaches_the_gpu_readout():
    stray = {"pid": 1, "gpu": 1, "mem_mb": 848, "label": "uvicorn",
             "expected": False, "container": True, "cmd": "x"}
    gpus = [{"index": 1, "name": "RTX 4060 Ti", "mem_used_gb": 5.2,
             "mem_total_gb": 16.0, "util_pct": 0, "temp_c": 36}]
    out = status._fmt_gpu(gpus, [stray])
    assert "unexpected container on GPU1" in out and "uvicorn" in out and "848 MB" in out


def test_gpu_readout_stays_quiet_when_everything_is_expected():
    gpus = [{"index": 0, "name": "RTX 5080", "mem_used_gb": 9.5,
             "mem_total_gb": 16.0, "util_pct": 0, "temp_c": 34}]
    ok = {"pid": 1, "gpu": 0, "mem_mb": 9520, "label": "llama-server",
          "expected": True, "container": False, "cmd": "x"}
    assert "unexpected" not in status._fmt_gpu(gpus, [ok])


def test_no_nvidia_smi_degrades_to_empty_not_a_raise(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("nvidia-smi")
    monkeypatch.setattr(status.subprocess, "run", boom)
    assert status._gpu_info() == {"gpus": [], "processes": []}


def _fake_smi(stdout: str):
    class R:
        pass
    def run(*a, **k):
        r = R()
        r.stdout = stdout
        return r
    return run


def test_label_prefers_a_real_binary_over_its_arguments():
    """llama-server is passed the model blob path; scanning args first labelled it `sha256-...`."""
    cmd = "/usr/local/lib/ollama/llama-server --model /home/alex/.ollama/models/blobs/sha256-a8cc13"
    assert status._vram_label(cmd, "/usr/local/lib/ollama/llama-server") == "llama-server"


def test_label_falls_through_to_the_script_for_interpreters():
    # no -m, so the first path-like argument is the useful name (the speaches signature)
    cmd = "/home/ubuntu/speaches/.venv/bin/python /home/ubuntu/speaches/.venv/bin/uvicorn --factory"
    assert status._vram_label(cmd, "/home/ubuntu/speaches/.venv/bin/python") == "uvicorn"


def test_label_survives_an_unreadable_cmdline():
    assert status._vram_label("", "/usr/local/lib/ollama/llama-server") == "llama-server"
