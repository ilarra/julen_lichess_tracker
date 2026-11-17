# Julen's chess study tracker

Tracks progress on the Lichess studies a teacher shares, and writes a local HTML report.
All data stays on this machine in a SQLite file under `data/`.

## What it measures

Julen copies each class study into his account and works in the copy. The tracker reads his copies,
so new classes appear without any setup.

| Signal | How |
|---|---|
| **Exercises** | Chapters starting from a set-up position that aren't games or solutions. Names containing `ETXE` are homework, the rest class exercises |
| **Moves** | Moves Julen added. If the teacher's original is linked in `extra_studies`, the teacher's own moves are left out |
| **Check** | Julen's main line compared with the teacher's solution chapter (`EBAZPENA`) for the same position, usually in the next class: *Solved*, *Right start*, *Seen in analysis* (right move only in a side line), *Missed*, or *No solution yet* |
| **Difficulty** | Length of the solution, or a `[Difficulty "1"–"5"]` tag if the teacher sets one |
| **Vs computer** | Games against the Lichess AI that started from a position in the exercise or Julen's line: won/played and highest level beaten |
| **Tracks** | Sections of the dashboard, assigned by words in the study name (`[[tracks]]` in the config) |

Studies without exercises (tournament games, free analysis) and duplicate copies are folded away.

## Hosted on GitHub (daily sync + web page)

`.github/workflows/sync.yml` runs the sync every day at 05:23 UTC on GitHub's servers,
keeps the database on the `data` branch, and publishes the report with GitHub Pages at
`https://ilarra.github.io/julen_lichess_tracker/`.

One-time setup in the repository settings:

1. **Pages** (Settings → Pages): set *Source* to **GitHub Actions**.
   Pages needs a public repository on a free plan, or GitHub Pro for a private one.
   The published page is public either way; it's marked `noindex` so search engines skip it.
2. **Token** (Settings → Secrets and variables → Actions → New repository secret):
   name `LICHESS_TOKEN`, value = Julen's `study:read` token.
3. **First run**: Actions tab → *Sync and publish* → *Run workflow*.

Usernames come from `config.example.toml`.

## Local setup (mini PC, optional)

```bash
cd julen_lichess_tracker
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.toml config.toml   # then edit usernames + token
```

Create the token while logged in as Julen, with only the `study:read` scope:
https://lichess.org/account/oauth/token/create?scopes[]=study:read&description=Study+tracker

## Use

```bash
.venv/bin/python tracker.py sync     # fetch from Lichess, update data/tracker.db, write data/report.html
.venv/bin/python tracker.py report   # rebuild the report from stored data only
.venv/bin/python tracker.py demo     # made-up data → data/demo-report.html
.venv/bin/python -m unittest test_tracker
```

Daily sync via cron (`crontab -e`):

```
17 7 * * * cd /path/to/julen_lichess_tracker && .venv/bin/python tracker.py sync >> data/sync.log 2>&1
```

## Things to check on the first real run

* **Private teacher studies.** The list endpoint only returns studies Julen's token can see.
  If one is missing, add its link to `extra_studies`.
* **Clone matching.** If a clone shows as *Not cloned*, pin it under `[clone_links]`.
* **Shared opening positions.** A game against the computer from a position that appears in
  several chapters is counted for each of them.
* "Last activity" for moves in a clone is the sync time when the change was first seen, so a daily sync gives day-level accuracy.

Each distinct version of every chapter is kept in `chapter_versions`, so the history can be charted later.
