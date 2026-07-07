# 2026 FIFA World Cup — subscribable calendar feed

A calendar of every 2026 FIFA World Cup match that Apple Calendar (or any
calendar app) can subscribe to. It refreshes every 6 hours so knockout matches
fill in with the actual qualified teams as the bracket resolves. **Scores are
never written into the feed** — a finished match never becomes a spoiler.

**Subscribe:** `webcal://mindteam-ai.github.io/worldcup-ical/worldcup.ics`
(or add `https://mindteam-ai.github.io/worldcup-ical/worldcup.ics` as a
calendar subscription). Landing page:
[mindteam-ai.github.io/worldcup-ical](https://mindteam-ai.github.io/worldcup-ical/)

## How it works

- `scripts/generate_worldcup_ics.py` (Python stdlib only) pulls the schedule
  from fixturedownload.com's free JSON feed and writes `public/worldcup.ics`.
- `.github/workflows/update-worldcup-ical.yml` regenerates the feed every
  6 hours, commits it when it changed, and deploys `public/` to GitHub Pages.
- Undecided knockout slots are labeled from the bracket ("France/Morocco"
  meaning that match's winner); events carry venue + city, a Peacock watch
  link, per-round categories, and a 30-minute kickoff reminder.
- The generator validates hard before writing (match count, rounds, venues,
  dates, no score leakage). On any anomaly it exits non-zero and the
  previously published feed keeps serving.

Full docs: [docs/WORLD_CUP_ICAL.md](docs/WORLD_CUP_ICAL.md)

## Run it locally

```bash
python3 scripts/generate_worldcup_ics.py --out public/worldcup.ics
```
