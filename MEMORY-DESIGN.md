# Hestia memory — the design (and the build-vs-reuse call)

## The call: build the brain fresh, steal the memory *design*

**Verdict: fresh brain.** Odysseus fails the ≥70% test *as a brain*. It's a
full **workspace** — chat UI, documents, email, calendar, image editor — and the
agent is a feature inside it, built on opencode. To use it as Hestia's headless
brain you'd be carving a small endpoint out of a large app and fighting its UI and
feature surface forever (we already spent a session patching its serving internals).
A fresh brain is a few hundred lines of glue around commodity parts; that's cheaper
to own than a fork is to tame.

**But its memory instinct is right, and worth taking.** The thing you liked — the
agent deciding, mid-task, to *post a JSON record of something to remember* — is
exactly the runtime behavior we want. Combine that with a structured, inspectable
store and a background "note-taker," and you get your "it gets smarter over time."

So: **fresh code, synthesized memory design** = Odysseus's write-it-down reflex +
a human-readable structured store + passive background extraction.

---

## Principles

1. **Inspectable & versionable.** Memory is markdown files in a git repo, like a
   human's notebook. You can read everything it knows, diff what changed, and delete
   what's wrong. No opaque blob.
2. **Recall by meaning *and* by key.** Vector search for "what's relevant," plus
   direct lookup for known facts. Markdown is the source of truth; the vector index
   is derived and rebuildable.
3. **Gets smarter passively.** After interactions, a cheap background pass proposes
   durable facts and writes them — "triggers making notes in the bkg." You wake up
   to a brain that knows more than yesterday, without having been told explicitly.
4. **One store, every interface.** Terminal, phone, and kitchen voice read and write
   the same memory. That shared state is the whole moat.

---

## Structure

Each memory is one record: a markdown file with frontmatter (the structured part)
plus free text (the human part) — the same shape as a good engineering note.

```markdown
---
id: coffee-orange-label
type: preference          # person | household | preference | routine | work | reference | episodic
confidence: 0.9
source: voice@2026-06-07  # where it came from
last_seen: 2026-06-07
links: [kitchen, groceries]
---
The good coffee is the bag with the orange label. Reorder when it's low.
```

- **Types** map to how a home brain actually thinks: *person* (family), *household*
  (the house and its things), *preference*, *routine*, *work*, *reference* (URLs,
  accounts), and *episodic* (a rolling log of what happened).
- **An index file** (one line per record) is loaded into context each session for
  cheap scanning; embeddings handle deep recall.
- **Household *state*** (is the garage open? thermostat?) is **not stored** — it's
  queried live from Home Assistant. Don't cache what HA already owns; store
  preferences and facts, not transient state.

---

## How it "gets smarter over time"

A background note-taker, not an in-loop tax:

1. After an exchange (or on a timer), a **small/cheap model** reads the transcript
   and proposes durable facts: *"User prefers TV downloads in 1080p, not 4K."*
2. Each proposal is **deduped** against existing records (by embedding similarity +
   id), then written or merged. Conflicts flag the old record rather than silently
   overwriting.
3. Records carry **confidence + last_seen**; repeated confirmation raises confidence,
   contradiction lowers it, age decays it. Stale/low-confidence facts surface for
   review instead of rotting.

Because it's all markdown in git, every autonomous edit is a diff you can audit or
revert. The brain learns in the open.

---

## Recall flow (per request)

```
user message
   → embed query, pull top-k semantic records
   → add any directly-keyed facts (by type/link)
   → add live household state from HA (only what the request implies)
   → inject as context → model → response
   → (async) note-taker extracts new durable facts
```

---

## Retention tiers & privacy

- **Never-forget:** explicitly pinned ("always remember X"). Never decays.
- **Durable:** background-extracted facts, subject to confidence/decay.
- **Ephemeral:** session/working memory, dropped after the conversation.
- **All local, all git-versioned.** You can see, trim, or wipe what it knows. No
  cloud, no third party — same local-first stance as the rest of Hestia.

---

## What we take from Odysseus vs. leave

| Take | Leave |
|---|---|
| The "agent writes a memory record mid-task" reflex | The whole workspace app |
| ChromaDB-style vector recall as a reference | Its UI / routes / feature surface |
| The idea of typed, structured memories | Coupling memory to a serving stack |
