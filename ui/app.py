"""
ui/app.py — the Tkinter GUI.

Model-based agent role
----------------------
This is the agent's display **actuator** (the PRESENT state) and the mounting
point for its one true **sensor** (the Worked / Didn't work buttons, which enter
the FSM at VERIFY).  Everything else here is presentation.

The dependency arrow runs one way: `ui` imports `agent`, never the reverse.  The
FSM knows nothing about Tkinter — it is handed a `presenter` callback and calls
it.  That is why the same agent runs headless in a terminal with no changes.

What this window is *for*
-------------------------
CLAUDE.md's test for any feature here is "does this make the internal model more
visible to someone watching a five-minute video?", so the layout is organised
around making belief legible rather than around showing codes:

  * a confidence **meter** per code, not just a number, so a fall is visible at
    a glance and across a compressed screen recording;
  * **hover text** on every meter giving the arithmetic behind it, so the score
    is explainable rather than magic (`model.explain()`);
  * the **transition log** live in the right pane, tinted by state, because the
    log is where "the agent decayed its beliefs although every source failed"
    actually shows up;
  * the **virtual clock** controls, which are the demo's central beat: press
    +1 day and watch bars fall with no new information arriving;
  * the **refinement toggle**, so the agent can be demonstrated pure
    (model-based) and then again with source-trust learning on.

Nothing in this window can redeem a code.  The Copy button puts it on the
clipboard and the user pastes it into Roblox themselves — see CLAUDE.md
constraint 1, which is an ethics rule and also the reason the agent has a sensor
at all.
"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

from agent.clock import Clock, VirtualClock
from agent.fsm import AgentFSM, TickResult
from agent.model import ScoreBreakdown
from agent.state import CodeRecord, CodeStatus, VerificationOutcome
from ui import theme

MIN_WIDTH = 1180
MIN_HEIGHT = 720


# --------------------------------------------------------------------------- #
# Tooltip — how confidence becomes legible
# --------------------------------------------------------------------------- #

class Tooltip:
    """
    A hover panel showing why a confidence score is what it is.

    CLAUDE.md: "Confidence must be legible. The user should be able to look at a
    bar and understand roughly why it is where it is. Hover text explaining the
    score is worth building."  The text comes straight from
    `model.explain().lines`, so the window cannot drift from the maths — if the
    model changes, the explanation changes with it.
    """

    def __init__(self, widget: tk.Widget, text_provider):
        self.widget = widget
        self.text_provider = text_provider
        self.window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event=None) -> None:
        if self.window is not None:
            return
        try:
            text = self.text_provider()
        except Exception:  # noqa: BLE001 - a tooltip must never break the app
            return
        if not text:
            return

        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8

        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{x}+{y}")
        self.window.configure(bg=theme.BORDER_STRONG)

        label = tk.Label(
            self.window,
            text=text,
            justify="left",
            bg="#22262b",
            fg="#f0f2f4",
            padx=12,
            pady=10,
            wraplength=460,
            font=("Helvetica", 11),
        )
        label.pack(padx=1, pady=1)

    def _hide(self, _event=None) -> None:
        if self.window is not None:
            self.window.destroy()
            self.window = None


# --------------------------------------------------------------------------- #
# One row of the ranked list
# --------------------------------------------------------------------------- #

class CodeRow(tk.Frame):
    """
    One code, laid out as: status pill | code + reward | confidence meter |
    source badges | age | actions.

    The meter is drawn on a Canvas rather than using ttk.Progressbar so its
    colour can carry the status — a green bar and an amber bar of the same
    length say different things, and that pairing is what the video is meant to
    show changing.
    """

    def __init__(self, parent, app: "CodeSeekerApp", record: CodeRecord,
                 breakdown: ScoreBreakdown, fonts: dict):
        super().__init__(parent, bg=theme.CARD, highlightbackground=theme.BORDER,
                         highlightthickness=1, bd=0)
        self.app = app
        self.record = record
        self.breakdown = breakdown
        self.fonts = fonts

        fg, soft = theme.status_colours(breakdown.status)
        self.columnconfigure(1, weight=1)

        # --- status pill: the row's colour anchor -------------------------- #
        pill = tk.Frame(self, bg=soft)
        pill.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(0, 14))
        tk.Label(
            pill, text=breakdown.status.value, bg=soft, fg=fg,
            font=fonts["badge"], padx=10, pady=4,
        ).pack(expand=True, fill="both")

        # --- code and reward ----------------------------------------------- #
        identity = tk.Frame(self, bg=theme.CARD)
        identity.grid(row=0, column=1, rowspan=2, sticky="w", pady=10)
        tk.Label(identity, text=record.code, bg=theme.CARD, fg=theme.TEXT,
                 font=fonts["code"], anchor="w").pack(anchor="w")
        reward = record.claimed_reward or "no reward listed"
        tk.Label(identity, text=reward[:52], bg=theme.CARD, fg=theme.TEXT_MUTED,
                 font=fonts["small"], anchor="w").pack(anchor="w")

        # --- confidence meter ---------------------------------------------- #
        meter_box = tk.Frame(self, bg=theme.CARD)
        meter_box.grid(row=0, column=2, rowspan=2, sticky="e", padx=16)

        top = tk.Frame(meter_box, bg=theme.CARD)
        top.pack(anchor="e")
        tk.Label(top, text="confidence", bg=theme.CARD, fg=theme.TEXT_FAINT,
                 font=fonts["small"]).pack(side="left", padx=(0, 8))
        tk.Label(top, text=f"{breakdown.confidence:.2f}", bg=theme.CARD, fg=fg,
                 font=fonts["metric"]).pack(side="left")

        canvas = tk.Canvas(meter_box, width=190, height=7, bg=theme.CARD,
                           highlightthickness=0, bd=0)
        canvas.pack(anchor="e", pady=(4, 0))
        canvas.create_rectangle(0, 0, 190, 7, fill=theme.TRACK, outline="")
        filled = max(2, int(190 * breakdown.confidence))
        canvas.create_rectangle(0, 0, filled, 7, fill=fg, outline="")

        # Threshold ticks, so the user can see *where* the bands are and read a
        # bar as "just above suspect" rather than as a bare fraction.
        for threshold in (self.app.fsm.params.dead_threshold,
                          self.app.fsm.params.suspect_threshold):
            x = int(190 * threshold)
            canvas.create_line(x, 0, x, 7, fill=theme.CARD, width=1)

        # The legibility requirement: the arithmetic, on hover.
        for widget in (canvas, top, meter_box):
            Tooltip(widget, lambda b=breakdown: b.explanation())

        # --- provenance and age -------------------------------------------- #
        meta = tk.Frame(self, bg=theme.CARD)
        meta.grid(row=0, column=3, rowspan=2, sticky="e", padx=(0, 16))

        badges = tk.Frame(meta, bg=theme.CARD)
        badges.pack(anchor="e")
        for source_id in record.source_ids[:3]:
            trust = self.app.fsm.store.trust_for(source_id)
            tk.Label(
                badges, text=f"{_short_source(source_id)} {trust:.2f}",
                bg=theme.BG, fg=theme.TEXT_MUTED, font=fonts["badge"],
                padx=6, pady=2,
            ).pack(side="left", padx=2)

        age_text = (
            "listed today" if breakdown.age_days < 1
            else f"last listed {breakdown.age_days:.1f}d ago"
        )
        tk.Label(meta, text=f"{record.kind.value} · {age_text}", bg=theme.CARD,
                 fg=theme.TEXT_FAINT, font=fonts["small"]).pack(anchor="e", pady=(4, 0))

        # --- actions -------------------------------------------------------- #
        actions = tk.Frame(self, bg=theme.CARD)
        actions.grid(row=0, column=4, rowspan=2, sticky="e", padx=(0, 12))

        self._button(actions, "Copy", theme.ACCENT,
                     lambda: self.app.copy_code(record.code)).pack(side="left", padx=3)
        # These two are the sensor.  The user redeems by hand and reports back;
        # nothing here touches Roblox.
        self._button(actions, "Worked", theme.ACTIVE,
                     lambda: self.app.report(record.code, VerificationOutcome.WORKING)
                     ).pack(side="left", padx=3)
        self._button(actions, "Didn't work", theme.DEAD,
                     lambda: self.app.report(record.code, VerificationOutcome.DEAD)
                     ).pack(side="left", padx=3)

        for widget in (self, identity, meta):
            widget.bind("<Enter>", self._hover_on, add="+")
            widget.bind("<Leave>", self._hover_off, add="+")

    def _button(self, parent, label: str, colour: str, command) -> tk.Button:
        return tk.Button(
            parent, text=label, command=command, font=self.fonts["small"],
            bg=theme.CARD, fg=colour, activeforeground=colour,
            activebackground=theme.BG, relief="flat", bd=0,
            highlightthickness=1, highlightbackground=theme.BORDER,
            padx=10, pady=5, cursor="hand2",
        )

    def _hover_on(self, _event=None) -> None:
        self.configure(highlightbackground=theme.BORDER_STRONG)

    def _hover_off(self, _event=None) -> None:
        self.configure(highlightbackground=theme.BORDER)


def _short_source(source_id: str) -> str:
    """'example_community' -> 'COMMUNITY'.  Badges have to fit."""
    tail = source_id.split("_")[-1]
    return tail[:9].upper()


# --------------------------------------------------------------------------- #
# The window
# --------------------------------------------------------------------------- #

class CodeSeekerApp:
    """
    Wires the FSM to a window.

    Holds no beliefs of its own: every number on screen comes from a
    `TickResult` the FSM handed over.  That means the display cannot get out of
    step with the agent, and it means closing the window loses nothing.
    """

    def __init__(
        self,
        fsm: AgentFSM,
        clock: Clock,
        config: dict,
        belief_path: Path,
        snapshot_dir: Path,
    ):
        self.fsm = fsm
        self.clock = clock
        self.config = config
        self.belief_path = belief_path
        self.snapshot_dir = snapshot_dir

        self.root = tk.Tk()
        self.root.title("CodeSeeker — a model-based agent for Roblox game codes")
        self.root.geometry(f"{MIN_WIDTH}x{MIN_HEIGHT}")
        self.root.minsize(980, 620)
        self.root.configure(bg=theme.BG)

        self.fonts = theme.build_fonts()
        self.refinement_var = tk.BooleanVar(value=fsm.refinement_enabled)
        self.game_var = tk.StringVar(value=fsm.game)
        self.status_var = tk.StringVar(value="starting up")
        self.clock_var = tk.StringVar(value="")

        self._rows: list[CodeRow] = []
        self._poll_job: str | None = None

        self._build_header()
        self._build_body()
        self._build_statusbar()

        # PRESENT delivers here.  The FSM calls this; the GUI never reaches in.
        self.fsm.presenter = self.present

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(120, self._first_tick)

    # -- construction ------------------------------------------------------- #

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=theme.CARD, highlightbackground=theme.BORDER,
                          highlightthickness=1)
        header.pack(fill="x", side="top")

        inner = tk.Frame(header, bg=theme.CARD)
        inner.pack(fill="x", padx=20, pady=14)

        left = tk.Frame(inner, bg=theme.CARD)
        left.pack(side="left")
        tk.Label(left, text="CodeSeeker", bg=theme.CARD, fg=theme.TEXT,
                 font=self.fonts["title"]).pack(anchor="w")
        tk.Label(
            left,
            text="ranked by the agent's belief that each code still works — "
                 "you redeem by hand, the agent learns from what you report",
            bg=theme.CARD, fg=theme.TEXT_MUTED, font=self.fonts["small"],
        ).pack(anchor="w")

        right = tk.Frame(inner, bg=theme.CARD)
        right.pack(side="right")

        # --- game picker ---------------------------------------------------- #
        games = list(self.config.get("games", {}).keys())
        picker = tk.Frame(right, bg=theme.CARD)
        picker.pack(side="left", padx=(0, 18))
        tk.Label(picker, text="GAME", bg=theme.CARD, fg=theme.TEXT_FAINT,
                 font=self.fonts["badge"]).pack(anchor="w")
        combo = ttk.Combobox(picker, values=games, textvariable=self.game_var,
                             state="readonly", width=14, font=self.fonts["body"])
        combo.pack()
        combo.bind("<<ComboboxSelected>>", self._on_game_changed)

        # --- clock ---------------------------------------------------------- #
        clock_box = tk.Frame(right, bg=theme.CARD)
        clock_box.pack(side="left", padx=(0, 18))
        tk.Label(clock_box, text="CLOCK", bg=theme.CARD, fg=theme.TEXT_FAINT,
                 font=self.fonts["badge"]).pack(anchor="w")
        tk.Label(clock_box, textvariable=self.clock_var, bg=theme.CARD,
                 fg=theme.TEXT, font=self.fonts["metric"]).pack(anchor="w")

        controls = tk.Frame(right, bg=theme.CARD)
        controls.pack(side="left")
        tk.Label(controls, text="AGENT", bg=theme.CARD, fg=theme.TEXT_FAINT,
                 font=self.fonts["badge"]).pack(anchor="w")
        buttons = tk.Frame(controls, bg=theme.CARD)
        buttons.pack()

        self._header_button(buttons, "Poll now", self.poll_now).pack(side="left", padx=2)
        if isinstance(self.clock, VirtualClock):
            # The demo beat.  Fast-forwarding is what makes an internal model
            # visible: nothing new arrives and the beliefs change anyway.
            self._header_button(buttons, "+1 hour",
                                lambda: self.advance(hours=1)).pack(side="left", padx=2)
            self._header_button(buttons, "+1 day",
                                lambda: self.advance(days=1)).pack(side="left", padx=2)
            self._header_button(buttons, "Reset clock",
                                self.reset_clock).pack(side="left", padx=2)

        toggle = tk.Checkbutton(
            controls,
            text="source-trust refinement",
            variable=self.refinement_var,
            command=self._on_refinement_toggled,
            bg=theme.CARD, fg=theme.TEXT_MUTED, font=self.fonts["small"],
            activebackground=theme.CARD, selectcolor=theme.CARD,
            highlightthickness=0, bd=0, cursor="hand2",
        )
        toggle.pack(anchor="w", pady=(4, 0))
        Tooltip(toggle, lambda: (
            "OFF: a pure model-based agent. A user report corrects that one code only.\n\n"
            "ON: the agent also revises how much it trusts every source that listed "
            "the code — model refinement, in the sense of Russell & Norvig's learning "
            "agent. Mark a code dead with this on and watch the other codes from that "
            "source drop too."
        ))

    def _header_button(self, parent, label: str, command) -> tk.Button:
        return tk.Button(
            parent, text=label, command=command, font=self.fonts["small"],
            bg=theme.BG, fg=theme.TEXT, activebackground=theme.BORDER,
            relief="flat", bd=0, padx=10, pady=5, cursor="hand2",
        )

    def _build_body(self) -> None:
        body = tk.Frame(self.root, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=18, pady=14)

        # --- left: the ranked list ----------------------------------------- #
        left = tk.Frame(body, bg=theme.BG)
        left.pack(side="left", fill="both", expand=True)

        heading = tk.Frame(left, bg=theme.BG)
        heading.pack(fill="x", pady=(0, 8))
        tk.Label(heading, text="Ranked codes", bg=theme.BG, fg=theme.TEXT,
                 font=self.fonts["subtitle"]).pack(side="left")

        legend = tk.Frame(heading, bg=theme.BG)
        legend.pack(side="right")
        for status, label in (
            (CodeStatus.ACTIVE, "try this"),
            (CodeStatus.SUSPECT, "try it last"),
            (CodeStatus.DEAD, "don't bother"),
        ):
            fg, soft = theme.status_colours(status)
            chip = tk.Frame(legend, bg=theme.BG)
            chip.pack(side="left", padx=6)
            tk.Frame(chip, bg=fg, width=9, height=9).pack(side="left", padx=(0, 5))
            tk.Label(chip, text=label, bg=theme.BG, fg=theme.TEXT_MUTED,
                     font=self.fonts["small"]).pack(side="left")

        # Scrollable canvas holding one CodeRow per code.
        outer = tk.Frame(left, bg=theme.BG)
        outer.pack(fill="both", expand=True)
        self.list_canvas = tk.Canvas(outer, bg=theme.BG, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical",
                                  command=self.list_canvas.yview)
        self.list_frame = tk.Frame(self.list_canvas, bg=theme.BG)

        self.list_frame.bind(
            "<Configure>",
            lambda e: self.list_canvas.configure(
                scrollregion=self.list_canvas.bbox("all")
            ),
        )
        self._list_window = self.list_canvas.create_window(
            (0, 0), window=self.list_frame, anchor="nw"
        )
        self.list_canvas.bind(
            "<Configure>",
            lambda e: self.list_canvas.itemconfig(self._list_window, width=e.width),
        )
        self.list_canvas.configure(yscrollcommand=scrollbar.set)
        self.list_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.list_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        # --- right: the transition log -------------------------------------- #
        right = tk.Frame(body, bg=theme.BG, width=430)
        right.pack(side="right", fill="y", padx=(18, 0))
        right.pack_propagate(False)

        log_heading = tk.Frame(right, bg=theme.BG)
        log_heading.pack(fill="x", pady=(0, 8))
        tk.Label(log_heading, text="State transitions", bg=theme.BG, fg=theme.TEXT,
                 font=self.fonts["subtitle"]).pack(side="left")
        tk.Label(log_heading, text="docs/FSM.md", bg=theme.BG, fg=theme.TEXT_FAINT,
                 font=self.fonts["small"]).pack(side="right")

        log_card = tk.Frame(right, bg=theme.CARD, highlightbackground=theme.BORDER,
                            highlightthickness=1)
        log_card.pack(fill="both", expand=True)
        self.log_text = tk.Text(
            log_card, bg=theme.CARD, fg=theme.TEXT_MUTED, font=self.fonts["log"],
            wrap="word", relief="flat", bd=0, padx=12, pady=10, state="disabled",
            spacing1=2, spacing3=4,
        )
        log_scroll = ttk.Scrollbar(log_card, orient="vertical",
                                   command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        for state, colour in theme.LOG_COLOURS.items():
            self.log_text.tag_configure(state, foreground=colour)
        self.log_text.tag_configure("warning", foreground="#dc2626")
        self.log_text.tag_configure("reason", foreground=theme.TEXT_MUTED)

    def _build_statusbar(self) -> None:
        bar = tk.Frame(self.root, bg=theme.CARD, highlightbackground=theme.BORDER,
                       highlightthickness=1)
        bar.pack(fill="x", side="bottom")
        tk.Label(bar, textvariable=self.status_var, bg=theme.CARD,
                 fg=theme.TEXT_MUTED, font=self.fonts["small"],
                 anchor="w").pack(side="left", padx=16, pady=7)
        tk.Label(
            bar,
            text="the agent never redeems codes — copy, paste it into Roblox "
                 "yourself, then tell it what happened",
            bg=theme.CARD, fg=theme.TEXT_FAINT, font=self.fonts["small"],
        ).pack(side="right", padx=16)

    # -- PRESENT ------------------------------------------------------------ #

    def present(self, result: TickResult) -> None:
        """
        The PRESENT state's actuator: render a TickResult.

        Called by the FSM, not by the window.  Rebuilds the rows wholesale —
        at a few dozen codes that is far cheaper than diffing, and it guarantees
        what is on screen is exactly what the agent last believed.
        """
        for row in self._rows:
            row.destroy()
        self._rows.clear()

        if not result.ranked:
            empty = tk.Label(
                self.list_frame,
                text="No codes known yet.\nPress “Poll now” to operate the agent's sensors.",
                bg=theme.BG, fg=theme.TEXT_FAINT, font=self.fonts["body"],
                justify="center", pady=40,
            )
            empty.pack(fill="x")
            self._rows.append(empty)  # type: ignore[arg-type]
        else:
            for record, breakdown in result.ranked:
                row = CodeRow(self.list_frame, self, record, breakdown, self.fonts)
                row.pack(fill="x", pady=3, padx=1)
                self._rows.append(row)

        self._append_log(result)
        self._update_clock_label()

        tally: dict[str, int] = {}
        for _, breakdown in result.ranked:
            tally[breakdown.status.value] = tally.get(breakdown.status.value, 0) + 1
        parts = ", ".join(f"{count} {status.lower()}" for status, count in sorted(tally.items()))
        trust = ", ".join(
            f"{_short_source(s)} {v:.2f}" for s, v in sorted(self.fsm.store.trust_map().items())
        )
        self.status_var.set(
            f"{len(result.ranked)} code(s) — {parts or 'none'}    |    "
            f"source trust: {trust or 'none'}    |    "
            f"refinement {'ON' if self.fsm.refinement_enabled else 'OFF'}"
        )

    def _append_log(self, result: TickResult) -> None:
        epoch = self.clock.start if isinstance(self.clock, VirtualClock) else None
        self.log_text.configure(state="normal")
        for transition in result.transitions:
            elapsed = (
                (transition.at - epoch).total_seconds() / 86400.0 if epoch else None
            )
            stamp = (
                f"day {elapsed:5.2f}" if elapsed is not None
                else transition.at.strftime("%H:%M:%S")
            )
            self.log_text.insert(
                "end",
                f"[{stamp}] {transition.from_state.value} → {transition.to_state.value}\n",
                transition.to_state.value,
            )
            self.log_text.insert("end", f"          {transition.reason}\n", "reason")
        for warning in result.warnings:
            self.log_text.insert("end", f"          ! {warning}\n", "warning")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # -- actions ------------------------------------------------------------ #

    def copy_code(self, code: str) -> None:
        """
        Put a code on the clipboard.  This is as close to Roblox as the agent
        gets: the user pastes it themselves.
        """
        self.root.clipboard_clear()
        self.root.clipboard_append(code)
        self.status_var.set(f"copied {code} — paste it into Roblox, then report back")

    def report(self, code: str, outcome: VerificationOutcome) -> None:
        """
        The sensor firing.  Enters the FSM at VERIFY — belief meets ground truth.
        """
        self.fsm.report_outcome(code, outcome, self.clock.now())
        self.fsm.store.save(self.belief_path)

    def poll_now(self) -> None:
        self.fsm.tick(self.clock.now())
        self.fsm.store.save(self.belief_path)

    def advance(self, days: float = 0.0, hours: float = 0.0) -> None:
        """
        Move the virtual clock and run a tick.

        The tick still polls, which is deliberate: some codes are still listed
        and hold their confidence while others have dropped off and fall.  That
        contrast is a sharper demonstration than a uniform slide would be,
        because it shows the agent discriminating rather than just counting down.
        """
        if isinstance(self.clock, VirtualClock):
            self.clock.advance(days=days, hours=hours)
        self.poll_now()

    def reset_clock(self) -> None:
        if isinstance(self.clock, VirtualClock):
            self.clock.reset()
            self._update_clock_label()
            self.status_var.set("virtual clock reset to day 0")

    def _on_refinement_toggled(self) -> None:
        self.fsm.refinement_enabled = self.refinement_var.get()
        state = "ON" if self.fsm.refinement_enabled else "OFF"
        self.status_var.set(
            f"source-trust refinement {state} — "
            + ("the agent now revises its trust in sources from your reports"
               if self.fsm.refinement_enabled
               else "the agent corrects individual codes only (pure model-based)")
        )

    def _on_game_changed(self, _event=None) -> None:
        self.fsm.select_game(self.game_var.get(), self.clock.now())

    # -- lifecycle ---------------------------------------------------------- #

    def _first_tick(self) -> None:
        self.poll_now()
        self._schedule_poll()

    def _schedule_poll(self) -> None:
        """
        Re-poll on the configured interval, but only on a real clock.

        On a virtual clock time moves when the user presses a button, so an
        automatic timer would fight them for control of the demo.
        """
        if isinstance(self.clock, VirtualClock):
            return
        minutes = float(self.config.get("poll_interval_minutes", 30))
        self._poll_job = self.root.after(int(minutes * 60_000), self._auto_poll)

    def _auto_poll(self) -> None:
        self.poll_now()
        self._schedule_poll()

    def _update_clock_label(self) -> None:
        if isinstance(self.clock, VirtualClock):
            self.clock_var.set(f"virtual · day {self.clock.elapsed_days:.2f}")
        else:
            self.clock_var.set(self.clock.now().strftime("%H:%M:%S UTC"))

    def _on_mousewheel(self, event) -> None:
        try:
            self.list_canvas.yview_scroll(int(-1 * (event.delta / 2)), "units")
        except tk.TclError:
            pass

    def _on_close(self) -> None:
        """Persist belief before closing, so the agent remembers across runs."""
        if self._poll_job is not None:
            self.root.after_cancel(self._poll_job)
        try:
            self.fsm.store.save(self.belief_path)
        except OSError:
            pass
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def launch(
    fsm: AgentFSM,
    clock: Clock,
    config: dict,
    belief_path: Path,
    snapshot_dir: Path,
) -> None:
    """Entry point called by `main.py`."""
    CodeSeekerApp(fsm, clock, config, belief_path, snapshot_dir).run()
