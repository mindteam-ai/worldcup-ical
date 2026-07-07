#!/usr/bin/env python3
"""Generate an iCalendar (.ics) feed for the 2026 FIFA World Cup.

Design goals (per request):
  * Lists every World Cup match (group stage + all knockout rounds).
  * Re-runs every 6 hours so knockout matches fill in with the *actual*
    qualified teams as the bracket resolves.
  * Each event shows the host stadium + city in LOCATION.
  * Scores are NEVER written into the feed (so a finished match's event
    never mutates into "Team A 2-1 Team B").
  * Each event links to Peacock to watch live or the full replay.

Data source
-----------
fixturedownload.com free JSON feed -- no API key, complete, and it updates
HomeTeam/AwayTeam as knockout teams are confirmed. If a run fails validation
the script exits non-zero, CI commits nothing, and the previously published
feed keeps serving -- stale-but-correct beats fresh-but-wrong.

Output is deterministic given the same input so the GitHub Action only commits
(and Netlify only redeploys) when something actually changed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

USER_AGENT = "WorldCupICalBot/1.0 (https://github.com/mindteam-ai/worldcup-ical; carl@mindteam.ai)"
FIXTUREDOWNLOAD_URL = "https://fixturedownload.com/feed/json/fifa-world-cup-2026"
# Peacock's Spanish-language (Telemundo) World Cup hub. All 104 matches stream
# live here for Premium subscribers, with full replays afterward. Per-match
# deep links are GUID-based and not constructable ahead of time, so every
# event points at the hub.
PEACOCK_URL = "https://www.peacocktv.com/sports/world-cup"

# Kickoff window blocked out on the calendar. Knockout matches get a longer
# block because extra time + a penalty shootout runs ~2h45 end to end. Events
# are TRANSP:TRANSPARENT either way, so this is display-only.
GROUP_DURATION = timedelta(hours=2)
KNOCKOUT_DURATION = timedelta(hours=2, minutes=45)

# Display alarm before kickoff; None (or --no-alarms) disables. Note: Apple
# Calendar honors feed alarms unless the subscriber strips them; Google
# Calendar ignores alarms in subscribed feeds entirely.
REMINDER_MINUTES: int | None = 30

CALNAME = "2026 FIFA World Cup"
CALDESC = ("All 2026 FIFA World Cup matches. Knockout teams update "
           "automatically as groups finish. Watch on Peacock. No scores shown.")
CALCOLOR = "#0B8043"  # pitch green (Apple hex + closest CSS name below)
CALCOLOR_CSS = "forestgreen"
PRODID = "-//mindteam-ai//World Cup 2026 iCal Feed//EN"
UID_DOMAIN = "worldcup2026.mindteam.ai"

# Stadium / city resolution. Keys are lowercase substrings that may appear in a
# source's "Location"/"stadium" field (tournament name, sponsor name, or city).
# Value: (display stadium name, city, country).
VENUES: list[tuple[tuple[str, ...], tuple[str, str, str]]] = [
    (("metlife", "new york new jersey", "new york/new jersey", "east rutherford"),
     ("MetLife Stadium", "New York / New Jersey", "USA")),
    (("at&t stadium", "at&t", "dallas", "arlington"),
     ("AT&T Stadium", "Dallas (Arlington)", "USA")),
    (("mercedes-benz", "mercedes benz", "atlanta"),
     ("Mercedes-Benz Stadium", "Atlanta", "USA")),
    (("nrg", "houston"),
     ("NRG Stadium", "Houston", "USA")),
    (("arrowhead", "kansas city"),
     ("Arrowhead Stadium", "Kansas City", "USA")),
    (("sofi", "los angeles", "inglewood"),
     ("SoFi Stadium", "Los Angeles (Inglewood)", "USA")),
    (("hard rock", "miami"),
     ("Hard Rock Stadium", "Miami", "USA")),
    (("lincoln financial", "philadelphia"),
     ("Lincoln Financial Field", "Philadelphia", "USA")),
    (("levi's", "levis", "san francisco", "santa clara", "bay area"),
     ("Levi's Stadium", "San Francisco Bay Area (Santa Clara)", "USA")),
    (("lumen", "seattle"),
     ("Lumen Field", "Seattle", "USA")),
    (("gillette", "foxborough", "boston"),
     ("Gillette Stadium", "Boston (Foxborough)", "USA")),
    (("bmo", "toronto"),
     ("BMO Field", "Toronto", "Canada")),
    (("bc place", "vancouver"),
     ("BC Place", "Vancouver", "Canada")),
    (("azteca", "banorte", "mexico city", "ciudad de mexico"),
     ("Estadio Azteca", "Mexico City", "Mexico")),
    (("akron", "guadalajara", "zapopan"),
     ("Estadio Akron", "Guadalajara", "Mexico")),
    (("bbva", "monterrey", "guadalupe"),
     ("Estadio BBVA", "Monterrey", "Mexico")),
]

# Bracket feeders for knockout matches from the round of 16 onward: match
# number -> (kind, home feeder match, away feeder match). Lets an undecided
# slot render as "France/Morocco" instead of "TBD" once its feeder match has
# teams. Verified against the official 2026 bracket (FIFA / AXS listings) AND
# empirically against the resolved R16/QF matchups in the live data.
# R32 slots (73-88) come from group positions + best-thirds routing, which
# only FIFA's table defines authoritatively -- deliberately not mapped here.
KNOCKOUT_FEEDERS: dict[int, tuple[str, int, int]] = {
    89: ("winner", 74, 77), 90: ("winner", 73, 75),
    91: ("winner", 76, 78), 92: ("winner", 79, 80),
    93: ("winner", 83, 84), 94: ("winner", 81, 82),
    95: ("winner", 86, 88), 96: ("winner", 85, 87),
    97: ("winner", 89, 90), 98: ("winner", 93, 94),
    99: ("winner", 91, 92), 100: ("winner", 95, 96),
    101: ("winner", 97, 98), 102: ("winner", 99, 100),
    103: ("loser", 101, 102), 104: ("winner", 101, 102),
}


def resolve_venue(raw: str) -> tuple[str, str, str]:
    """Map a raw location string to (stadium, city, country)."""
    low = (raw or "").lower()
    for needles, info in VENUES:
        if any(n in low for n in needles):
            return info
    # Unknown venue: keep whatever the source gave us, no city/country.
    return (raw.strip() or "Venue TBD", "", "")


# --------------------------------------------------------------------------- #
# Match model
# --------------------------------------------------------------------------- #

@dataclass
class Match:
    number: int
    start: datetime  # timezone-aware UTC
    home: str
    away: str
    venue_raw: str
    round_label: str  # e.g. "Group A", "Round of 32", "Final"

    @property
    def is_knockout(self) -> bool:
        return not self.round_label.lower().startswith("group")


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read()


# --------------------------------------------------------------------------- #
# Source: fixturedownload.com
# --------------------------------------------------------------------------- #

def _parse_dt(value: str) -> datetime:
    """Parse fixturedownload's DateUtc into an aware UTC datetime."""
    v = value.strip().replace("Z", "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(v, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date format: {value!r}")


