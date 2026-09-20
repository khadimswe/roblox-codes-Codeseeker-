"""
sources/web.py — the live sensor: fetch real code listings from public pages.

Model-based agent role
----------------------
Same role as `sources/canned.py` — a **sensor** the agent operates through
`poll()` — but pointed at the real environment instead of a replay.  The agent
cannot tell the difference, which is the point: the belief model is identical
either way, so the demo recorded against fixtures is an honest demonstration of
what runs against live pages.

Politeness (CLAUDE.md hard constraint 2)
----------------------------------------
This module talks to servers that owe us nothing, so:

  * **An honest User-Agent.** It says what this is and that it is a student
    project.  Pretending to be a browser would be the first step toward
    behaviour the operator has not agreed to.
  * **robots.txt is respected**, fetched once per host and cached.  A
    disallowed URL is skipped and reported as a source failure, which the agent
    already knows how to survive.
  * **At least `MIN_DELAY_SECONDS` between requests to the same host**, enforced
    globally rather than per-source, because two configured sources can easily
    live on one host.
  * **Responses are cached on disk** during development, so iterating on a
    parser does not re-hit anyone's server.
  * **Requests to one host are never parallelised.** `poll()` is synchronous and
    sequential on purpose; there is no async here and none is wanted.

Scope boundary (CLAUDE.md hard constraint 1)
--------------------------------------------
This module reads public pages.  It never touches Roblox itself — no client
automation, no input injection, no account actions, no redemption.  Redemption
is the user's, by hand, and their report of the outcome is the agent's only true
sensor reading.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from sources.base import RawListing, Source, SourceError

#: Honest and descriptive, with a contact route.  See the politeness note above.
#:
#: ASCII only, deliberately.  HTTP headers are latin-1 encoded, so a stray
#: em-dash here raises UnicodeEncodeError on every request — a failure that
#: looks like a network problem and is not one.
USER_AGENT = (
    "CodeSeeker/1.0 (AI 3642 student coursework project; model-based agent demo; "
    "polls a handful of public code listings at most twice an hour; "
    "+https://github.com/khadimswe/roblox-codes-Codeseeker- "
    "- contact via repository issues)"
)

#: Minimum seconds between requests to the same host.  CLAUDE.md sets the floor
#: at 2; this is deliberately a little higher because nothing here is urgent.
MIN_DELAY_SECONDS = 2.5

#: How long a cached page stays usable.  Long enough that editing a parser never
#: re-hits a server, short enough that a real run sees real changes.
CACHE_TTL_SECONDS = 15 * 60

REQUEST_TIMEOUT_SECONDS = 15

#: Last request time per host, so the rate limit spans sources rather than
#: resetting for each one.
_last_request_at: dict[str, float] = {}
_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #

def _host_of(url: str) -> str:
    return urlparse(url).netloc.lower()


def _respect_rate_limit(host: str) -> None:
    """
    Block until this host may be contacted again.

    A plain sleep, deliberately.  The whole point is that the agent is slower
    than it could be; anything cleverer here would be optimising away the
    courtesy this is supposed to provide.
    """
    last = _last_request_at.get(host)
    if last is not None:
        wait = MIN_DELAY_SECONDS - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_request_at[host] = time.monotonic()


def _robots_allows(url: str) -> bool:
    """
    Check robots.txt for this host, fetching it at most once per run.

    A host whose robots.txt cannot be read is treated as allowing the fetch,
    which is the convention the standard describes for an unreachable file.  A
    host that explicitly disallows the path is not fetched.
    """
    host = _host_of(url)
    if host not in _robots_cache:
        parser = urllib.robotparser.RobotFileParser()
        robots_url = urljoin(f"{urlparse(url).scheme}://{host}", "/robots.txt")
        try:
            _respect_rate_limit(host)
            import requests

            response = requests.get(
                robots_url,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 200:
                parser.parse(response.text.splitlines())
                _robots_cache[host] = parser
            else:
                _robots_cache[host] = None
        except Exception:  # noqa: BLE001 - unreachable robots.txt means "allowed"
            _robots_cache[host] = None

    parser = _robots_cache[host]
    if parser is None:
        return True
    return parser.can_fetch(USER_AGENT, url)


def _cache_path(cache_dir: Path, url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{digest}.json"


def fetch(url: str, cache_dir: Path | None = None, use_cache: bool = True) -> str:
    """
    Fetch one page politely, via the on-disk cache when possible.

    Raises `SourceError` for every failure mode — a missing dependency, a
    disallowed path, a timeout, a bad status.  The caller catches it and
    degrades the run rather than ending it.
    """
    try:
        import requests
    except ImportError as exc:                       # pragma: no cover
        raise SourceError(
            "the 'requests' package is required for --source web "
            "(pip install -r requirements.txt)"
        ) from exc

    if cache_dir and use_cache:
        path = _cache_path(cache_dir, url)
        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                age = time.time() - cached["fetched_at"]
                if age < CACHE_TTL_SECONDS:
                    return cached["body"]
            except (json.JSONDecodeError, KeyError, OSError):
                pass                                  # a bad cache entry is not fatal

    if not _robots_allows(url):
        raise SourceError(f"robots.txt disallows fetching {url}")

    _respect_rate_limit(_host_of(url))
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        raise SourceError(f"{type(exc).__name__}: {exc}") from exc

    if response.status_code != 200:
        raise SourceError(f"HTTP {response.status_code}")

    if cache_dir:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            _cache_path(cache_dir, url).write_text(
                json.dumps({"url": url, "fetched_at": time.time(), "body": response.text}),
                encoding="utf-8",
            )
        except OSError:
            pass                                      # caching is a convenience

    return response.text


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

#: Inline markup that wraps a bare code on a listing page.  Only consulted by
#: the second strategy below, and only when the tag contains *nothing but* a
#: code.
_INLINE_TAGS = ("code", "strong", "b", "kbd", "samp", "mark", "em")

#: A code-shaped token: uppercase-ish, no spaces, long enough not to be a word
#: fragment.  Codes are matched case-insensitively, so this is applied to the
#: upper-cased text.
_CODE_TOKEN = re.compile(r"[A-Z][A-Z0-9_]{3,31}")

#: Header wording that marks the column holding codes, for the table strategy.
_CODE_HEADER = re.compile(r"\bcodes?\b", re.IGNORECASE)


def parse_generic_list(html: str, source_id: str) -> list[RawListing]:
    """
    Pull candidate codes out of a listing page.

    Two strategies, in precision order:

    1. **Tables with a "Code" column.**  Nearly every codes page publishes a
       table of code / reward / date, and a table cell is an unambiguous
       container — the cell *is* the code, so there is no guessing where the
       code ends and the prose begins.  This is where almost all real codes
       come from.

    2. **Inline tags containing nothing but a code**, e.g.
       ``<strong>RELEASE26</strong> - 30 Spins``.  The requirement that the tag
       hold *only* the code is the important part: matching the first token of
       any tag instead would turn the sentence "Slayers 2 on Roblox. Click..."
       into the code "SLAYERS", and a page's navigation into TIKTOK, INSTAGRAM
       and FACEBOOK.  Precision beats recall here — a missed code costs the user
       one code, an invented one costs the agent its credibility.

    Whatever survives still has to get past `agent/percepts.py`, which rejects
    anything that is not code-shaped and drops a stoplist of page furniture.
    Keeping that strictness in one shared place means a sloppy parser can only
    ever cost recall; it can never inject a fabricated code into the belief
    store.

    The reward text beside a code is captured too, because
    `percepts.infer_kind()` reads it to decide how fast that code should decay.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:                        # pragma: no cover
        raise SourceError(
            "the \'beautifulsoup4\' package is required for --source web "
            "(pip install -r requirements.txt)"
        ) from exc

    soup = BeautifulSoup(html, "html.parser")
    for junk in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        junk.decompose()

    listings: list[RawListing] = []
    seen: set[str] = set()

    def add(code: str, reward: str) -> None:
        key = code.upper()
        if key in seen:
            return
        seen.add(key)
        listings.append(
            RawListing(source_id=source_id, code=code, claimed_reward=reward[:120])
        )

    # ---- strategy 1: code tables ----------------------------------------- #
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue

        header_cells = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
        code_column = next(
            (i for i, text in enumerate(header_cells) if _CODE_HEADER.search(text)),
            None,
        )
        if code_column is None:
            # No "Code" header: only trust the table if its first column looks
            # uniformly code-shaped, which rules out generic content tables.
            code_column = 0
            firsts = [
                r.find_all(["td", "th"])[0].get_text(" ", strip=True)
                for r in rows[1:6]
                if r.find_all(["td", "th"])
            ]
            if not firsts or not all(_CODE_TOKEN.fullmatch(f.upper()) for f in firsts):
                continue

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) <= code_column:
                continue
            code = cells[code_column].get_text(" ", strip=True)
            if not _CODE_TOKEN.fullmatch(code.upper()):
                continue
            reward = " ".join(
                c.get_text(" ", strip=True)
                for i, c in enumerate(cells)
                if i != code_column
            )
            add(code, reward)

    # ---- strategy 2: inline tags holding nothing but a code --------------- #
    for element in soup.find_all(_INLINE_TAGS):
        text = element.get_text(" ", strip=True).strip(" :-\u2013\u2014.,\u2022|")
        if not text or not _CODE_TOKEN.fullmatch(text.upper()):
            continue
        add(text, _reward_near(element, text))

    return listings


