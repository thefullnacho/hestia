"""Model eval harness — compare candidate models for Hestia's resident brain slot.

Runs the SAME agent machinery the brain uses (real system prompt + live light/soil
catalog + tools + temp 0.3 + tool loop), against a battery of cases that target the
qwen2.5:14b failure modes: language drift, fabricated tool output, the catalog
fast-path, tool selection, and the records write-path. Each case runs N times so we
measure consistency, not luck.

Trustworthiness (added 2026-06-21 after a contaminated run):
  - Live cases (soil moisture, light state) are judged against a snapshot taken ONCE at
    start, so every model sees the same reality and a turned-off light or drifted sensor
    can't masquerade as a model fail. They report as a separate 'live/advisory' score.
  - The confabulation check derives the real plant vocabulary from records, instead of
    hard-coding plant names that rot as the garden changes.
  - Every response + tool trace is dumped to brain/eval_results/eval-<stamp>.json, so a
    failure is inspectable instead of needing hand-reproduction.
  - No bash: production has no shell tool (tools/__init__.py — deliberately removed), so
    testing one is meaningless. Destructive-action safety lives in the code-level gate.

Usage:
  uv run --project brain python brain/eval_models.py qwen3:8b qwen3:14b
  (append :nothink / :think to force qwen3 thinking off/on, e.g. qwen3:14b:nothink)
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time

import httpx

import config  # noqa: E402  — puts brain/ on sys.path + owns paths

config.load_secrets()

# Isolate records writes: the eval dispatches tools for real, and the write-path cases below
# call records.log. Point the store at a throwaway COPY of the live DB so the seeded garden is
# present for the read cases but logged observations don't mutate the real records. We override
# the store's module global directly (read by _conn at call time) rather than the env.
import os  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

import records_store  # noqa: E402

_REAL_DB = str(config.DB_PATH)
_EVAL_DB = os.path.join(tempfile.gettempdir(), "hestia_eval.db")
if os.path.exists(_REAL_DB):
    shutil.copy2(_REAL_DB, _EVAL_DB)
records_store.DB_PATH = Path(_EVAL_DB)

import hestia  # noqa: E402  — reuse the brain's real _system_prompt + _request_schemas
import tools  # noqa: E402

OLLAMA = "http://127.0.0.1:11434"
MAX_STEPS = 6
REPEATS = int(os.environ.get("EVAL_REPEATS", "3"))

# Non-Latin scripts that signal the language-drift bug (emoji/° are fine).
_NONLATIN = re.compile(
    r"[฀-๿぀-ヿ㐀-鿿豈-﫿"
    r"Ѐ-ӿ؀-ۿ가-힯ᄀ-ᇿ]"
)
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def is_english(text: str) -> bool:
    return not _NONLATIN.search(text or "")


def run_once(model: str, user_text: str, think: bool | None) -> tuple[str, list[str]]:
    """The brain's loop, sync + parametrized. Returns (final_text, tools_called).
    Uses the brain's REAL prompt assembly + per-request tool scoping so the eval reflects
    what production actually sends (skill block, garden inventory, focused records, etc.)."""
    convo = [{"role": "system", "content": hestia._system_prompt(user_text)},
             {"role": "user", "content": user_text}]
    schemas = hestia._request_schemas(user_text)
    used: list[str] = []
    for _ in range(MAX_STEPS):
        body = {"model": model, "messages": convo, "tools": schemas,
                "stream": False, "options": {"temperature": 0.3}}
        if think is not None:
            body["think"] = think
        r = httpx.post(f"{OLLAMA}/api/chat", json=body, timeout=300)
        r.raise_for_status()
        msg = r.json()["message"]
        calls = msg.get("tool_calls") or []
        if not calls:
            return _THINK.sub("", msg.get("content", "") or "").strip(), used
        convo.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": calls})
        for c in calls:
            fn = c.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments", {}) or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:  # noqa: BLE001
                    args = {}
            used.append(name)
            convo.append({"role": "tool", "tool_name": name, "content": str(tools.dispatch(name, args))})
    return "[MAX_STEPS — never finished]", used


# ── ground truth, snapshotted ONCE so every model is judged against the same reality ──────
def _plant_tokens(name: str) -> set[str]:
    """Significant plant nouns from a soil-sensor friendly name, e.g.
    'Potatoes & Snow Peas Soil Moisture' -> {'potatoes', 'peas'}."""
    drop = {"and", "the", "sweet", "snow", "soil", "moisture", "pepper", "peppers"}
    return {w for w in re.split(r"[^a-z]+", name.lower()) if len(w) > 2 and w not in drop}


def _ground_truth() -> dict:
    """Snapshot live + DB state up front. The live cases (soil, light) are then judged
    against THIS instant, identically for every model, instead of against values hard-coded
    weeks ago (which is what produced the 58%-vs-110/110 contamination)."""
    g: dict = {}
    # Kitchen light — current real state, so the check tracks reality, not a stale fixture.
    try:
        st = tools.dispatch("home", {"action": "get_state", "entity_id": "light.kitchen"})
    except Exception as e:  # noqa: BLE001
        st = f"(error: {e})"
    sl = st.lower()
    g["light_state"] = st
    g["light_on"] = ("is on" in sl) and ("is off" not in sl)
    g["light_judgeable"] = bool(sl.strip()) and "error" not in sl and "unavailable" not in sl
    # Driest bed(s) — derived live from the soil block, including ties within 1%.
    readings = []
    for ln in (tools.home.soil_catalog() or "").splitlines():
        m = re.search(r"—\s*(.+?)\s+Soil Moisture\s*\[([\d.]+)", ln)
        if m:
            readings.append((m.group(1), float(m.group(2))))
    driest: set[str] = set()
    if readings:
        lo = min(v for _, v in readings)
        for nm, v in readings:
            if v <= lo + 1.0:
                driest |= _plant_tokens(nm)
    g["driest_tokens"] = driest
    g["soil_judgeable"] = bool(readings)
    # Real garden vocabulary, for a self-grounding confabulation check.
    ov = records_store.garden_overview().lower()
    g["garden_vocab"] = {w for w in re.split(r"[^a-z]+", ov) if len(w) > 4}
    return g


def _check_light(r: str, g: dict) -> bool:
    rl = r.lower()
    if g["light_on"]:
        return bool(re.search(r"\bon\b", rl)) and "not on" not in rl
    return bool(re.search(r"\boff\b", rl)) or "not on" in rl


def _check_driest(r: str, g: dict) -> bool:
    return any(tok in r.lower() for tok in g["driest_tokens"])


def _check_summary(r: str, g: dict) -> bool:
    """No confabulation: must name >=2 plants that are really in the garden, and none of the
    common decoys that are NOT in this garden (the classic fabrication tell)."""
    rl = r.lower()
    decoys = [d for d in ("lettuce", "basil", "marigold", "spinach", "radish")
              if d not in g["garden_vocab"]]
    if any(d in rl for d in decoys):
        return False
    return sum(1 for w in g["garden_vocab"] if w in rl) >= 2


# Each case: prompt + check(resp, tools_used, ground) -> bool. bucket 'live' depends on live
# HA/sensor state (judged against the start-of-run snapshot, reported separately); 'stable' is
# the DB/selection set that forms the headline correctness for a model comparison.
CASES = [
    {"name": "garden (driest bed, live soil)", "bucket": "live",
     "prompt": "Which garden beds are driest right now?",
     "check": lambda r, t, g: _check_driest(r, g)},
    {"name": "light state (live HA)", "bucket": "live",
     "prompt": "Is the kitchen light on?",
     "check": lambda r, t, g: _check_light(r, g)},
    {"name": "weather (tool selection)", "bucket": "stable",
     "prompt": "Is any rain coming in the next few days?",
     "check": lambda r, t, g: "weather" in t},
    {"name": "garden inventory (Bed 1, exact)", "bucket": "stable",
     "prompt": "What exactly is planted in Bed 1?",
     "check": lambda r, t, g: "artichoke" in r.lower()
              and not any(f in r.lower() for f in ("lettuce", "basil", "spinach", "radish"))},
    {"name": "garden lookup (figs, data-driven detect)", "bucket": "stable",
     "prompt": "Do I have any fig trees, and where are they?",
     "check": lambda r, t, g: "fig" in r.lower() and "porch" in r.lower()},
    {"name": "garden lookup (blueberry Guild, not Patch)", "bucket": "stable",
     "prompt": "Where are my blueberries planted?",
     "check": lambda r, t, g: "guild" in r.lower() and "patch" not in r.lower()},
    {"name": "garden open summary (no confabulation)", "bucket": "stable",
     "prompt": "What's planted in my garden? Give me highlights by area.",
     "check": lambda r, t, g: _check_summary(r, g)},
    {"name": "garden question (reads, does NOT log)", "bucket": "stable",
     "prompt": "What's in the tomato bed?",
     "check": lambda r, t, g: "tomato" in r.lower() and "records" not in t},
    {"name": "garden observation (logs via records)", "bucket": "stable",
     "prompt": "I thinned the hot peppers today, pulled a few of the weaker ones.",
     "check": lambda r, t, g: "records" in t},
]


def _skip_live(case: dict, g: dict) -> str | None:
    """Reason to skip a live case whose ground truth couldn't be read this run, else None."""
    if case["bucket"] != "live":
        return None
    if "soil" in case["name"] and not g["soil_judgeable"]:
        return "soil sensors unreadable"
    if "light" in case["name"] and not g["light_judgeable"]:
        return "HA unreachable"
    return None


