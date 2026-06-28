# Hestia Repository Audit

**Date**: 2026-06-12  
**Repo**: ~/hestia (personal local homelab brain)  
**Auditor**: Grok (read-only deep exploration of all sources + design docs)  
**Scope**: Architecture fidelity, code quality, deployment/ops, security, testing, maintainability, completeness vs. own stated goals, missed opportunities. All findings grounded in actual files read (24 Python sources, ARCHITECTURE.md, MEMORY-DESIGN.md, RESULTS.md, services, scripts, skills, memory examples, .gitignore, etc.).

---

## Executive Summary

Hestia is a high-quality, opinionated, personal "one stateful brain, many thin windows" system. The core thesis — expose a single OpenAI-compatible `/v1` endpoint so terminal/phone/HA voice all hit the *same* agent with memory + tools — is executed cleanly. The brain owns the loop; HA is just another thin client + a controllable tool.

**Grades** (out of 10):
- **Design fidelity**: 9.5 — The implementation hews extremely closely to the excellent ARCHITECTURE.md and MEMORY-DESIGN.md. The "build the seam, wire commodity parts" philosophy is visible everywhere.
- **Implementation craft**: 8.5 — Pragmatic, defensive, well-commented code with real thought about model limitations (14B tool-calling), safety, and inspectability. Some rough edges from rapid personal development.
- **Sustainability / maintainability**: 6 — Zero automated tests, pervasive absolute paths + import hacks, minimal packaging, no lint/type/CI. Works great for the owner today; would be brittle for anyone else or future self after a long break.
- **Ops / reliability**: 8 — Strong budgets/timeouts added after real incidents (SearXNG hang), excellent `hestiactl`, integrity-checked backups, journal tracing. Proactive garden-watch is a nice touch.
- **Overall**: Strong bespoke personal system (≈8/10). The moat (memory + scoped tools + one brain) is real and working.

The project already avoids many common AI-agent pitfalls (unscoped shell, silent destructive actions, opaque vector-only memory, model routing everything).

---

## What Is Excellent

