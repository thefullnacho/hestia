---
name: whelping
description: Use for the dog-breeding lifecycle — gestation and due dates, signs a dam is approaching whelp, neonatal puppy care, and logging litters/puppies into records. Covers our Lhasa Apso program (dams, sires, litters) and when to escalate to a vet.
triggers: puppy, puppies, pup, pups, dam, sire, litter, litters, whelp, whelping, pregnant, pregnancy, gestation, breeding, breed, born, birth, nursing, neonatal, labor, straining, contractions, kennel, lily, bodhi, fiona
tools: records, reminder
metadata:
  domain: breeding
  version: 0.1.0
---

# Whelping & kennel

Use this skill for anything about the breeding program: is a dam pregnant or due,
what to watch for as she nears labor, caring for newborn pups, and — importantly —
keeping the records straight (litters and individual puppies live in `records`).

## Load these resources
- `references/knowledge.md` — our breeding roster, gestation/whelping facts for a
  small breed, neonatal basics, and the vet-escalation lines.
- `references/decide.md` — the procedures: due-date, whelp-watch, logging a birth,
  and answering progeny questions.

(`references/learn.md` is the offline preference job — do not load it to answer.)

## Working rules
- This is husbandry guidance, not a substitute for the vet. For anything that reads
  as an emergency (see the red flags in knowledge.md), say to call the vet now — do
  not coach through it.
- Litters and puppies are structured data: write them through `records`, don't just
  remember them as loose facts. A puppy weight is `records` `weigh`, never a free-text
  note — `puppy_watch` reads those numbers twice a day and cannot read prose.
- A tie is `records` `log` with `kind='breeding'`, `did='tied'`, `subject` = the dam, and `ts` =
  when it actually happened, never today's date for a past tie. Do not set the day-28 pregnancy
  check or the day-56 whelp-watch reminders yourself: code files both when a tie goes on the
  books, and the twice-daily puppy watch backstops it. The tool reply says what was set; repeat
  that, and never say a reminder is set unless the reply does.
- The fading-pup alerts are a timer doing arithmetic, not your judgement. Don't recompute a
  curve in your head or decide a pup is fine; report the weights and the direction.
- The `/whelp` board is the fallback for capture. Offer it whenever a name is ambiguous, or
  when the operator is clearly mid-whelp: it writes straight to records and cannot claim a
  write it did not make.
- Names like Lily, Bodhi, Fiona resolve from the roster — use them as-is.
