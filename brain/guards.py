"""Code's checks on the model's words: no model call in here, and none should ever be added.

The claim guard catches a reply that says something was done when no tool ran ("Reminder #23
set", "I've saved that"); the loop shows UNDONE_CLAIM instead. The safety referrals add fixed
lines after library answers on medication doses and on eating wild plants. A bare link gets a
fixed reply without the model. These stay code
because they are the check on the model: a model cannot be trusted to grade its own claims.
"""
from __future__ import annotations

import re

# A reply that claims an action when no tool ran this turn. Shown instead of the claim.
UNDONE_CLAIM = ("I didn't actually do that: nothing was saved or set. "
                "Please say it again, for example \"remind me to ... on ...\".")
# A message that is only a link. Chat can't read a page, so code answers without the model.
LINK_ONLY_REPLY = ("I can't open a link from chat. If it's a recipe, open Recipes in the app, "
                   "choose \"Recipe or PDF URL\" under Import a recipe, paste the link and press "
                   "Prepare a draft. Facebook and Instagram links usually can't be read: choose "
                   "\"Paste recipe text\" and paste the recipe itself instead.")
_URL = re.compile(r"https?://\S+", re.I)
_ASKS = re.compile(r"\b(?:save|import|add|keep|draft|read|summar\w*|open|what|how|why|can|could|would|"
                   r"please|recipe|tell|look|check)\b|\?", re.I)
# Completion claims that only a tool receipt may make: an action verb and a store in one
# sentence ("imported ... into your recipe collection", "Reminder #23 set"). Narrow on purpose,
# so "I've put together a summary" passes; and it only applies when no tool ran at all.
_CLAIM_VERB = (r"\b(?:set|added|logged|saved|imported|scheduled|created|recorded|drafted|booked|"
               r"marked|crossed off|removed)\b")
_CLAIM_STORE = (r"\b(?:reminders?|alarm|timer|(?:shopping|grocery|to-?do) list|list|calendar|recipes?|"
                r"collection|drafts?|log|records?|inventory|pantry|goals?|plans?|steps?)\b")
_CLAIMS = re.compile(rf"\breminder #\d+ set\b|{_CLAIM_VERB}[^.!?\n]{{0,80}}{_CLAIM_STORE}|"
                     rf"{_CLAIM_STORE}[^.!?\n]{{0,40}}\b(?:is|are|has been|have been|was) "
                     rf"(?:now )?{_CLAIM_VERB}", re.I)
# The same claim in the shapes smaller models use (2026-10-29, Nanbeige and Gemma 4 E4B/E2B):
# "I have saved that ...", "Saved that to memory", "Turning off the kitchen lights", "It's
# queued and downloading". Held to the first person, a pronoun object, or on/off, so "Turning
# the compost weekly helps" and "Saved seeds keep for years" stay advice.
_DID = (r"(?:saved|logged|recorded|added|set|scheduled|created|stored|queued|started|grabbed|"
        r"downloaded|marked|updated|removed|deleted|turned|switched|booked)")
_OPENER = r"(?:(?:done|ok(?:ay)?|sure|got it|all set)\W+)?"
QUESTION = re.compile(r"\b(?:what|how|why|which|where|should|tell me)\b|\?", re.I)
# A tool call written out as text ("records:log(did='vaccinated', ...)"): the model knew the call
# and fumbled the format. Never run from the text; it only earns the schema-checked repair.
TEXT_CALL = re.compile(r"^\s*`?[a-z_]+[:.][a-z_]+\((?:[a-z_]+\s*=|\))", re.I)
_SAID_DONE = re.compile(
    rf"\bI(?:'ve| have)?\s+(?:just\s+|now\s+|also\s+|already\s+)?{_DID}\b"
    rf"|^{_OPENER}{_DID}\s+(?:that|it|this|them|those|to)\b"
    rf"|^{_OPENER}(?:turning|switching)\s+(?:on|off)\b[^.!?]{{0,40}}"
    rf"\b(?:lights?|lamps?|fans?|switch(?:es)?|everything|all of them)(?:\s+now)?[.!]?$"
    rf"|^{_OPENER}(?:grabbing|downloading|queu(?:e)?ing)\s+(?!(?:is|was|are|can|takes?|will|may|might)\b)"
    rf"[^.!?]*\b(?:for you|now|it|that)\b"
    rf"|\b(?:it's|it is|they're|they are)\s+(?:now\s+)?(?:queued|downloading|saved|logged|recorded|stored)\b",
    re.I)