- **Agent loop is production-grade for its scale** ([brain/hestia.py](brain/hestia.py)): `run_agent` (lines ~144-199) has hard `TURN_BUDGET`/`TOOL_BUDGET`, per-step `_log` + `_short` (safe truncation), JSON arg validation that refuses rather than defaulting, MAX_STEPS guard, and clean final-answer extraction. Timeouts were added after a real 5-minute hang (documented in comments).
- **records_store is sophisticated and correct** ([brain/records_store.py](brain/records_store.py)): Entity aliases (case-insensitive, exact first), kind-scoped `resolve(..., strict=True)` mint guards to prevent cross-domain false-attach (photo Shortcut vs conversational), litter grouping by (dam, sire, whelp_date) with *derived* puppy count, bidirectional relations, `due_assets`, `garden_overview` + `garden_lookup` (stopword list + plant-name matching so the model is grounded on *exact* planted data). `add_birth` is non-trivial and handles the litter math right.
- **Deterministic skill injection protects the model** ([brain/tools/skill.py](brain/tools/skill.py) + [brain/hestia.py](brain/hestia.py)): Keyword whole-word trigger scoring + best-match only; optional `tools:` allow-list in SKILL.md frontmatter so the 14B cannot mis-route to `search` on a garden question. Injects `SKILL.md` body + `knowledge.md`/`decide.md` references (learn.md is correctly excluded from the loop). See garden_bed and whelping skills.
- **Benchmark-driven decisions with real harness reuse** ([benchmarks/RESULTS.md](benchmarks/RESULTS.md), [benchmarks/bench.py](benchmarks/bench.py), [brain/eval_models.py](brain/eval_models.py)): 20-case tool/args/reasoning battery + 33-case stress (multi-step, distractor, safety, arg extraction). The harness re-uses the *actual* SYSTEM_PROMPT + live catalog + tools + 0.3 temp loop. Verdict (qwen2.5-14b single 5080) + safety findings directly drove prompt hardening and the budgets. Excellent.
- **Deliberate, documented scoping of power** ([brain/tools/__init__.py](brain/tools/__init__.py)): The `bash` tool was removed with a clear comment: "Hestia is a home/records assistant, not a sysadmin shell" and "an unauthenticated brain with arbitrary shell access is a far bigger liability". Safety rules in [brain/prompt.py](brain/prompt.py) (never destructive without explicit confirm, ask on ambiguity) are enforced in code paths too.
- **Memory design executed well** ([MEMORY-DESIGN.md](MEMORY-DESIGN.md), [brain/memory_store.py](brain/memory_store.py), [brain/records_store.py](brain/records_store.py)): Markdown + YAML frontmatter as source of truth (git-auditable, human-readable), `INDEX.md` auto-reindex, pinned/confidence/last_seen, separation of soft facts (memory) vs. relational/time-series (records). Keyword recall v1 is simple and dependency-free (vector planned as derived index later).
- **Backup is correct and safe** ([deploy/backup/hestia-backup.sh](deploy/backup/hestia-backup.sh)): Uses SQLite online `.backup()` (never cp of live file), verifies `PRAGMA integrity_check` *before* shipping, dated snapshots + prune policy, memory md copied, only irreplaceable state is sent off-box. The service/timer pair is clean.
- **`hestiactl` is outstanding homelab UX** ([deploy/hestiactl](deploy/hestiactl)): One command for status (brain health + local units + remote docker ps), power with confirms for core services (adguard, gluetun, ha), logs, VPN kill-switch verification. Color, jq fallback, resolve map — exactly the right amount of polish for daily use.
- **HA seam is architecturally correct** ([deploy/ha/custom_components/hestia/conversation.py](deploy/ha/custom_components/hestia/conversation.py), [ARCHITECTURE.md](ARCHITECTURE.md)): Thin forwarder. HA is input device + tool; the brain owns memory/tools/loop. Verified round-trips (lights + memory recall) are mentioned in README.
- **Defensive + observable everywhere**: Broad `except Exception` that *always* return a useful string to the model (never silent failure), catalog caching, photo ingest with clear error responses for misconfigured Shortcuts, garden-watch saturation streak state machine to avoid post-watering false alerts.

---

## Gaps vs. Own Design Documents

- **Background note-taker / passive learning** (MEMORY-DESIGN.md "How it 'gets smarter over time'"): The design explicitly calls for an async/cheap-model post-exchange step that proposes durable facts, dedupes by embedding+id, writes/merges with confidence, and surfaces conflicts for review. This is *not implemented* in the current running system (garden_watch is only proactive alerts; `seed_garden.py` appears to be one-time seeding). The brain gets smarter only when the user explicitly tells it things via the `memory`/`records` tools.
- **Voice phase incomplete** (README status, ARCHITECTURE phase 3): "Phase 3 — Voice (Assist pipeline) — not started". Whisper/Piper on the 4060 Ti + Wyoming satellites + setting the conversation agent to the Hestia custom component are still future work. The seam exists; the full far-field voice loop does not.
- **Thin clients / "many windows"** (ARCHITECTURE "three journeys"): The HA voice + terminal curl work. The phone PWA / dedicated clients mentioned as the win are not present (or are external/one-off Shortcuts). Reachability over Tailscale is solved; the polished client experiences are not.
- **Vector recall** (MEMORY-DESIGN.md): Explicitly "a later upgrade". Current `recall` is pure keyword overlap. Fine for v1, but the design wanted meaning-based + direct key lookup.
- **No general "after interaction" memory extraction** visible in hestia.py loop or garden_watch.

These are mostly "not yet" rather than "wrong", but they are gaps against the docs the project wrote for itself.

---

## Prioritized Backlog of Improvements & Missed Opportunities

### P0 — Correctness, Reliability, Safety (Agentic System Basics)

