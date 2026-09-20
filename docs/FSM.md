# Finite State Machine Specification

This is the agent's control loop. Section 2 of the report needs a diagram of it — build the image from this spec.

---

## States

| State | What happens | Model-based agent role |
| :---- | :---- | :---- |
| `IDLE` | Waiting for the next poll interval or a user action. | — |
| `POLL_SOURCES` | Request current listings from each configured source. | Actuator |
| `EXTRACT` | Parse raw pages into normalized `RawListing` records. | Sensor processing |
| `RECONCILE` | Match listings against known codes. New codes added; seen codes get `last_corroborated` refreshed. | "What my actions do" |
| `DECAY` | Age every known code and reduce confidence. Runs **whether or not** new information arrived. | "How the world evolves" |
| `RANK` | Score and sort; assign status ACTIVE / SUSPECT / DEAD. | Belief → decision |
| `PRESENT` | Update the GUI with the ranked list. | Actuator |
| `VERIFY` | Handle a user-reported redemption outcome. Snap that code's belief to truth; update source trust if refinement is enabled. | **The correction step** |
| `PURGE` | Retire codes below the **purge** threshold [0.05], which is deliberately stricter than the DEAD label [0.15] so a code stays visible in grey for a while first. Kept in the record, dropped from the active list. | Belief maintenance |

---

## Transitions

IDLE ──(poll timer fires)──────────► POLL\_SOURCES

IDLE ──(user reports outcome)──────► VERIFY

IDLE ──(user picks another game)───► RANK

&nbsp;

POLL\_SOURCES ──(listings returned)──► EXTRACT

POLL\_SOURCES ──(all sources failed)─► DECAY        \[log warning, continue\]

&nbsp;

EXTRACT ─────────────────────────────► RECONCILE

RECONCILE ───────────────────────────► DECAY

DECAY ───────────────────────────────► RANK

RANK ────────────────────────────────► PRESENT

PRESENT ──(any code below purge threshold)─► PURGE

PRESENT ──(otherwise)────────────────► IDLE

PURGE ───────────────────────────────► IDLE

&nbsp;

VERIFY ──────────────────────────────► RANK

---

## Notes for the diagram

Three things are worth making visually obvious:

1. **`DECAY` is reachable even when polling fails entirely.** That edge is the clearest single illustration that the agent models a world it cannot currently observe — it updates its beliefs with no new input at all.

2. **`VERIFY` enters from outside the main loop.** It's driven by the user, not the timer, and it's the only state where belief meets ground truth. Draw it entering from the side and give it visual weight.

3. **The main cycle is a closed loop** through `IDLE`. Show that clearly so the diagram reads as a continuously running agent rather than a one-shot script.

Suggested tooling: draw.io, Excalidraw, or Graphviz. Export PNG or SVG to `docs/fsm-diagram.png` and drop it into Section 2 of the report.

&nbsp;