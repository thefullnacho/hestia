"""`status` tool — a single health readout for the whole Hestia stack.

Hestia has a conversational front door (chat/CLI) but no at-a-glance one: "did the movie
grab finish?" is easy, "is everything healthy right now?" was not. This answers the second.

Design: a `snapshot()` data layer (returns a plain dict) + an `execute()` formatter for the
model. The future web /status endpoint serves the SAME `snapshot()` as JSON — one collector,
two consumers (the brain tool and the dashboard), so health is never implemented twice.

Reuse over reinvention: service URLs/keys come straight from the existing tools (`media`'s
Sonarr/Radarr/Lidarr config, `home`'s Home Assistant connection), not a second copy. The
only genuinely new code is the health probes + system/GPU metrics, which read `/proc` and
`nvidia-smi` directly so nothing new has to be installed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

import httpx

from . import home, media

# The resident inference engine (see hestia-ollama.service / hestia-brain.service).
OLLAMA_URL = os.environ.get("HESTIA_OLLAMA", "http://127.0.0.1:11434").rstrip("/")
BRAIN_MODEL = os.environ.get("HESTIA_MODEL", "qwen3:14b")

# Probes are best-effort and short — the whole readout runs them in parallel, so even with
# several services down the call returns in about one timeout, not the sum of them.
_TIMEOUT = 3.0

# Swap usage is THE early-warning signal for the kind of thrash that took hl-relay down once
# (see hestia-hl-relay-perf-incident); surface it even when small.
SCHEMA = {
    "type": "function",
    "function": {
        "name": "status",
        "description": ("Health check for the whole Hestia stack at a glance — answers 'is "
                        "everything ok/up/healthy?', 'is anything down?', 'how's the brain "
                        "box?'. Reports each service (Sonarr/Radarr/Lidarr/Plex/Home "
                        "Assistant/Ollama) up or down, whether the resident model is loaded, "
                        "GPU memory/utilisation, the brain box's load/RAM/swap/disk, and how "
                        "many downloads are active. section defaults to 'all'; pass one to "
                        "narrow it."),
        "parameters": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["all", "services", "brain", "gpu", "system", "downloads"],
                    "description": "which part to report; omit for the full readout",
                },
            },
            "required": [],
        },
    },
}


# ---------- service probes (reuse the tools' own URLs/keys) ----------

def _probe(name: str, url: str, headers: dict | None = None,
           params: dict | None = None) -> dict:
    """Generic 'is it answering?' check. up=True iff it returns a non-error HTTP status."""
    try:
        r = httpx.get(url, headers=headers, params=params, timeout=_TIMEOUT)
        if r.status_code >= 400:
            return {"name": name, "up": False, "detail": f"HTTP {r.status_code}"}
        return {"name": name, "up": True, "detail": ""}
    except Exception as e:  # noqa: BLE001 — a probe failure IS the signal, never an exception
        return {"name": name, "up": False, "detail": type(e).__name__}


def _arr_probe(name: str, app: dict, api: str) -> dict:
    """Sonarr/Radarr (v3) and Lidarr (v1) all answer at /api/<v>/system/status; report the
    running version when up. URL + key are reused from the media tool, not re-declared."""
    out = _probe(name, f"{app['base']}/api/{api}/system/status",
                 headers={"X-Api-Key": app["key"]})
    if out["up"]:
        try:
            v = httpx.get(f"{app['base']}/api/{api}/system/status",
                          headers={"X-Api-Key": app["key"]}, timeout=_TIMEOUT).json()
            out["detail"] = f"v{v.get('version', '?')}"
        except Exception:  # noqa: BLE001 — version is a nicety; up/down already decided
            pass
    return out


def _service_checks() -> list:
    """The probes to fan out. Each entry is a (callable) returning the dict above."""
    checks = []
    if media.APPS["tv"]["key"]:
        checks.append(lambda: _arr_probe("Sonarr", media.APPS["tv"], "v3"))
    if media.APPS["movie"]["key"]:
        checks.append(lambda: _arr_probe("Radarr", media.APPS["movie"], "v3"))
    if media.LIDARR["key"]:
        checks.append(lambda: _arr_probe("Lidarr", media.LIDARR, "v1"))
    plex_url = os.environ.get("PLEX_URL", "").rstrip("/")
    if plex_url:
        # /identity answers without auth; pass the token anyway so a locked-down server is ok.
        checks.append(lambda: _probe("Plex", f"{plex_url}/identity",
                                     params={"X-Plex-Token": os.environ.get("PLEX_TOKEN", "")}))
    if home.HA_TOKEN:
        checks.append(lambda: _probe("Home Assistant", f"{home.HA_URL}/api/", headers=home._HDRS))
    return checks


# ---------- brain / model ----------

def _brain() -> dict:
    """Is the inference engine up, and is the resident model actually loaded? A loaded model
    is what makes the brain answer instantly instead of cold-starting on the first request."""
    info = {"ollama_up": False, "version": "", "model": BRAIN_MODEL, "resident": False}
    try:
        info["version"] = httpx.get(f"{OLLAMA_URL}/api/version", timeout=_TIMEOUT).json().get("version", "")
        info["ollama_up"] = True
    except Exception:  # noqa: BLE001
        return info
    try:
        ps = httpx.get(f"{OLLAMA_URL}/api/ps", timeout=_TIMEOUT).json()
        base = BRAIN_MODEL.split(":")[0]
        info["resident"] = any((m.get("name", "")).split(":")[0] == base
                               for m in ps.get("models", []))
    except Exception:  # noqa: BLE001
        pass
    return info


# ---------- GPU (local nvidia-smi) ----------

def _gpus() -> list:
    """Per-GPU memory/util/temp via nvidia-smi. On the brain box GPU0 (5080) holds the
    resident model and GPU1 (4060 Ti) is the free card — see hestia-gpu-box-ops."""
    try:
        raw = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=_TIMEOUT, check=True).stdout
    except Exception:  # noqa: BLE001 — no GPU / no nvidia-smi: just omit the section
        return []
    gpus = []
    for line in raw.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        idx, name, used, total, util, temp = parts
        gpus.append({
            "index": int(idx), "name": name,
            "mem_used_gb": round(float(used) / 1024, 1),
            "mem_total_gb": round(float(total) / 1024, 1),
            "util_pct": int(float(util)), "temp_c": int(float(temp)),
        })
    return gpus


# ---------- system (the brain box, straight from /proc — no psutil dependency) ----------

def _meminfo() -> dict:
    out = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, rest = line.partition(":")
                out[k] = int(rest.strip().split()[0])  # kB
    except OSError:
        pass
    return out


def _system() -> dict:
    sysinfo: dict = {}
    try:
        sysinfo["load1"] = round(os.getloadavg()[0], 2)
        sysinfo["cpus"] = os.cpu_count()
    except OSError:
        pass
    mi = _meminfo()
    if mi.get("MemTotal"):
        sysinfo["mem_pct"] = round(100 * (1 - mi.get("MemAvailable", 0) / mi["MemTotal"]))
    if mi.get("SwapTotal"):
        used = mi["SwapTotal"] - mi.get("SwapFree", 0)
        sysinfo["swap_pct"] = round(100 * used / mi["SwapTotal"])
        sysinfo["swap_used_gb"] = round(used / 1024 / 1024, 1)
    else:
        sysinfo["swap_pct"] = 0
        sysinfo["swap_used_gb"] = 0.0
    try:
        du = shutil.disk_usage("/")
        sysinfo["disk_pct"] = round(100 * du.used / du.total)
    except OSError:
        pass
    return sysinfo


# ---------- downloads (reuse the media queue endpoints) ----------

def _downloads() -> dict:
    """How many items are actively in the *arr queues right now (a count, not the detail —
    `media` action='status' gives the per-item rundown)."""
    total = 0
    for app, api in ((media.APPS["tv"], "v3"), (media.APPS["movie"], "v3"), (media.LIDARR, "v1")):
        if not app["key"]:
            continue
        try:
            q = httpx.get(f"{app['base']}/api/{api}/queue", headers={"X-Api-Key": app["key"]},
                          params={"pageSize": 1}, timeout=_TIMEOUT).json()
            total += int(q.get("totalRecords", 0))
        except Exception:  # noqa: BLE001
            pass
    return {"active": total}


# ---------- snapshot: the shared data layer ----------

def snapshot() -> dict:
    """Collect the whole health picture as a plain dict. The brain tool formats this; the web
    /status endpoint will serialise the very same dict. All probes run in parallel."""
    checks = _service_checks()
    with ThreadPoolExecutor(max_workers=max(1, len(checks) + 4)) as pool:
        services_f = [pool.submit(c) for c in checks]
        brain_f = pool.submit(_brain)
        gpus_f = pool.submit(_gpus)
        system_f = pool.submit(_system)
        downloads_f = pool.submit(_downloads)
        services = [f.result() for f in services_f]
        return {
            "services": services,
            "brain": brain_f.result(),
            "gpus": gpus_f.result(),
            "system": system_f.result(),
            "downloads": downloads_f.result(),
        }


# ---------- formatting for the model ----------

def _fmt_services(svcs: list) -> str:
    if not svcs:
        return "Services: none configured."
    parts = []
    for s in svcs:
        if s["up"]:
            parts.append(f"{s['name']} ok" + (f" ({s['detail']})" if s["detail"] else ""))
        else:
            parts.append(f"{s['name']} DOWN ({s['detail']})")
    down = sum(1 for s in svcs if not s["up"])
    head = "all up" if not down else f"{down} DOWN"
    return f"Services ({len(svcs) - down}/{len(svcs)} up — {head}): " + ", ".join(parts) + "."


def _fmt_brain(b: dict) -> str:
    if not b["ollama_up"]:
        return "Brain: Ollama DOWN — the model is not serving."
    res = f"{b['model']} loaded" if b["resident"] else f"{b['model']} NOT loaded (will cold-start)"
    return f"Brain: {res} (ollama {b['version']})."


def _fmt_gpu(gpus: list) -> str:
    if not gpus:
        return "GPU: no readings."
    lines = ["GPU:"]
    for g in gpus:
        lines.append(f"  GPU{g['index']} {g['name']}: {g['mem_used_gb']}/{g['mem_total_gb']} GB, "
                     f"{g['util_pct']}% util, {g['temp_c']}C")
    return "\n".join(lines)


def _fmt_system(s: dict) -> str:
    bits = []
    if "load1" in s:
        bits.append(f"load {s['load1']}/{s.get('cpus', '?')} cores")
    if "mem_pct" in s:
        bits.append(f"RAM {s['mem_pct']}%")
    bits.append(f"swap {s.get('swap_pct', 0)}%"
                + (f" ({s['swap_used_gb']} GB)" if s.get("swap_used_gb") else ""))
    if "disk_pct" in s:
        bits.append(f"disk {s['disk_pct']}% used")
    return "System (brain box): " + ", ".join(bits) + "."


def _fmt_downloads(d: dict) -> str:
    n = d.get("active", 0)
    return "Downloads: nothing active." if not n else f"Downloads: {n} active."


_FORMATTERS = {
    "services": lambda s: _fmt_services(s["services"]),
    "brain": lambda s: _fmt_brain(s["brain"]),
    "gpu": lambda s: _fmt_gpu(s["gpus"]),
    "system": lambda s: _fmt_system(s["system"]),
    "downloads": lambda s: _fmt_downloads(s["downloads"]),
}


def execute(section: str = "all") -> str:
    try:
        snap = snapshot()
    except Exception as e:  # noqa: BLE001 — the tool must hand back a string, never raise
        return f"Error gathering status: {e}"
    if section and section != "all":
        fmt = _FORMATTERS.get(section)
        return fmt(snap) if fmt else f"Error: unknown section '{section}'."
    # Full readout: a one-line headline (anything wrong?) then each section.
    down = [s["name"] for s in snap["services"] if not s["up"]]
    if not snap["brain"]["ollama_up"]:
        down.append("Ollama")
    head = ("Hestia status: all systems nominal." if not down
            else f"Hestia status: {len(down)} issue(s) — {', '.join(down)} down.")
    return "\n".join([
        head,
        _fmt_services(snap["services"]),
        _fmt_brain(snap["brain"]),
        _fmt_gpu(snap["gpus"]),
        _fmt_system(snap["system"]),
        _fmt_downloads(snap["downloads"]),
    ])
