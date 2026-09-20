# Build Plan

Milestones in dependency order. Each one ends at something runnable — don't move on until the current milestone actually runs.

Deliverable deadlines and what goes to D2L are at the bottom.

---

## M0 — Repo skeleton

- [ ] `requirements.txt`, `.gitignore`, virtualenv  
- [ ] Directory tree per `README.md`  
- [ ] `config/games.json` from the example, set to Slayers 2  
- [ ] `main.py` parses `--source`, `--demo`, `--reset` and exits cleanly

**Done when:** `python main.py --help` prints usage.

---

## M1 — The internal model

This is the heart of the assignment. Build it before anything touches the network.

- [ ] `agent/state.py`: `CodeRecord` dataclass and `BeliefStore` (add, update, query, JSON persistence to `data/belief.json`)  
- [ ] `agent/model.py`:  
      - [ ] `decay(record, now)` — confidence falls with age since last corroboration  
      - [ ] `corroborate(record, sources)` — multi-source agreement raises confidence  
      - [ ] `score(record, now)` — combined confidence, 0.0 to 1.0  
      - [ ] `apply_verification(record, outcome)` — snap belief to observed truth  
- [ ] Unit tests for the math. A code unseen for two weeks should decay well below a fresh one; a code in three sources should outrank the same-age code in one.

**Done when:** tests pass and you can hand-build a `BeliefStore`, advance a fake clock, and watch scores fall.

> Keep the true world state and the agent's belief in **separate structures that never touch**. In the canned source, `data/snapshots/` is ground truth; `BeliefStore` is belief. They meet only through percepts. This separation is what you'll point at in the report.

---

## M2 — Canned source and the FSM

- [ ] `sources/base.py` — the `Source` interface (`poll(game, now) -> list[RawListing]`)  
- [ ] `sources/canned.py` — replays `data/snapshots/` against a virtual clock  
- [ ] `data/snapshots/` fixtures for Slayers 2 (see `CLAUDE.md` on fixture codes)  
- [ ] `agent/percepts.py` — normalize raw listings into percepts  
- [ ] `agent/fsm.py` — the state machine in `docs/FSM.md`, with a transition log

**Done when:** a headless tick loop runs the full cycle and prints state transitions and the ranked list to the terminal. No GUI yet.

---

## M3 — GUI

- [ ] `ui/app.py` — Tkinter  
- [ ] Game dropdown (Slayers 2 selected)  
- [ ] Ranked code list: code, confidence bar, source badges, age, claimed reward  
- [ ] Colour by status — green ACTIVE, amber SUSPECT, grey DEAD  
- [ ] **Copy button** per code  
- [ ] **Worked / Didn't work** buttons per code — this is the sensor  
- [ ] Transition log pane, live  
- [ ] Toggle: source-trust refinement on/off (the demo beat from `ABOUT.md`)  
- [ ] Virtual clock control in demo mode — step forward a day

**Done when:** you can run it, fast-forward, watch a code decay from green to amber to grey, mark one dead, and see other codes from that source lose confidence.

---

## M4 — Live source

- [ ] `sources/web.py` — fetch and parse real listings, rate limited  
- [ ] Graceful failure: a dead source degrades the run, never crashes it  
- [ ] Verify `--source web` populates real codes

**Done when:** live mode works and failure of one source doesn't take the app down.

---

## M5 — Report materials

- [ ] FSM diagram image for Section 2 — export from `docs/FSM.md`  
- [ ] Screenshots: code with comments visible, GUI at rest, mid-decay, post-correction  
- [ ] Terminal output showing state transitions  
- [ ] Confirm every source file is commented to the standard in `CLAUDE.md`

---

## M6 — Report and video

Report sections, in the order the assignment specifies:

1. **Your Information** — course, name, student ID  
2. **Agent Design** — FSM diagram, plus the model-based justification from `ABOUT.md`  
3. **Tasks** — what the agent solves; adapt the ABOUT summary  
4. **Codes and Outputs** — screenshots

Formatting: 10 or 12 pt, 1 inch margins, submitted as PDF.

Also required: the **AI use disclosure** — tool and model version, a summary of how it was used, and a shareable link to the chat thread (or a transcript if no link is available). Final output alone is not sufficient documentation.

### Video (5 minutes max)

Suggested order — the middle beat is the one that proves the agent is model-based:

1. 30s — what it does, why codes expiring silently is the problem  
2. 60s — the design: hidden state, the two model components, the FSM  
3. 90s — **the demo beat.** Fast-forward the clock. Confidence bars fall on their own with no new information arriving; a green code goes amber, then grey. Then mark a code dead and watch belief snap and that source's other codes drop. This is the whole argument, visible in one shot.  
4. 60s — walk the code: `BeliefStore`, the decay function, the FSM loop  
5. 30s — wrap

---

## Submission checklist

Four items, uploaded **separately**. No zipped files.

- [ ] Exercise table PDF (PEAS \+ properties: soccer, book shopping, tennis, knitting) — 20 pts  
- [ ] Report PDF — design, commented source, output screenshots  
- [ ] Source code  
- [ ] Video, MP4 or YouTube link

&nbsp;