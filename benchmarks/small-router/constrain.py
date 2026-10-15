"""Constrained decoding for the tool caller: a program picks the next token wherever the answer
is determined, and the model only chooses where judgment is needed.

At each step the model's top candidates are checked, best first, against a parser of
FunctionGemma's call format: tool and argument names must be ones this domain offers, enum
values must be listed, numbers must be digits, and free-text values must continue a phrase the
person actually said (from a word boundary). A plain-text answer (a question, or nothing) is
left alone: asking stays the model's call. If no candidate fits, the model's own choice stands
and the gate after the caller still applies. This is defense in depth, not a replacement.
"""
from __future__ import annotations

import re

import torch
from transformers import LogitsProcessor

import caller

CALL, END_CALL, ESC = "<start_function_call>", "<end_function_call>", "<escape>"
DONE = "<start_function_response>"


def _phrase_ok(value: str, said: str, starts: list[int]) -> bool:
    """value is the start of a phrase in what was said, beginning at a word boundary."""
    v = re.sub(r"\s+", " ", value.lower()).lstrip()
    if not v:
        return True
    if "all".startswith(v):  # light_set's "everything"
        return True
    return any(said.startswith(v, i) for i in starts)


class CallChecker:
    """Is a partial model output still a valid call for these tools and this utterance?"""

    def __init__(self, tools: list[dict], utterance: str):
        self.params = {t["function"]["name"]: t["function"]["parameters"]["properties"] for t in tools}
        self.said = re.sub(r"\s+", " ", utterance.lower())
        self.starts = [m.start() for m in re.finditer(r"\b\w", self.said)]

    def ok(self, text: str) -> bool:
        if not (text.startswith(CALL) or CALL.startswith(text)):
            return CALL not in text  # a plain answer (a question, or nothing) must stay plain
        pos = 0
        while pos < len(text):
            rest = text[pos:]
            if CALL.startswith(rest):
                return True
            if not rest.startswith(CALL):
                return DONE.startswith(rest) or rest.startswith(DONE)
            pos += len(CALL)
            got = self._call(text[pos:])
            if got is True:
                return True
            if got is False:
                return False
            pos += got
        return True

    def _call(self, s: str):
        """True: valid but unfinished. False: invalid. int: length of a complete call."""
        head = "call:"
        if not s.startswith(head):
            return head.startswith(s)
        m = re.match(r"call:(\w*)", s)
        name = m.group(1)
        i = m.end()
        if i == len(s):
            return any(n.startswith(name) for n in self.params)
        if name not in self.params or s[i] != "{":
            return False
        props, i, used = self.params[name], i + 1, set()
        while True:
            if i == len(s):
                return True
            if s[i] == "}":
                rest = s[i + 1:]
                if len(rest) < len(END_CALL):
                    return END_CALL.startswith(rest)
                return i + 1 + len(END_CALL) if rest.startswith(END_CALL) else False
            if s[i] == "," and used:
                i += 1
                continue
            m = re.match(r"(\w*)", s[i:])
            key = m.group(1)
            i += m.end()
            if i == len(s):
                return any(k.startswith(key) and k not in used for k in props)
            if key not in props or key in used or s[i] != ":":
                return False
            used.add(key)
            i += 1
            spec = props[key]
            got = self._value(s[i:], spec)
            if got is True or got is False:
                return got
            i += got

    def _value(self, s: str, spec: dict):
        if spec.get("type") == "integer":
            m = re.match(r"-?\d*", s)
            if m.end() == len(s):
                return True
            return m.end() if m.end() and s[m.end()] in ",}" and m.group(0) != "-" else False
        if spec.get("type") == "array":
            if not s:
                return True
            if s[0] != "[":
                return False
            i = 1
            while True:
                if i == len(s):
                    return True
                if s[i] == "]":
                    return i + 1
                if s[i] == "," and i > 1:
                    i += 1
                    continue
                got = self._string(s[i:], None)
                if got is True or got is False:
                    return got
                i += got
        return self._string(s, spec.get("enum"))

    def _string(self, s: str, enum):
        if len(s) < len(ESC):
            return ESC.startswith(s)
        if not s.startswith(ESC):
            return False
        body = s[len(ESC):]
        end = body.find(ESC)
        value = body if end < 0 else body[:end]
        if enum is not None:
            fits = any(e.startswith(value) for e in enum) if end < 0 else value in enum
        else:
            fits = _phrase_ok(value, self.said, self.starts) and (end < 0 or value.strip() != "")
        if not fits:
            return False
        return True if end < 0 else len(ESC) + end + len(ESC)


class Constrain(LogitsProcessor):
    """Greedy over the model's top-k: keep the best token that leaves the output valid."""

    def __init__(self, tok, checkers: list[CallChecker], prompt_len: int, k: int = 50):
        self.tok, self.checkers, self.prompt_len, self.k = tok, checkers, prompt_len, k
        self.fallbacks = 0

    def __call__(self, input_ids, scores):
        for row in range(scores.shape[0]):
            gen = [t for t in input_ids[row, self.prompt_len:].tolist() if t != self.tok.pad_token_id]
            top = torch.topk(scores[row], self.k).indices.tolist()
            # decode the whole output with each candidate: a lone token can lose its leading space
            pick = next((t for t in top if self.checkers[row].ok(
                self.tok.decode(gen + [t], skip_special_tokens=False))), None)
            if pick is None:
                self.fallbacks += 1
                continue
            keep = scores[row, pick].clone()
            scores[row].fill_(float("-inf"))
            scores[row, pick] = keep
        return scores


def checkers_for(rows: list[dict]) -> list[CallChecker]:
    return [CallChecker(caller.DOMAINS[caller.domain_of(r)], r["text"]) for r in rows]
