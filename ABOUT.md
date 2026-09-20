# About This Project

## Assignment context

**Course:** AI 3642 — Programming Assignment \#1 **Component:** Programming (80 points) **Requirement:** Implement a model-based AI agent that carries out a simple task.

The assignment asks specifically for a **model-based** agent, one of the four agent types in Chapter 2 of Russell & Norvig's *Artificial Intelligence: A Modern Approach*. This document explains why this particular task genuinely requires one.

---

## Why a model-based agent, and not a reflex agent

Russell & Norvig draw the line clearly in the Chapter 2 summary: simple reflex agents respond directly to percepts, while model-based reflex agents maintain internal state in order to track aspects of the world that are not evident in the current percept.

The deciding question for any agent design, then, is: **is there something important about the world that the agent cannot see right now?**

For Roblox game codes, there is, and it is the single most important thing about the task.

Roblox promotional codes expire without announcement. A fan wiki, a YouTube video, and a Discord pin may each list a set of codes for Slayers 2, and some unknown subset of those codes is already dead. No source announces the death of a code. Often the listing sites themselves do not know. The only way to establish validity with certainty is to paste a code into the game and observe whether it is accepted.

So the agent is operating under genuine **partial observability**. It cannot perceive the true state of the world — the set of currently-valid codes — at any moment. It can only perceive:

- what various sources *claim* right now, and  
- the outcome of a single redemption attempt, when the user reports one.

Everything between those observations has to be inferred. That inference is the internal model, and building it is the assignment.

---

## The two model components

Russell & Norvig describe a model-based agent as needing two kinds of knowledge. This project implements both explicitly, in `agent/model.py`.

### 1\. How the world evolves on its own

Codes die whether or not the agent is watching. The agent therefore decays its confidence in every known code as a function of age, and the decay is not uniform across codes:

- Codes tied to a limited-time event decay faster than milestone codes (for example, a "100K likes" reward code tends to last longer than a weekend event code).  
- Codes tend to arrive in clusters around game updates, and older codes are more likely to be swept out when a new batch lands.  
- A code that has gone a long time without corroboration from any source is more likely to have quietly been removed.

None of this is observed. All of it is predicted.

### 2\. What the agent's own actions do

Polling a source changes what the agent believes. Corroboration across independent sources raises confidence in a code; appearance in only one low-trust source does not. Retiring a code removes it from the ranked list but keeps it in the record.

---

## The correction step

This is the part that makes the design demonstrable rather than merely asserted.

When the user redeems a code by hand and reports the result, the agent receives its one true sensor reading. Two things happen:

1. **The belief about that code snaps to truth.** Confirmed working, or confirmed dead.  
2. **The agent revises its trust in every source that listed that code.** A site that repeatedly lists dead codes loses influence over future rankings; a site that is consistently right gains it.

The second point is model refinement in the sense described in Russell & Norvig's section on learning agents: observing successive states lets an agent improve its own "how the world evolves" and "what my actions do" components, and bringing model components into closer agreement with reality is almost always worthwhile.

Because that edges toward learning-agent territory and the assignment asks for a model-based agent, source-trust updating is implemented behind a toggle in the GUI. The agent can be demonstrated with it off (pure model-based) and then on (model-based with refinement). This is a deliberate demo beat, not an accident — see `PLAN.md`.

---

## Representation

Each code is stored as a **factored representation** in Russell & Norvig's terms: not an opaque atomic state, but a vector of attributes.

CodeRecord {

&nbsp;&nbsp;&nbsp;&nbsp;code            the string the user pastes

&nbsp;&nbsp;&nbsp;&nbsp;game            which game it belongs to

&nbsp;&nbsp;&nbsp;&nbsp;sources\[\]       which sources have listed it, and when

&nbsp;&nbsp;&nbsp;&nbsp;first\_seen      when the agent first learned of it

&nbsp;&nbsp;&nbsp;&nbsp;last\_corroborated  most recent time any source listed it

&nbsp;&nbsp;&nbsp;&nbsp;last\_verified   most recent user-reported outcome, if any

&nbsp;&nbsp;&nbsp;&nbsp;claimed\_reward  what the sources say it gives

&nbsp;&nbsp;&nbsp;&nbsp;status          ACTIVE | SUSPECT | DEAD | UNVERIFIED

&nbsp;&nbsp;&nbsp;&nbsp;confidence      the agent's current belief, 0.0 to 1.0

}

Two code records can share some attributes and differ on others, which is exactly the property that makes factored representations easier to reason over than atomic ones.

---

## PEAS description

|  |  |
| :---- | :---- |
| **Performance measure** | Fraction of presented codes that actually work; how quickly a new code is surfaced after release; how few dead codes the user wastes attempts on; ranking quality |
| **Environment** | Public code listings (fan wikis, video descriptions, community posts), the Roblox game's own code redemption system, the passage of time, the game developer's unannounced expiration decisions |
| **Actuators** | Source poll requests, belief-store writes, the ranked display, the clipboard copy action |
| **Sensors** | Source page contents, timestamps, and user-reported redemption outcomes |

### Environment properties

- **Partially observable** — true code validity is never directly visible  
- **Single-agent** — no adversary, though the game developer acts as an unpredictable external force  
- **Stochastic** — expiration timing is not predictable from the agent's information  
- **Sequential** — evidence accumulates; earlier observations inform later beliefs  
- **Dynamic** — codes expire while the agent deliberates  
- **Discrete** — a finite set of codes with discrete status values, polled at discrete intervals  
- **Partially known** — the rules governing expiration are not published and must be inferred

---

## Scope boundary

The agent **does not automate Roblox in any way**. It does not launch the client, send input to it, or redeem codes. It fetches public listings, reasons about them, and hands the user a code to paste themselves.

This is a deliberate line, and it happens to be load-bearing for the design: manual redemption by the user *is* the agent's sensor. Automating it away would remove the very observation the model depends on.

Web fetching is rate limited and identifies itself honestly. See `CLAUDE.md`.

&nbsp;