# fixturedownload uses "Group A" .. and for knockout the Group field carries the
# round name; RoundNumber distinguishes stages. Map RoundNumber -> label when
# Group is missing/ambiguous.
_ROUND_BY_NUMBER = {
    8: "Round of 32",
    9: "Round of 16",
    10: "Quarter-final",
    11: "Semi-final",
    12: "Third-place play-off",
    13: "Final",
}


# Strings various sources use for an undecided knockout slot.
_TBD_VALUES = {"", "to be announced", "tbd", "tba", "to be determined", "winner", "loser"}


def _clean_team(name: str) -> str:
    name = (name or "").strip()
    if name.lower() in _TBD_VALUES:
        return "TBD"
    return name


def _round_for_number(number: int, group: str, round_no: int) -> str:
    """Authoritative round label from the fixed 2026 match numbering.

    1-72 group stage, 73-88 R32, 89-96 R16, 97-100 QF, 101-102 SF,
    103 third-place play-off, 104 final. The source's own round/group field is
    unreliable for the knockout stage (it mislabels the final as "Round of 32"),
    so the match number wins whenever it is present.
    """
    if 1 <= number <= 72:
        return group if group.lower().startswith("group") else (group or "Group stage")
    if 73 <= number <= 88:
        return "Round of 32"
    if 89 <= number <= 96:
        return "Round of 16"
    if 97 <= number <= 100:
        return "Quarter-final"
    if 101 <= number <= 102:
        return "Semi-final"
    if number == 103:
        return "Third-place play-off"
    if number == 104:
        return "Final"
    # No usable match number: fall back to the source's fields.
    if group.lower().startswith("group"):
        return group
    return _normalize_round_label(group or _ROUND_BY_NUMBER.get(round_no, ""), round_no)


