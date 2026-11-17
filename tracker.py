#!/usr/bin/env python3
"""Track Julen's progress on the Lichess studies his teacher shares.

How it works
------------
* The teacher's studies are the assignments. They are discovered by listing the
  teacher's studies (with Julen's token, so shared private ones can show up) plus
  any study links listed in config.toml.
* Julen clones each study and plays moves in the clone. A clone is matched to its
  original by study name, falling back to overlap of chapter names.
* Per chapter, Julen's work = moves/comments in his clone that are not in the
  teacher's original.
* Games Julen played against the Lichess computer that started from a position
  in a chapter are linked to that chapter ("practice vs computer").
* Everything is stored in a local SQLite file; `report` writes a static HTML page.

Commands:  sync | report | demo   (see README.md)
"""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import os
import re
import sqlite3
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import chess
import chess.pgn

ROOT = Path(__file__).resolve().parent
API = "https://lichess.org"
STUDY_URL_RE = re.compile(r"(?:lichess\.org/study/)?([A-Za-z0-9]{8})(?:/([A-Za-z0-9]{8}))?")
COMMAND_RE = re.compile(r"\[%[^\]]*\]")  # [%clk ...], [%eval ...], [%csl ...], [%cal ...]


# --------------------------------------------------------------------------- config

@dataclass
class Config:
    student: str
    teacher: str
    token: str = ""
    extra_studies: list[str] = field(default_factory=list)
    clone_links: dict[str, str] = field(default_factory=dict)  # teacher study id -> clone id
    done_markers: list[str] = field(default_factory=lambda: ["✅", "done", "hecho", "eginda"])
    db_path: Path = ROOT / "data" / "tracker.db"
    report_path: Path = ROOT / "data" / "report.html"

    @classmethod
    def load(cls, path: Path) -> "Config":
        raw = tomllib.loads(path.read_text())
        cfg = cls(student=raw["student"], teacher=raw["teacher"])
        cfg.token = os.environ.get("LICHESS_TOKEN") or raw.get("token", "")
        if cfg.token.startswith("lip_xxx"):
            cfg.token = ""
        cfg.extra_studies = [study_id_from(s) for s in raw.get("extra_studies", [])]
        cfg.clone_links = {study_id_from(k): study_id_from(v)
                           for k, v in raw.get("clone_links", {}).items()}
        cfg.done_markers = raw.get("done_markers", cfg.done_markers)
        if "db_path" in raw:
            cfg.db_path = (path.parent / raw["db_path"]).resolve()
        if "report_path" in raw:
            cfg.report_path = (path.parent / raw["report_path"]).resolve()
        return cfg


def study_id_from(text: str) -> str:
    m = STUDY_URL_RE.search(text.strip())
    if not m:
        raise ValueError(f"Not a Lichess study id or link: {text!r}")
    return m.group(1)


# --------------------------------------------------------------------------- lichess client