def _reward_near(element, code: str) -> str:
    """
    The prose sitting next to a code - usually "- 1,000 Spins" or a sibling cell.

    Only a hint for kind inference and for display, so a miss is harmless: the
    code simply decays at the baseline rate.
    """
    parts: list[str] = []
    for sibling in list(element.next_siblings)[:2]:
        text = getattr(sibling, "get_text", lambda *_a, **_k: str(sibling))(" ", strip=True)
        if text:
            parts.append(text)
    if not parts and element.parent is not None:
        parts.append(element.parent.get_text(" ", strip=True).replace(code, "", 1))
    return re.sub(r"\s+", " ", " ".join(parts)).strip(" -\u2013\u2014:\u2022|")[:120]


PARSERS = {"generic_list": parse_generic_list}


# --------------------------------------------------------------------------- #
# The source
# --------------------------------------------------------------------------- #

class WebSource(Source):
    """One configured URL, fetched politely and parsed into raw listings."""

    def __init__(
        self,
        source_id: str,
        display_name: str,
        url: str,
        parser: str = "generic_list",
        cache_dir: Path | None = None,
    ):
        self.source_id = source_id
        self.display_name = display_name
        self.url = url
        self.parser_name = parser
        self.cache_dir = cache_dir

    def poll(self, game: str, now: datetime) -> list[RawListing]:
        """
        Fetch and parse.  `now` is used only to stamp the listings — a live page
        shows what it shows, and there is no fast-forwarding the real world.
        """
        if not self.url:
            raise SourceError(
                f"no url configured for {self.source_id}; "
                f"fill it in config/games.json"
            )

        parser = PARSERS.get(self.parser_name)
        if parser is None:
            raise SourceError(f"unknown parser {self.parser_name!r}")

        html = fetch(self.url, cache_dir=self.cache_dir)
        listings = parser(html, self.source_id)

        if not listings:
            # Almost always a layout change rather than an empty page.  Saying
            # so explicitly beats silently reporting zero codes, which the agent
            # would otherwise read as "everything has been removed".
            raise SourceError(
                f"parsed 0 codes from {self.url} — the page layout has probably changed"
            )

        return [
            RawListing(
                source_id=listing.source_id,
                code=listing.code,
                claimed_reward=listing.claimed_reward,
                kind_hint=listing.kind_hint,
                observed_at=now,
            )
            for listing in listings
        ]