| ID | Title | Impact | Effort | Key Files | Sketch (reuse existing) | Verify |
|----|-------|--------|--------|-----------|-------------------------|--------|
| P0-1 | Add automated tests for stores + tool dispatch | High (catches regressions in lineage, resolve guards, event log, due logic) | Med | `brain/records_store.py`, `brain/memory_store.py`, `brain/tools/records.py`, `brain/tools/memory_tool.py`, new `tests/` | Use pytest + tmp dirs for DB/MEMORY_DIR. Test `add_birth` litter math, `resolve(strict=True)` mint guards, alias merging, keyword recall scoring, `dispatch` error strings. Re-use the eval_models pattern of isolating a throwaway DB copy. | `pytest`, add to a CI step later; run before any records change. |
| P0-2 | Test the agent loop boundaries (budgets, malformed args, MAX_STEPS) | High (the thing users actually hit) | Low-Med | `brain/hestia.py` (run_agent, _ollama_chat), `brain/tools/__init__.py` | Mock httpx client + tools.dispatch; drive the exact paths in run_agent (timeout before model, timeout inside tool thread, bad JSON args, 0 calls final answer). | Unit tests that assert the exact strings returned to the client ("Sorry — that took too long...", "arguments ... not valid JSON"). |
| P0-3 | Harden photo ingest + records attachment edge cases | Med (mobile Shortcut is a real ingestion path) | Low | `brain/hestia.py:250` (ingest_photo), `records_store.attach_photo` | More validation on domain, better error taxonomy, test the strict_subject path. | Manual + unit: post bad token, bad subject, missing file; assert the record is created with correct domain kind. |

### P1 — Maintainability / Portability / DX (Daily Friction + Onboarding)

| ID | Title | Impact | Effort | Key Files | Sketch (reuse existing) | Verify |
|----|-------|--------|--------|-----------|-------------------------|--------|
| P1-1 | Eliminate absolute ~/hestia + Tailscale IP hardcodes | High (makes moving the brain or restoring on new hardware painful; multiple files) | Med | `brain/hestia.py:30-32,58`, `brain/garden_watch.py:24-25,35`, `deploy/systemd/*.service`, `deploy/ha/custom_components/hestia/const.py:6`, `deploy/hestiactl:20-21`, `deploy/backup/hestia-backup.sh:15+` | Central `config.py` (or just more `os.environ.get(..., default)`) + one helper for secrets dir. Make Tailscale IP / host an env (already partially done for some). Services can still hardcode the *production* values but document the env overrides clearly. | `HESTIA_*` overrides work; `grep -r ~/hestia` only hits docs + the one canonical config. |
| P1-2 | Turn brain/ into a real importable package (kill the sys.path hacks) | High (4+ files do load_dotenv + sys.path.insert; fragile) | Med | `brain/hestia.py`, `brain/tools/memory_tool.py:7`, `brain/garden_watch.py:25`, `brain/eval_models.py:25`, `brain/pyproject.toml`, systemd units | Add `src/brain` layout or `[tool.setuptools.packages.find]` + proper relative imports. Or keep flat but install in editable mode and use `python -m brain.hestia`. Update ExecStart in the three services. | `uv run --project brain python -c "from brain import hestia; from brain.tools import home"` works cleanly; no more sys.path in the four files. |
| P1-3 | Add minimal dev tooling (ruff, basic mypy or pyright, pytest config) | Med (enforces the already-good style) | Low | `brain/pyproject.toml` (add [tool.ruff], [tool.pytest], dev deps), new `pyproject.toml` at root or workspace, `.pre-commit-config.yaml` (optional) | Use ruff for format + lint (the code is already close). Add `requires-python` consistency. | `ruff check .` / `ruff format --check` clean; `uv sync --dev` in brain/ brings in test/lint tools. |
| P1-4 | Document the SKILL.md authoring contract + add one example "new skill" template | Med (powerful but only two skills; future extension is tribal) | Low | `brain/tools/skill.py` (the parser + _INJECT_REFS), `brain/skills/`, new `brain/skills/TEMPLATE/` or docs in ARCHITECTURE | One-paragraph "how to add a skill" in README or a SKILL-AUTHORING.md. Include the frontmatter keys (name, description, triggers, optional tools) and which references are loaded. | A new skill dir parses and injects correctly without touching code. |

