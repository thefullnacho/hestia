"""Map Home Assistant's labelled intent test sentences onto this eval's tools.

    git clone https://github.com/home-assistant/intents && git -C intents checkout f073cc9
    uv run --no-project --with pyyaml python import_ha_intents.py path/to/intents

Writes ha_intents.jsonl, which build_train.py folds into train.jsonl. The sentences are
Home Assistant's, under CC BY 4.0 (https://github.com/home-assistant/intents, see NOTICE.md);
the mapping onto these tools is ours. Intents for things these tools don't do (fans, covers,
climate, vacuums, the time) become no-call examples, so the caller learns to stay quiet when
the router hands it something outside its tools. Sentences that name a value the test cases
use (an item, a song, a title) are dropped, so the tests keep their held-out vocabulary.
"""
from __future__ import annotations

import json
import re
import subprocess
import zlib
import sys
from collections import Counter
from pathlib import Path

import yaml

HERE = Path(__file__).parent
PINNED = "f073cc925fef0724834c9a4b5d0f135aa29dd9d8"
CAP = {"light_set": 220, "none": 160, "music_control": 120}  # keep HA's biggest intents from swamping the rest
MUSIC_KINDS = {"artist": "artist", "album": "album", "track": "song", "playlist": "playlist",
               "genre": "genre"}
NO_TOOL = {"HassClimateGetTemperature", "HassClimateSetTemperature", "HassClimateSetFanMode",
           "HassFanSetSpeed", "HassLawnMowerDock", "HassLawnMowerStartMowing", "HassSetPosition",
           "HassVacuumCleanArea", "HassVacuumReturnToBase", "HassVacuumStart", "HassBroadcast",
           "HassGetCurrentDate", "HassGetCurrentTime", "HassNevermind", "HassCancelTimer",
           "HassCancelAllTimers", "HassGetState"}
NUM = r"(?:\d+(?:\.\d+)?|an?|one|two|three|four|five|ten|fifteen|twenty|thirty|forty|forty five|sixty|ninety|half an?)"
DURATION = re.compile(rf"\b{NUM}\s*(?:and a half\s*)?(?:hours?|hrs?|minutes?|mins?|seconds?|secs?)"
                      rf"(?:\s*(?:and\s*)?{NUM}\s*(?:minutes?|mins?|seconds?|secs?))*\b", re.I)


def T(tool, **args):
    return {"tool": tool, "args": {k: v for k, v in args.items() if v is not None or k == "target"}}


def lower(v):
    return str(v).strip().lower()


def entity_domains(root: Path) -> dict[str, str]:
    """Entity name -> HA domain, from the shared fixtures (ids look like light.bedroom_lamp)."""
    fx = yaml.safe_load((root / "tests/en/_fixtures.yaml").read_text())
    return {lower(e["name"]): e.get("domain") or e["id"].split(".")[0] for e in fx.get("entities", [])}


def which_list(name) -> str | None:
    n = lower(name or "shopping")
    if any(w in n for w in ("shop", "grocer")):
        return "shopping"
    if any(w in n for w in ("to do", "todo", "to-do", "chore", "task")):
        return "todo"
    return None


def mapped(intent: str, sentence: str, slots: dict, domains: dict) -> tuple[str, dict | None] | None:
    """(our domain, call) for one sentence; call None means no call. None means skip it."""
    s = {k: v for k, v in (slots or {}).items()}
    place = s.get("area") or s.get("floor")
    name = s.get("name")
    dom = s.get("domain") or (domains.get(lower(name)) if name else None)
    if isinstance(dom, list):
        dom = dom[0] if len(dom) == 1 else None
    if intent in ("HassTurnOn", "HassTurnOff"):
        state = "on" if intent == "HassTurnOn" else "off"
        if dom == "scene" and name:
            return "lights", T("scene_activate", name=lower(name))
        if dom == "light":
            if name:
                return "lights", T("light_set", target=lower(name), state=state)
            if place:
                return "lights", T("light_set", target=lower(place), state=state)
            if re.search(r"\b(all|every|house|home|everywhere|whole)\b", sentence, re.I) and "in here" not in sentence:
                return "lights", T("light_set", target="all", state=state)
            return "lights", T("light_set", target=None, state=state)
        if dom:  # a fan, a cover, a lock: a home command these tools can't do
            return "lights", None
        return None
    if intent == "HassLightSet":
        target = lower(name or place) if (name or place) else None
        if "brightness" in s:
            return "lights", T("light_set", target=target, brightness=int(s["brightness"]))
        if "color" in s:
            return "lights", T("light_set", target=target, color=lower(s["color"]))
        return None
    if intent in ("HassShoppingListAddItem", "HassListAddItem"):
        lst = "shopping" if intent == "HassShoppingListAddItem" else which_list(name)
        return ("lists", T("list_add", list=lst, items=[lower(s["item"])])) if lst and "item" in s else None
    if intent in ("HassShoppingListCompleteItem", "HassListCompleteItem", "HassListRemoveItem"):
        lst = "shopping" if intent == "HassShoppingListCompleteItem" else which_list(name)
        return ("lists", T("list_remove", list=lst, items=[lower(s["item"])])) if lst and "item" in s else None
    simple = {"HassMediaPause": "pause", "HassMediaUnpause": "resume", "HassMediaNext": "skip",
              "HassMediaPrevious": "previous"}
    if intent in simple:
        return "music", T("music_control", action=simple[intent])
    if intent == "HassMediaPlayerMute":
        return "music", T("music_control", action="set_volume", level=0)
    if intent == "HassSetVolume" and "volume_level" in s:
        return "music", T("music_control", action="set_volume", level=int(s["volume_level"]))
    if intent == "HassSetVolumeRelative":
        step = s.get("volume_step", "")
        if step in ("up", "down") or isinstance(step, int):
            louder = step == "up" or (isinstance(step, int) and step > 0)
            return "music", T("music_control", action="louder" if louder else "quieter")
        return None
    if intent == "HassMediaSearchAndPlay":
        kind = MUSIC_KINDS.get(lower(s.get("media_class", "")))
        q = s.get("search_query")
        return ("music", T("music_play", query=lower(q), kind=kind)) if kind and q else None
    if intent == "HassGetWeather":
        return None if name else ("weather", T("weather_get", when="now"))
    if intent == "HassStartTimer":
        if s.get("conversation_command"):
            return None  # a delayed command, not a reminder
        m = DURATION.search(sentence)
        if not m:
            return None
        return "alerts", T("reminder_add", when=m.group(0).strip(), text=lower(name) if name else None)
    if intent == "HassTimerStatus":
        return "alerts", T("alerts_read")
    if intent in NO_TOOL:
        if intent == "HassGetState" and dom == "light":
            return "lights", None
        return "none", None
    return None


