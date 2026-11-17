# Julen's chess study tracker

Tracks progress on the Lichess studies a teacher shares, and writes a local HTML report.
All data stays on this machine in a SQLite file under `data/`.

## What it measures

| Signal | Where it comes from |
|---|---|
| **New assignment** | A new study in the teacher's list, or a link added to `extra_studies` |
| **Cloned / not cloned** | Julen's studies matched to the teacher's by name, then by chapter names |
| **Moves played, notes** | Moves and comments in Julen's clone that aren't in the teacher's original |
| **Vs computer** | Julen's games against the Lichess AI that started from any position in a chapter: games won and the highest level beaten |
| **Done** | Julen adds a comment containing ✅ / done / hecho / eginda (configurable) |
| **Difficulty** | A `[Difficulty "1"–"5"]` chapter tag if the teacher adds one, otherwise an estimate from the size of the chapter |

Chapter states: *Not cloned* → *Cloned, not started* → *In progress* → *Done*.

## Setup (mini PC)

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
