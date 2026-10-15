"""Shared pieces for the tool caller: prompts, parsing FunctionGemma output, and scoring.

The caller only ever sees one domain's tools; the router picked the domain. Scoring follows
build_cases.py: free-text args pass when every expected word is in the model's value, enum and
number args must match exactly, args not in the expectation are not checked, an arg expected
as null must be left out, multi-call cases
pass when the set of calls matches in any order, and `accept` lists other answers that pass.
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

import schema

HERE = Path(__file__).parent
DEV = "You are a model that can do function calling with the following functions"
DOMAINS = schema.load()
TOOL_DOMAIN = {t["function"]["name"]: d for d, ts in DOMAINS.items() for t in ts}
PARAMS = {t["function"]["name"]: t["function"]["parameters"]["properties"]
          for ts in DOMAINS.values() for t in ts}


def domain_of(row: dict) -> str | None:
    """The domain whose tools the caller sees for this row, or None to skip it here."""
    if row["calls"]:
        return TOOL_DOMAIN[row["calls"][0]["tool"]]
    if row["domain"] in DOMAINS:
        return row["domain"]
    if row["domain"] == "none":
        # a misrouted "nothing to do" lands on some domain; the caller must still do nothing
        return random.Random(row["id"]).choice(sorted(DOMAINS))
    return None  # ambiguous with no domain: only the end-to-end run can score it


def messages(row: dict, with_answer: bool = False) -> list[dict]:
    dev = f"{row['context']}\n{DEV}" if row.get("context") else DEV
    msgs = [{"role": "developer", "content": dev}, {"role": "user", "content": row["text"]}]
    if with_answer:
        if row["calls"]:
            msgs.append({"role": "assistant", "tool_calls": [
                {"type": "function", "function": {"name": c["tool"], "arguments": c["args"]}}
                for c in row["calls"]]})
        else:
            msgs.append({"role": "assistant", "content": row.get("say", "")})
    return msgs


CALL = re.compile(r"<start_function_call>call:(\w+)\{(.*?)\}<end_function_call>", re.S)
ARG = re.compile(r"(\w+):(?:<escape>(.*?)<escape>|\[(.*?)\]|([^,}]+))", re.S)


def parse(text: str) -> tuple[list[dict], str]:
    """Calls the model made, and whatever plain text it said."""
    calls = []
    for name, body in CALL.findall(text):
        args = {}
        for key, esc, arr, bare in ARG.findall(body):
            if arr is not None and arr != "":
                args[key] = re.findall(r"<escape>(.*?)<escape>", arr) or [x.strip() for x in arr.split(",")]
            elif esc != "" or bare == "":
                args[key] = esc
            else:
                bare = bare.strip()
                args[key] = int(bare) if re.fullmatch(r"-?\d+", bare) else bare
        calls.append({"tool": name, "args": args})
    said = CALL.sub("", text)
    said = re.sub(r"<[a-z_]+>", "", said).strip()
    return calls, said


def words(s) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(s).lower())


def arg_ok(tool: str, key: str, want, got) -> bool:
    if want is None:  # must be left out, e.g. no room named: code fills in the device's room
        return got is None or got == ""
    if got is None:
        return False
    spec = PARAMS.get(tool, {}).get(key, {})
    if isinstance(want, bool) or isinstance(want, int):
        try:
            return int(got) == want
        except (TypeError, ValueError):
            return False
    if isinstance(want, list):
        got_items = got if isinstance(got, list) else [got]
        return all(any(set(words(w)) <= set(words(g)) for g in got_items) for w in want)
    if "enum" in spec:
        return str(got).strip().lower() == str(want).lower()
    return set(words(want)) <= set(words(got))


def call_ok(want: dict, got: dict) -> bool:
    return want["tool"] == got["tool"] and all(
        arg_ok(want["tool"], k, v, got["args"].get(k)) for k, v in want["args"].items())


def calls_match(want: list[dict], got: list[dict]) -> bool:
    if len(want) != len(got):
        return False
    left = list(got)
    for w in want:
        hit = next((g for g in left if call_ok(w, g)), None)
        if hit is None:
            return False
        left.remove(hit)
    return True


def passes(row: dict, calls: list[dict], said: str) -> bool:
    if row["expect"] == "call" and calls_match(row["calls"], calls):
        return True
    if row["expect"] in ("ask", "none") and not calls:
        return True
    for alt in row.get("accept", []):
        if alt in (["none"], ["ask"]) and not calls:
            return True
        if alt and isinstance(alt[0], dict) and calls_match(alt, calls):
            return True
    return False


def load_rows(name: str) -> list[dict]:
    return [json.loads(l) for l in (HERE / name).read_text().splitlines() if l.strip()]
