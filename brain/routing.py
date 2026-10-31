"""Which tools and context a turn gets, decided by code from the words and local data.

context_plan() picks skills, the garden bed in focus, and whether lights or soil readings belong
in the prompt. request_schemas() narrows the offered tools from that plan and the intent words.
required_writes() spots explicit commands ("turn off the kitchen lights", "remind me to ...") so
the loop can insist a tool runs. No model call and no network I/O in here: routing is English
keywords today, and a learned router (exp/small-router) would replace this module, not the guards.
"""
from __future__ import annotations

import re
from contextvars import ContextVar

import briefing_store
import goals_store
import records_store
import tools

# The turn's plan, computed once by the loop and reused by request_schemas().
prepared: ContextVar[dict | None] = ContextVar("prepared_context", default=None)


_LIGHT_CONTEXT = ("light", "lights", "lamp", "lamps", "bulb", "bulbs", "lighting",
                  "dim", "brighten", "brightness", "light strip", "lamppost")


def context_plan(user_text: str) -> dict:
    """Classify the dynamic context a turn needs using only local data.

    This runs in a worker thread because skill discovery and garden lookup read disk/SQLite.
    It lets ordinary conversation skip Home Assistant instead of fetching every light and soil
    sensor before every model call.
    """
    selected = tools.skill.matches(user_text)
    matched = selected[0] if selected else None
    garden_focus = records_store.garden_lookup(user_text)
    garden_topic = bool(garden_focus) or any(s["name"] == "garden_bed" for s in selected)
    text = user_text.lower()
    return {"matched": matched, "selected": selected, "query": user_text, "garden_focus": garden_focus, "garden_topic": garden_topic,
            "lights": any(s["name"] == "home_control" for s in selected)
            or any(re.search(rf"\b{re.escape(term)}\b", text) for term in _LIGHT_CONTEXT),
            # The garden skill's procedure relies on live readings, including broad bed questions.
            # Keep that behavior, but do not load soil for unrelated turns.
            "soil": garden_topic}





# A soil-STATE readout ("what's the moisture", "are the beds dry", "soil readings") is
# answered straight from the injected SOIL block — no tool call is ever correct. We offer
# ZERO tools for these so the 14B can't misfire into `weather` (rain forecast), which it did
# intermittently on "what's the moisture of the garden beds". A watering DECISION ("should I
# water the carrots") keeps the full garden toolset, because that genuinely needs the forecast.
_SOIL_STATE = ("moisture", "moist", "soil", "dry", "wet", "damp", "parched", "soaked", "dryness")
_WATER_DECISION = ("water", "irrigat")


def is_soil_readout(user_text: str) -> bool:
    """Only an unambiguous single state question may suppress tools."""
    t = user_text.lower().strip()
    return (bool(re.match(r"^(?:what|how|is|are|show|tell me)\b", t))
            and any(re.search(rf"\b{word}\b", t) for word in _SOIL_STATE)
            and not re.search(r"\b(?:and|then|also|log|record|remember|remind|water|watering|irrigate|irrigation)\b|[;\n]", t))


INTENT_TOOLS = {
    'home': r"\b(?:light|lights|lamp|lamps)\b|\bturn\b[^.!?]{0,120}\b(?:on|off)\b",
    'reminder': r"\b(?:remind|reminder|timer|alarm)\b",
    'goals': r"\bgoals?\b|\bmilestones?\b|\bplan (?:out|for)\b|\bmake a plan\b",
    'calendar': r"\b(?:calendar|schedule|scheduled|appointment|appointments|event|events|birthday|birthdays|anniversary|coming up|this week|next week|weekend)\b",
    'shopping': r"\b(?:shopping|grocery|groceries|out of|buy)\b|\badd\b.+\blist\b",
    'memory': r"\b(?:remember|recall|preference|prefer|previously told)\b",
    'records': r"\b(?:log|record|harvest|harvested|picked|vaccinated|thinned)\b",
    'search': r"\b(?:search|look up|news|web)\b",
    'status': r"\b(?:disk|system status|service status|uptime)\b",
    # How-to, timing and diagnosis words from the library's subjects (LIBRARY-DESIGN.md).
    'library': (r"\b(?:how (?:do|to|should|can|often|long)|when (?:do|should|to)|what(?:'s| is) (?:eating|wrong)|"
                r"prun\w*|propagat\w*|fertili[sz]\w*|compost\w*|mulch\w*|overwinter\w*|pests?|aphids?|"
                r"mites?|borers?|sawfl\w*|leafhoppers?|fung\w*|mildew|mold|mould|blight|diseases?|invasive|"
                r"ticks?|chickens?|go bag|expir\w*|sell[- ]by|use[- ]by|emergency|power (?:is )?out|"
                r"outage|seeds?|frost|drought|irrigat\w*|pollinat\w*|bees?|earthworms?|microgreens?|"
                r"canning|canner|canned|jars?|headspace|pickl\w*|ferment\w*|jams?|jell(?:y|ies)|"
                r"preserv\w*|maple|sap|syrup|evaporator|forag\w*|edible|wild (?:plants?|food)|herbs?|"
                r"elderberr\w*|purif\w*|boil(?:ing)? water|drinking water|water (?:filter|treatment)|"
                r"survival|shelter|fire ?starting|start a fire|navigat\w*|first aid|snake ?bites?|"
                r"bug (?:in|out)|evacuat\w*|prepared\w*|go ?bag)\b"),
}


