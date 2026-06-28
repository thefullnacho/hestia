#!/usr/bin/env python3
"""Generate the tool-firing SFT dataset for Hestia — see SFT_DATASET.md.

Emits JSONL (OpenAI messages + `tools`) of (system, user) -> assistant tool_call OR
`final`, built from TEMPLATED gold over GENERIC entities — never the owner's real garden: we
train the tool *shape*, not the furniture. Verifiable by construction: the target tool +
args come deterministically from the intent, so no grading is needed.

Strata: actuation, passive-capture, recall, reminders, media/live, boundary-contrast,
hard-negatives(final), ambiguity(clarify). Heavy weight on contrast + negatives — that's
what stops the boundary whack-a-mole and the over-firing. The real tool schemas
(`tools.SCHEMAS`) ride in every record, so we never drift from what production serves.

Usage:
  uv run --project brain python brain/sft_gen.py --per-intent 8 \
      --out brain/sft_data/toolfire.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import config  # noqa: E402 — puts brain/ on sys.path + owns paths

config.load_secrets()

import tools  # noqa: E402 — real tool schemas, single source of truth

SCHEMAS = tools.SCHEMAS

# Exact eval prompts — never emit these, so eval_keymatch/eval_models stay a clean test set.
EVAL_BLOCKLIST = {
    "remind me to bring an umbrella tomorrow at 7am.",
    "set a reminder to check the oven in 20 minutes.",
    "remember that the good coffee is the bag with the orange label.",
    "which coffee did i say was the good one?",
    "we got a new puppy, her name is biscuit — she's a corgi.",
    "i vaccinated the dogs today.",
    "when did i last see a deer in the yard?",
    "is any rain coming in the next few days?",
    "what happened with the fed meeting today?",
    "grab the latest season of severance for me.",
    "turn off the kitchen lights.",
    "which garden beds are driest right now?",
    "is the kitchen light on?",
    "what's planted in my garden? give me highlights by area.",
    "what's in the tomato bed?",
    "i thinned the hot peppers today, pulled a few of the weaker ones.",
}

# ── GENERIC entities (deliberately NOT the real homestead) ────────────────────────────────
ROOMS = ["living room", "office", "bedroom", "hallway", "garage", "basement", "porch", "den"]
LAMPS = ["desk lamp", "floor lamp", "reading light", "corner lamp"]
GENERIC_BEDS = ["bed 3", "the north bed", "raised bed 2", "the side plot", "the back row"]
GENERIC_PLANTS = ["kale", "garlic", "bush beans", "summer squash", "leeks", "chard"]
PETS = [("dog", "Rex"), ("cat", "Mittens"), ("dog", "Scout"), ("cat", "Pepper")]
ANIMALS = ["fox", "rabbit", "hawk", "raccoon", "deer", "owl"]
SHOWS = ["The Bear", "Andor", "Foundation", "Shogun", "Slow Horses"]
MOVIES = ["Oppenheimer", "Sinners", "The Brutalist", "Conclave"]
SONGS = ["Black Hole Sun", "Teardrop", "Pyramid Song", "Karma Police"]
TIMES = ["7am", "9pm", "tomorrow at 7", "tonight", "tomorrow morning", "at 5:30"]
TEMPS = ["68", "70", "65", "72"]

SYNONYM = {  # a tiny paraphrase engine: surface variety without changing intent
    "turn_off": ["turn off", "switch off", "kill", "shut off", "cut"],
    "turn_on": ["turn on", "switch on", "put on", "flip on"],
}


def paraphrase(key: str) -> str:
    return random.choice(SYNONYM[key])


# ── synthetic context blocks (so read-from-context finals are grounded) ───────────────────
BASE_SYSTEM = (
    "You are Hestia, a calm, capable home assistant. You have tools to control the house, "
    "manage media, set reminders, and keep durable memory and structured records. When the "
    "user states an intent that a tool serves, CALL THE TOOL — do not just reply. When the "
    "user states a fact about an entity (a pet, place, asset) or reports that something "
    "happened, record it even if they didn't ask. Answer state questions from the context "
    "blocks below without calling a tool. If a target or intent is unclear, ask. Reply in "
    "English."
)


def light_block(states: dict[str, str]) -> str:
    lines = "\n".join(f"  light.{r.replace(' ', '_')}_lights — {r.title()} [{s}]"
                      for r, s in states.items())
    return f"Lights you can control (exact entity_id):\n{lines}"


def garden_block(rows: list[tuple[str, list[str]]]) -> str:
    lines = "\n".join(f"  - {bed}: {', '.join(pl)}" for bed, pl in rows)
    return f"The garden — what's planted where:\n{lines}"


def sys_with(*blocks: str) -> str:
    return BASE_SYSTEM + ("\n\n" + "\n\n".join(b for b in blocks if b) if any(blocks) else "")


# ── record assembly ───────────────────────────────────────────────────────────────────────
def _call(name: str, args: dict) -> dict:
    return {"type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def ex_call(system: str, user: str, name: str, args: dict, stratum: str,
            heldout: bool = False, steps: list[dict] | None = None) -> dict:
    """A tool-call example. `steps` carries an optional multi-step tail:
    [tool_result_str, final_text] -> assistant calls, tool returns, assistant finalizes."""
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if steps:
        result, final = steps
        msgs.append({"role": "assistant", "content": None, "tool_calls": [_call(name, args)]})
        msgs.append({"role": "tool", "tool_call_id": f"call_{name}", "content": result})
        msgs.append({"role": "assistant", "content": final})
    else:
        msgs.append({"role": "assistant", "content": None, "tool_calls": [_call(name, args)]})
    return {"messages": msgs, "tools": SCHEMAS,
            "meta": {"stratum": stratum, "tool": name, "heldout": heldout,
                     "multistep": bool(steps)}}


def ex_final(system: str, user: str, text: str, stratum: str) -> dict:
    return {"messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user},
                         {"role": "assistant", "content": text}],
            "tools": SCHEMAS,
            "meta": {"stratum": stratum, "tool": "final", "heldout": False, "multistep": False}}


# ── strata generators ─────────────────────────────────────────────────────────────────────
def gen_actuation(n: int) -> list[dict]:
    out = []
    for _ in range(n):
        r = random.choice(ROOMS)
        ent = f"light.{r.replace(' ', '_')}_lights"
        out.append(ex_call(sys_with(), f"{paraphrase('turn_off')} the {r} lights",
                           "home", {"action": "turn_off", "entity_id": ent}, "actuation"))
        out.append(ex_call(sys_with(), f"{paraphrase('turn_on')} the {r} lights",
                           "home", {"action": "turn_on", "entity_id": ent}, "actuation"))
        out.append(ex_call(sys_with(), f"set the thermostat to {random.choice(TEMPS)}",
                           "home", {"action": "set", "entity_id": "climate.thermostat",
                                    "value": random.choice(TEMPS)}, "actuation"))
    # held-out action: open/close (never trained — used to measure generalization)
    for _ in range(max(2, n // 2)):
        out.append(ex_call(sys_with(), "open the garage door", "home",
                           {"action": "open", "entity_id": "cover.garage_door"},
                           "actuation", heldout=True))
        out.append(ex_call(sys_with(), "close the garage door", "home",
                           {"action": "close", "entity_id": "cover.garage_door"},
                           "actuation", heldout=True))
    return out


def gen_passive_capture(n: int) -> list[dict]:
    out = []
    for _ in range(n):
        kind, name = random.choice(PETS)
        out.append(ex_call(sys_with(), f"we got a new {kind}, her name is {name}",
                           "records", {"action": "remember", "kind": "pet", "name": name},
                           "passive-capture"))
        pet = random.choice(PETS)[1]
        out.append(ex_call(sys_with(), f"I gave {pet} her shots today",
                           "records", {"action": "log", "kind": "health", "subject": pet,
                                       "did": "vaccinated"}, "passive-capture"))
        animal, place = random.choice(ANIMALS), random.choice(["yard", "field", "woods", "fence line"])
        out.append(ex_call(sys_with(), f"saw a {animal} by the {place} this morning",
                           "records", {"action": "log", "kind": "sighting", "subject": animal,
                                       "did": "observed", "location": place}, "passive-capture"))
        bed = random.choice(GENERIC_BEDS)
        out.append(ex_call(sys_with(), f"I mulched {bed} today",
                           "records", {"action": "log", "kind": "chore", "subject": bed,
                                       "did": "mulched"}, "passive-capture"))
    return out


def gen_recall(n: int) -> list[dict]:
    out = []
    for _ in range(n):
        thing = random.choice(["tea", "olive oil", "paint", "router password"])
        out.append(ex_call(sys_with(), f"what did I say was the good {thing}?",
                           "memory", {"op": "recall", "content": thing}, "recall"))
        pet = random.choice(PETS)[1]
        out.append(ex_call(sys_with(), f"when did I last worm {pet}?",
                           "records", {"action": "recent", "subject": pet}, "recall"))
    return out


def gen_reminders(n: int) -> list[dict]:
    out, tasks = [], ["take the bread out", "move the sprinkler", "call the vet",
                      "flip the laundry", "bring in the cushions"]
    for _ in range(n):
        t, w = random.choice(tasks), random.choice(TIMES)
        out.append(ex_call(sys_with(), f"remind me to {t} {w}",
                           "reminder", {"action": "create", "text": t, "when": w}, "reminders"))
    return out


_WEATHER_Q = ["what's the forecast, any frost coming?", "is it going to rain this week?",
              "how hot does it get tomorrow?", "any storms in the next few days?",
              "will it freeze tonight?", "what's the weather looking like for the weekend?"]
_SEARCH_Q = ["what's the latest on the rail strike?", "who won the game last night?",
             "what's the news on interest rates?", "when does the hardware store close today?",
             "what's a good price for a cord of firewood right now?"]
_STATUS_Q = ["is everything on the stack healthy?", "are all the services up?",
             "is the media stack running ok?", "give me a status check", "is anything down?"]


def gen_media_live(n: int) -> list[dict]:
    out = []
    for _ in range(n):
        out.append(ex_call(sys_with(), f"download the latest season of {random.choice(SHOWS)}",
                           "media", {"action": "download", "query": random.choice(SHOWS)}, "media/live"))
        out.append(ex_call(sys_with(), f"find the movie {random.choice(MOVIES)}",
                           "media", {"action": "search", "query": random.choice(MOVIES)}, "media/live"))
        out.append(ex_call(sys_with(), f"play the song {random.choice(SONGS)}",
                           "media", {"action": "download", "kind": "music",
                                     "query": random.choice(SONGS)}, "media/live"))
        out.append(ex_call(sys_with(), random.choice(_WEATHER_Q), "weather", {"days": 7}, "media/live"))
        q = random.choice(_SEARCH_Q)
        out.append(ex_call(sys_with(), q, "search", {"query": q}, "media/live"))
        out.append(ex_call(sys_with(), random.choice(_STATUS_Q), "status", {}, "media/live"))
    return out


def gen_multistep(n: int) -> list[dict]:
    """call -> tool result -> assistant FINALIZES (stops). Trains the loop to terminate
    instead of re-querying (the 8B looped `home` 6x to MAX_STEPS)."""
    out = []
    for _ in range(n):
        show = random.choice(SHOWS)
        out.append(ex_call(sys_with(), "what's downloading right now?", "media",
                           {"action": "status"}, "multistep",
                           steps=[f"2 active: {show} S3 (45%), a movie (queued)",
                                  f"Two going — {show} S3 at 45%, and a movie is queued."]))
        out.append(ex_call(sys_with(), random.choice(_STATUS_Q), "status", {}, "multistep",
                           steps=["All green: Plex, Sonarr, Radarr, qBittorrent, HA all up.",
                                  "Everything's healthy — Plex, the *arr stack, downloads, "
                                  "and Home Assistant are all up."]))
        out.append(ex_call(sys_with(), random.choice(_WEATHER_Q), "weather", {"days": 3}, "multistep",
                           steps=["Tonight 31F (frost likely), tomorrow 44/29, dry through Fri.",
                                  "Yes — frost is likely tonight at 31F. Cover anything tender. "
                                  "It stays dry through Friday."]))
    return out


def gen_read_context(n: int) -> list[dict]:
    """Answer state from the injected block, no tool — including the aggregation the live
    'driest bed' case needs (pick the minimum from the readings, don't tool-loop)."""
    out = []
    for _ in range(n):
        beds = random.sample(GENERIC_BEDS, 3)
        vals = random.sample(range(8, 60), 3)
        lo = beds[vals.index(min(vals))]
        block = "Soil moisture (live):\n" + "\n".join(
            f"  {b} [{v}%]" for b, v in zip(beds, vals))
        out.append(ex_final(sys_with(block), "which bed is driest right now?",
                            f"{lo.capitalize()} is the driest at {min(vals)}%.", "read-context"))
    return out


def gen_boundary(n: int) -> list[dict]:
    """Minimal pairs sharing surface tokens — the anti-whack-a-mole core."""
    out = []
    for _ in range(n):
        # "remember"/X: preference vs reminder vs entity
        out.append(ex_call(sys_with(), "remember the spare key is under the blue pot",
                           "memory", {"op": "write", "content": "spare key is under the blue pot"},
                           "boundary"))
        out.append(ex_call(sys_with(), "remind me to water the seedlings at 6pm",
                           "reminder", {"action": "create", "text": "water the seedlings",
                                        "when": "6pm"}, "boundary"))
        kind, name = random.choice(PETS)
        out.append(ex_call(sys_with(), f"we adopted a {kind} and named him {name}",
                           "records", {"action": "remember", "kind": "pet", "name": name},
                           "boundary"))
        # read vs act on a device (state lives in the catalog)
        r = random.choice(ROOMS)
        state = random.choice(["on", "off"])
        lb = light_block({r: state})
        out.append(ex_final(sys_with(lb), f"is the {r} light on?",
                            f"The {r} light is currently {state}.", "boundary"))
        ent = f"light.{r.replace(' ', '_')}_lights"
        out.append(ex_call(sys_with(lb), f"{paraphrase('turn_off')} the {r} light",
                           "home", {"action": "turn_off", "entity_id": ent}, "boundary"))
        # question vs event on a bed (inventory lives in the prompt)
        bed = random.choice(GENERIC_BEDS)
        plants = random.sample(GENERIC_PLANTS, 2)
        gb = garden_block([(bed, plants)])
        out.append(ex_final(sys_with(gb), f"what's in {bed}?",
                            f"{bed.capitalize()} has {plants[0]} and {plants[1]}.", "boundary"))
        out.append(ex_call(sys_with(gb), f"I pulled the weeds from {bed}",
                           "records", {"action": "log", "kind": "chore", "subject": bed,
                                       "did": "weeded"}, "boundary"))
    return out


def gen_negatives(n: int) -> list[dict]:
    out = []
    chit = [("thanks, that's all for now", "Anytime — I'm here if you need anything."),
            ("good morning", "Morning! What can I help you with?"),
            ("you're a lifesaver", "Happy to help."),
            ("what can you do?", "I can run the house, manage media and reminders, and keep "
                                 "track of your garden, pets, and records. What do you need?")]
    for _ in range(n):
        u, a = random.choice(chit)
        out.append(ex_final(sys_with(), u, a, "hard-negative"))
        # read-from-context garden summary (must NOT call records)
        rows = [(b, random.sample(GENERIC_PLANTS, 2)) for b in random.sample(GENERIC_BEDS, 2)]
        gb = garden_block(rows)
        summ = "; ".join(f"{b}: {', '.join(p)}" for b, p in rows)
        out.append(ex_final(sys_with(gb), "what's planted right now?",
                            f"Here's what's in: {summ}.", "hard-negative"))
    return out


def gen_ambiguity(n: int) -> list[dict]:
    out = []
    for _ in range(n):
        out.append(ex_final(sys_with(), "turn it off",
                            "Happy to — which light or device do you mean?", "ambiguity"))
        out.append(ex_final(sys_with(), "remind me",
                            "Sure — what should I remind you about, and when?", "ambiguity"))
        out.append(ex_final(sys_with(), "log that",
                            "What happened, and what's it about?", "ambiguity"))
    return out


# share weights from SFT_DATASET.md (× a base unit set by --per-intent)
PLAN = [
    (gen_actuation, 3), (gen_passive_capture, 3), (gen_recall, 2), (gen_reminders, 2),
    (gen_media_live, 2), (gen_boundary, 5), (gen_negatives, 3), (gen_ambiguity, 1),
    (gen_multistep, 2), (gen_read_context, 2),
]


def build(per_intent: int) -> list[dict]:
    rows: list[dict] = []
    for fn, weight in PLAN:
        rows += fn(per_intent * weight)
    # drop eval-leaking prompts, de-dup exact (system,user,target) triples
    seen, clean = set(), []
    for r in rows:
        user = r["messages"][1]["content"].strip().lower()
        if user in EVAL_BLOCKLIST:
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
    ap.add_argument("--per-intent", type=int, default=8, help="base unit; strata scale by weight")
    ap.add_argument("--out", default="brain/sft_data/toolfire.jsonl")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    random.seed(args.seed)

    rows = build(args.per_intent)
    train = [r for r in rows if not r["meta"]["heldout"]]
    heldout = [r for r in rows if r["meta"]["heldout"]]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in train))
    hp = out.with_suffix(".heldout.jsonl")
    hp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in heldout))

    # summary
    from collections import Counter
    strat = Counter(r["meta"]["stratum"] for r in train)
    tool = Counter(r["meta"]["tool"] for r in train)
    multistep = sum(r["meta"]["multistep"] for r in train)
    total = len(train)
    print(f"train={total}  heldout={len(heldout)}  multistep={multistep}")
    print("by stratum:")
    for k, v in strat.most_common():
        print(f"  {k:<16} {v:>4}  ({v/total*100:.0f}%)")
    print("by tool target:")
    for k, v in tool.most_common():
        print(f"  {k:<10} {v:>4}")
    print(f"\ntrain  -> {out}")
    print(f"heldout-> {hp}  (home open/close — generalization probe)")


if __name__ == "__main__":
    main()
