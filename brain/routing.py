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
    # House docs (docs_store.py): a write-up, a card, a checklist, a note on something owned.
    'doc': r"\b(?:write (?:it |that |this )?up|write-up|writeup|(?:make|draft|write|put together|create) "
           r"(?:me |us )?(?:a |an )?(?:[\w-]+ ){0,3}(?:doc|document|page|card|checklist|guide|cheat ?sheet)|"
           r"add (?:a )?note (?:to|on|about)|house docs?)\b",
    'calendar': r"\b(?:calendar|schedule|scheduled|appointment|appointments|event|events|birthday|birthdays|anniversary|coming up|this week|next week|weekend)\b",
    'shopping': r"\b(?:shopping|grocery|groceries|out of|buy)\b|\badd\b.+\blist\b",
    'memory': r"\b(?:remember|recall|preference|prefer|previously told)\b",
    'records': r"\b(?:log|record|harvest|harvested|picked|vaccinated|thinned)\b",
    'search': r"\b(?:search|look up|news|web)\b",
    'status': r"\b(?:disk|system status|service status|uptime)\b",
    # How-to, timing and diagnosis words from the library's subjects (LIBRARY-DESIGN.md).
    # The house's own supplies: bought, used up, counted, put somewhere, asked after.
    'inventory': (r"\b(?:inventory|in stock|on hand|do we (?:still )?have|have we got|how (?:much|many) .*\b(?:left|have)|"
                  r"bought|picked up|used (?:up|the last|the rest|half|some|all|a bit|a little|a couple|a few|\d)|"
                  r"ran out|running low|left over|found .{0,40}\b(?:in|under|behind) (?:a|the|my|our) "
                  r"(?:drawer|shed|garage|pantry|closet|cupboard|basement|cabinet|box|bin|attic)|"
                  r"where(?:'s| is| are| did (?:i|we) put) (?:the|my|our)|put (?:the|it|them) in|is in the|are in the|"
                  r"old stock|expired|stocked)\b"),
    'library': (r"\b(?:how (?:do|to|should|can|often|long)|when (?:do|should|to)|what(?:'s| is) (?:eating|wrong)|"
                r"prun\w*|propagat\w*|fertili[sz]\w*|compost\w*|mulch\w*|overwinter\w*|pests?|aphids?|"
                r"mites?|borers?|sawfl\w*|leafhoppers?|fung\w*|mildew|mold|mould|blight|diseases?|invasive|"
                r"ticks?|chickens?|go bag|expir\w*|sell[- ]by|use[- ]by|emergency|power (?:is )?out|"
                r"outage|seeds?|frost|drought|irrigat\w*|pollinat\w*|bees?|earthworms?|microgreens?|"
                r"canning|canner|canned|jars?|headspace|pickl\w*|ferment\w*|jams?|jell(?:y|ies)|"
                r"preserv\w*|maple|sap|syrup|evaporator|forag\w*|edible|wild (?:plants?|food)|herbs?|"
                r"elderberr\w*|purif\w*|boil(?:ing)? water|drinking water|water (?:filter|treatment)|"
                r"survival|shelter|fire ?starting|start a fire|navigat\w*|first aid|snake ?bites?|"
                r"bug (?:in|out)|evacuat\w*|prepared\w*|go ?bag|"
                # Radio and off-grid communications, backup power, emergency medicine.
                r"radios?|gmrs|frs|murs|cb|ham (?:radio|license)|antennas?|repeaters?|frequenc\w*|walkie|"
                r"batter(?:y|ies)|generators?|inverters?|solar|volts?|voltage|amps?|wiring|multimeter|"
                r"bleed\w*|tourniquet|wounds?|burns?|fractur\w*|splint\w*|sprain\w*|hypotherm\w*|"
                r"frostbite|heat ?stroke|heat exhaustion|dehydrat\w*|cpr|chok\w*|concussion|stitch\w*|"
                r"sutur\w*|infect\w*|toothache|abscess|childbirth|in labor|"
                # The household's own papers, filed from the phone (library_store.add_scanned).
                r"warrant(?:y|ies)|guarantee|manuals?|user guide|instructions|serial (?:number|no)|"
                r"model (?:number|no))\b"),
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
        'doc': start + (r"(?:write (?:it |that |this )?up|(?:make|draft|write|put together|create) "
                        r"(?:me |us )?(?:a |an )?(?:[\w-]+ ){0,3}(?:doc|document|page|card|checklist|guide|"
                        r"cheat ?sheet)|add (?:a )?note (?:to|on|about))\b"),
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


