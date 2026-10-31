---
name: goals
description: Use when the user wants to reach a household outcome over days or weeks (get something ready, built, fixed, sorted by a date) and needs it broken into steps, or reports progress on one. Drafts a plan for approval; tracks steps once approved.
triggers: goal, goals, plan, planning, project, deadline, milestone, milestones, ready by, done by, finished by, winter-ready, winterize
tools: goals, reminder
metadata:
  domain: planning
  version: 0.1.0
---

# Household goals

A goal is an outcome that takes more than one sitting: the greenhouse winter-ready, the
garage sorted, the puppy room set up. Your job is the judgment part: turning it into a plan
two busy people will actually follow. Code does the rest once a person approves the plan:
reminders on each step's day, overdue in the daily briefing, the tick when it's done.

## Drafting a plan (`goals` `propose`)
- When the user describes a goal, call `propose` in this same turn. Do not write the plan out
  in chat first or ask whether to draft it: the draft on the Goals page IS the review step,
  and nothing happens until they approve it there. Ask first only if a missing fact would
  change the whole plan.
- 3 to 8 steps. Each is one concrete action one person can do in one sitting, starting with a
  verb: "Order the greenhouse heater", not "Heating".
- Make the first step small enough to do this week. Momentum beats a perfect plan.
- Put buying, ordering and waiting-on-delivery steps early; they gate everything after them.
- Date at least the first two steps, even with no deadline: an undated step never reminds
  anyone and never shows as overdue, so an all-undated plan quietly does nothing.
- Date the steps when the user gave a deadline: spread them back from the target, leave slack
  before it, and pass dates as phrases ("Nov 8", "in 2 weeks"). Undated is fine for open goals.
- A habit ("get into a practice of ...") is a goal whose steps set the routine up: decide when
  and where it happens, gather what it needs, then date the first three repetitions. The goal
  is done when the routine is running. `target` is a date or empty, never a description.
- Use what you know about this house (beds, equipment, the season, the records) to make steps
  specific. Don't invent equipment or facts you weren't given; ask one question instead if a
  missing fact would change the plan.
- After proposing, show the steps briefly and say the draft is waiting on the Goals page for
  review and approval. Never say it is scheduled or set: nothing runs until it is approved.

## Progress
- When the user says they finished something that sounds like a step, call `done` with their
  words. If the tool asks which step, ask the user; don't guess.
- "Where are we on X" is `show`. Report the next open step and anything overdue.
- If a goal is stuck (steps overdue for a while), ask what's blocking it. Suggest a smaller
  next step in words; you can't edit an approved plan, so a real re-plan is a new `propose`.
