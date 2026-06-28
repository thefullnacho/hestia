#!/usr/bin/env python3
"""v2 tool-firing SFT data — BESPOKE, real-prompt fidelity. See SFT_DATASET.md.

v1's miss: it trained under a short synthetic system prompt, but production sends the real
8358-char `hestia._system_prompt` + a live light catalog. Catalog-independent agency
(records.log) transferred; catalog-dependent agency (home actuation) did NOT. v2 fixes the
fidelity: EVERY example is rendered with the real `_system_prompt` + real `_request_schemas`
+ real entity_ids — i.e. trained on exactly what the brain will see at inference.

This is deliberately house-specific (the owner's lights, beds) — the experiment is whether a 4B
*can* be SFT'd to fire under the real prompt at all. If yes, the bespoke-per-house path is
validated; the agnostic question (vary catalogs, keep structure) is a separate follow-up.

Held-out: the OUTSIDE lights are never trained — the within-house generalization probe
(does home-firing learned on kitchen/living/dining carry to a device it never saw?).

Run on the brain box (needs HA reachable for the catalog):
  uv run --project brain python brain/sft_gen_v2.py --out brain/sft_data/toolfire_v2.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

import config  # noqa: E402

config.load_secrets()

import hestia          # noqa: E402 — the REAL prompt + schema assembly
import records_store   # noqa: E402
import tools           # noqa: E402

EVAL_BLOCKLIST = {  # never emit the exact eval prompts
    "turn off the kitchen lights.", "is the kitchen light on?",
    "which garden beds are driest right now?", "what's in the tomato bed?",
    "i thinned the hot peppers today, pulled a few of the weaker ones.",
    "remember that the good coffee is the bag with the orange label.",
    "which coffee did i say was the good one?", "i vaccinated the dogs today.",
    "we got a new puppy, her name is biscuit — she's a corgi.",
}


def sys_tools(user: str) -> tuple[str, list]:
    """The EXACT system prompt + scoped tools production sends for this request."""
    return hestia._system_prompt(user), hestia._request_schemas(user)


def _call(name: str, args: dict) -> dict:
    return {"type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def ex_call(user: str, name: str, args: dict, stratum: str, heldout: bool = False) -> dict:
    sp, sch = sys_tools(user)
    return {"messages": [{"role": "system", "content": sp},
                         {"role": "user", "content": user},
                         {"role": "assistant", "content": None, "tool_calls": [_call(name, args)]}],
            "tools": sch,
            "meta": {"stratum": stratum, "tool": name, "heldout": heldout}}


def ex_final(user: str, text: str, stratum: str) -> dict:
    sp, sch = sys_tools(user)
    return {"messages": [{"role": "system", "content": sp},
                         {"role": "user", "content": user},
                         {"role": "assistant", "content": text}],
            "tools": sch,
            "meta": {"stratum": stratum, "tool": "final", "heldout": False}}


# ── real grounding (parsed from the live catalog) ─────────────────────────────────────────
def real_lights() -> dict[str, dict]:
    """room word -> {entity, state} from the live light catalog (group entities only)."""
    rooms: dict[str, dict] = {}
    for ln in tools.home.catalog().splitlines():
        m = re.search(r"(light\.light_(\w+?)_lights)\s+—.*\[(\w+)\]", ln)
        if m:
            ent, word, state = m.group(1), m.group(2).replace("_", " "), m.group(3)
            rooms[word] = {"entity": ent, "state": state}
    return rooms


ROOMS = real_lights()                       # e.g. {"kitchen": {...}, "living room": {...}, ...}
HELDOUT_ROOM = "outside"                     # never trained — generalization probe
PETS = [("dog", "Rex"), ("cat", "Mittens"), ("dog", "Scout")]
ANIMALS = ["fox", "rabbit", "hawk", "raccoon", "owl", "turkey"]
SHOWS = ["The Bear", "Andor", "Shogun", "Slow Horses"]
TIMES = ["7am", "9pm", "tomorrow at 7", "tonight", "tomorrow morning", "at 5:30"]
OFF = ["turn off", "switch off", "kill", "shut off", "cut"]
ON = ["turn on", "switch on", "put on", "flip on"]


ACT_TMPL = [
    "{v} the {r} lights", "{v} the lights in the {r}", "can you {v} the {r} lights",
    "{v} the {r} light", "please {v} the {r} lights", "{v} the {r}", "go {v} the {r} lights",
]


def gen_actuation(_: int) -> list[dict]:
    """Enumerate templates × verbs × rooms — bespoke means few devices, so we get coverage
    from phrasing breadth, not fake entities. Outside is held out wholesale."""
    out = []
    for word, info in ROOMS.items():
        held = word == HELDOUT_ROOM
        for tmpl in ACT_TMPL:
            for v in OFF:
                out.append(ex_call(tmpl.format(v=v, r=word), "home",
                                   {"action": "turn_off", "entity_id": info["entity"]}, "actuation", held))
            for v in ON:
                out.append(ex_call(tmpl.format(v=v, r=word), "home",
                                   {"action": "turn_on", "entity_id": info["entity"]}, "actuation", held))
    return out


def gen_passive_capture(n: int) -> list[dict]:
    out = []
    for _ in range(n):
        kind, name = random.choice(PETS)
        out.append(ex_call(f"we got a new {kind}, her name is {name}", "records",
                           {"action": "remember", "kind": "pet", "name": name}, "passive-capture"))
        pet = random.choice(PETS)[1]
        out.append(ex_call(f"I gave {pet} her shots today", "records",
                           {"action": "log", "kind": "health", "subject": pet, "did": "vaccinated"},
                           "passive-capture"))
        a, place = random.choice(ANIMALS), random.choice(["yard", "field", "woods", "fence line"])
        out.append(ex_call(f"saw a {a} by the {place} this morning", "records",
                           {"action": "log", "kind": "sighting", "subject": a, "did": "observed",
                            "location": place}, "passive-capture"))
    return out


def gen_recall(n: int) -> list[dict]:
    out = []
    for _ in range(n):
        thing = random.choice(["tea", "olive oil", "paint", "wifi password"])
        out.append(ex_call(f"what did I say was the good {thing}?", "memory",
                           {"op": "recall", "content": thing}, "recall"))
        pet = random.choice(PETS)[1]
        out.append(ex_call(f"when did I last worm {pet}?", "records",
                           {"action": "recent", "subject": pet}, "recall"))
    return out


def gen_reminders(n: int) -> list[dict]:
    tasks = ["take the bread out", "move the sprinkler", "call the vet", "flip the laundry"]
    return [ex_call(f"remind me to {random.choice(tasks)} {random.choice(TIMES)}", "reminder",
                    {"action": "create", "text": random.choice(tasks), "when": random.choice(TIMES)},
                    "reminders") for _ in range(n)]


def gen_media(n: int) -> list[dict]:
    out = []
    for _ in range(n):
        s = random.choice(SHOWS)
        out.append(ex_call(f"download the latest season of {s}", "media",
                           {"action": "download", "query": s}, "media"))
        out.append(ex_call("what's downloading right now?", "media", {"action": "status"}, "media"))
    return out


def gen_boundary(reps: int) -> list[dict]:
    """read-vs-act on a REAL room (state lives in the injected catalog) + reminder/memory split."""
    out = []
    actable = [(w, i) for w, i in ROOMS.items() if w != HELDOUT_ROOM]
    for _ in range(reps):
        word, info = random.choice(actable)
        out.append(ex_final(f"is the {word} light on?",
                            f"The {word} lights are {info['state']}.", "boundary"))
        out.append(ex_call(f"{random.choice(OFF)} the {word} light", "home",
                           {"action": "turn_off", "entity_id": info["entity"]}, "boundary"))
        out.append(ex_call("remind me to water the seedlings at 6pm", "reminder",
                           {"action": "create", "text": "water the seedlings", "when": "6pm"}, "boundary"))
        out.append(ex_call("remember the spare key is under the blue pot", "memory",
                           {"op": "write", "content": "spare key is under the blue pot"}, "boundary"))
    return out


def gen_negatives(_: int) -> list[dict]:
    """Enumerate — the over-firing guard. Chitchat / acknowledgements that must NOT fire a tool."""
    chit = [("thanks, that's all for now", "Anytime — I'm here if you need anything."),
            ("good morning", "Morning. What can I help you with?"),
            ("you're a lifesaver", "Happy to help."),
            ("thanks", "You're welcome."),
            ("never mind", "No problem."),
            ("good night", "Good night — rest well."),
            ("how's it going?", "All good here and keeping an eye on the house. What do you need?"),
            ("you're awesome", "Kind of you to say. What can I do?"),
            ("ok cool", "Anything else?"),
            ("what can you do?", "I can run the lights, manage media and reminders, and keep track "
                                 "of your garden, pets, and records. What do you need?")]
    return [ex_final(u, a, "hard-negative") for u, a in chit]


def gen_ambiguity(n: int) -> list[dict]:
    amb = [("turn it off", "Happy to — which light do you mean?"),
           ("remind me", "Sure — what should I remind you about, and when?"),
           ("log that", "What happened, and what's it about?")]
    return [ex_final(*random.choice(amb), "ambiguity") for _ in range(n)]


def _bed_plantings() -> dict[str, str]:
    out = {}
    for ln in records_store.garden_overview().splitlines():
        m = re.match(r"\s*- ([\w &]+?)(?:\s*\[has soil sensor\])?:\s*(.+)", ln)
        if m and m.group(2).strip():
            out[m.group(1).strip()] = m.group(2).strip()
    return out


BEDS = _bed_plantings()


def gen_read_context(_: int) -> list[dict]:
    """Grounded over-firing guard: garden questions answered FROM the injected context, no tool.
    The real _system_prompt injects the garden for these, so the target is to read it, not fire."""
    out = []
    for bed, plants in list(BEDS.items())[:22]:
        out.append(ex_final(f"what's planted in {bed}?", f"{bed} has {plants}.", "read-context"))
    return out


PLAN = [(gen_actuation, 0), (gen_passive_capture, 16), (gen_recall, 12), (gen_reminders, 18),
        (gen_media, 10), (gen_boundary, 16), (gen_negatives, 0), (gen_ambiguity, 12),
        (gen_read_context, 0)]
ACTUATION_CAP = 90  # keep home-firing ~40% of train so the model fires without OVER-firing


def build() -> list[dict]:
    rows: list[dict] = []
    for fn, w in PLAN:
        rows += fn(w)
    # cap trained actuation so the dataset isn't home-dominated (over-firing guard)
    act = [r for r in rows if r["meta"]["stratum"] == "actuation" and not r["meta"]["heldout"]]
    rest = [r for r in rows if not (r["meta"]["stratum"] == "actuation" and not r["meta"]["heldout"])]
    random.shuffle(act)
    rows = rest + act[:ACTUATION_CAP]
    seen, clean = set(), []
    for r in rows:
        u = r["messages"][1]["content"].strip().lower()
        if u in EVAL_BLOCKLIST:
            continue
        key = (r["messages"][1]["content"], json.dumps(r["messages"][-1], sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        clean.append(r)
    random.shuffle(clean)
    return clean


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="brain/sft_data/toolfire_v2.jsonl")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    random.seed(args.seed)
    if not ROOMS:
        raise SystemExit("no lights parsed from the catalog — is HA reachable?")
    print(f"grounding: rooms={list(ROOMS)} (held-out={HELDOUT_ROOM})")

    rows = build()
    train = [r for r in rows if not r["meta"]["heldout"]]
    held = [r for r in rows if r["meta"]["heldout"]]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in train))
    out.with_suffix(".heldout.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in held))

    from collections import Counter
    strat = Counter(r["meta"]["stratum"] for r in train)
    tool = Counter(r["meta"]["tool"] for r in train)
    print(f"train={len(train)}  heldout={len(held)} (outside-lights probe)")
    for k, v in strat.most_common():
        print(f"  {k:<16}{v:>4}")
    print("tools:", dict(tool))
    print(f"train  -> {out}")


if __name__ == "__main__":
    main()
