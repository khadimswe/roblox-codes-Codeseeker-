# CLAUDE.md — Instructions for the AI coding agent

Working instructions for this repository. Read `ABOUT.md` for why the project is designed the way it is, and `PLAN.md` for build order.

---

## What this project is

A model-based AI agent for a university AI course assignment (AI 3642, Chapter 2 agent types). It tracks Roblox game codes for Slayers 2, maintains a belief about which codes are probably still valid, and presents a ranked copy-ready list.

**The academic requirement drives the architecture.** This is not primarily a scraper. It is a demonstration that an agent maintains an internal model of a partially observable world. Code that makes the model more visible is more valuable here than code that makes the scraping more thorough.

---

## Hard constraints

**1\. Never automate Roblox.** No client automation, no input injection, no auto-redemption, no Roblox API calls that perform actions on an account. The agent fetches public listings and displays them. The user redeems by hand. If a feature request would cross this line, stop and say so rather than implementing it.

This is not only an ethics rule — manual redemption is the agent's only true sensor. The design depends on it.

**2\. Scrape politely.** In `sources/web.py`: a real descriptive User-Agent, a minimum delay of 2 seconds between requests to the same host, respect `robots.txt`, cache responses during development so iteration doesn't hammer anyone, and never parallelize requests to one host.

**3\. Never fabricate real-looking code strings.** Fixture codes in `data/snapshots/` must be either (a) genuinely recorded from a live source, with the capture date noted in the fixture file, or (b) obviously synthetic and prefixed `DEMO_`. Do not invent plausible Slayers 2 codes — a grader may check them, and invented ones would be indistinguishable from fabricated results.

**4\. Keep truth and belief in separate structures.** `data/snapshots/` is ground truth for the canned source. `BeliefStore` is what the agent believes. They must never share objects or be merged for convenience. They communicate only through percepts. This separation is the thing the report points at.

---

## Architecture

main.py              entry, arg parsing, wires source → agent → UI

agent/

&nbsp;&nbsp;state.py           CodeRecord, BeliefStore, persistence

&nbsp;&nbsp;model.py           decay / corroboration / scoring / verification

&nbsp;&nbsp;percepts.py        raw source output → normalized percepts

&nbsp;&nbsp;fsm.py             state machine driver \+ transition log

sources/

&nbsp;&nbsp;base.py            Source interface

&nbsp;&nbsp;canned.py          snapshot replay, virtual clock

&nbsp;&nbsp;web.py             live fetch, rate limited

ui/app.py            Tkinter

**Dependency direction:** `ui` → `agent` → `sources`. `agent/` must never import from `ui/`. `model.py` must be pure — no I/O, no network, no clock reads. Pass `now` in as a parameter. That keeps it testable and keeps the virtual clock working.

---

## Code style

**Comment for a grader, not for a maintainer.** The assignment requires a "well-commented source program" and comments are read as part of the grade.

- Every module opens with a docstring saying what it does *and how it maps to the model-based agent architecture* — for example, `model.py` implements "how the world evolves" and "what my actions do".  
- Every non-obvious function gets a docstring with its reasoning, not just its signature.  
- Where the code implements a specific textbook concept, say so in a comment: the belief state, the factored representation, the correction step, the decay model.  
- Prefer a clear explanatory comment over a clever one-liner. Readability beats brevity in this repo.

**General:**

- Python 3.10+, type hints throughout  
- `dataclasses` for records  
- Standard library where reasonable; every added dependency needs a justification  
- Descriptive names — `confidence_after_decay`, not `cad`

---

## Testing

- `pytest`, tests live in `tests/`  
- `model.py` must have real unit tests — decay behaves monotonically, corroboration raises score, verification overrides both  
- Use a fake clock, never `datetime.now()` in tests  
- Sources get tests against fixtures, never against the live network

---

## Things to get right

- **The virtual clock.** Demo mode must fast-forward days in seconds. Everything time-dependent takes `now` as a parameter. No hidden clock reads anywhere.  
- **Source failure is not app failure.** A 404, timeout, or layout change degrades the run and logs a warning. It never crashes the GUI.  
- **The transition log is a feature.** It's shown in the GUI and recorded in the video. Log every state transition with a human-readable reason: "POLL → RECONCILE: 3 listings from 2 sources, 1 new code".  
- **Confidence must be legible.** The user should be able to look at a bar and understand roughly why it is where it is. Hover text explaining the score is worth building.

---

## Things to avoid

- Don't add a web framework, database, or async stack. Tkinter and JSON files are correct at this scale.  
- Don't over-engineer the scraper. Two or three sources parsed well beats ten parsed badly.  
- Don't silently change the confidence model without updating `ABOUT.md` — the report describes this math and the two must agree.  
- Don't refactor `agent/` into something clever. A grader has to read it in a few minutes.

---

## When in doubt

Ask: *does this make the internal model more visible to someone watching a five-minute video?* If yes, it's probably worth building. If no, it can wait.

&nbsp;