def build_web_sources(config: dict, game: str, cache_dir: Path | None = None) -> list[WebSource]:
    """
    Construct one WebSource per configured source that has a url.

    Entries without a url are skipped rather than built and left to fail.  The
    config deliberately carries a few url-less entries — they are the source ids
    the synthetic fixtures in `data/snapshots/` use, so that `--demo` has
    differentiated trust to work with — and a source with no url is simply not a
    live source.  Building them anyway would add a warning to every live poll
    that told the user nothing.
    """
    game_config = (config.get("games", {}) or {}).get(game, {}) or {}
    return [
        WebSource(
            source_id=entry["id"],
            display_name=entry.get("display_name", entry["id"]),
            url=entry["url"],
            parser=entry.get("parser", "generic_list"),
            cache_dir=cache_dir,
        )
        for entry in game_config.get("sources", [])
        if entry.get("url")
    ]


# --------------------------------------------------------------------------- #
# Recording real snapshots
# --------------------------------------------------------------------------- #

def record_snapshot(
    sources: list[Source], game: str, snapshot_dir: Path, now: datetime
) -> int:
    """
    Fetch live listings once and save them as RECORDED fixtures.

    This is the honest path to real fixture data.  CLAUDE.md allows fixture
    codes that were "genuinely recorded from a live source, with the capture
    date noted in the fixture file", and this writes exactly that: real codes,
    a real `captured_at`, and `provenance: "RECORDED"`.  The canned source
    replays recorded and synthetic fixtures identically.

    Run it on a few different days and the canned source will replay those days
    as a timeline, which makes the demo run on genuine data.
    """
    from sources.base import poll_safely

    snapshot_dir = Path(snapshot_dir) / game / "recorded"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d")
    written = 0

    for source in sources:
        outcome = poll_safely(source, game, now)
        if not outcome.ok:
            print(f"  {source.source_id}: SKIPPED ({outcome.error})")
            continue

        payload = {
            "game": game,
            "source_id": source.source_id,
            "source_display_name": source.display_name,
            "offset_days": None,
            "provenance": "RECORDED",
            "captured_at": now.isoformat(),
            "capture_note": (
                f"RECORDED from {getattr(source, 'url', 'live source')} on "
                f"{now.date().isoformat()} by 'python main.py --source web --record'. "
                f"These are real listings as published on that date; the agent makes "
                f"no claim that they still work."
            ),
            "listings": [
                {"code": l.code, "claimed_reward": l.claimed_reward}
                for l in outcome.listings
            ],
        }
        path = snapshot_dir / f"recorded_{stamp}_{source.source_id}.json"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"  {source.source_id}: wrote {len(outcome.listings)} listings -> {path}")
        written += 1

    if written == 0:
        print("no snapshots recorded — check the urls in config/games.json")
        return 1
    print(f"\nrecorded {written} snapshot file(s). Replay with: python main.py --demo")
    return 0