def fetch_fixturedownload() -> list[Match]:
    data = json.loads(_http_get(FIXTUREDOWNLOAD_URL).decode("utf-8"))
    matches: list[Match] = []
    for row in data:
        number = int(row.get("MatchNumber") or 0)
        group = (row.get("Group") or "").strip()
        round_no = int(row.get("RoundNumber") or 0)
        round_label = _round_for_number(number, group, round_no)
        matches.append(
            Match(
                number=number,
                start=_parse_dt(row["DateUtc"]),
                home=_clean_team(row.get("HomeTeam")),
                away=_clean_team(row.get("AwayTeam")),
                venue_raw=(row.get("Location") or "").strip(),
                round_label=round_label,
            )
        )
    matches.sort(key=lambda m: (m.start, m.number))
    return matches


def _normalize_round_label(label: str, round_no: int) -> str:
    low = label.lower()
    if low.startswith("group"):
        return label
    if "round of 32" in low or low in {"r32"}:
        return "Round of 32"
    if "round of 16" in low or low in {"r16"}:
        return "Round of 16"
    if "quarter" in low:
        return "Quarter-final"
    if "semi" in low:
        return "Semi-final"
    if "third" in low or "3rd" in low or "bronze" in low:
        return "Third-place play-off"
    if low == "final" or "final" == low.strip():
        return "Final"
    return _ROUND_BY_NUMBER.get(round_no, label or f"Round {round_no}")


# --------------------------------------------------------------------------- #
# Validation — fail loudly rather than publish a broken feed
# --------------------------------------------------------------------------- #

_EXPECTED_ROUNDS = {
    "Round of 32": 16,
    "Round of 16": 8,
    "Quarter-final": 4,
    "Semi-final": 2,
    "Third-place play-off": 1,
    "Final": 1,
}
_TOURNAMENT_WINDOW = (
    datetime(2026, 6, 1, tzinfo=timezone.utc),
    datetime(2026, 8, 1, tzinfo=timezone.utc),
)


def validate(matches: list[Match]) -> None:
    """Exit non-zero on any structural anomaly so CI never commits bad data."""
    errors: list[str] = []

    numbers = sorted(m.number for m in matches)
    if numbers != list(range(1, 105)):
        errors.append(f"match numbers are not exactly 1..104 "
                      f"(count={len(matches)}, sample={numbers[:5]}…{numbers[-3:]})")

    group_ct = sum(1 for m in matches if not m.is_knockout)
    if group_ct != 72:
        errors.append(f"expected 72 group-stage matches, got {group_ct}")
    for label, want in _EXPECTED_ROUNDS.items():
        got = sum(1 for m in matches if m.round_label == label)
        if got != want:
            errors.append(f"expected {want} × {label!r}, got {got}")

    for m in matches:
        _, city, country = resolve_venue(m.venue_raw)
        if not city or not country:
            errors.append(f"match {m.number}: unresolved venue {m.venue_raw!r}")
        if not (_TOURNAMENT_WINDOW[0] <= m.start <= _TOURNAMENT_WINDOW[1]):
            errors.append(f"match {m.number}: kickoff {m.start} outside tournament window")
        for team in (m.home, m.away):
            if not team:
                errors.append(f"match {m.number}: empty team name")
            elif re.search(r"\d", team):
                # No national team name contains a digit; one showing up means
                # the source started embedding scores or changed schema.
                errors.append(f"match {m.number}: suspicious team name {team!r}")

    if errors:
        for e in errors:
            print(f"[error] validation: {e}", file=sys.stderr)
        raise SystemExit(1)


# --------------------------------------------------------------------------- #
# iCalendar emission
# --------------------------------------------------------------------------- #

