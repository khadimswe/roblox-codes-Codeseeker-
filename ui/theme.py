"""
ui/theme.py — colours, fonts and spacing for the Tkinter GUI.

Kept separate from `ui/app.py` so the widget code stays about layout and the
palette stays about meaning.  Every colour here is tied to something the agent
believes, not to decoration:

    ACTIVE   green   try this one
    SUSPECT  amber   try it last
    DEAD     grey    don't bother

PLAN.md fixes those three, and the report's screenshots depend on them reading
clearly, so the palette is high-contrast on a light background rather than
pretty — a confidence bar has to be legible in a compressed screen recording.
"""

from __future__ import annotations

import tkinter.font as tkfont

from agent.state import CodeStatus

# -- surfaces ---------------------------------------------------------------- #
BG = "#f4f5f7"
CARD = "#ffffff"
CARD_HOVER = "#fbfcfd"
BORDER = "#e1e4e8"
BORDER_STRONG = "#cbd1d8"

# -- text -------------------------------------------------------------------- #
TEXT = "#16191d"
TEXT_MUTED = "#6a727c"
TEXT_FAINT = "#98a0aa"

# -- meaning ----------------------------------------------------------------- #
ACCENT = "#2563eb"
ACTIVE = "#15a34a"
ACTIVE_SOFT = "#e7f6ec"
SUSPECT = "#c2740a"
SUSPECT_SOFT = "#fdf3e3"
DEAD = "#8b939d"
DEAD_SOFT = "#eef0f2"
TRACK = "#e6e9ec"

#: Log lines are tinted by which state they moved into, so the two transitions
#: that carry the argument — DECAY with no input, and VERIFY — stand out on a
#: recording without anyone having to pause and read.
LOG_COLOURS = {
    "POLL_SOURCES": "#6a727c",
    "EXTRACT": "#6a727c",
    "RECONCILE": "#2563eb",
    "DECAY": "#c2740a",
    "RANK": "#6a727c",
    "PRESENT": "#6a727c",
    "VERIFY": "#9333ea",
    "PURGE": "#dc2626",
    "IDLE": "#98a0aa",
}

STATUS_COLOURS: dict[CodeStatus, tuple[str, str]] = {
    CodeStatus.ACTIVE: (ACTIVE, ACTIVE_SOFT),
    CodeStatus.SUSPECT: (SUSPECT, SUSPECT_SOFT),
    CodeStatus.DEAD: (DEAD, DEAD_SOFT),
    CodeStatus.UNVERIFIED: (TEXT_MUTED, DEAD_SOFT),
}


def status_colours(status: CodeStatus) -> tuple[str, str]:
    """(foreground, background) for a status pill and its meter fill."""
    return STATUS_COLOURS.get(status, (TEXT_MUTED, DEAD_SOFT))


def build_fonts() -> dict[str, tkfont.Font]:
    """
    Named fonts, built after the root window exists.

    A monospace face for codes specifically: they are meant to be read
    character by character and then pasted, and a proportional font makes
    O/0 and I/l ambiguous in exactly the place where a mistake costs the user
    a failed redemption.
    """
    mono = "SF Mono" if _has_font("SF Mono") else ("Menlo" if _has_font("Menlo") else "Courier")
    sans = "Helvetica Neue" if _has_font("Helvetica Neue") else "Helvetica"
    return {
        "title": tkfont.Font(family=sans, size=17, weight="bold"),
        "subtitle": tkfont.Font(family=sans, size=11),
        "code": tkfont.Font(family=mono, size=14, weight="bold"),
        "body": tkfont.Font(family=sans, size=11),
        "small": tkfont.Font(family=sans, size=10),
        "badge": tkfont.Font(family=sans, size=9, weight="bold"),
        "log": tkfont.Font(family=mono, size=10),
        "metric": tkfont.Font(family=mono, size=11, weight="bold"),
    }


def _has_font(name: str) -> bool:
    try:
        return name in tkfont.families()
    except Exception:  # noqa: BLE001 - font enumeration needs a live Tk root
        return False