def routing_text(messages: list[dict]) -> str:
    latest = messages[-1]['content']
    if len(latest) < 300 and re.match(r'^(?:what about|how about|and |which one|was that|did it)\b', latest, re.I):
        topics = [m['content'] for m in messages[-7:-1] if m['role'] == 'user' and briefing_store.requested(m['content'])]
        if topics:
            return topics[-1][-1000:] + '\nFollow-up: ' + latest
    if len(latest) < 300 and re.search(r"\b(?:it|them|those|that|there|same|again|back)\b", latest, re.I):
        prior = [m['content'] for m in messages[:-1] if m['role'] == 'user']
        if prior:
            return prior[-1][-1000:] + "\nFollow-up: " + latest
    return latest


def required_writes(text: str) -> set[str]:
    """Narrow explicit command detection, not a general intent classifier."""
    start = r"(?:^|\band\s+|\bthen\s+)(?:please\s+)?"
    patterns = {
        'records': start + r"(?:log|record)\b",
        'home': start + r"(?:(?:turn|switch|flip|shut)\b[^.!?]{0,120}\b(?:on|off)\b|(?:kill|cut|put)\b)",
        'reminder': start + r"(?:remind me|set (?:a |an )?(?:timer|reminder))\b",
        'shopping': start + r"add\b[^.!?]*\b(?:shopping|grocery) list\b",
        'calendar': start + r"(?:add|put|schedule)\b[^.!?]*\bcalendar\b",
    }
    found = {name for name, pattern in patterns.items() if re.search(pattern, text, re.I)}
    # The home tool only actuates lights: "turn on the news" is not a write it could attempt.
    # "Kill", "cut" and "put" need a light named: "cut it out" and "put it on the list" are not
    # light commands. Only turn/switch/flip/shut with on/off may refer back ("turn it off").
    on_off = re.search(start + r"(?:turn|switch|flip|shut)\b[^.!?]{0,120}\b(?:on|off)\b", text, re.I)
    referent = r"light|lights|lamp|lamps|everything|all" + (r"|it|them|those" if on_off else "")
    if 'home' in found and not (re.search(rf"\b(?:{referent})\b", text, re.I)
                                or names_a_light_area(text, start)):
        found.discard('home')
    return found


def names_a_light_area(text: str, start: str) -> bool:
    """"Shut off the outside": a command whose object is one of the house's light areas and
    ends the sentence, so "kill the outside noise" or "turn on the kitchen radio" stays talk."""
    areas = tools.home.light_areas()
    if not areas:
        return False
    area = r"(?:the\s+|my\s+)?(?:" + "|".join(map(re.escape, areas)) + r")(?:\s+lights?)?"
    tail = r"\s*(?:now|please)?\s*[.!]*\s*$"
    verb_first = r"(?:(?:turn|switch|flip|put)\s+(?:on|off)|shut\s+off|kill|cut)\s+"
    verb_last = r"(?:turn|switch|flip|put|shut)\s+" + area + r"\s+(?:on|off)" + tail
    return bool(re.search(start + r"(?:" + verb_first + area + tail + "|" + verb_last + ")", text, re.I))


def finishes_open_step(query: str) -> bool:
    try:
        return records_store.DB_PATH.exists() and bool(goals_store.match_open_steps(query))
    except Exception:  # noqa: BLE001 — routing must never fail a turn over the goals table
        return False


def request_schemas(user_text: str) -> list:
    """Reuse the prepared plan. Schema selection performs no network I/O."""
    plan = prepared.get() or context_plan(user_text)
    selected = plan.get('selected', [])
    allow = {name for skill in selected for name in skill.get('tools', [])}
    query = plan.get('query', user_text).lower()
    explicit = {name for name, pattern in INTENT_TOOLS.items() if re.search(pattern, query)}
    if plan['garden_topic']:
        allow.update(('home', 'weather', 'records', 'library'))
    allow.update(explicit)
    # "Finally ordered the heater" names no goal, but it finishes an open step: offer the tool
    # when the words overlap one. A local read; an empty allow set already means every tool.
    if allow and 'goals' not in allow and finishes_open_step(query):
        allow.add('goals')
    if (plan['garden_topic'] and is_soil_readout(user_text) and plan.get('soil_available')
            and not explicit):
        return []
    return [s for s in tools.SCHEMAS if not allow or s['function']['name'] in allow]
