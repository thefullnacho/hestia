"""Builds train.jsonl: labelled utterances for fine-tuning the tool caller and seeding the router.

    python3 build_train.py            # writes train.jsonl, prints counts and leak checks

Same schema and the same policy as cases.jsonl (see build_cases.py and REVIEW.md), with two
differences that keep the test fair:
  - the vocabulary is held out: items, tools, artists, titles and errands that appear in the
    test cases are not used here, so a pass means the model generalised, not recognised
  - any utterance that matches a test utterance after normalising is dropped

Labels come from templates, not a model, so every argument is right by construction.
One policy the test set splits on ("add 2 gallons of milk" -> list, "add 2 dozen eggs" ->
inventory) is deliberately not taught. `split` marks a held-out tenth for picking the
router's floor and gap and for fine-tune validation, so the test cases are never tuned on.
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

HERE = Path(__file__).parent
rng = random.Random(1015)
OUT: list[dict] = []


def T(_name, **args):
    return {"tool": _name, "args": args}


def add(domain, text, *calls, expect="call", say=None, context=None, kind=None):
    OUT.append({"domain": domain, "text": text, "expect": expect, "calls": list(calls),
                **({"say": say} if say else {}), **({"context": context} if context else {}),
                "template": kind})


def fill(templates, slots, n, make, kind):
    """Draw n distinct (template, slot values) pairs and hand each to make(text, values)."""
    seen = set()
    tries = 0
    while len(seen) < n and tries < n * 50:
        tries += 1
        t = rng.choice(templates)
        vals = {k: rng.choice(v) for k, v in slots.items() if "{" + k + "}" in t}
        text = t.format(**vals)
        if text in seen:
            continue
        seen.add(text)
        make(text, vals, kind)


NUM_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 8: "eight", 10: "ten", 12: "twelve"}


def num(n):
    return NUM_WORDS[n] if n in NUM_WORDS and rng.random() < 0.4 else str(n)


# --- vocabulary, held out from the test cases ------------------------------------------------
ROOMS = ["garage", "basement", "den", "laundry room", "mudroom", "shop", "back deck", "attic",
         "guest room", "kids room", "front hall", "pantry", "barn", "kitchen", "bedroom", "office",
         "living room", "patio", "stairs", "nursery"]
LIGHT_THINGS = ["lamp", "light", "lights", "overhead", "string lights", "ceiling light"]
COLORS = ["red", "green", "purple", "soft white", "daylight", "orange", "pink", "cool white"]
SCENES = ["reading", "morning", "relax", "party", "bedtime", "focus", "away", "cleaning"]
ARTISTS = ["dolly parton", "miles davis", "the eagles", "taylor swift", "willie nelson",
           "bob marley", "adele", "the rolling stones", "norah jones", "creedence",
           "patsy cline", "stevie wonder", "chris stapleton", "queen", "nina simone"]
ALBUMS = ["abbey road", "thriller", "graceland", "blue", "back in black", "jolene", "kind of blue"]
SONGS = [("landslide", ""), ("hey jude", "the beatles"), ("respect", "aretha franklin"),
         ("ring of fire", ""), ("wagon wheel", "old crow medicine show"), ("africa", "toto")]
GENRES = ["bluegrass", "classical", "country", "blues", "folk", "reggae", "old time", "soul",
          "piano music", "eighties rock", "gospel", "motown"]
PLAYLISTS = ["workout", "dinner party", "road trip", "sunday", "chores", "sleep"]
MOODS = ["upbeat", "mellow", "calm", "fun", "quiet", "happy"]

# pantry and shop items: (item, unit or "")
STOCK = [("canned soup", "cans"), ("pasta", "boxes"), ("olive oil", "bottles"), ("oats", "bags"),
         ("peanut butter", "jars"), ("black beans", "cans"), ("honey", "jars"), ("cereal", "boxes"),
         ("light bulbs", ""), ("lag bolts", ""), ("wood glue", "bottles"), ("trash bags", "boxes"),
         ("hose clamps", ""), ("air filters", ""), ("baling twine", "spools"), ("seed starting mix", "bags"),
         ("wood pellets", "bags"), ("motor oil", "quarts"), ("fence staples", "boxes"), ("chick starter", "bags"),
         ("canning lids", "boxes"), ("dish soap", "bottles"), ("laundry detergent", "jugs"), ("bleach", "jugs"),
         ("cat litter", "bags"), ("charcoal", "bags"), ("rubber gloves", "pairs"), ("work gloves", "pairs")]
BULK = ["salt", "cornmeal", "brown sugar", "dish soap", "laundry detergent", "cat litter",
        "bird seed", "firewood", "kindling", "grass seed", "fertilizer", "potting soil",
        "baking soda", "vinegar", "ketchup", "maple syrup", "tea", "hot sauce", "cheese",
        "yogurt", "lettuce", "onions", "garlic", "lemons"]
PLACES = ["pantry", "garage", "shed", "barn", "basement", "shop", "workbench", "chest freezer",
          "mudroom closet", "feed room"]
TOOLS = ["chainsaw", "pressure washer", "ladder", "tile saw", "shop vac", "leaf blower",
         "wheelbarrow", "log splitter", "socket set", "level", "tiller", "jigsaw", "staple gun"]
PEOPLE = ["my brother", "the neighbor", "dad", "uncle jim", "the guy down the road", "sarah",
          "my sister", "the kids"]
GROCERY = ["cheese", "yogurt", "tortillas", "lettuce", "onions", "garlic", "lemons",
           "ground beef", "bacon", "cream", "cereal", "peanut butter", "pasta", "maple syrup",
           "ketchup", "hot sauce", "tea", "sour cream", "frozen peas", "crackers", "paper plates",
           "dish soap", "trash bags", "foil", "ice cream", "hamburger buns", "salad dressing"]
CHORES = ["clean the chicken coop", "change the furnace filter", "patch the fence",
          "split firewood", "pay the water bill", "wash the truck", "fix the screen door",
          "oil the hinges", "mow the back field", "clean out the fridge", "rake the leaves",
          "drain the hoses", "re-caulk the tub", "take the recycling in", "order seeds",
          "schedule the chimney sweep", "check the tire pressure", "renew the tags"]
CHORE_NEED = ["clean the chicken coop", "patch the fence", "split firewood", "pay the water bill",
              "wash the truck", "fix the screen door", "rake the leaves", "drain the hoses"]
CHORE_DONE = [("cleaned the chicken coop", "chicken coop"), ("patched the fence", "fence"),
              ("split the firewood", "firewood"), ("paid the water bill", "water bill"),
              ("washed the truck", "truck"), ("raked the leaves", "leaves"),
              ("drained the hoses", "hoses"), ("fixed the screen door", "screen door")]
EVENTS = ["eye doctor", "piano lesson", "book club", "oil change", "board meeting",
          "kids' recital", "4-h meeting", "vet checkup", "hay delivery", "plumber",
          "parent pickup", "church potluck", "physical therapy", "date night", "birthday party",
          "farmers market", "tax appointment", "chimney sweep"]
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
TIMES = ["9", "10am", "11:30", "1", "2pm", "3:15", "4", "6pm", "7", "noon", "8 in the morning"]
DATES = ["the 3rd", "the 9th", "the 18th", "the 27th", "november 4th", "december 12th", "the first"]
REMIND = ["check the canner", "flip the compost", "close the coop", "move the hose",
          "start the dishwasher", "call the feed store", "put the chickens up", "turn off the oven",
          "pick up the kids", "water the seedlings", "take my pills", "switch the laundry",
          "check on the bread", "bring the dog in", "pay the electric bill"]
DURS = ["10 minutes", "25 minutes", "half an hour", "45 minutes", "an hour", "two hours", "90 minutes"]
CLOCK = ["4pm", "7:30", "6 tonight", "noon", "9 tomorrow morning", "tomorrow at 8", "3:45", "friday at 10"]

# --- music -----------------------------------------------------------------------------------
def music():
    P, C = "music_play", "music_control"
    for a in ARTISTS:
        for t in rng.sample(["play {a}", "put on some {a}", "play something by {a}",
                             "I want to listen to {a}", "can you play {a}", "throw on some {a}"], 3):
            add("music", t.format(a=a), T(P, query=a, kind="artist"), kind="play-artist")
    for al in ALBUMS:
        add("music", f"play the album {al}", T(P, query=al, kind="album"), kind="play-album")
        add("music", f"put on {al}, the whole album", T(P, query=al, kind="album"), kind="play-album")
    for s, by in SONGS:
        text = f"play {s}" + (f" by {by}" if by else "")
        add("music", text, T(P, query=f"{s} {by}".strip(), kind="song"), kind="play-song")
        add("music", f"can you put on the song {s}", T(P, query=s, kind="song"), kind="play-song")
    for g in GENRES:
        for t in rng.sample(["play some {g}", "put on {g}", "I'm in the mood for {g}",
                             "play {g} music", "something {g}, please"], 2):
            add("music", t.format(g=g), T(P, query=g, kind="genre"), kind="play-genre")
    for p in PLAYLISTS:
        add("music", f"play my {p} playlist", T(P, query=p, kind="playlist"), kind="play-playlist")
        add("music", f"start the {p} mix", T(P, query=p, kind="playlist"), kind="play-playlist")
    for m in MOODS:
        add("music", f"play something {m}", T(P, query=m), kind="play-mood")
    for t in ["turn on the radio", "play the radio", "radio please", "put the radio station on"]:
        add("music", t, T(P, kind="radio"), kind="play-radio")
    controls = {
        "pause": ["pause it", "pause the song", "hold the music", "pause for a second", "freeze the music"],
        "resume": ["unpause", "start it back up", "keep going with the music", "carry on playing", "play it again from where it stopped"],
        "skip": ["skip", "next song", "skip this one", "not this song", "play the next one", "skip to the next track"],
        "previous": ["back one song", "previous song", "play the last one again", "go back a track"],
        "stop": ["stop playing", "turn the music off", "music off", "that's enough music", "kill the music"],
        "louder": ["louder", "volume up", "crank it up", "turn the music up", "I can't hear it", "bump the volume"],
        "quieter": ["quieter", "volume down", "turn the music down", "too loud", "bring it down a bit", "softer please"],
    }
    for action, texts in controls.items():
        for t in texts:
            add("music", t, T(C, action=action), kind=f"control-{action}")
    for lvl in [20, 35, 40, 60, 75, 80]:
        for t in ["volume to {l}", "set the volume at {l}", "make the volume {l} percent"]:
            add("music", t.format(l=lvl), T(C, action="set_volume", level=lvl), kind="control-volume")


# --- lights ----------------------------------------------------------------------------------
def lights():
    L, S = "light_set", "scene_activate"
    def onoff(text, v, kind):
        add("lights", text, T(L, target=v["r"], state=v["s"]), kind=kind)
    fill(["turn {s} the {r} {x}", "{r} {x} {s}", "switch {s} the {r} {x}", "turn the {r} {x} {s}",
          "can you turn {s} the {r} {x}", "flip the {r} {x} {s}", "{x} {s} in the {r}",
          "please turn the {x} {s} in the {r}", "turn {s} the {r}"],
         {"s": ["on", "off"], "r": ROOMS, "x": LIGHT_THINGS}, 70, onoff, "light-onoff")
    def dim(text, v, kind):
        add("lights", text, T(L, target=v["r"], brightness=int(v["b"])), kind=kind)
    fill(["dim the {r} to {b}", "set the {r} lights to {b} percent", "{r} to {b}%",
          "make the {r} {b} percent", "bring the {r} lights down to {b}", "{r} brightness {b}"],
         {"r": ROOMS, "b": ["5", "15", "20", "25", "40", "60", "70", "80", "90"]}, 45, dim, "light-dim")
    def color(text, v, kind):
        add("lights", text, T(L, target=v["r"], color=v["c"]), kind=kind)
    fill(["make the {r} {c}", "set the {r} lights to {c}", "turn the {r} {c}", "{r} lights {c}"],
         {"r": ROOMS, "c": COLORS}, 30, color, "light-color")
    for t in ["turn every light off", "all the lights off", "shut off all the lights",
              "turn all the lights on", "every light on please", "lights on everywhere"]:
        add("lights", t, T(L, target="all", state="on" if " on" in t else "off"), kind="light-all")
    for sc in SCENES:
        for t in rng.sample(["{n} mode", "set the {n} scene", "turn on {n} scene", "switch to {n}",
                             "{n} lighting please"], 3):
            add("lights", t.format(n=sc), T(S, name=sc), kind="scene")
    # No room named: target is left out and code fills in the room the device is in.
    here = [("turn the light on", {"state": "on"}), ("lights on", {"state": "on"}),
            ("lights out", {"state": "off"}), ("can I get some light", {"state": "on"}),
            ("shut the lights off", {"state": "off"}), ("lights to 40", {"brightness": 40}),
            ("too bright in here", {"brightness": 30}), ("make the lights red", {"color": "red"}),
            ("it's dark", {"state": "on"}), ("switch the light off", {"state": "off"}),
            ("dim the lights", {"brightness": 30}), ("lights all the way up", {"brightness": 100}),
            ("turn the lights down to 20", {"brightness": 20}), ("hit the lights", {"state": "off"}),
            ("light please", {"state": "on"}), ("lights", {"state": "on"}),
            ("make it bright in here", {"brightness": 100}), ("soft white lights", {"color": "soft white"})]
    for t, args in here:
        add("lights", t, T(L, **args), kind="light-here")
    for t, thing, s in [("shut the lamp off", "lamp", "off"), ("turn on the overhead", "overhead", "on"),
                        ("string lights on", "string lights", "on"), ("ceiling light off", "ceiling light", "off")]:
        add("lights", t, T(L, target=thing, state=s), kind="light-here-thing")
    # A pronoun could be the lights or the music: the room doesn't settle it, so ask.
    for t, q in [("turn that off", "What should I turn off?"), ("dim it", "Dim what?"),
                 ("make it red", "Which light?"), ("turn it on", "What should I turn on?")]:
        add("lights", t, expect="ask", say=q, kind="ask-pronoun")
    for t in ["don't turn on the garage light", "leave the barn light on", "is the shop light on?",
              "keep the porch lights off tonight", "who left the basement light on",
              "the den lamp is broken", "don't touch the office lights"]:
        add("lights", t, expect="none", kind="none-negation")


# --- inventory -------------------------------------------------------------------------------
def inventory():
    A, M, ST, F, TS = "inventory_adjust", "inventory_mark", "inventory_set", "inventory_find", "tool_status"
    for item, unit in STOCK:
        u = f"{unit} of " if unit else ""
        n1, n2 = rng.choice([2, 3, 4, 5, 6]), rng.choice([2, 3, 4, 6, 8, 10, 12])
        place = rng.choice(PLACES)
        used = rng.choice(["used {n} {u}{i}", "I used {n} {u}{i}", "we went through {n} {u}{i}",
                           "took {n} {u}{i} out", "{n} {u}{i} got used up"])
        add("inventory", used.format(n=num(n1), u=u, i=item), T(A, item=item, change=-n1), kind="adjust-use")
        got = rng.choice(["bought {n} {u}{i}", "picked up {n} {u}{i}", "we got {n} more {u}{i}",
                          "just brought home {n} {u}{i}"])
        add("inventory", got.format(n=num(n2), u=u, i=item), T(A, item=item, change=n2), kind="adjust-add")
        put = rng.choice(["put {n} {u}{i} in the {p}", "stocked {n} {u}{i} in the {p}",
                          "added {n} {u}{i} to the {p}", "there's {n} new {u}{i} in the {p}"])
        add("inventory", put.format(n=num(n2), u=u, i=item, p=place),
            T(A, item=item, change=n2, location=place), kind="adjust-place")
        cnt = rng.choice([3, 7, 9, 11, 14, 16, 24, 30])
        st = rng.choice(["we have {n} {u}{i}", "there are {n} {u}{i} left", "counted {n} {u}{i}",
                         "{n} {u}{i} on the shelf now", "we're down to {n} {u}{i}"])
        add("inventory", st.format(n=cnt, u=u, i=item), T(ST, item=item, count=cnt), kind="set")
    for item in BULK:
        for lvl, ts in {"out": ["we're out of {i}", "no more {i}", "used the last of the {i}",
                                "the {i} is gone", "we ran out of {i}", "all out of {i}"],
                        "low": ["we're low on {i}", "the {i} is running low", "almost out of {i}",
                                "getting low on {i}", "not much {i} left"],
                        "plenty": ["we have plenty of {i}", "stocked up on {i}", "lots of {i} now",
                                   "the {i} is full again"]}.items():
            add("inventory", rng.choice(ts).format(i=item), T(M, item=item, level=lvl), kind=f"mark-{lvl}")
    find_t = ["where's the {i}", "where do we keep the {i}", "how many {i} do we have",
              "do we have any {i}", "is there any {i} left", "where did the {i} go",
              "how much {i} is there", "I can't find the {i}"]
    for item in [s[0] for s in STOCK] + BULK[:8]:
        add("inventory", rng.choice(find_t).format(i=item), T(F, item=item), kind="find")
    for tool in TOOLS:
        t = rng.choice(["where's the {i}", "where did we put the {i}", "where do we keep the {i}",
                        "have you seen the {i}", "which shelf is the {i} on"])
        add("inventory", t.format(i=tool), T(F, item=tool), kind="find")
    for tool in TOOLS:
        p = rng.choice(PEOPLE)
        add("inventory", rng.choice(["the {t} broke", "the {t} quit working", "busted the {t}",
                                     "the {t} is broken"]).format(t=tool),
            T(TS, tool=tool, status="broke"), kind="tool-broke")
        add("inventory", rng.choice(["lent the {t} to {p}", "{p} borrowed the {t}",
                                     "{p} has the {t}"]).format(t=tool, p=p),
            T(TS, tool=tool, status="lent", note=p), kind="tool-lent")
        add("inventory", rng.choice(["{p} brought the {t} back", "got the {t} back from {p}",
                                     "the {t} is back"]).format(t=tool, p=p),
            T(TS, tool=tool, status="returned"), kind="tool-returned")
        add("inventory", rng.choice(["I lost the {t}", "the {t} is missing", "can't find the {t}, it's gone"]).format(t=tool),
            T(TS, tool=tool, status="lost"), kind="tool-lost")
    for t in ["I grabbed the ladder", "borrowing the jigsaw for a bit", "using the shop vac",
              "took the level out to the barn"]:
        add("inventory", t, expect="none", kind="none-tool-policy")
    for t, q in [("used some of the oats", "About how much?"), ("we used a few", "A few of what?"),
                 ("took some out of the pantry", "What did you take?"), ("used two", "Two of what?"),
                 ("we're out of the good stuff", "Out of what, exactly?"), ("add some", "Some of what?"),
                 ("used a bunch of the bolts", "About how many?")]:
        add("inventory", t, expect="ask", say=q, kind="ask-missing")
    for a, b in [("cheese", "yogurt"), ("salt", "vinegar"), ("onions", "garlic"), ("tea", "lemons")]:
        add("multi", f"we're out of {a} and {b}", T(M, item=a, level="out"), T(M, item=b, level="out"),
            kind="multi-mark")


# --- lists -----------------------------------------------------------------------------------
def lists():
    LA, LR, LD = "list_add", "list_remove", "list_read"
    for _ in range(60):
        a, b = rng.sample(GROCERY, 2)
        t = rng.choice(["add {a} to the shopping list", "put {a} on the list", "we need {a}",
                        "add {a}", "{a} on the shopping list", "get {a} next time", "we need more {a}",
                        "put {a} on the grocery list", "add {a} and {b} to the list",
                        "we need {a} and {b}", "put {a} and {b} on the shopping list"])
        items = [a, b] if "{b}" in t else [a]
        add("lists", t.format(a=a, b=b), T(LA, list="shopping", items=items), kind="add-shopping")
    for ch in CHORES:
        t = rng.choice(["add {c} to my to-do list", "put {c} on the to do list", "add {c}",
                        "to do: {c}", "I need to {c}", "I have to {c}", "add {c} to the list of chores"])
        add("lists", t.format(c=ch), T(LA, list="todo", items=[ch]), kind="add-todo")
    for g in rng.sample(GROCERY, 18):
        t = rng.choice(["take {g} off the list", "I got the {g}", "bought the {g}",
                        "cross off {g}", "never mind the {g}", "we don't need {g} anymore",
                        "remove {g} from the shopping list"])
        add("lists", t.format(g=g), T(LR, list="shopping", items=[g]), kind="remove-shopping")
    for done, key in CHORE_DONE:
        t = rng.choice(["I {d}", "{d}, cross it off", "already {d}", "check off {k}", "done with the {k}"])
        add("lists", t.format(d=done, k=key), T(LR, list="todo", items=[key]), kind="remove-todo")
    for t, l in [("read me the shopping list", "shopping"), ("what's on the grocery list", "shopping"),
                 ("what are we out of at the store", "shopping"), ("what's on my list for town", "shopping"),
                 ("what do we need to buy", "shopping"), ("what's on the to-do list", "todo"),
                 ("what chores are left", "todo"), ("read my to do list", "todo"),
                 ("what's left on my list of chores", "todo"), ("is cheese on the shopping list", "shopping")]:
        add("lists", t, T(LD, list=l), kind="read")
    for t, q in [("add the stuff for chili", "What should I add for chili?"),
                 ("put the usual on the list", "What's the usual?"),
                 ("add what we need for the party", "What do you need for the party?"),
                 ("I got everything", "Should I clear the whole shopping list?")]:
        add("lists", t, expect="ask", say=q, kind="ask-vague")
    for t in ["don't put cheese on the list", "we still have plenty of crackers, don't add them",
              "no need to add tortillas", "skip the bacon this week"]:
        add("lists", t, expect="none", kind="none-negation")


# --- calendar --------------------------------------------------------------------------------
def calendar():
    CR, CA = "calendar_read", "calendar_add"
    for w in ["today", "tomorrow", "this week", "next week", "this weekend", "tonight"] + DAYS:
        t = rng.choice(["what's on the calendar {w}", "anything going on {w}", "what do I have {w}",
                        "am I busy {w}", "what's planned for {w}", "check my schedule {w}"])
        when = w.replace("this ", "") if w.startswith("this ") else w
        add("calendar", t.format(w=w if w in ("today", "tomorrow", "tonight", "this week", "next week", "this weekend") else "on " + w),
            T(CR, when=when), kind="read")
    for ev in EVENTS:
        d = rng.choice(DAYS + DATES)
        tm = rng.choice(TIMES + [""])
        when = f"{d} {tm}".strip()
        spoken = f"{d} at {tm}" if tm else d
        t = rng.choice(["put {e} on the calendar for {w}", "add {e} {w}", "schedule {e} for {w}",
                        "{e} is {w}", "I've got {e} {w}", "we have {e} on {w}", "book {e} {w}"])
        add("calendar", t.format(e=ev, w=spoken), T(CA, title=ev, when=when.replace(" at ", " ")), kind="add")
    for t, q in [("put something on for thursday", "What should I put on Thursday?"),
                 ("add an appointment", "What and when?"), ("schedule the plumber", "When should I schedule the plumber?"),
                 ("book a time with the vet", "When?")]:
        add("calendar", t, expect="ask", say=q, kind="ask-missing")
    for t in ["cancel book club", "push the eye doctor to friday", "delete the piano lesson",
              "reschedule the plumber", "move date night to saturday"]:
        add("calendar", t, expect="none", kind="none-gap")


# --- weather ---------------------------------------------------------------------------------
def weather():
    W = "weather_get"
    rows = [
        ("is it raining", "now", "rain"), ("is it raining right now", "now", "rain"),
        ("will it rain tonight", "tonight", "rain"), ("any rain this week", "this week", "rain"),
        ("rain this weekend?", "weekend", "rain"), ("should I bring an umbrella", "today", "rain"),
        ("chance of rain tomorrow", "tomorrow", "rain"), ("do I need to water today", "today", "rain"),
        ("will it frost tonight", "tonight", "frost"), ("frost tomorrow night?", "tomorrow", "frost"),
        ("should I bring the seedlings in tonight", "tonight", "frost"), ("any frost this week", "this week", "frost"),
        ("is it windy out", "now", "wind"), ("how windy will it be tomorrow", "tomorrow", "wind"),
        ("too windy to burn today?", "today", "wind"), ("wind this weekend", "weekend", "wind"),
        ("how hot is it", "now", "temperature"), ("what's the temperature", "now", "temperature"),
        ("how cold is it outside", "now", "temperature"), ("what's the low tonight", "tonight", "temperature"),
        ("how warm will it be tomorrow", "tomorrow", "temperature"), ("is it going to be hot this weekend", "weekend", "temperature"),
        ("what's the weather today", "today", None), ("weather tomorrow", "tomorrow", None),
        ("what's the weather like right now", "now", None), ("forecast for tonight", "tonight", None),
        ("what's the weather this week", "this week", None), ("how's the weekend looking", "weekend", None),
        ("what's it doing outside", "now", None), ("give me the forecast", "today", None),
        ("good weather for a bonfire tonight?", "tonight", None), ("nice day for a hike tomorrow?", "tomorrow", None),
        ("will it be nice saturday", "weekend", None), ("can we cut hay this week", "this week", "rain"),
    ]
    for t, w, a in rows:
        args = {"when": w, **({"aspect": a} if a else {})}
        add("weather", t, T(W, **args), kind="weather")


# --- alerts ----------------------------------------------------------------------------------
def alerts():
    R, AR, AA = "reminder_add", "alerts_read", "alert_act"
    for _ in range(70):
        what = rng.choice(REMIND)
        if rng.random() < 0.5:
            d = rng.choice(DURS)
            t = rng.choice(["remind me to {x} in {d}", "in {d} remind me to {x}", "{d} from now, remind me to {x}",
                            "give me a reminder in {d} to {x}"])
            when = d
        else:
            d = rng.choice(CLOCK)
            t = rng.choice(["remind me to {x} at {d}", "remind me at {d} to {x}", "set a reminder for {d} to {x}",
                            "don't let me forget to {x} at {d}"])
            when = d
        add("alerts", t.format(x=what, d=d), T(R, text=what, when=when), kind="reminder")
    for d in ["10 minutes", "5 minutes", "an hour", "20 minutes", "40 minutes"]:
        add("alerts", f"timer for {d}", T(R, when=d), kind="timer")
        if d[0].isdigit():
            span = d.replace("minutes", "minute")
            add("alerts", f"start a {span} timer", T(R, when=span), kind="timer")
        else:
            add("alerts", f"set a timer for {d}", T(R, when=d), kind="timer")
    for c in ["5:30am", "7am", "6:15", "5 in the morning"]:
        add("alerts", f"wake me up at {c}", T(R, when=c), kind="alarm")
        add("alerts", f"alarm for {c}", T(R, when=c), kind="alarm")
    for t in ["what reminders are set", "do I have any reminders", "anything coming up I should know about",
              "read my alerts", "what's pending", "any timers going"]:
        add("alerts", t, T(AR), kind="read")
    just = "An alert just went off: '{x}'."
    for x in rng.sample(REMIND, 8):
        ctx = just.format(x=x[0].upper() + x[1:])
        add("alerts", rng.choice(["got it", "ok, done", "dismiss it", "turn that off", "stop it", "I did it"]),
            T(AA, action="dismiss"), context=ctx, kind="act-dismiss")
        m = rng.choice([5, 10, 15, 30])
        add("alerts", rng.choice([f"snooze for {m}", f"give me {m} more minutes", f"remind me in {m} minutes",
                                  f"{m} more minutes"]), T(AA, action="snooze", minutes=m), context=ctx, kind="act-snooze")
        add("alerts", rng.choice(["snooze it", "not now", "in a bit"]), T(AA, action="snooze"), context=ctx, kind="act-snooze")
    for t, q in [("remind me to call the farrier", "When should I remind you?"),
                 ("set a reminder", "What should I remind you about, and when?"),
                 ("remind me at 4", "What should I remind you about?"),
                 ("remind me later", "What, and when?"), ("set a timer", "For how long?")]:
        add("alerts", t, expect="ask", say=q, kind="ask-missing")


# --- nothing to do ---------------------------------------------------------------------------
def nothing():
    for t in ["thanks", "thanks a lot", "cool", "perfect", "nice", "good job", "you're the best",
              "morning", "hi there", "hey", "hello", "what's up", "how's it going", "who are you",
              "are you there", "can you hear me", "never mind that", "forget it", "oops", "sorry",
              "wrong button", "testing", "uh", "okay", "yep", "nope", "I'm home", "be right back",
              "what's the meaning of life", "tell me a joke", "what year is it", "who won the game last night",
              "how far is the moon", "what's two plus two", "are you a robot", "do you like music",
              "I'm tired", "long day", "the dog is barking again", "I'm heading out", "talk to you later",
              "see you tomorrow", "that's all", "stop", "quiet", "not you"]:
        add("none", t, expect="none", kind="chitchat")


def boost():
    """Extra phrasings for the tools the templates above leave thin, so no tool is a rarity."""
    CR, CA, W = "calendar_read", "calendar_add", "weather_get"
    LD, LR, AR, AA = "list_read", "list_remove", "alerts_read", "alert_act"
    reads = ["what's going on {w}", "do I have anything {w}", "what's my schedule {w}",
             "any appointments {w}", "is anything planned {w}", "what's on deck {w}",
             "read me the calendar for {w}", "how busy am I {w}"]
    whens = [("today", "today"), ("tomorrow", "tomorrow"), ("tonight", "tonight"),
             ("this week", "week"), ("next week", "next week"), ("this weekend", "weekend"),
             ("on monday", "monday"), ("on wednesday", "wednesday"), ("on saturday", "saturday"),
             ("on the 12th", "12th"), ("next friday", "next friday")]
    for t in reads:
        for said, when in rng.sample(whens, 4):
            add("calendar", t.format(w=said), T(CR, when=when), kind="read")
    for ev in rng.sample(EVENTS, 14):
        d, tm = rng.choice(DAYS), rng.choice(TIMES)
        t = rng.choice(["add {e} on {d} at {t}", "{e} {d} at {t}, put it on the calendar",
                        "can you schedule {e} {d} at {t}", "new appointment: {e}, {d} at {t}"])
        add("calendar", t.format(e=ev, d=d, t=tm), T(CA, title=ev, when=f"{d} {tm}"), kind="add")
        dt = rng.choice(DATES)
        add("calendar", f"{ev} on {dt}", T(CA, title=ev, when=dt), kind="add")
    aspects = {"rain": ["is it going to rain {w}", "rain {w}?", "will we get rain {w}", "should I expect rain {w}"],
               "frost": ["any chance of frost {w}", "will it freeze {w}", "do I need to cover the garden {w}"],
               "wind": ["how windy {w}", "will it be gusty {w}", "is it too windy for the ladder {w}"],
               "temperature": ["how cold {w}", "how hot {w}", "what's the high {w}", "what temperature {w}"],
               None: ["what's the weather {w}", "forecast {w}", "what's it going to be like {w}", "how's the weather {w}"]}
    wsaid = [("today", "today"), ("tonight", "tonight"), ("tomorrow", "tomorrow"),
             ("this week", "this week"), ("this weekend", "weekend"), ("right now", "now")]
    for a, ts in aspects.items():
        for t in ts:
            for said, when in rng.sample(wsaid, 3):
                if a == "frost" and when == "now":
                    continue
                add("weather", t.format(w=said), T(W, when=when, **({"aspect": a} if a else {})), kind="weather")
    for t, l in [("what's on the list", "shopping"), ("tell me the shopping list", "shopping"),
                 ("what groceries do we need", "shopping"), ("what's left to buy", "shopping"),
                 ("go through the shopping list", "shopping"), ("anything on the grocery list", "shopping"),
                 ("what was on the list again", "shopping"), ("what do I still need to get", "shopping"),
                 ("what's on my to do", "todo"), ("what needs doing", "todo"),
                 ("what's on my chore list", "todo"), ("tell me my to-dos", "todo"),
                 ("what jobs are left", "todo"), ("anything left on my to-do list", "todo"),
                 ("list my chores", "todo")]:
        add("lists", t, T(LD, list=l), kind="read")
    for g in rng.sample(GROCERY, 16):
        t = rng.choice(["got {g}", "picked up the {g}", "{g} is taken care of", "scratch {g}",
                        "we're good on {g}, take it off", "found {g}, cross it off"])
        add("lists", t.format(g=g), T(LR, list="shopping", items=[g]), kind="remove-shopping")
    for done, key in CHORE_DONE:
        t = rng.choice(["finished the {k}", "the {k} is done", "mark the {k} done", "{k}, done"])
        add("lists", t.format(k=key), T(LR, list="todo", items=[key]), kind="remove-todo")
    for t in ["what reminders are coming up", "list my reminders", "any reminders today",
              "what have I asked you to remind me", "is anything due", "check my reminders",
              "do I have alarms set", "what alerts are waiting", "anything I need to remember",
              "what's on my reminders"]:
        add("alerts", t, T(AR), kind="read")
    just = "An alert just went off: '{x}'."
    dismiss = ["thanks, got it", "done", "handled", "I'm on it", "already did it", "okay okay",
               "dismiss", "clear it", "yep, done", "all right, I heard you", "stop the alarm", "that's done"]
    snooze = ["snooze", "later", "give me a minute", "hold off", "remind me again in a bit", "not yet"]
    for t in dismiss:
        x = rng.choice(REMIND)
        add("alerts", t, T(AA, action="dismiss"), context=just.format(x=x[0].upper() + x[1:]), kind="act-dismiss")
    for t in snooze:
        x = rng.choice(REMIND)
        add("alerts", t, T(AA, action="snooze"), context=just.format(x=x[0].upper() + x[1:]), kind="act-snooze")
    for m in [2, 3, 20, 45]:
        x = rng.choice(REMIND)
        add("alerts", f"snooze {m} minutes", T(AA, action="snooze", minutes=m),
            context=just.format(x=x[0].upper() + x[1:]), kind="act-snooze")
    asks = [("music", "play that one", "Which one should I play?"), ("music", "set it to 30", "The volume, or a light?"),
            ("music", "play the thing from earlier", "What were you listening to?"),
            ("inventory", "how many are left", "How many of what?"), ("inventory", "where is it", "Where is what?"),
            ("inventory", "the thing broke", "Which tool broke?"), ("inventory", "lent it to the neighbor", "What did you lend?"),
            ("lists", "add that", "What should I add?"), ("lists", "take it off", "What should I take off?"),
            ("lists", "put it on the list", "What should I put on the list?"),
            ("alerts", "remind me", "What should I remind you about?"), ("alerts", "snooze it", "There's no alert going. Did you mean a reminder?"),
            ("calendar", "put it on the calendar", "What should I put on the calendar, and when?"),
            ("calendar", "when is it", "When is what?"), ("lights", "off", "What should I turn off?"), ("lights", "change it", "Change what?")]
    for dom, t, q in asks:
        add(dom, t, expect="ask", say=q, kind="ask-no-referent")


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def main():
    for build in (music, lights, inventory, lists, calendar, weather, alerts, nothing, boost):
        build()
    test = [json.loads(l) for l in (HERE / "cases.jsonl").read_text().splitlines() if l.strip()]
    test_text = {norm(c["text"]) for c in test}
    seen, rows, dropped = set(), [], []
    for r in OUT:
        k = norm(r["text"])
        if k in test_text:
            dropped.append(r["text"])
        elif k not in seen:
            seen.add(k)
            rows.append(r)
    rng.shuffle(rows)
    for i, r in enumerate(rows):
        r["id"] = f"tr-{i:04d}"
        r["split"] = "val" if i % 10 == 0 else "train"
    (HERE / "train.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    from collections import Counter
    tools = Counter(c["tool"] for r in rows for c in r["calls"])
    print(f"{len(rows)} utterances, {sum(r['split'] == 'val' for r in rows)} held out as val")
    print("by expect:", dict(Counter(r["expect"] for r in rows)))
    print("by domain:", dict(Counter(r["domain"] for r in rows)))
    print("by tool:", dict(sorted(tools.items())))
    print(f"dropped as exact test matches ({len(dropped)}):", dropped)
    # vocabulary leak: test argument values that turn up verbatim in training text
    vals = {str(v).lower() for c in test for call in c["calls"] for v in call["args"].values()
            if isinstance(v, str) and len(v) > 3}
    vals |= {i.lower() for c in test for call in c["calls"] for v in call["args"].values()
             if isinstance(v, list) for i in v}
    hits = Counter(v for r in rows for v in vals if re.search(rf"\b{re.escape(v)}\b", r["text"].lower()))
    print("test argument values that appear in training text:", dict(hits.most_common(25)))


if __name__ == "__main__":
    main()