### P2 — Completeness vs. Design + Obvious Extensions

| ID | Title | Impact | Effort | Key Files | Sketch | Verify |
|----|-------|--------|--------|-----------|--------|--------|
| P2-1 | Implement (or explicitly defer) the background note-taker | High (this is *the* "it gets smarter over time" mechanism in the design docs) | Med-High | New `brain/note_taker.py` (or inside garden_watch pattern), hestia.py loop (post final answer hook or separate timer), `memory_store` + `records_store` write paths | Cheap model (qwen 7B or the 4060 pool) reads last N turns (or a transcript file), proposes typed records using the same write tool shape, dedupes (simple embedding or even keyword+id first), writes with confidence. Run async after response or on a timer. Store proposals for review. | After a conversation containing a durable fact, a cheap pass proposes it and it appears in memory/ or records (or a review queue). |
| P2-2 | Wire a vision-capable path for photo ingest (the VL models are already downloaded) | Med (photos land on disk + records event; never "seen" by the brain today) | Med | `brain/hestia.py` ingest, new vision tool or direct describe step, Ollama vision support, `records` attrs for description | On successful photo save, optionally call a describe step (or expose a `vision` tool) using one of the mmproj + Qwen2.5-VL models. Store a short caption in the photo event attrs. | "What did the photo of Lily show?" or "describe the latest garden photo" works and is grounded in the actual image via the model. |
| P2-3 | Add minimal persistent per-conversation or rolling session memory | Low-Med (current design relies on injected memory + model context window) | Low | `brain/hestia.py` (the convo list passed to run_agent) | Optionally keep a small rolling buffer of the last k assistant/user turns (ephemeral) and inject a "Recent turns" block, distinct from durable memory. Or just document that long convos may need explicit "remember" nudges. | Long multi-turn sessions don't lose critical recent context the user gave 10 messages ago. |
| P2-4 | Thin client(s) or at least a documented phone-friendly flow | Med (the "phone" journey in ARCHITECTURE is only curl/Shortcuts today) | Low (docs) or Med (simple PWA or TUI) | README, new `clients/` or just docs | Even a tiny static HTML + JS page that talks to the Tailscale /v1 (with streaming if possible) would close the loop. Or a small Python `hestia-chat` CLI that pretty-prints. | Documented one-command or one-tap way for the "on the train" journey. |

### P3 — Ops, Observability, Polish

- Better structured logging / request IDs so a single voice command's full tool trace is one journalctl filter.
- Expose more in `/health` and `/` (skill versions, last garden_watch run, record counts per type, model load time).
- Prometheus-style metrics (optional but fits homelab).
- Model download / update automation or at least a `hestiactl model ...` helper that wraps the benchmarks/download flow.
- Skill "learn.md" jobs made runnable (they exist in the references dirs for a reason).
- Consider sops/age or a single encrypted secrets bundle instead of multiple plaintext .env files (still gitignored).

---

## Quick Wins (Low Effort, High Value, ≤1-2 Files Each)

1. **Central tiny config helper** — one `brain/config.py` (or just top of hestia.py) that does all the `os.environ.get` + sensible defaults + the secrets dir. Update the four absolute-path sites + the three services docs. 30 min.
2. **Add ruff to brain/pyproject.toml** + a one-line `ruff format` + `ruff check --fix` in a Makefile or just a comment. Enforces the style that already mostly exists.
3. **pytest skeleton** — `brain/pyproject.toml` + `tests/test_records_store.py` + `tests/test_memory_store.py` using tmp_path. One birth test + one resolve strict test catches a huge class of future bugs.
4. **Improve /health** to also report whether SearXNG/HA/media base URLs are reachable (cheap HEAD or /api/tags style). Currently only checks Ollama + model presence.
5. **Stale pycache cleanup** + add a note in .gitignore or a `make clean` if desired.
6. **Document the ingest token** more prominently in README (how to send as X-Ingest-Token header from Shortcuts).