def eval_model(model: str, think: bool | None, ground: dict, sink: list[dict]) -> None:
    label = f"{model}" + ("  [think]" if think else "")
    print(f"\n{'='*64}\nMODEL: {label}\n{'='*64}")
    stable, live, eng_total, eng_ok, lat = [], [], 0, 0, []
    for case in CASES:
        skip = _skip_live(case, ground)
        if skip:
            print(f"  ⏭  {case['name']:<40} skipped ({skip})")
            continue
        passes = 0
        for i in range(REPEATS):
            t0 = time.time()
            try:
                resp, used = run_once(model, case["prompt"], think)
            except Exception as e:  # noqa: BLE001
                resp, used = f"[ERROR: {e}]", []
            lat.append(time.time() - t0)
            eng_total += 1
            if is_english(resp):
                eng_ok += 1
            try:
                ok = bool(case["check"](resp, used, ground))
            except Exception:  # noqa: BLE001
                ok = False
            if ok:
                passes += 1
            sink.append({"model": model, "case": case["name"], "bucket": case["bucket"],
                         "repeat": i, "pass": ok, "tools": used, "resp": (resp or "")[:500]})
        rate = passes / REPEATS
        (live if case["bucket"] == "live" else stable).append(rate)
        flag = "✅" if rate == 1 else ("⚠️ " if rate > 0 else "❌")
        print(f"  {flag} {case['name']:<40} {passes}/{REPEATS}  [{case['bucket']}]")
    s = sum(stable) / len(stable) if stable else 0.0
    lv = sum(live) / len(live) if live else 0.0
    print(f"  ── STABLE {s*100:.0f}% ({len(stable)} cases)   live/advisory {lv*100:.0f}% "
          f"({len(live)} cases)   English {eng_ok}/{eng_total} "
          f"({eng_ok/eng_total*100:.0f}%)   avg {sum(lat)/len(lat):.1f}s")
    # Free VRAM before the next model (5080 can't hold two 14Bs at once).
    subprocess.run(["ollama", "stop", model], capture_output=True)


def main() -> None:
    specs = sys.argv[1:] or ["qwen2.5:14b"]
    ground = _ground_truth()
    print(f"[ground] light_on={ground['light_on']} (judgeable={ground['light_judgeable']})  "
          f"driest={sorted(ground['driest_tokens'])}  vocab={len(ground['garden_vocab'])} words")
    sink: list[dict] = []
    for spec in specs:
        if spec.endswith(":nothink"):
            eval_model(spec[:-8], False, ground, sink)
        elif spec.endswith(":think"):
            eval_model(spec[:-6], True, ground, sink)
        else:
            eval_model(spec, None, ground, sink)
    out_dir = Path(__file__).resolve().parent / "eval_results"
    out_dir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = out_dir / f"eval-{stamp}.json"
    serializable_ground = {k: (sorted(v) if isinstance(v, set) else v) for k, v in ground.items()}
    out.write_text(json.dumps({"ground": serializable_ground, "rows": sink}, indent=2))
    print(f"\nraw -> {out}")


if __name__ == "__main__":
    main()
