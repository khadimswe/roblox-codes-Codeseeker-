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
git clone https://github.com/khadimswe/roblox-codes-Codeseeker-
cd roblox-codes-Codeseeker-
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp config/games.example.json config/games.json
# ships configured for Slayers 2; add more games here if you want

python main.py                   # launches the GUI, canned source by default
python main.py --demo            # canned feed with accelerated virtual clock
python main.py --source web      # live fetching (see warning below)

python main.py --demo --headless        # terminal run: transitions + ranked list
python main.py --source web --record    # capture today's real listings as a fixture
python main.py --snapshots recorded     # replay those real listings
python -m pytest                        # 125 tests
```

Python 3.10 or newer. The GUI uses Tkinter, which ships with most Python installs.

---

## Two source modes

| Mode | Flag | Use it for |
|---|---|---|
| Canned | default | Development, testing, and **the video demo** |
| Live | `--source web` | Showing that real fetching works |

Three live sources ship configured in `config/games.example.json`, all checked
against `robots.txt` and fetched at most once every 2.5 seconds per host with an
honest User-Agent:

| Source | Trust | Why |
|---|---|---|
| [Project Slayers 2 Wiki](https://projectslayers2roblox.wiki/codes/) | 0.70 | Dated code table — says when each code was last confirmed |
| [Dexerto](https://www.dexerto.com/roblox/slayers-2-codes-3410351/) | 0.65 | Clean code table |
| [Pocket Tactics](https://www.pockettactics.com/slayers-2-codes) | 0.60 | Lower recall, which is what the trust difference is for |

### Two fixture sets

`data/snapshots/slayers2/` holds the **synthetic** demo narrative — every code
prefixed `DEMO_`, arranged so some codes hold their confidence while others
decay. That is what `--demo` replays and what the video should show.

`data/snapshots/slayers2/recorded/` holds **genuinely recorded** listings
written by `--source web --record`, each stamped with the date it was captured.
Replay them with `--snapshots recorded`.

They are kept apart so it is never ambiguous which codes in a screenshot are
real. Run `--record` on a few different days and the recorded set replays as a
timeline, so the demo runs on real data.

The canned source replays recorded snapshots against a virtual clock, so you can
fast-forward several days in seconds and actually watch confidence decay and codes die.
**Record the video demo using the canned source.** Live scraping during a presentation
depends on a website not changing its layout that morning.

---

## Project layout

```
codeseeker/
├── main.py                  entry point, argument parsing, wiring
├── agent/
│   ├── state.py             CodeRecord, BeliefStore — the internal model
│   ├── model.py             decay, corroboration, confidence math (pure)
│   ├── percepts.py          normalizes raw source output into percepts
│   ├── fsm.py               the finite state machine driver + transition log
│   └── clock.py             real and virtual clocks
├── sources/
│   ├── base.py              Source interface, RawListing, poll_safely
│   ├── canned.py            replays recorded snapshots against a virtual clock
│   └── web.py               live fetcher, rate limited, robots-aware
├── ui/
│   ├── app.py               Tkinter GUI
│   └── theme.py             palette and fonts
├── tests/                   pytest — model, FSM, sources, state, architecture
├── tools/
│   └── make_demo_snapshots.py   regenerates the synthetic demo fixtures
├── data/
│   ├── snapshots/           canned feed fixtures (ground truth)
│   │   └── slayers2/        DEMO_ narrative + recorded/ real captures
│   ├── cache/               fetched pages during development (gitignored)
│   └── belief.json          persisted belief state (gitignored)
├── config/games.json        which games to track and where to look
└── docs/                    FSM spec + diagram, screenshots for the report
```

Run the tests with `python -m pytest`. Beyond the belief math, `tests/test_architecture.py`
asserts the structural claims the report rests on: ground truth and belief share no
objects, `agent/` never imports `ui/`, `model.py` reads no clock, and nothing anywhere
imports an input-automation library.

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