# Hestia — market landscape

*Market research, 2026-06-23. External/competitive intel (not architecture — see
[ARCHITECTURE.md](ARCHITECTURE.md)). Forager-wide context lives in the wiki at
`~/Documents/Forager/forager-wiki/`.*

## 1. What "akin to Hestia" means

Hestia isn't one product category — it sits at the **intersection of four**, and that
intersection is its whole identity:

> **A self-hosted, local-first AI "household brain" with persistent memory, reachable as a
> private service from every device, that both controls the home (voice + automation) and keeps
> the household's records.**

No single competitor occupies all four corners. Read the market by segment, then ask where the
overlaps leave Hestia exposed vs. defensible.

## 2. The market & the tailwind

- **AI-in-smart-home** is forecast at **~$31.9B in 2026 → $129.4B by 2033 (~23% CAGR)**. The
  broader smart-home market is ~$96B in 2026.
- The decisive 2026 trend is **the move off cloud-only to "local AI hubs"** — compact boxes
  running real-time models on-prem for lower latency + privacy. This is *exactly* Hestia's
  thesis, now a mainstream selling point rather than a hobbyist preference.
- Concrete adoption signal: **17,000+ Home Assistant users already run local STT/LLM pipelines**,
  and the community has converged on the **Qwen3 family** — the same model lineage Hestia runs.
  Hestia is riding a wave, not paddling against one.

## 3. Competitive landscape, by segment

**A. Big-tech ambient assistants — Alexa+, Gemini for Home, Apple Siri AI.**
The mass market. 2026 saw all three go LLM-native: Alexa+ as the household-integration leader,
Gemini for Home as the smartest "brain" (rolling back to 2016-era Nest hardware), Apple leaning
on on-device privacy. *Relationship to Hestia:* the thing it's defined against. Hestia's founding
complaints — Siri friction, Alexa amnesia, disconnection — are these products. They own
distribution and convenience; they will never give you a server-side brain *you* own. Positioning
fuel, not a beatable competitor.

**B. Open-source local voice assistants — Home Assistant Voice PE / Nabu Casa, OpenVoiceOS,
Rhasspy, Willow, Leon.**
Hestia's home turf and its biggest strategic question. Nabu Casa's **Voice Preview Edition**
(open, on-device wake word, "Okay Nabu / Hey Jarvis," local or privacy-cloud STT) is the credible
commercial incumbent, funding the Open Home Foundation. Willow (ESP32, <$50), Rhasspy (offline on
a Pi), OVOS, and Leon round it out. **Key distinction:** these are mostly *plumbing*
(STT→intent→device control). Hestia's differentiator is that it **inverts the HA relationship** —
HA is an input + a tool, not the brain — so memory and tools sit in the loop everywhere. None of
the plumbing projects assert that.

**C. Self-hostable "second brain" personal AI — Khoj (closest analog) + the local/private
assistant field.**
**Khoj** is the nearest neighbor on the memory + self-hosting axis: open-source (AGPL-3.0), YC
W24, 34k+ GitHub stars, runs any local model via Ollama, RAG over your own docs, custom agents.
*What it lacks vs. Hestia: the home.* Khoj is a knowledge/research brain; it doesn't control
lights, run a media stack, or model pets/garden/chores as live records. Hestia ≈ "Khoj that also
runs the house."

**D. Homestead/household record-keeping — Mind the Farm, FarmKeep, Homestead Planner.**
Hestia's most under-appreciated and most contested wedge. **Mind the Farm** is the alarming one:
a 2026 product whose pitch is "manage your homestead records by talking to an AI assistant
instead of filling out forms" — natural-language livestock/breeding records. That directly
overlaps Hestia's `records` tool (pets/lineage, wildlife, chores, service reminders, garden).
FarmKeep (1,200+ species, breeding/health tracking) and Homestead Planner are form-based
incumbents. But all are cloud SaaS, single-purpose, and have no home control or general brain.

## 4. Closest analogs, head-to-head

