# CodeSeeker — A Model-Based AI Agent for Roblox Game Codes

A model-based AI agent that tracks promotional codes for Roblox anime games, maintains an
internal belief about which codes are probably still valid, and presents a ranked,
copy-ready list.

Built for **AI 3642 Programming Assignment #1**.

---

## The short version

Roblox game codes expire silently. A fan wiki might list twelve codes; four of them are
already dead, and nothing on the page tells you which four. You only ever find out by
pasting one into the game and seeing whether it works.

That makes this a **partially observable** environment, which is the whole reason this
project needs a model-based agent rather than a simple reflex agent. The agent cannot
perceive code validity directly, so it must maintain an internal model of the world and
update it from indirect evidence.

The agent:

1. Polls one or more code sources for a chosen game.
2. Reconciles what it finds against codes it already knows about.
3. Decays its confidence in every code as it ages, because codes die unobserved.
4. Ranks codes by predicted validity and shows them with a one-click copy button.
5. Corrects its beliefs when the user reports that a code worked or failed — and adjusts
   how much it trusts each source based on that outcome.

**The agent never redeems codes automatically.** It does not drive, automate, or interact
with the Roblox client in any way. Redemption is done by the user, by hand, and the
user's report of the result is the agent's only true sensor reading. This is both an
ethical design choice and the mechanism that makes the agent work.

---

## Quickstart

```bash
git clone <your-repo-url>
cd codeseeker
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp config/games.example.json config/games.json
# ships configured for Slayers 2; add more games here if you want

python main.py                   # launches the GUI, canned source by default
python main.py --source web      # live fetching (see warning below)
python main.py --demo            # canned feed with accelerated virtual clock
```

Python 3.10 or newer. The GUI uses Tkinter, which ships with most Python installs.

---

## Two source modes

| Mode | Flag | Use it for |
|---|---|---|
| Canned | default | Development, testing, and **the video demo** |
| Live | `--source web` | Showing that real fetching works |

The canned source replays recorded snapshots against a virtual clock, so you can
fast-forward several days in seconds and actually watch confidence decay and codes die.
**Record the video demo using the canned source.** Live scraping during a presentation
depends on a website not changing its layout that morning.

---

## Project layout

```
codeseeker/
├── main.py                  entry point, argument parsing
├── agent/
│   ├── state.py             CodeRecord, BeliefStore — the internal model
│   ├── model.py             decay, corroboration, confidence math
│   ├── percepts.py          normalizes raw source output into percepts
│   └── fsm.py               the finite state machine driver
├── sources/
│   ├── base.py              Source interface
│   ├── canned.py            replays recorded snapshots
│   └── web.py               live fetcher, rate limited
├── ui/
│   └── app.py               Tkinter GUI
├── data/
│   ├── snapshots/           canned feed fixtures
│   └── belief.json          persisted belief state (gitignored)
├── config/games.json        which games to track and where to look
└── docs/                    FSM diagram, screenshots for the report
```

---

## Documentation

- `ABOUT.md` — assignment context and the academic framing
- `PLAN.md` — build order and milestone checklist
- `CLAUDE.md` — working instructions for the AI coding agent
- `docs/FSM.md` — state machine specification

---

## AI use disclosure

This project was developed with AI assistance. See the disclosure section in the
submitted report for the model used, a summary of how it was used, and a link to the
full chat transcript, as required by the course policy and the KSU Code of Academic
Integrity.