---

## Structural / Philosophy Suggestions

- **Make the brain relocatable by default.** The current "edit three files + three systemd units + two py files with sys.path" story is the biggest barrier to "I want to run this on a new box or recover after a disk failure." A single canonical `HESTIA_ROOT` or `HESTIA_CONFIG` env + proper packaging would be a big win for the "personal but durable" goal.
- **Test strategy for agentic code.** The eval harness is great for model selection. Unit + contract tests for the stores, dispatch, skill matcher, and the budget/timeout paths in the loop are still needed. Integration tests against real backends can stay manual or be opt-in.
- **Consider a tiny root pyproject.toml workspace** (or just document the uv commands) so `uv run --project brain ...` and benchmark commands are discoverable from the repo root.
- **Version/pin the prompt + skills.** They are effectively part of the "model personality." A simple `prompt_version` or git tag + injection of the commit in the system prompt (or health) would make regressions easier to bisect.

---

## Appendix: Raw Observations

- **Python surface**: Exactly 24 `.py` files (grep glob). Concentrated in `brain/` (core + 7 tools + 4 supporting) and `deploy/ha/custom_components/hestia/` (5 files). Benchmarks have their own harnesses.
- **Testing**: 0 matches for pytest/unittest/test_ patterns across the entire tree.
- **TODO surface**: Only "todo" as a variable name in `benchmarks/stress.py` (A/B prompt variants). No code TODO/FIXME comments — either very clean or issues are tracked externally.
- **Hardcode count** (partial grep): ≥11 direct home-dir path strings + multiple literal Tailscale IPs and internal hostnames. Scattered across brain runtime, garden_watch, all three relevant systemd units, HA const, hestiactl defaults, backup script.
- **Import style**: Brain is executed with `WorkingDirectory` set to the brain dir and bare `import memory_store`, `import tools`. This forces the sys.path hacks in every script that wants to be run from elsewhere (garden_watch, eval_models, memory_tool).
- **Secrets**: Three explicit `load_dotenv` calls for ha.env / media.env / ingest.env at the top of hestia.py (before tool imports that read at import time). Good that they are gitignored; less good that paths are absolute.
- **Other artifacts in tree**: Several dated lovelace-homelab-status-backup-*.json and ws_*.py scripts under `deploy/ha/`. Useful history but pollute a clean clone.
- **Models dir**: Several GGUF + mmproj files present (including VL). Vision is *possible* today but completely un-wired.
- **Data layout respected**: `memory/` gitignored except README/INDEX; `data/*.db` ignored; photos under data/photos with date-stamped names.

---

## Recommended Next Actions

1. **This AUDIT.md is the audit.** No source changes have been made. The report exists so future you (or a collaborator) has a map.
2. Pick 1-2 items from P0 or the Quick Wins (tests for records + one hardcode reduction pass would be my personal recommendation).
3. When implementing anything, use the verification steps listed and the "Key Existing Utilities" patterns so changes stay in the spirit of the existing high-quality defensive code.
4. Consider adding a short "Known Gaps & Roadmap" pointer in the main README that links to this file (or a living issues list).
5. Re-run a slice of the benchmarks/stress harness after any prompt, tool, or loop change — the project already has the muscle memory for this.

The system is already delightful and useful for its owner. These suggestions are about making it more *durable*, *testable*, and *true to its own ambitious design documents* over the next 6-18 months of personal evolution.

---

*End of audit. Generated from full read-only exploration on 2026-06-12. All claims traceable to specific files and the two primary design documents.*