# HA's list tests all use "apples" and "take out the trash", which the test cases also use.
# The phrasing is the valuable part, so the item is swapped for one from the training vocabulary.
SWAP_ITEMS = ["tortillas", "ground beef", "sour cream", "frozen peas", "crackers", "hot sauce",
              "salad dressing", "hamburger buns", "foil", "cream", "bacon", "ketchup"]
SWAP_CHORES = ["patch the fence", "clean the chicken coop", "drain the hoses", "wash the truck"]


def swap_item(intent: str, sentence: str, slots: dict) -> tuple[str, dict]:
    item = (slots or {}).get("item")
    if "List" not in intent or not item or item.lower() not in sentence.lower():
        return sentence, slots
    pool = SWAP_CHORES if " " in item else SWAP_ITEMS
    new = pool[zlib.crc32(sentence.encode()) % len(pool)]
    return re.sub(re.escape(item), new, sentence, flags=re.I), {**slots, "item": new}


def held_out_values() -> set[str]:
    """Item, song, title and reminder values the test cases use; rooms and times are shared."""
    vals = set()
    for line in (HERE / "cases.jsonl").read_text().splitlines():
        for call in json.loads(line)["calls"]:
            for k, v in call["args"].items():
                if k in ("item", "items", "query", "title", "text", "tool", "name"):
                    for x in (v if isinstance(v, list) else [v]):
                        if isinstance(x, str) and len(x) > 2:
                            vals.add(x.lower())
    return vals


def main():
    root = Path(sys.argv[1])
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if head != PINNED:
        sys.exit(f"intents checkout is at {head or '?'}; check out {PINNED} so the set is reproducible")
    domains = entity_domains(root)
    held = held_out_values()
    rows, seen, why = [], set(), Counter()
    for f in sorted((root / "tests/en").glob("*/*.yaml")):
        intent = f.parent.name
        doc = yaml.safe_load(f.read_text()) or {}
        local = {lower(e["name"]): e.get("domain") for e in doc.get("entities", []) if "name" in e}
        for test in doc.get("tests", []):
            for sentence in test.get("sentences", []):
                if "{" in sentence or sentence.lower() in seen:
                    continue
                sentence, slots = swap_item(intent, sentence, test.get("slots"))
                got = mapped(intent, sentence, slots, {**domains, **local})
                if got is None:
                    why[f"skip {intent}"] += 1
                    continue
                low = sentence.lower()
                if any(re.search(rf"\b{re.escape(v)}\b", low) for v in held):
                    why["held-out vocabulary"] += 1
                    continue
                seen.add(low)
                domain, call = got
                rows.append({"domain": domain, "text": sentence, "expect": "call" if call else "none",
                             "calls": [call] if call else [], "template": f"ha:{intent}", "source": "ha-intents"})
    rng_rows, out, used = sorted(rows, key=lambda r: zlib.crc32(r["text"].encode())), [], Counter()
    for r in rng_rows:
        key = r["calls"][0]["tool"] if r["calls"] else "none"
        if used[key] >= CAP.get(key, 10**9):
            why[f"cap {key}"] += 1
            continue
        used[key] += 1
        out.append(r)
    (HERE / "ha_intents.jsonl").write_text("".join(json.dumps(r) + "\n" for r in out))
    print(f"{len(out)} sentences mapped:", dict(sorted(used.items())))
    print("not used:", dict(why.most_common()))


if __name__ == "__main__":
    main()