class Lichess:
    """Minimal Lichess API client (stdlib only). Retries once on HTTP 429."""

    def __init__(self, token: str = ""):
        self.token = token

    def _get(self, path: str, params: dict | None = None, accept: str = "*/*") -> str:
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        headers = {"Accept": accept, "User-Agent": "study-progress-tracker"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        for attempt in range(2):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                    return r.read().decode("utf-8")
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt == 0:
                    print("  rate limited by Lichess, waiting 60s…", file=sys.stderr)
                    time.sleep(60)
                    continue
                if e.code == 404:
                    return ""
                raise
        return ""

    def list_studies(self, username: str) -> list[dict]:
        text = self._get(f"/api/study/by/{username}", accept="application/x-ndjson")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def study_pgn(self, study_id: str) -> str:
        time.sleep(1)  # be polite: study export is rate limited
        return self._get(f"/api/study/{study_id}.pgn", {"clocks": "false", "orientation": "true"})

    def ai_games(self, username: str, since_ms: int) -> list[dict]:
        text = self._get(f"/api/games/user/{username}",
                         {"since": since_ms, "moves": "true", "opening": "false"},
                         accept="application/x-ndjson")
        games = [json.loads(line) for line in text.splitlines() if line.strip()]
        return [g for g in games if any("aiLevel" in p for p in g.get("players", {}).values())]


# --------------------------------------------------------------------------- pgn analysis

def fen_key(board: chess.Board) -> str:
    """Position identity ignoring move counters."""
    return " ".join(board.fen().split()[:4])


def fen_key_str(fen: str) -> str:
    return " ".join(fen.split()[:4])


def clean_comment(text: str) -> str:
    return COMMAND_RE.sub("", text or "").strip()


@dataclass
class Chapter:
    study_id: str
    chapter_id: str
    name: str
    order: int
    tags: dict[str, str]
    edges: set[tuple[str, str]]          # (position key, uci move)
    comments: set[tuple[str, str]]       # (position key after move, text)
    positions: set[str]                  # every position in the tree
    mainline_plies: int
    pgn: str

    @property
    def difficulty_tag(self) -> int | None:
        raw = self.tags.get("Difficulty", "").strip()
        return int(raw) if raw.isdigit() else None

    def content_hash(self) -> str:
        return hashlib.sha1(self.pgn.encode()).hexdigest()


def parse_study_pgn(study_id: str, pgn_text: str) -> list[Chapter]:
    chapters: list[Chapter] = []
    stream = io.StringIO(pgn_text)
    order = 0
    while True:
        start = stream.tell()
        game = chess.pgn.read_game(stream)
        if game is None:
            break
        raw = pgn_text[start:stream.tell()].strip()
        tags = dict(game.headers)
        url = tags.get("ChapterURL", "")
        m = STUDY_URL_RE.search(url)
        chapter_id = m.group(2) if m and m.group(2) else f"ch{order:03d}"
        name = tags.get("ChapterName") or tags.get("Event", f"Chapter {order + 1}")

        edges, comments, positions = set(), set(), set()
        root_board = game.board()
        positions.add(fen_key(root_board))
        if clean_comment(game.comment):
            comments.add((fen_key(root_board), clean_comment(game.comment)))
        stack = [game]
        while stack:
            node = stack.pop()
            board = node.board()
            parent_key = fen_key(board)
            for child in node.variations:
                edges.add((parent_key, child.move.uci()))
                child_key = fen_key(child.board())
                positions.add(child_key)
                if clean_comment(child.comment):
                    comments.add((child_key, clean_comment(child.comment)))
                stack.append(child)
        mainline = sum(1 for _ in game.mainline_moves())
        chapters.append(Chapter(study_id, chapter_id, name, order, tags, edges,
                                comments, positions, mainline, raw))
        order += 1
    return chapters


def estimate_difficulty(ch: Chapter) -> int:
    """Rough 1–5 guess from the size of the teacher's material. Shown as an estimate."""
    branching = max(0, len(ch.edges) - ch.mainline_plies)
    score = ch.mainline_plies / 12 + branching / 6
    return max(1, min(5, 1 + int(score)))


# --------------------------------------------------------------------------- storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS studies (
  id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
  created_at INTEGER, updated_at INTEGER, fetched_updated_at INTEGER,
  clone_of TEXT, first_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chapter_versions (
  study_id TEXT NOT NULL, chapter_id TEXT NOT NULL, hash TEXT NOT NULL,
  seen_at TEXT NOT NULL, name TEXT NOT NULL, ord INTEGER NOT NULL, pgn TEXT NOT NULL,
  PRIMARY KEY (study_id, chapter_id, hash)
);
CREATE TABLE IF NOT EXISTS current_chapters (
  study_id TEXT NOT NULL, chapter_id TEXT NOT NULL, hash TEXT NOT NULL, ord INTEGER NOT NULL,
  PRIMARY KEY (study_id, chapter_id)
);
CREATE TABLE IF NOT EXISTS games (
  id TEXT PRIMARY KEY, created_at INTEGER, initial_fen TEXT, ai_level INTEGER,
  color TEXT, winner TEXT, status TEXT, plies INTEGER, raw TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def store_study(db, meta: dict, owner: str):
    db.execute("""INSERT INTO studies (id, owner, name, created_at, updated_at, first_seen)
                  VALUES (?,?,?,?,?,?)
                  ON CONFLICT(id) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at""",
               (meta["id"], owner, meta["name"], meta.get("createdAt"), meta.get("updatedAt"), now_iso()))


def store_chapters(db, study_id: str, chapters: list[Chapter]):
    """Keep every distinct version of a chapter (history) and remember the current set."""
    db.execute("DELETE FROM current_chapters WHERE study_id=?", (study_id,))
    for ch in chapters:
        db.execute("INSERT OR REPLACE INTO current_chapters VALUES (?,?,?,?)",
                   (study_id, ch.chapter_id, ch.content_hash(), ch.order))
        db.execute("""INSERT OR IGNORE INTO chapter_versions
                      (study_id, chapter_id, hash, seen_at, name, ord, pgn) VALUES (?,?,?,?,?,?,?)""",
                   (ch.study_id, ch.chapter_id, ch.content_hash(), now_iso(), ch.name, ch.order, ch.pgn))


def latest_chapters(db, study_id: str) -> list[Chapter]:
    rows = db.execute("""SELECT v.pgn FROM current_chapters c JOIN chapter_versions v
                           ON v.study_id=c.study_id AND v.chapter_id=c.chapter_id AND v.hash=c.hash
                         WHERE c.study_id=? ORDER BY c.ord""", (study_id,)).fetchall()
    return parse_study_pgn(study_id, "\n\n".join(r["pgn"] for r in rows))


# --------------------------------------------------------------------------- sync

def sync(cfg: Config, client: Lichess, db: sqlite3.Connection, log=print):
    # 1. study metadata
    teacher_meta = client.list_studies(cfg.teacher)
    listed = {s["id"] for s in teacher_meta}
    for sid in cfg.extra_studies:
        if sid not in listed:
            pgn = client.study_pgn(sid)
            chs = parse_study_pgn(sid, pgn)
            name = chs[0].tags.get("StudyName", sid) if chs else sid
            teacher_meta.append({"id": sid, "name": name, "updatedAt": int(time.time() * 1000), "_pgn": pgn})
    student_meta = client.list_studies(cfg.student)
    log(f"Found {len(teacher_meta)} teacher studies, {len(student_meta)} of {cfg.student}'s studies")

    # 2. fetch PGN only for studies that changed since last fetch
    for owner, metas in ((cfg.teacher, teacher_meta), (cfg.student, student_meta)):
        for meta in metas:
            store_study(db, meta, owner)
            row = db.execute("SELECT fetched_updated_at FROM studies WHERE id=?", (meta["id"],)).fetchone()
            if row["fetched_updated_at"] == meta.get("updatedAt") and "_pgn" not in meta:
                continue
            pgn = meta.get("_pgn") or client.study_pgn(meta["id"])
            store_chapters(db, meta["id"], parse_study_pgn(meta["id"], pgn))
            db.execute("UPDATE studies SET fetched_updated_at=? WHERE id=?", (meta.get("updatedAt"), meta["id"]))
            log(f"  updated: {meta['name']}")

    # 3. match clones
    match_clones(cfg, db)

    # 4. games vs computer since last sync
    since = int(db.execute("SELECT COALESCE((SELECT value FROM meta WHERE key='games_since'),0)").fetchone()[0])
    games = client.ai_games(cfg.student, since)
    for g in games:
        players = g["players"]
        color = "white" if "user" in players.get("white", {}) else "black"
        ai = players["black" if color == "white" else "white"]
        db.execute("""INSERT OR REPLACE INTO games VALUES (?,?,?,?,?,?,?,?,?)""",
                   (g["id"], g.get("createdAt"), g.get("initialFen", chess.STARTING_FEN), ai.get("aiLevel"),
                    color, g.get("winner"), g.get("status"), len(g.get("moves", "").split()), json.dumps(g)))
    if games:
        newest = max(g.get("createdAt", 0) for g in games)
        db.execute("INSERT OR REPLACE INTO meta VALUES ('games_since', ?)", (str(newest + 1),))
    db.execute("INSERT OR REPLACE INTO meta VALUES ('last_sync', ?)", (now_iso(),))
    db.commit()
    log(f"Stored {len(games)} new games vs computer")


def normalise_name(name: str) -> str:
    name = re.sub(r"\s*\((copy|clone|copia)\)\s*$", "", name, flags=re.I)
    return re.sub(r"\s+", " ", name).strip().casefold()


def match_clones(cfg: Config, db):
    teacher = db.execute("SELECT id, name FROM studies WHERE owner=?", (cfg.teacher,)).fetchall()
    student = db.execute("SELECT id, name FROM studies WHERE owner=?", (cfg.student,)).fetchall()
    db.execute("UPDATE studies SET clone_of=NULL WHERE owner=?", (cfg.student,))
    taken: set[str] = set()

    def link(clone_id, original_id):
        db.execute("UPDATE studies SET clone_of=? WHERE id=?", (original_id, clone_id))
        taken.add(clone_id)

    for orig, clone in cfg.clone_links.items():
        link(clone, orig)
    linked_orig = set(cfg.clone_links)
    # exact (normalised) name match
    by_name = {}
    for s in student:
        by_name.setdefault(normalise_name(s["name"]), []).append(s["id"])
    for t in teacher:
        if t["id"] in linked_orig:
            continue
        for sid in by_name.get(normalise_name(t["name"]), []):
            if sid not in taken:
                link(sid, t["id"])
                linked_orig.add(t["id"])
                break
    # fallback: chapter-name overlap
    chap_names = lambda sid: {normalise_name(r["name"]) for r in
                              db.execute("""SELECT v.name FROM current_chapters c JOIN chapter_versions v
                                ON v.study_id=c.study_id AND v.chapter_id=c.chapter_id AND v.hash=c.hash
                                WHERE c.study_id=?""", (sid,))}
    for t in teacher:
        if t["id"] in linked_orig:
            continue
        tn = chap_names(t["id"])
        best, best_score = None, 0.0
        for s in student:
            if s["id"] in taken:
                continue
            sn = chap_names(s["id"])
            score = len(tn & sn) / len(tn | sn) if tn | sn else 0
            if score > best_score:
                best, best_score = s["id"], score
        if best and best_score >= 0.6:
            link(best, t["id"])


# --------------------------------------------------------------------------- progress

@dataclass
class ChapterProgress:
    name: str
    status: str            # not_cloned | untouched | in_progress | done
    moves_added: int
    comments_added: int
    practice_games: int
    practice_wins: int
    best_ai_level_beaten: int | None
    difficulty: int
    difficulty_source: str  # "teacher" | "estimate"
    last_activity: str | None


def chapter_progress(cfg: Config, db, original: Chapter, clone: Chapter | None,
                     clone_seen: dict[str, str], games: list[sqlite3.Row]) -> ChapterProgress:
    moves_added = comments_added = 0
    done = False
    if clone:
        moves_added = len(clone.edges - original.edges)
        new_comments = clone.comments - original.comments
        comments_added = len(new_comments)
        markers = [m.casefold() for m in cfg.done_markers]
        done = any(any(m in text.casefold() for m in markers) for _, text in new_comments)

    practice = [g for g in games if fen_key_str(g["initial_fen"]) in original.positions
                and fen_key_str(g["initial_fen"]) != fen_key_str(chess.STARTING_FEN)]
    wins = [g for g in practice if g["winner"] == g["color"]]
    best = max((g["ai_level"] for g in wins), default=None)

    if clone is None:
        status = "not_cloned" if not practice else "in_progress"
    elif done:
        status = "done"
    elif moves_added or comments_added or practice:
        status = "in_progress"
    else:
        status = "untouched"

    activity = [clone_seen.get(clone.chapter_id)] if clone and (moves_added or comments_added) else []
    activity += [datetime.fromtimestamp(g["created_at"] / 1000, timezone.utc).isoformat(timespec="seconds")
                 for g in practice if g["created_at"]]
    tag = original.difficulty_tag
    return ChapterProgress(original.name, status, moves_added, comments_added, len(practice), len(wins), best,
                           tag or estimate_difficulty(original), "teacher" if tag else "estimate",
                           max((a for a in activity if a), default=None))


def build_progress(cfg: Config, db) -> list[dict]:
    games = db.execute("SELECT * FROM games").fetchall()
    out = []
    for t in db.execute("SELECT * FROM studies WHERE owner=? ORDER BY created_at DESC", (cfg.teacher,)):
        clone_row = db.execute("SELECT * FROM studies WHERE clone_of=?", (t["id"],)).fetchone()
        originals = latest_chapters(db, t["id"])
        clone_chs = {}
        clone_seen = {}
        if clone_row:
            for ch in latest_chapters(db, clone_row["id"]):
                clone_chs[normalise_name(ch.name)] = ch
            for r in db.execute("""SELECT v.chapter_id, v.seen_at AS s FROM current_chapters c
                                   JOIN chapter_versions v ON v.study_id=c.study_id
                                     AND v.chapter_id=c.chapter_id AND v.hash=c.hash
                                   WHERE c.study_id=?""", (clone_row["id"],)):
                clone_seen[r["chapter_id"]] = r["s"]
        clone_by_order = sorted(clone_chs.values(), key=lambda c: c.order)

        def find_clone(o: Chapter) -> Chapter | None:
            if not clone_row:
                return None
            # same chapter name, else same position in the study if the chapter count didn't change
            if normalise_name(o.name) in clone_chs:
                return clone_chs[normalise_name(o.name)]
            if len(clone_by_order) == len(originals):
                return clone_by_order[o.order]
            return None

        chapters = [chapter_progress(cfg, db, o, find_clone(o), clone_seen, games) for o in originals]
        out.append({"id": t["id"], "name": t["name"], "created_at": t["created_at"],
                    "clone_id": clone_row["id"] if clone_row else None, "chapters": chapters})
    return out


# --------------------------------------------------------------------------- report

STATUS_LABEL = {"done": "Done", "in_progress": "In progress", "untouched": "Cloned, not started",
                "not_cloned": "Not cloned"}


def render_report(cfg: Config, db) -> str:
    studies = build_progress(cfg, db)
    last_sync = (db.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone() or [None])[0]
    e = html.escape
    all_ch = [c for s in studies for c in s["chapters"]]
    totals = {k: sum(1 for c in all_ch if c.status == k) for k in STATUS_LABEL}
    n = len(all_ch) or 1

    def date(ms):
        return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%d %b %Y") if ms else ""

    cards = []
    for s in studies:
        chs = s["chapters"]
        done = sum(1 for c in chs if c.status == "done")
        started = sum(1 for c in chs if c.status in ("done", "in_progress"))
        pct = round(100 * started / len(chs)) if chs else 0
        link = f'<a href="{API}/study/{s["id"]}">teacher\'s study</a>'
        if s["clone_id"]:
            link += f' · <a href="{API}/study/{s["clone_id"]}">{e(cfg.student)}\'s copy</a>'
        rows = []
        for c in chs:
            practice = "—"
            if c.practice_games:
                practice = f"{c.practice_wins}/{c.practice_games} won"
                if c.best_ai_level_beaten:
                    practice += f" · beat level {c.best_ai_level_beaten}"
            dots = "".join(f'<i class="{"on" if i < c.difficulty else ""}"></i>' for i in range(5))
            rows.append(f"""<tr class="{c.status}">
              <td>{e(c.name)}</td>
              <td><span class="pill {c.status}">{STATUS_LABEL[c.status]}</span></td>
              <td class="num">{c.moves_added or "—"}</td>
              <td class="num">{c.comments_added or "—"}</td>
              <td>{practice}</td>
              <td><span class="dots" title="{c.difficulty_source}">{dots}</span>
                  {'<small>est.</small>' if c.difficulty_source == 'estimate' else ''}</td>
              <td>{(c.last_activity or '')[:10]}</td></tr>""")
        cards.append(f"""<section class="card">
          <header><div><h2>{e(s['name'])}</h2><p class="sub">Shared {date(s['created_at'])} · {link}</p></div>
            <div class="score"><b>{done}</b>/{len(chs)} done</div></header>
          <div class="bar"><span style="width:{pct}%"></span></div>
          <div class="scroll"><table>
            <thead><tr><th>Chapter</th><th>Status</th><th class="num">Moves played</th>
              <th class="num">Notes</th><th>Vs computer</th><th>Difficulty</th><th>Last activity</th></tr></thead>
            <tbody>{''.join(rows)}</tbody></table></div></section>""")

    summary = "".join(f'<div class="stat {k}"><b>{v}</b><span>{STATUS_LABEL[k]}</span></div>'
                      for k, v in totals.items())
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{e(cfg.student)}'s Chess Studies</title>
<style>
:root{{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1b;--mut:#6b6a66;--line:#e6e4de;--acc:#2f6f4f;
  --done:#2f6f4f;--prog:#b7791f;--idle:#7a7a75;--none:#b23b3b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--card:#1f1f1d;--ink:#ecebe7;--mut:#a3a29d;
  --line:#33322f;--acc:#6fbf94;--done:#6fbf94;--prog:#e2b04a;--idle:#9a9994;--none:#e07070}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1040px;margin:0 auto;padding:28px 16px 60px}}
h1{{margin:0 0 4px;font-size:26px}}h2{{margin:0;font-size:18px}}.sub{{margin:2px 0 0;color:var(--mut);font-size:13px}}
a{{color:var(--acc)}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:20px 0 26px}}
.stat{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}}
.stat b{{display:block;font-size:24px}}.stat span{{color:var(--mut);font-size:13px}}
.stat.done b{{color:var(--done)}}.stat.in_progress b{{color:var(--prog)}}.stat.not_cloned b{{color:var(--none)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:16px}}
.card header{{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}}
.score{{white-space:nowrap;color:var(--mut)}}.score b{{color:var(--ink);font-size:20px}}
.bar{{height:6px;background:var(--line);border-radius:3px;margin:12px 0 8px;overflow:hidden}}
.bar span{{display:block;height:100%;background:var(--acc)}}
.scroll{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-size:14px;min-width:640px}}
th{{text-align:left;font-weight:600;color:var(--mut);font-size:12px;padding:8px 6px;border-bottom:1px solid var(--line)}}
td{{padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:middle}}tr:last-child td{{border-bottom:0}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}
.pill{{font-size:12px;padding:2px 8px;border-radius:99px;white-space:nowrap;border:1px solid currentColor}}
.pill.done{{color:var(--done)}}.pill.in_progress{{color:var(--prog)}}.pill.untouched{{color:var(--idle)}}
.pill.not_cloned{{color:var(--none)}}
.dots i{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:3px;background:var(--line)}}
.dots i.on{{background:var(--ink)}}small{{color:var(--mut)}}
footer{{color:var(--mut);font-size:12px;margin-top:24px}}
</style></head><body><main>
<h1>{e(cfg.student)}'s chess studies</h1>
<p class="sub">Studies from {e(cfg.teacher)} · last synced {e((last_sync or 'never')[:16].replace('T', ' '))} UTC</p>
<div class="stats">{summary}</div>
{''.join(cards) or '<p>No studies yet — run <code>sync</code>.</p>'}
<footer>“Moves played” and “Notes” count what {e(cfg.student)} added in their copy beyond the teacher's original.
A chapter is Done when they write one of: {e(', '.join(cfg.done_markers))} in a comment.
Difficulty comes from a <code>[Difficulty "1-5"]</code> chapter tag when the teacher sets one, otherwise it is an estimate.</footer>
</main></body></html>"""


# --------------------------------------------------------------------------- cli

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["sync", "report", "demo"])
    ap.add_argument("--config", type=Path, default=ROOT / "config.toml")
    args = ap.parse_args(argv)

    if args.command == "demo":
        from demo_data import FakeLichess, DEMO_CONFIG
        cfg = DEMO_CONFIG
        if cfg.db_path.exists():
            cfg.db_path.unlink()
        db = connect(cfg.db_path)
        sync(cfg, FakeLichess(), db)
        cfg.report_path.write_text(render_report(cfg, db))
        print(f"Demo report: {cfg.report_path}")
        return

    cfg = Config.load(args.config)
    db = connect(cfg.db_path)
    if args.command == "sync":
        if not cfg.token:
            print("Warning: no Lichess token set; only public studies will be visible.", file=sys.stderr)
        sync(cfg, Lichess(cfg.token), db)
    cfg.report_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.report_path.write_text(render_report(cfg, db))
    print(f"Report: {cfg.report_path}")


if __name__ == "__main__":
    main()
