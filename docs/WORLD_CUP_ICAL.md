# 2026 FIFA World Cup — subscribable iCal feed

A calendar feed of every 2026 FIFA World Cup match that your Apple Calendar can
subscribe to. It refreshes every 6 hours so knockout matches fill in with the
**actual** qualified teams as the bracket resolves. Each event shows the host
stadium/city and links to Peacock to watch live or the full replay. **Scores are
never written into the feed**, so a finished match never becomes a spoiler.

## How it works

```
scripts/generate_worldcup_ics.py   # builds public/worldcup.ics
        │  (data: fixturedownload.com JSON — free, no API key)
        ▼
public/worldcup.ics                # the feed
        ▲
.github/workflows/update-worldcup-ical.yml   # cron every 6h -> regenerate,
        │                                    # commit, deploy public/ to Pages
        ▼
GitHub Pages  (https://mindteam-ai.github.io/worldcup-ical/worldcup.ics)
        ▼
Apple Calendar  ->  webcal://mindteam-ai.github.io/worldcup-ical/worldcup.ics
```

- **Schedule + venues + knockout teams** come from the free
  [fixturedownload.com](https://fixturedownload.com/feed/json/fifa-world-cup-2026)
  JSON feed (no API key). It updates `HomeTeam`/`AwayTeam` as teams qualify, so
  the every-6-hours job picks up the real knockout matchups automatically.
- The generator **validates hard** before writing: exactly matches 1–104, the
  right per-round distribution, every venue resolved to a city/country, all
  kickoffs inside the tournament window, and no digits in team names (a score
  sneaking into the schema). Any anomaly exits non-zero, the Action commits
  nothing, and the previously published feed keeps serving.
- **Event titles** use flag + FIFA trigram with the round in parentheses —
  `⚽ 🇦🇷 ARG vs 🇪🇬 EGY (Round of 16)` — with full country names in the
  description. The name→flag/trigram table is `TEAM_STYLE` in the script.
- **Undecided knockout slots** are labeled from the bracket: once a feeder
  match has teams, its slot renders as e.g. `🇫🇷 FRA/🇲🇦 MAR` (meaning that
  match's winner); before that, `Winner M97` / `Loser M101`.
- Every event carries a **30-minute pre-kickoff reminder** (`VALARM`). Apple
  Calendar honors these unless you check "Remove alerts" on the subscription;
  Google Calendar ignores alarms in subscribed feeds. Disable with
  `--no-alarms` (or set `REMINDER_MINUTES = None` in the script).

## Hosting

The feed is published by **GitHub Pages** straight from this (public) repo:
the workflow regenerates the feed, commits it when it changed, and deploys
`public/` via `actions/deploy-pages`. The `schedule:` cron fires because the
workflow lives on the default branch — no further setup needed.

`netlify.toml` is kept as an alternative: if this repo ever needs to go
private, free GitHub Pages stops working; connect the repo to a Netlify site
instead (build settings are read from the file) and point subscribers at
`https://<site>.netlify.app/worldcup.ics`.

### Subscribe in Apple Calendar

- **macOS:** Calendar → File → New Calendar Subscription →
  `webcal://mindteam-ai.github.io/worldcup-ical/worldcup.ics`. Set *Auto-refresh* to
  *Every hour* (or your preference).
- **iPhone/iPad:** Settings → Calendar → Accounts → Add Account → Other →
  *Add Subscribed Calendar* → paste the `https://.../worldcup.ics` URL.

## Customizing

- **Peacock link** — `PEACOCK_URL` near the top of the script. Per-match deep
  links aren't publicly constructable, so every event points at Peacock's World
  Cup hub (live + replays live there).
- **Match duration** — `GROUP_DURATION` (2 h) and `KNOCKOUT_DURATION`
  (2 h 45 min, covering extra time and penalties). Events are
  `TRANSP:TRANSPARENT`, so they never block your availability either way.
- **Reminders** — `REMINDER_MINUTES` (default 30; `None` or `--no-alarms`
  disables).
- **Calendar color** — `CALCOLOR` (Apple hex) / `CALCOLOR_CSS` (RFC 7986 name).
- **Venue names/cities** — the `VENUES` table maps stadium/city strings to a
  display name, city, and country.

> A best-effort Wikipedia scraper (`--source wikipedia`) used to live here but
> was removed: it treated venue-local kickoff times as UTC and synthesized
> match numbers from sort order, which would have broken stable UIDs. Git
> history has it if ever needed.

## Run it locally

```bash
python3 scripts/generate_worldcup_ics.py --out public/worldcup.ics
```