def _esc(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """Fold a content line to <=75 octets per RFC 5545."""
    out = []
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    # Fold on octet boundaries, continuation lines begin with a space.
    chunk = []
    size = 0
    first = True
    for ch in line:
        clen = len(ch.encode("utf-8"))
        limit = 75 if first else 74
        if size + clen > limit:
            out.append("".join(chunk))
            chunk = [ch]
            size = clen
            first = False
        else:
            chunk.append(ch)
            size += clen
    out.append("".join(chunk))
    return "\r\n ".join(out)


def _fmt_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _slot_label(m: Match, side: str, by_number: dict[int, Match]) -> str:
    """Label one side of a knockout match, using the bracket feeder when the
    team itself is still undecided."""
    team = m.home if side == "home" else m.away
    if team != "TBD":
        return team
    feeder_info = KNOCKOUT_FEEDERS.get(m.number)
    if not feeder_info:
        return "TBD"
    kind, home_feed, away_feed = feeder_info
    feeder = by_number.get(home_feed if side == "home" else away_feed)
    prefix = "Winner" if kind == "winner" else "Loser"
    if feeder and feeder.home != "TBD" and feeder.away != "TBD":
        pair = f"{feeder.home}/{feeder.away}"
        return pair if kind == "winner" else f"{pair} loser"
    return f"{prefix} M{home_feed if side == 'home' else away_feed}"


def _summary(m: Match, by_number: dict[int, Match]) -> str:
    if m.is_knockout:
        home = _slot_label(m, "home", by_number)
        away = _slot_label(m, "away", by_number)
        return f"⚽ {m.round_label}: {home} vs {away}"
    return f"⚽ {m.home} vs {m.away} ({m.round_label})"


def build_ics(matches: list[Match], reminder_minutes: int | None) -> str:
    by_number = {m.number: m for m in matches}
    lines: list[str] = []
    add = lines.append
    add("BEGIN:VCALENDAR")
    add("VERSION:2.0")
    add(f"PRODID:{PRODID}")
    add("CALSCALE:GREGORIAN")
    add("METHOD:PUBLISH")
    # Calendar name/description/color: X-WR-* for Apple/Google legacy, plus the
    # RFC 7986 properties for clients that understand them.
    add(_fold(f"X-WR-CALNAME:{CALNAME}"))
    add(_fold(f"NAME:{CALNAME}"))
    add("X-WR-TIMEZONE:UTC")
    add(_fold(f"X-WR-CALDESC:{CALDESC}"))
    add(f"X-APPLE-CALENDAR-COLOR:{CALCOLOR}")
    add(f"COLOR:{CALCOLOR_CSS}")
    # Hint Apple/Google to re-pull every 6 hours.
    add("REFRESH-INTERVAL;VALUE=DURATION:PT6H")
    add("X-PUBLISHED-TTL:PT6H")

    for m in matches:
        start = m.start
        end = m.start + (KNOCKOUT_DURATION if m.is_knockout else GROUP_DURATION)
        stadium, city, country = resolve_venue(m.venue_raw)
        loc_parts = [stadium] + [p for p in (city, country) if p]
        location = ", ".join(loc_parts)

        desc_lines = [
            f"{m.round_label}  ·  Match {m.number}",
            location,
            "",
            f"\U0001f4fa Watch live or replay on Peacock (Spanish-language / "
            f"Telemundo, Premium subscription): {PEACOCK_URL}",
            "",
            "Auto-updating feed — knockout teams fill in as the bracket "
            "resolves. Scores are intentionally not shown.",
        ]
        description = "\n".join(desc_lines)

        add("BEGIN:VEVENT")
        add(f"UID:wc2026-match-{m.number}@{UID_DOMAIN}")
        # Deterministic DTSTAMP (= start) so unchanged matches don't churn the file.
        add(f"DTSTAMP:{_fmt_dt(start)}")
        add(f"DTSTART:{_fmt_dt(start)}")
        add(f"DTEND:{_fmt_dt(end)}")
        add(_fold(f"SUMMARY:{_esc(_summary(m, by_number))}"))
        add(_fold(f"LOCATION:{_esc(location)}"))
        add(_fold(f"DESCRIPTION:{_esc(description)}"))
        add(_fold(f"CATEGORIES:{_esc('FIFA World Cup 2026')},{_esc(m.round_label)}"))
        add(_fold(f"URL:{PEACOCK_URL}"))
        add("TRANSP:TRANSPARENT")
        if reminder_minutes:
            add("BEGIN:VALARM")
            add("ACTION:DISPLAY")
            add(_fold(f"DESCRIPTION:Kickoff in {reminder_minutes} minutes"))
            add(f"TRIGGER:-PT{reminder_minutes}M")
            add("END:VALARM")
        add("END:VEVENT")

    add("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the World Cup 2026 .ics feed")
    ap.add_argument("--out", default="public/worldcup.ics",
                    help="Output .ics path")
    ap.add_argument("--no-alarms", action="store_true",
                    help="Omit the pre-kickoff VALARM reminders")
    args = ap.parse_args()

    matches = fetch_fixturedownload()
    validate(matches)

    reminder = None if args.no_alarms else REMINDER_MINUTES
    ics = build_ics(matches, reminder)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(ics, encoding="utf-8", newline="")

    knockout = sum(1 for m in matches if m.is_knockout)
    resolved_ko = sum(1 for m in matches if m.is_knockout
                      and m.home != "TBD" and m.away != "TBD")
    print(f"[ok] Wrote {len(matches)} matches to {out} "
          f"({knockout} knockout, {resolved_ko} with confirmed teams).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
