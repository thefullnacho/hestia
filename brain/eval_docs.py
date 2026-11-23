"""House docs probe: asked for a doc, does the model propose one, of the right kind, about the right
thing, citing only what it was shown, and say it waits on Today rather than that it's saved?

Production agent loop over the isolated eval fixtures (eval_support), with a three-document shelf
(keyword search, no embedder) and two records entities.

  EVAL_REPEATS=2 uv run python eval_docs.py
"""
from __future__ import annotations

import os
import re

import eval_support as ev
from eval_support import hestia, records_store

import docs_store
import library_store

REPEATS = int(os.environ.get("EVAL_REPEATS", "2"))
SAVED = re.compile(r"\b(?:saved|filed|added (?:it )?to the library|it'?s in the library|kept it)\b", re.I)

CASES = [
    ("guide", None, "Make me a GMRS card for the family: our channels, how to call, and when."),
    ("guide", None, "Draft a checklist for running the generator during a power outage."),
    ("guide", None, "Put together a one-page guide for treating hypothermia, for the go bag."),
    ("account", None, "Write up what happened: the generator wouldn't start this morning at 15F, it took "
                      "the choke closed and six pulls, then ran fine for four hours."),
    ("account", None, "Write up last night's outage: power out 9pm to 2am, we ran the generator, the sump "
                      "pump kept up, and the GMRS radios reached Dawn's parents on channel 20."),
    ("account", None, "Write up the Biscuit vet visit: ear infection, drops twice a day for ten days."),
    ("note", "Generator", "Add a note to the generator: the choke sticks below 20F, tap it twice before pulling."),
    ("note", "Generator", "Add a note on the generator that the oil fill cap cross-threads easily, start it by hand."),
    ("note", "Biscuit", "Add a note to Biscuit: he hates the ear drops, give a treat first."),
    ("note", "Generator", "Look up how the manual says to start the generator cold, and add a note to the "
                          "generator about our own cold-start trick: tap the choke twice."),
]


def seed() -> None:
    library_store.index_doc("gen.pdf", [
        "Cold start: set the choke to CLOSED, pull the starter slowly until resistance, then pull briskly. "
        "Open the choke gradually as the engine warms. Never run indoors; carbon monoxide kills.",
        "Oil: check before each use. Change after the first 20 hours, then every 50 hours."],
        fix={"title": "Portable Generator Owner's Manual", "year": 2021})
    library_store.index_doc("gmrs.pdf", [
        "GMRS channels 15 through 22 allow up to 50 watts. Channels 8 through 14 are limited to 0.5 watts. "
        "Identify with your call sign every 15 minutes and at the end of a conversation."],
        fix={"title": "47 CFR Part 95: Personal Radio Services", "year": 2025})
    library_store.index_doc("hypo.pdf", [
        "Hypothermia: remove wet clothing, insulate from the ground, wrap in dry blankets, handle gently. "
        "Shivering that stops is a danger sign. Warm sweet drinks only if fully awake."],
        fix={"title": "Hypothermia Prevention and Management", "year": 2023})
    records_store.upsert_entity("asset", "Generator")


def run_case(kind, about, text):
    with ev.fixtures():
        seed()
        answer = ev.run(hestia.run_agent([{"role": "user", "content": text}]))
        docs = docs_store.pending()
    problems = []
    if not docs:
        problems.append("no proposal")
    else:
        d = docs[-1]
        if d["kind"] != kind:
            problems.append(f"kind {d['kind']}")
        if about and (d["about"] or "").lower() != about.lower():
            problems.append(f"about {d['about']!r}")
    if SAVED.search(answer) and "today" not in answer.lower():
        problems.append("says saved")
    return problems, answer


def main() -> None:
    model = ev.backend.OPENAI_MODEL if ev.backend.BACKEND == "openai" else ev.backend.MODEL
    print(f"MODEL: {model}  (repeats={REPEATS})")
    good = total = 0
    for kind, about, text in CASES:
        results = [run_case(kind, about, text) for _ in range(REPEATS)]
        n = sum(not p for p, _ in results)
        good, total = good + n, total + REPEATS
        flags = sorted({x for p, _ in results for x in p})
        print(f"  {'✅' if n == REPEATS else '❌'} {kind:7} {text[:58]:58} {n}/{REPEATS}  {', '.join(flags)}")
    print(f"  -- {good}/{total}")


if __name__ == "__main__":
    main()