def link_only(messages: list[dict]) -> bool:
    """The newest message is a link with at most a short label and no request in it."""
    if not messages or messages[-1].get("role") != "user":
        return False
    text = messages[-1].get("content")
    if not isinstance(text, str) or len(_URL.findall(text)) != 1:
        return False
    rest = _URL.sub(" ", text)
    return len(re.findall(r"[A-Za-z']+", rest)) <= 4 and not _ASKS.search(rest)


_ON_STORE = re.compile(rf"\b(?:it's|it is|that's|that is|they're|they are)\s+(?:now\s+)?(?:on|in)\s+"
                       rf"(?:the|your)\s+(?:\w+\s+)?{_CLAIM_STORE}", re.I)
# Advice and offers mention the same verbs without claiming anything happened.
_NOT_A_CLAIM = re.compile(r"\b(?:could|should|can|might|may|want to|wish to|need to|like me to|shall|"
                          r"try|if you|to (?:set|add|log|save|import|schedule|create))\b", re.I)


def claims_action(answer: str) -> bool:
    """A sentence that says something was done: never a question, never advice or an offer."""
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer or ""):
        sentence = sentence.strip()
        if not sentence or sentence.endswith("?"):
            continue
        hit = _CLAIMS.search(sentence) or _ON_STORE.search(sentence) or _SAID_DONE.search(sentence)
        if hit and not _NOT_A_CLAIM.search(sentence[:hit.end()]):
            return True
    return False


_MED_ASK = re.compile(r"\b(?:dose|doses|dosage|how much|stop(?:ping)? taking|skip(?:ping)?|substitut\w*|"
                      r"double up|ration\w*|cut (?:my|her|his|the) pills?)\b", re.I)
_MED_WORD = re.compile(r"\b(?:medic(?:ation|ine)s?|meds|insulin|prescriptions?|pills?|inhalers?)\b", re.I)
MED_REFERRAL = "For a dose, a substitute, or stopping a medication, check with your pharmacist or doctor."
_EAT_ASK = re.compile(r"\b(?:edible|safe to eat|can (?:i|we|you) eat|ok to eat|okay to eat|poisonous|toxic)\b", re.I)
EDIBLE_REFERRAL = ("Never eat a wild plant, berry or mushroom on a written description alone; have the "
                   "identification confirmed in person by someone who knows the species.")


def library_addendum(answer: str, citations: list, question: str) -> str:
    """Code's lines after a library answer: the source when the reply names none of the
    passages it was given, and the pharmacist line for any dose or stopping question."""
    out = []
    if citations:
        titles = [re.sub(r", (?:\d{4}, )?p\.\d+$", "", c) for c in citations]
        named = any(" ".join(t.lower().split()[:3]) in answer.lower() for t in titles)
        # "The library doesn't cover that" is an answer with no source; citing one would
        # suggest the opposite of what was said.
        declined = re.search(r"library (?:does ?n[o']t|doesn't|has no|lacks)|not in the library|"
                             r"(?:no|any) (?:specific )?information (?:on|about)", answer, re.I)
        if not named and not declined:
            out.append("Source: " + citations[0] + ".")
    if (_MED_WORD.search(question) and _MED_ASK.search(question)
            and not re.search(r"\b(?:pharmacist|doctor|physician|prescriber|nurse|health ?care provider)\b", answer, re.I)):
        out.append(MED_REFERRAL)
    if (citations and _EAT_ASK.search(question)
            and not re.search(r"\bin person\b|\bexpert\b|positive(?:ly)? identif", answer, re.I)):
        out.append(EDIBLE_REFERRAL)
    return "\n".join(out)