# What the person asked to have written, in their own words, for the untrusted-content guard
# (HARNESS.md, "Actions and retries"): once a turn has read a web page, a write runs only if its
# tool is in asked_writes(). These cover phrasings required_writes() leaves out on purpose,
# because that set also drives the missing-write nudge. Narrow English on purpose: a tool with no
# match here simply cannot write after web content in that turn.
_NOT = r"(?<!don't )(?<!do not )(?<!never )"
_ASKED = {
    'memory': _NOT + r"(?<!do you )(?<!did you )\bremember (?:that|this|it|my|our|me|we|i)\b|\bremember:|"
              r"\bsave (?:that|this|it)\b|\bnote (?:that|this|it|down)\b|\bmake a note\b|"
              r"\bdon'?t forget\b|\bdo not forget\b|\bkeep in mind\b",
    'records': r"\b(?:log|record)\s+(?:that|this|it|the|a|an|my|our|today|\d)|"
               r"\bkeep track of\b|\bstart tracking\b|\b(?:i|we) (?:just )?(?:picked|harvested)\b|"
               r"\b(?:was|were) born\b|\bweigh(?:s|ed)\b",
    'reminder': _NOT + r"\bremind (?:me|us)\b|\bset (?:a |an )?(?:timer|reminder|alarm)\b|"
                r"\b(?:cancel|delete|remove|drop)\b[^.!?]{0,40}\breminders?\b",
    'shopping': r"\b(?:add|put)\b[^.!?]{0,80}\b(?:on|to) (?:the |my |our )?(?:shopping |grocery )?list\b|"
                r"\b(?:take|cross|remove)\b[^.!?]{0,80}\b(?:off|from) (?:the |my |our )?(?:shopping |grocery )?list\b|"
                r"\bwe(?:'re| are) out of\b",
    'calendar': r"\b(?:add|put|schedule)\b[^.!?]{0,80}\bcalendar\b",
    'media': _NOT + r"\bdownload\b|"
             r"\b(?:grab|queue(?: up)?)\b[^.!?]{0,60}\b(?:movie|film|show|series|season|episode|album|song|track|music)s?\b|"
             r"\b(?:grab|queue(?: up)?) (?:it|that|this|them)\b|"
             r"\badd\b[^.!?]{0,80}\b(?:movie|film|show|series|season|album|song)s?\b(?![^.!?]*\blist\b)|"
             r"\badd\b[^.!?]{0,80}\bto (?:plex|the library|sonarr|radarr|lidarr)\b",
    'recipe': r"\b(?:save|import|keep|add|store)\b[^.!?]{0,60}\brecipes?\b|"
              r"\brecipe\b[^.!?]{0,40}\b(?:save|import|keep)\b|\bsave (?:that|this|it)\b",
    'inventory': r"\b(?:i|we) (?:just |also )?(?:bought|picked up|moved|stored|counted|ran out of|"
                 r"used (?:up|the last|the rest|half|some|all|a bit|a few|\d)|put (?:the|it|them|\d))\b|"
                 r"\b(?:add|log)\b[^.!?]{0,60}\binventory\b|\bwe(?:'re| are) (?:out of|down to|low on)\b|"
                 r"\bwe have (?:\d+|a few|a couple|some|two|three|four|five|six|a dozen)\b",
    'doc': r"\b(?:write (?:it |that |this )?up|write-up|writeup|(?:make|draft|write|put together|create) "
           r"(?:me |us )?(?:a |an )?(?:[\w-]+ ){0,3}(?:doc|document|page|card|checklist|guide|cheat ?sheet)|"
           r"add (?:a )?note (?:to|on|about)|house docs?)\b",
    'goals': r"\b(?:make|draft|propose|set up|create|start)\b[^.!?]{0,40}\b(?:goal|plan)\b|"
             r"\bplan (?:out|for)\b|\b(?:mark|check off)\b[^.!?]{0,60}\b(?:done|complete|finished)\b|"
             r"\b(?:i|we) (?:just |finally )?(?:finished|ordered|built|completed)\b",
    'home': r"\b(?:turn|switch|flip|shut)\b[^.!?]{0,120}\b(?:on|off)\b",
}


def asked_writes(text: str) -> set[str]:
    """The write tools the person asked for in their own words: required_writes() plus the
    phrasings above. Read only by the guard on writes after web content."""
    required = required_writes(text)
    extra = {name for name, pattern in _ASKED.items() if re.search(pattern, text, re.I)}
    # "Turn off the news" is not a light: the home tool needs a light or a light area named.
    areas = "|".join(map(re.escape, tools.home.light_areas()))
    if 'home' in extra - required and not re.search(
            r"\b(?:light|lights|lamp|lamps|it|them|everything" + (f"|{areas}" if areas else "") + r")\b", text, re.I):
        extra.discard('home')
    return required | extra


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
    if 'doc' in allow:
        allow.update(('library', 'records'))   # a doc draws on the shelf and the house's own records
    # "Finally ordered the heater" names no goal, but it finishes an open step: offer the tool
    # when the words overlap one. A local read; an empty allow set already means every tool.
    if allow and 'goals' not in allow and finishes_open_step(query):
        allow.add('goals')
    if (plan['garden_topic'] and is_soil_readout(user_text) and plan.get('soil_available')
            and not explicit):
        return []
    return [s for s in tools.SCHEMAS if not allow or s['function']['name'] in allow]
