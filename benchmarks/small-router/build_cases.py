"""Builds cases.jsonl and REVIEW.md for the small-model router eval from the lists below.

Each case is one utterance and what a reliable device must do with it:
  call  the expected tool call(s); `accept` holds other answers that also pass
  ask   no call, a clarifying question instead
  none  no call at all

Scoring (see REVIEW.md): free-text args pass when every expected word appears in the model's
value (so "dreams fleetwood mac" passes for "Dreams by Fleetwood Mac"); enum and number args
must match exactly; args not listed in the expectation are not checked, and an arg expected as null must be left
out (light_set's target is left out when no room is named: code fills in the device's room). Multi-call cases
pass when the set of calls matches, in any order.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
CASES: list[dict] = []


def c(domain, text, tool, args=None, tags=(), accept=(), context=None):
    CASES.append({"domain": domain, "text": text, "expect": "call",
                  "calls": [{"tool": tool, "args": args or {}}],
                  "accept": [list(a) if isinstance(a, list) else [a] for a in accept],
                  "tags": list(tags), **({"context": context} if context else {})})


def multi(domain, text, calls, tags=()):
    CASES.append({"domain": domain, "text": text, "expect": "call",
                  "calls": [{"tool": t, "args": a} for t, a in calls], "accept": [],
                  "tags": ["multi", *tags]})


def ask(domain, text, tags=(), accept=()):
    CASES.append({"domain": domain, "text": text, "expect": "ask", "calls": [],
                  "accept": [[a] for a in accept], "tags": list(tags)})


def none(domain, text, tags=(), accept=()):
    CASES.append({"domain": domain, "text": text, "expect": "none", "calls": [],
                  "accept": [[a] for a in accept], "tags": list(tags)})


def T(_name, **args):
    return {"tool": _name, "args": args}


# --- music ----------------------------------------------------------------------------------
P, C = "music_play", "music_control"
c("music", "play some Fleetwood Mac", P, {"query": "fleetwood mac", "kind": "artist"})
c("music", "put on Rumours", P, {"query": "rumours"})
c("music", "play Dreams by Fleetwood Mac", P, {"query": "dreams fleetwood mac", "kind": "song"})
c("music", "I want to hear some jazz", P, {"query": "jazz", "kind": "genre"}, ["passive"])
c("music", "play my morning playlist", P, {"query": "morning", "kind": "playlist"})
c("music", "can you play something relaxing", P, {"query": "relaxing"})
c("music", "play lo-fi beats", P, {"query": "lo-fi"})
c("music", "put on the radio", P, {"kind": "radio"})
c("music", "play Johnny Cash", P, {"query": "johnny cash"})
c("music", "play the Hamilton soundtrack", P, {"query": "hamilton"})
c("music", "play some music", P, {}, ["vague"])
c("music", "play something for cooking", P, {"query": "cooking"})
c("music", "play Christmas music", P, {"query": "christmas"})
c("music", "play the Beatles in the kitchen", P, {"query": "beatles"}, ["extra-detail"])
c("music", "shuffle my liked songs", P, {"query": "liked"})
c("music", "pause", C, {"action": "pause"}, ["terse"])
c("music", "pause the music", C, {"action": "pause"})
c("music", "hold on, stop the music for a sec", C, {"action": "pause"}, accept=[T(C, action="stop")])
c("music", "resume", C, {"action": "resume"}, ["terse"])
c("music", "keep playing", C, {"action": "resume"})
c("music", "skip this song", C, {"action": "skip"})
c("music", "next", C, {"action": "skip"}, ["terse"])
c("music", "I hate this song", C, {"action": "skip"}, ["implicit"])
c("music", "skip ahead", C, {"action": "skip"})
c("music", "go back one", C, {"action": "previous"})
c("music", "play that last song again", C, {"action": "previous"})
c("music", "stop the music", C, {"action": "stop"}, accept=[T(C, action="pause")])
c("music", "turn it up", C, {"action": "louder"}, ["pronoun"])
c("music", "a little louder please", C, {"action": "louder"})
c("music", "turn it down", C, {"action": "quieter"}, ["pronoun"])
c("music", "it's too loud", C, {"action": "quieter"}, ["implicit"])
c("music", "set the volume to 30", C, {"action": "set_volume", "level": 30})
c("music", "volume 70", C, {"action": "set_volume", "level": 70}, ["terse"])
c("music", "volume to half", C, {"action": "set_volume", "level": 50}, ["arithmetic"])
c("music", "mute it", C, {"action": "set_volume", "level": 0}, accept=[T(C, action="pause")])
none("music", "what's playing?", ["gap"])

# --- lights ---------------------------------------------------------------------------------
L, S = "light_set", "scene_activate"
c("lights", "turn on the kitchen lights", L, {"target": "kitchen", "state": "on"})
c("lights", "kitchen lights off", L, {"target": "kitchen", "state": "off"}, ["terse"])
c("lights", "turn off the living room", L, {"target": "living room", "state": "off"})
c("lights", "lights on in the bedroom", L, {"target": "bedroom", "state": "on"})
c("lights", "dim the living room to 30 percent", L, {"target": "living room", "brightness": 30})
c("lights", "make the bedroom lamp brighter", L, {"target": "bedroom lamp", "state": "on"}, ["relative"])
c("lights", "set the office light to blue", L, {"target": "office", "color": "blue"})
c("lights", "turn the porch light on", L, {"target": "porch", "state": "on"})
c("lights", "turn everything off", L, {"target": "all", "state": "off"})
c("lights", "movie time", S, {"name": "movie"}, ["implicit"])
c("lights", "activate the movie scene", S, {"name": "movie"})
c("lights", "set the dinner scene", S, {"name": "dinner"})
c("lights", "turn on the greenhouse light", L, {"target": "greenhouse", "state": "on"})
c("lights", "switch off the hallway", L, {"target": "hallway", "state": "off"})
c("lights", "bedroom lights to 10%", L, {"target": "bedroom", "brightness": 10}, ["terse"])
c("lights", "turn the kitchen lights warm white", L, {"target": "kitchen", "color": "warm white"})
c("lights", "can you turn the lights back on in the kitchen", L, {"target": "kitchen", "state": "on"})
c("lights", "bright as it goes in the office", L, {"target": "office", "brightness": 100}, ["idiom"])
c("lights", "half brightness in the dining room", L, {"target": "dining room", "brightness": 50}, ["arithmetic"])
c("lights", "turn on the outside lights", L, {"target": "outside", "state": "on"})
c("lights", "lights on in the kitchen please, thank you", L, {"target": "kitchen", "state": "on"})
c("lights", "can you shut the bathroom light", L, {"target": "bathroom", "state": "off"}, ["idiom"])
# No room named means the room the device is in: the model leaves target out, code fills it.
c("lights", "kill the lights", L, {"target": None, "state": "off"}, ["room-aware", "idiom"])
c("lights", "it's too dark in here", L, {"target": None, "state": "on"}, ["room-aware", "implicit"],
  accept=[T(L, target=None, brightness=100)])
c("lights", "lights off", L, {"target": None, "state": "off"}, ["room-aware"])
c("lights", "turn on the lamp", L, {"target": "lamp", "state": "on"}, ["room-aware"])
none("lights", "don't turn off the porch light", ["negation"])
none("lights", "the kitchen light is on, right?", ["gap"])
c("lights", "goodnight", S, {"name": "goodnight"}, ["implicit"], accept=["none"])

# --- inventory ------------------------------------------------------------------------------
A, M, ST, F, TS = "inventory_adjust", "inventory_mark", "inventory_set", "inventory_find", "tool_status"
c("inventory", "we used three of the 2x4s", A, {"item": "2x4", "change": -3}, ["word-number"])
c("inventory", "I just bought 12 eggs", A, {"item": "eggs", "change": 12})
c("inventory", "add 50 deck screws to the garage bin", A, {"item": "deck screws", "change": 50, "location": "garage"})
c("inventory", "took two cans of tomatoes", A, {"item": "tomatoes", "change": -2}, ["word-number"])
c("inventory", "I used 4 zip ties", A, {"item": "zip ties", "change": -4})
c("inventory", "put 6 jars of salsa in the pantry", A, {"item": "salsa", "change": 6, "location": "pantry"})
c("inventory", "grabbed a box of nails from the shed", A, {"item": "nails", "change": -1, "location": "shed"}, ["unit"])
c("inventory", "two bags of mulch went on the beds", A, {"item": "mulch", "change": -2}, ["word-number", "passive"])
c("inventory", "we got 3 more propane tanks", A, {"item": "propane", "change": 3})
c("inventory", "we used six eggs for the cake", A, {"item": "eggs", "change": -6}, ["word-number"])
c("inventory", "add 2 dozen eggs", A, {"item": "eggs", "change": 24}, ["arithmetic"], accept=[T("list_add", list="shopping", items=["eggs"])])
c("inventory", "I put 10 pounds of sugar in the pantry", A, {"item": "sugar", "change": 10, "location": "pantry"}, ["unit"])
c("inventory", "used 2 quarts of oil on the tractor", A, {"item": "oil", "change": -2}, ["unit"])
c("inventory", "we're out of milk", M, {"item": "milk", "level": "out"}, ["passive"])
c("inventory", "used the last of the flour", M, {"item": "flour", "level": "out"}, ["implicit"])
c("inventory", "we're low on rice", M, {"item": "rice", "level": "low"}, ["passive"])
c("inventory", "plenty of potatoes now", M, {"item": "potatoes", "level": "plenty"}, ["terse"])
c("inventory", "the dog food is almost gone", M, {"item": "dog food", "level": "low"}, ["implicit"])
c("inventory", "coffee's running low", M, {"item": "coffee", "level": "low"})
c("inventory", "we have plenty of paper towels", M, {"item": "paper towels", "level": "plenty"})
c("inventory", "I finished the last roll of duct tape", M, {"item": "duct tape", "level": "out"}, ["implicit"])
c("inventory", "we're out of 3 inch deck screws", M, {"item": "3 inch deck screws", "level": "out"}, ["number-in-name"])
c("inventory", "restocked the first aid kit", M, {"item": "first aid", "level": "plenty"}, ["implicit"])
c("inventory", "used half the bag of flour", M, {"item": "flour", "level": "low"}, ["fuzzy"], accept=["ask"])
c("inventory", "we've got about 20 canning jars", ST, {"item": "canning jars", "count": 20}, ["estimate"])
c("inventory", "there are 12 bales of hay in the barn now", ST, {"item": "hay", "count": 12, "location": "barn"})
c("inventory", "where's the caulk gun", F, {"item": "caulk gun"})
c("inventory", "how many furnace filters do we have", F, {"item": "furnace filters"})
c("inventory", "do we have any AA batteries", F, {"item": "aa batteries"})
c("inventory", "where did we put the extension cords", F, {"item": "extension cords"})
c("inventory", "how much chicken feed is left", F, {"item": "chicken feed"})
c("inventory", "is there any more sandpaper", F, {"item": "sandpaper"})
c("inventory", "where do we keep the spare fuses", F, {"item": "fuses"})
c("inventory", "can't find the good hammer anywhere", F, {"item": "hammer"}, ["implicit"], accept=[T(TS, tool="hammer", status="lost")])
c("inventory", "the circular saw broke", TS, {"tool": "circular saw", "status": "broke"})
c("inventory", "I lent the post hole digger to the neighbor", TS, {"tool": "post hole digger", "status": "lent"})
c("inventory", "the neighbor gave back the post hole digger", TS, {"tool": "post hole digger", "status": "returned"})
none("inventory", "I took the drill", ["tool-policy"])
ask("inventory", "we used some of the chicken feed", ["missing-amount"], accept=[T(M, item="chicken feed", level="low")])
ask("inventory", "used a bunch of the tape", ["missing-amount"])
ask("inventory", "we're out of the good screws", ["ambiguous-item"])
ask("inventory", "used one", ["missing-item"])

# --- lists ----------------------------------------------------------------------------------
LA, LR, LD = "list_add", "list_remove", "list_read"
c("lists", "add milk to the shopping list", LA, {"list": "shopping", "items": ["milk"]})
c("lists", "put eggs and bread on the list", LA, {"list": "shopping", "items": ["eggs", "bread"]}, ["implicit-list"])
c("lists", "we need coffee filters", LA, {"list": "shopping", "items": ["coffee filters"]}, ["passive"])
c("lists", "add call the vet to my to-do list", LA, {"list": "todo", "items": ["call the vet"]})
c("lists", "I need to fix the gate", LA, {"list": "todo", "items": ["fix gate"]}, ["passive"])
c("lists", "add clean the gutters to the to do list", LA, {"list": "todo", "items": ["clean gutters"]})
c("lists", "put chicken, rice, and broccoli on the shopping list", LA, {"list": "shopping", "items": ["chicken", "rice", "broccoli"]})
c("lists", "add dog food", LA, {"list": "shopping", "items": ["dog food"]}, ["implicit-list"])
c("lists", "add sharpen the mower blades", LA, {"list": "todo", "items": ["sharpen mower blades"]}, ["implicit-list"])
c("lists", "also add butter", LA, {"list": "shopping", "items": ["butter"]}, ["implicit-list"])
c("lists", "put toilet paper on the list, the big pack", LA, {"list": "shopping", "items": ["toilet paper"]}, ["extra-detail"])
c("lists", "we need to call the plumber", LA, {"list": "todo", "items": ["call plumber"]}, ["passive"])
c("lists", "add 2 gallons of milk", LA, {"list": "shopping", "items": ["milk"]}, ["unit"])
c("lists", "add apples, oranges, bananas and grapes to shopping", LA, {"list": "shopping", "items": ["apples", "oranges", "bananas", "grapes"]})
c("lists", "put return library books on my list", LA, {"list": "todo", "items": ["return library books"]})
c("lists", "take milk off the shopping list", LR, {"list": "shopping", "items": ["milk"]})
c("lists", "I got the eggs", LR, {"list": "shopping", "items": ["eggs"]}, ["passive"])
c("lists", "nevermind the bread", LR, {"list": "shopping", "items": ["bread"]}, ["idiom"])
c("lists", "I fixed the gate", LR, {"list": "todo", "items": ["gate"]}, ["passive"])
c("lists", "cross off call the vet", LR, {"list": "todo", "items": ["call the vet"]})
c("lists", "I already did the dishes", LR, {"list": "todo", "items": ["dishes"]}, ["passive"])
c("lists", "mark the oil change as done", LR, {"list": "todo", "items": ["oil change"]})
c("lists", "what's on the shopping list", LD, {"list": "shopping"})
c("lists", "read me my to-do list", LD, {"list": "todo"})
c("lists", "what do I need from the store", LD, {"list": "shopping"}, ["implicit"])
c("lists", "is bread on the list", LD, {"list": "shopping"})
c("lists", "what do I have to do today", LD, {"list": "todo"}, accept=[T("calendar_read", when="today")])
none("lists", "don't add milk, we have some", ["negation"])
ask("lists", "add stuff for tacos", ["vague-items"])
ask("lists", "I bought everything on the list", ["gap"])

# --- calendar -------------------------------------------------------------------------------
CR, CA = "calendar_read", "calendar_add"
c("calendar", "what's on my calendar today", CR, {"when": "today"})
c("calendar", "anything tomorrow?", CR, {"when": "tomorrow"}, ["terse"])
c("calendar", "what's my week look like", CR, {"when": "week"})
c("calendar", "do I have anything friday", CR, {"when": "friday"})
c("calendar", "am I free saturday", CR, {"when": "saturday"})
c("calendar", "what's happening this weekend", CR, {"when": "weekend"})
c("calendar", "what time is my appointment tomorrow", CR, {"when": "tomorrow"})
c("calendar", "anything coming up next week", CR, {"when": "next week"})
c("calendar", "what's on tonight", CR, {"when": "tonight"})
c("calendar", "do we have anything on the 15th", CR, {"when": "15th"})
c("calendar", "when's the vet appointment", CR, {}, ["search"])
c("calendar", "put dentist on thursday at 2", CA, {"title": "dentist", "when": "thursday 2"})
c("calendar", "add a vet appointment next tuesday at 10am", CA, {"title": "vet", "when": "next tuesday 10am"})
c("calendar", "schedule dinner with mom saturday at 6", CA, {"title": "dinner with mom", "when": "saturday 6"})
c("calendar", "book the farrier for the 21st", CA, {"title": "farrier", "when": "21st"})
c("calendar", "add soccer practice tomorrow at 4", CA, {"title": "soccer practice", "when": "tomorrow 4"})
c("calendar", "I have a haircut on monday at noon", CA, {"title": "haircut", "when": "monday noon"}, ["passive"])
c("calendar", "add parent teacher conference october 30th at 5:30", CA, {"title": "parent teacher conference", "when": "october 30th 5:30"})
c("calendar", "date night friday 7pm", CA, {"title": "date night", "when": "friday 7pm"}, ["terse"])
c("calendar", "trash day every tuesday", CA, {"title": "trash", "when": "every tuesday"}, ["recurring"])
ask("calendar", "add a meeting", ["missing-time"])
ask("calendar", "put something on the calendar for friday", ["missing-title"])
none("calendar", "cancel the dentist", ["gap"])
none("calendar", "move the vet to wednesday", ["gap"])

# --- weather --------------------------------------------------------------------------------
W = "weather_get"
c("weather", "what's the weather", W, {"when": "now"}, accept=[T(W, when="today")])
c("weather", "is it going to rain today", W, {"when": "today", "aspect": "rain"})
c("weather", "will it rain tomorrow", W, {"when": "tomorrow", "aspect": "rain"})
c("weather", "do I need a jacket", W, {"when": "today"}, ["implicit"], accept=[T(W, when="now")])
c("weather", "is it going to freeze tonight", W, {"when": "tonight", "aspect": "frost"})
c("weather", "how cold will it get tonight", W, {"when": "tonight"})
c("weather", "what's the forecast for the weekend", W, {"when": "weekend"})
c("weather", "how windy is it", W, {"when": "now", "aspect": "wind"})
c("weather", "what's the high today", W, {"when": "today", "aspect": "temperature"})
c("weather", "should I water the garden", W, {"when": "today", "aspect": "rain"}, ["implicit"], accept=[T(W, when="tomorrow", aspect="rain")])
c("weather", "do I need to cover the plants tonight", W, {"when": "tonight", "aspect": "frost"}, ["implicit"])
c("weather", "what's it like outside", W, {"when": "now"})
c("weather", "week ahead forecast", W, {"when": "this week"}, ["terse"])
c("weather", "any storms coming", W, {"when": "this week"}, ["implicit"])
c("weather", "is it nice out", W, {"when": "now"})
c("weather", "good day to mow?", W, {"when": "today"}, ["implicit"])
c("weather", "will it be warm enough to plant tomatoes this week", W, {"when": "this week", "aspect": "temperature"})
c("weather", "how hot is it gonna get", W, {"when": "today", "aspect": "temperature"})
c("weather", "is it snowing", W, {"when": "now"})
c("weather", "can I hang laundry out today", W, {"when": "today"}, ["implicit"])

# --- alerts and reminders -------------------------------------------------------------------
R, AR, AA = "reminder_add", "alerts_read", "alert_act"
JUST = "An alert just went off: 'Feed the chickens'."
c("alerts", "remind me to take the bread out in 20 minutes", R, {"text": "bread out", "when": "20 minutes"})
c("alerts", "remind me tomorrow at 8 to call the vet", R, {"text": "call the vet", "when": "tomorrow 8"})
c("alerts", "set a reminder for 5pm to feed the chickens", R, {"text": "feed the chickens", "when": "5pm"})
c("alerts", "in an hour remind me to check the oven", R, {"text": "check the oven", "when": "hour"})
c("alerts", "remind me to move the sprinkler at 3", R, {"text": "move the sprinkler", "when": "3"})
c("alerts", "don't let me forget the trash tonight", R, {"text": "trash", "when": "tonight"}, ["idiom"])
c("alerts", "remind me every morning to give the dog her meds", R, {"text": "dog meds", "when": "every morning"}, ["recurring"])
c("alerts", "set an alarm for 6am", R, {"when": "6am"}, ["gap"])
c("alerts", "set a timer for 15 minutes", R, {"when": "15 minutes"}, ["gap"])
c("alerts", "what reminders do I have", AR)
c("alerts", "anything I'm forgetting", AR, tags=["implicit"])
c("alerts", "any alerts?", AR, tags=["terse"])
c("alerts", "dismiss", AA, {"action": "dismiss"}, ["terse"], context=JUST)
c("alerts", "ok got it", AA, {"action": "dismiss"}, ["implicit"], accept=["none"], context=JUST)
c("alerts", "stop beeping", AA, {"action": "dismiss"}, context=JUST)
c("alerts", "shut up", AA, {"action": "dismiss"}, ["rude"], accept=["none"], context=JUST)
c("alerts", "snooze", AA, {"action": "snooze"}, ["terse"], context=JUST)
c("alerts", "snooze 10 minutes", AA, {"action": "snooze", "minutes": 10}, context=JUST)
c("alerts", "remind me again in 5", AA, {"action": "snooze", "minutes": 5}, accept=[T(R, when="5")], context=JUST)
c("alerts", "later", AA, {"action": "snooze"}, ["terse"], context=JUST)
c("alerts", "what was that alert", AR, context=JUST)
ask("alerts", "remind me to buy milk", ["missing-time"], accept=[T("list_add", list="shopping", items=["milk"])])
ask("alerts", "remind me in 10", ["missing-text"])

# --- no domain: ask back --------------------------------------------------------------------
for t in ["turn it off", "add it", "do the thing", "the usual", "more", "off", "set it to 50",
          "how many", "change it", "put it on the list", "is it on?", "can you get that"]:
    ask("ambiguous", t, ["no-referent"])

# --- no domain: nothing to do ---------------------------------------------------------------
for t in ["thank you", "good morning", "how are you", "you're cute", "what's your name",
          "tell me a joke", "I love you", "never mind", "cancel", "who won the game last night",
          "what's the capital of France", "write me a poem", "ignore that", "ugh what a day",
          "hey hestia", "oops wrong button", "are you listening", "test test", "hmm",
          "what can you do", "what time is it"]:
    none("none", t, ["companion"] if t in ("you're cute", "tell me a joke", "I love you", "how are you") else [])
none("none", "good night", accept=[T(S, name="goodnight")])
none("none", "I'm going to bed", ["implicit"], accept=[T(S, name="goodnight")])

# --- several at once ------------------------------------------------------------------------
multi("multi", "turn off the kitchen lights and pause the music",
      [(L, {"target": "kitchen", "state": "off"}), (C, {"action": "pause"})])
multi("multi", "we're out of milk and eggs",
      [(M, {"item": "milk", "level": "out"}), (M, {"item": "eggs", "level": "out"})])
multi("multi", "add milk to the list and remind me to grab it at 5",
      [(LA, {"list": "shopping", "items": ["milk"]}), (R, {"text": "milk", "when": "5"})])
multi("multi", "movie time, and turn the music down",
      [(S, {"name": "movie"}), (C, {"action": "quieter"})])
multi("multi", "turn off the kitchen and the living room",
      [(L, {"target": "kitchen", "state": "off"}), (L, {"target": "living room", "state": "off"})])
multi("multi", "I used 3 2x4s and 20 screws",
      [(A, {"item": "2x4", "change": -3}), (A, {"item": "screws", "change": -20})])
multi("multi", "what's the weather tomorrow and do I have anything on",
      [(W, {"when": "tomorrow"}), (CR, {"when": "tomorrow"})])
multi("multi", "cross off eggs and add bread",
      [(LR, {"list": "shopping", "items": ["eggs"]}), (LA, {"list": "shopping", "items": ["bread"]})])


# --- write ----------------------------------------------------------------------------------
def _fmt_call(call) -> str:
    if call in ("ask", "none"):
        return call
    if isinstance(call, list):
        return " + ".join(_fmt_call(x) for x in call)
    args = ", ".join(f"{k}={json.dumps(v)}" for k, v in call["args"].items())
    return f"{call['tool']}({args})"


def main() -> None:
    for i, case in enumerate(CASES, 1):
        case["id"] = f"{case['domain'][:3]}-{i:03d}"
    with open(HERE / "cases.jsonl", "w") as f:
        for case in CASES:
            f.write(json.dumps(case) + "\n")
    lines = ["# Small-model router eval: utterances for review", "",
             f"{len(CASES)} cases. Generated by `build_cases.py` (edit there, not here).", "",
             "Expect is what a reliable device must do. `ask` = no call, a clarifying question. "
             "`none` = no call. Free-text args pass when every expected word appears in the "
             "model's value; enums and numbers must match; unlisted args aren't checked; an arg "
             "expected as null must be left out (no room named = the device's room, filled in by code).", ""]
    domains = list(dict.fromkeys(c["domain"] for c in CASES))
    for d in domains:
        rows = [c for c in CASES if c["domain"] == d]
        lines += [f"## {d} ({len(rows)})", "", "| id | utterance | expect | also passes | tags |",
                  "|---|---|---|---|---|"]
        for r in rows:
            exp = r["expect"] if r["expect"] != "call" else " + ".join(_fmt_call(x) for x in r["calls"])
            also = "; ".join(_fmt_call(a[0] if len(a) == 1 else a) for a in r["accept"])
            ctx = f" *(after: {r['context']})*" if r.get("context") else ""
            lines.append(f"| {r['id']} | {r['text']}{ctx} | `{exp}` | {('`' + also + '`') if also else ''} | {', '.join(r['tags'])} |")
        lines.append("")
    (HERE / "REVIEW.md").write_text("\n".join(lines))
    from collections import Counter
    print(len(CASES), "cases", dict(Counter(c["domain"] for c in CASES)),
          dict(Counter(c["expect"] for c in CASES)))


if __name__ == "__main__":
    main()
