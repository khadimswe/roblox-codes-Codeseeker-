# Demo script — 5 minute video

Run this, and nothing else:

```bash
cd roblox-codes-Codeseeker-
source .venv/bin/activate          # if you made one
python main.py --demo
```

`--demo` gives you the **virtual clock**, which is the whole point: you can move
a fortnight in fifteen seconds and the audience watches beliefs change with
nothing new arriving.

**Do not record against `--source web`.** A site changing its layout that
morning would take the demo with it, and live pages have no decay to show —
they are all listed today. The canned fixtures are built to make the argument
land in 90 seconds. Mention live mode, show it afterwards if you want.

Make the window about 1500px wide before you start, so nothing clips.

---

## Beat 1 — 0:00–0:30 · the problem

Window open at **day 0.00**, six codes, all green.

> "Roblox codes expire silently. A wiki lists twelve codes, four are already
> dead, and nothing on the page tells you which four. You only find out by
> pasting one in. That makes the true state of the world invisible — and that
> is why this needs a model-based agent rather than a reflex agent."

Point at the source badges: `COMMUNITY 0.50`, `VIDEO 0.40`, `WIKI 0.70`. Those
are trust values, not code data.

---

## Beat 2 — 0:30–1:30 · the design

Stay still and talk over the window.

- **Hidden state.** The agent can never see whether a code works.
- **Two model components.** How the world evolves: codes die whether or not
  anyone is watching. What its actions do: polling and corroboration change
  what it believes.
- **The FSM**, right-hand pane. Read one line out loud — they are written to be
  read.

Hover a confidence bar to show the tooltip: the arithmetic behind the number.

---

## Beat 3 — 1:30–3:00 · **the demo beat**

This is the part that proves the claim. Two moves.

### 3a. Time passes, sources stay up

Press **+1 day** six times, slowly. Say what is happening:

> "No announcements. No expiry notices. Nobody said anything. But
> DEMO_WEEKENDX2 has stopped appearing on the pages that used to list it, and
> the agent is drawing a conclusion from that absence."

Watch: `DEMO_WEEKENDX2` goes **green → amber**. `DEMO_LAUNCH100K` holds at
0.83 because it is still being listed by all three sources. **That contrast is
the point** — the agent is discriminating, not just counting down.

Press **+3 days** twice more. `DEMO_WEEKENDX2` goes **grey**.

### 3b. Every source goes dark

Tick **simulate source outage**, then press **+1 day** three times.

> "Now every source is unreachable. The agent has no new information at all —
> and its beliefs still change."

Point at the log: `POLL_SOURCES → DECAY: all 3 source(s) failed — ageing
beliefs anyway with no new evidence`. Every bar falls.

> "A simple reflex agent has nothing to do here. It responds to percepts, and
> there are no percepts. This one is reasoning about a world it cannot
> currently observe. That is the whole difference."

Untick the outage box.

### 3c. The correction step

Tick **source-trust refinement**. Note `DEMO_HEARSAY`'s confidence out loud —
it will be around 0.37.

Press **Failed** on **DEMO_RUMOUR**.

> "I pasted that code into Roblox by hand and it was rejected. That report is
> the agent's only true sensor reading — and it never touches Roblox itself,
> which is both an ethics line and the reason it has a sensor at all."

Three things happen at once, and they are all on screen:

1. `DEMO_RUMOUR` snaps to **0.00**, goes grey, is retired.
2. Every `COMMUNITY` badge changes **0.50 → 0.40** — the source that vouched
   for it lost trust.
3. **`DEMO_HEARSAY` falls to 0.30**, and nobody said anything about
   `DEMO_HEARSAY`. It just happens to come from the same source.

> "That last one is model refinement. The agent didn't just correct one belief,
> it corrected the thing that produced the belief."

Log shows `IDLE → VERIFY` in purple and `PRESENT → PURGE` in red.

---

## Beat 4 — 3:00–4:30 · walk the code

Editor, four files, roughly twenty seconds each:

| File | Say |
|---|---|
| `agent/state.py` | `CodeRecord` — the factored representation. A vector of attributes, not an opaque state. |
| `agent/model.py` | `decay()` is "how the world evolves"; `apply_verification()` is the correction step. Every function is pure and takes `now` as a parameter — that is what makes the virtual clock possible. |
| `agent/fsm.py` | The nine states of `docs/FSM.md`. Show `report_outcome` — two lines: compute the corrected belief, store it. |
| `tests/test_architecture.py` | Ground truth and belief share no objects, asserted by `id()`. `agent/` never imports `ui/`. Nothing anywhere imports an input-automation library. |

If you have time, run `python -m pytest` on camera. 125 tests, under a second.

---

## Beat 5 — 4:30–5:00 · wrap

> "Partially observable, stochastic, dynamic environment. The agent maintains an
> internal model, decays it as the world evolves, corrects it when it finally
> gets a true reading, and refines the model itself from that correction. It
> never automates Roblox — the manual redemption is the sensor."

---

## If you want to show live mode

Afterwards, in a terminal:

```bash
python main.py --source web --headless --steps 1
```

Four real Slayers 2 codes from three sites. `RELEASE26` scores 0.95 because all
three list it; the others score 0.83 on two sources each. Same belief model,
same code — only the sensor changed.

---

## Recovering if something goes wrong on camera

| Problem | Fix |
|---|---|
| Clicked too far, codes all grey | **Reset clock**, then **Poll now** |
| Belief carried over from an earlier take | Quit, `python main.py --demo --reset` |
| Want a clean start mid-recording | **Reset clock** returns to day 0 |