| Product | Self-host / local | Persistent memory | Home control | Household records | Gap vs. Hestia |
|---|---|---|---|---|---|
| **Khoj** | ✅ | ✅ (docs/RAG) | ❌ | ❌ | No home, no household domain |
| **HA Voice PE / Nabu Casa** | ✅ | ❌ (thin) | ✅ | ❌ | HA owns the loop; no server-side brain/memory |
| **Mind the Farm** | ❌ cloud | partial | ❌ | ✅ (AI voice) | Single-purpose SaaS, not yours |
| **Alexa+ / Gemini** | ❌ | ✅ cloud | ✅ | ❌ | Not yours; the amnesia/lock-in Hestia rejects |
| **Hestia** | ✅ | ✅ (server-side, learns) | ✅ (HA as tool) | ✅ | — *occupies all four* |

## 5. Hestia's defensible wedge (the white space)

The unoccupied square: **one privately-owned brain that unifies home control + household records
+ persistent memory, with HA demoted to an I/O device.**

- **"Memory is the moat" is real and rare.** Khoj has memory without the home; the voice projects
  have the home without memory; big tech has memory you don't own. Server-side memory that *gets
  smarter over time* (the note-taker / propose-don't-dispose inbox) across phone/terminal/mic is
  genuinely differentiated.
- **The records angle is a sleeper.** Mind the Farm proves demand for *talk-to-your-homestead-
  records* — but as cloud SaaS. Hestia gets that for free as one tool behind a brain you host. It
  ties straight into the Forager constellation's sensing layer (pest/plant ID → HA alerts).

## 6. Threats to watch

1. **Nabu Casa moving up the stack.** If HA's first-party Assist bolts on durable memory + an
   agent loop, it erodes Hestia's core architectural claim for the 17k+ users already there.
   Hestia's answer must stay "the brain is *yours* and lives above HA."
2. **Convenience gravity.** Alexa+/Gemini are "good enough + zero setup." Hestia's audience is
   self-hosters; the addressable market is the privacy/ownership segment, not the mass market —
   size the opportunity accordingly.
3. **Single-purpose AI-native SaaS** (Mind the Farm-style) picking off individual tools (records,
   garden) with slicker UX than a self-hosted generalist.

## 7. Takeaway

Hestia is **architecturally differentiated and riding the dominant 2026 tailwind** (local AI
hubs), but it's a **convergence play, not a category-creator** — every corner has an incumbent;
none holds the center. If productized, the sharpest wedge is **"your own household brain,"**
leading with the two things competitors *structurally cannot* combine — **ownership + cross-
surface memory** — with **conversational homestead records** as the concrete, demonstrable hook
(validated by Mind the Farm's existence). Lead against Alexa-amnesia and Khoj-has-no-house;
defend hardest against Nabu Casa climbing the stack.

## Sources

- AI-in-smart-home market size — https://www.insightaceanalytic.com/report/ai-in-smart-home-technology-market/2704
- Smart home trends 2026 (local AI hubs) — https://promwad.com/news/smart-home-trends-2026
- Local LLM + HA guide / 17k users — https://www.promptquorum.com/smart-home/local-llm-smart-home-complete-guide
- HA Ollama integration — https://www.home-assistant.io/integrations/ollama/
- Alexa+ vs Gemini — https://www.the-ambient.com/versus/alexa-plus-vs-gemini/
- Gemini replaces Assistant — https://www.webpronews.com/google-accelerates-gemini-ai-rollout-to-smart-homes-replaces-assistant/
- Willow — https://github.com/HeyWillow/willow
- OpenVoiceOS — https://github.com/openVoiceOS
- Rhasspy — https://rhasspy.readthedocs.io/
- HA Voice Preview Edition — https://www.home-assistant.io/voice-pe/
- Khoj (GitHub) — https://github.com/khoj-ai/khoj
- Khoj deploy/overview — https://railway.com/deploy/khoj
- Best private personal AI assistants 2026 — https://www.vellum.ai/blog/best-private-personal-ai-assistants
- Mind the Farm — https://mindthefarm.ai/compare
- FarmKeep — https://www.farmkeep.com/
- Homestead Planner — https://homesteadplanner.net/
