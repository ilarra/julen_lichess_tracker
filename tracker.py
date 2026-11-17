#!/usr/bin/env python3
"""Track Julen's progress on the Lichess studies from his chess classes.

How it works
------------
* Julen copies (clones) each class study into his account and works on it there.
  His copies are the source of truth: they are discovered automatically.
* Exercises are chapters that start from a set-up position and are neither games nor
  solution chapters. Homework is recognised by its chapter name (ETXERAKO…).
* If the teacher's original is known (a link in config.toml), a copy is matched to it by
  the chapters' starting positions and only Julen's own moves are counted.
* Answers are checked against the teacher's solution chapter (EBAZPENA) for the same
  position, wherever it appears, usually in the next class.
* Games Julen played against the Lichess computer from an exercise position are linked to it.
* Studies are grouped into tracks (weekly class, monthly gathering, 1-to-1) by name.
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
import unicodedata
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
    # chapter names (substring, case-insensitive) that hold the teacher's solutions
    solution_names: list[str] = field(default_factory=lambda: ["EBAZPENA", "SOLUCION", "SOLUCIÓN", "SOLUTION"])
    # chapter names that mark homework (other set-up positions count as class exercises)
    homework_names: list[str] = field(default_factory=lambda: ["ETXE", "HOMEWORK", "CASA"])
    # dashboard sections: first track whose words appear in the study name; a track without words catches the rest
    tracks: list[dict] = field(default_factory=lambda: [{"name": "Classes", "match": []}])
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
        cfg.solution_names = raw.get("solution_names", cfg.solution_names)
        cfg.homework_names = raw.get("homework_names", cfg.homework_names)
        cfg.tracks = raw.get("tracks", cfg.tracks)
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


STANDARD_START = " ".join(chess.STARTING_FEN.split()[:4])


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
    root: str = ""                       # position key of the chapter's starting position
    mainline: list[str] = field(default_factory=list)   # uci moves
    is_game: bool = False                # a played game (White/Black tags), not an exercise

    @property
    def set_up(self) -> bool:
        return self.root != STANDARD_START

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
        mainline = [m.uci() for m in game.mainline_moves()]
        is_game = all(tags.get(t, "?") not in ("?", "") for t in ("White", "Black"))
        chapters.append(Chapter(study_id, chapter_id, name, order, tags, edges, comments, positions,
                                len(mainline), raw, fen_key(root_board), mainline, is_game))
        order += 1
    return chapters


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


# --------------------------------------------------------------------------- matching

def normalise_name(name: str) -> str:
    name = re.sub(r"[\s\-_]*\(?\b(copy|clone|copia|kopia)\b\)?\s*$", "", name, flags=re.I)
    return re.sub(r"\s+", " ", name).strip().casefold()


def fold(text: str) -> str:
    """Case- and accent-insensitive form: 'Aloñamendi' -> 'alonamendi'."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()


def track_of(cfg: Config, study_name: str) -> str:
    fallback = None
    for t in cfg.tracks:
        words = t.get("match", [])
        if not words:
            fallback = fallback or t["name"]
        elif any(fold(w) in fold(study_name) for w in words):
            return t["name"]
    return fallback or "Other"


def chapter_kind(cfg: Config, ch: Chapter) -> str:
    """solution | game | homework | exercise | other"""
    upper = ch.name.upper()
    if any(n.upper() in upper for n in cfg.solution_names):
        return "solution"
    if ch.is_game:
        return "game"
    if not ch.set_up:
        return "other"   # starts from the initial position: opening work or free analysis
    if any(n.upper() in upper for n in cfg.homework_names):
        return "homework"
    return "exercise"


def signature(chapters: list[Chapter]) -> set[str]:
    return {c.root for c in chapters if c.set_up}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def match_clones(cfg: Config, db):
    """Link each of the student's studies to the teacher study it was copied from.
    Copies keep every chapter's starting position, so compare those, not names."""
    teacher = {r["id"]: r for r in db.execute("SELECT * FROM studies WHERE owner=?", (cfg.teacher,))}
    student = {r["id"]: r for r in db.execute("SELECT * FROM studies WHERE owner=?", (cfg.student,))}
    db.execute("UPDATE studies SET clone_of=NULL WHERE owner=?", (cfg.student,))
    manual = {clone: orig for orig, clone in cfg.clone_links.items()}
    tsig = {tid: signature(latest_chapters(db, tid)) for tid in teacher}
    for sid, row in student.items():
        best = manual.get(sid)
        if not best:
            ssig = signature(latest_chapters(db, sid))
            scored = sorted(((jaccard(ssig, sig), normalise_name(teacher[tid]["name"]) in normalise_name(row["name"]), tid)
                             for tid, sig in tsig.items() if sig), reverse=True)
            if scored and scored[0][0] >= 0.5:
                best = scored[0][2]
        if best:
            db.execute("UPDATE studies SET clone_of=? WHERE id=?", (best, sid))


# --------------------------------------------------------------------------- progress

VERDICTS = {
    "solved": "Solved",              # follows the teacher's solution as far as Julen went
    "partly": "Right start",         # first move right, then deviates
    "seen": "Seen in analysis",      # solution's first move is in his tree, but not his main line
    "missed": "Missed",
    "pending": "No solution yet",
    "not_tried": "Not tried",
    "not_cloned": "Not copied yet",
}


@dataclass
class ExerciseProgress:
    number: int
    name: str
    kind: str                     # homework | exercise
    moves: int                    # moves Julen added
    comments: int
    verdict: str                  # key of VERDICTS
    answer: str                   # Julen's first move (SAN)
    solution: str                 # teacher's first move (SAN), when known
    solution_plies: int
    difficulty: int | None        # 1–5
    difficulty_source: str        # teacher | solution | ""
    practice_games: int
    practice_wins: int
    best_ai_level_beaten: int | None
    done_marked: bool


def san(fen_tags: dict, ucis: list[str], n: int = 1) -> str:
    board = chess.Board(fen_tags.get("FEN", chess.STARTING_FEN))
    out = []
    for u in ucis[:n]:
        try:
            move = chess.Move.from_uci(u)
            out.append(board.san(move))
            board.push(move)
        except (ValueError, AssertionError):
            break
    return " ".join(out)


def grade(answer: list[str], tried: set[tuple[str, str]], root: str, solution: list[str]) -> str:
    if not solution:
        return "pending"
    k = 0
    while k < min(len(answer), len(solution)) and answer[k] == solution[k]:
        k += 1
    if k and (k == len(answer) or k == len(solution)):
        return "solved"
    if k:
        return "partly"
    if (root, solution[0]) in tried:
        return "seen"
    return "missed"


def build_progress(cfg: Config, db) -> list[dict]:
    games = db.execute("SELECT * FROM games").fetchall()
    studies = {r["id"]: r for r in db.execute("SELECT * FROM studies")}
    chapters = {sid: latest_chapters(db, sid) for sid in studies}

    # every solution chapter anywhere, by starting position (teacher's own copy preferred)
    solutions: dict[str, Chapter] = {}
    for sid, chs in sorted(chapters.items(), key=lambda kv: studies[kv[0]]["owner"] != cfg.teacher):
        for ch in chs:
            if chapter_kind(cfg, ch) == "solution" and ch.mainline:
                solutions.setdefault(ch.root, ch)

    # group the student's copies: same original, or (unmatched) nearly the same chapters
    groups: list[dict] = []
    for sid, row in sorted(studies.items(), key=lambda kv: kv[1]["created_at"] or 0):
        if row["owner"] != cfg.student:
            continue
        sig = signature(chapters[sid])
        for g in groups:
            if (row["clone_of"] and row["clone_of"] == g["original"]) or \
               (not row["clone_of"] and not g["original"] and jaccard(sig, g["sig"]) >= 0.8):
                g["copies"].append(sid)
                g["sig"] |= sig
                break
        else:
            groups.append({"original": row["clone_of"], "copies": [sid], "sig": set(sig)})
    copied = {g["original"] for g in groups if g["original"]}
    for tid in cfg.extra_studies:   # shared with Julen but not copied yet
        if tid in studies and tid not in copied:
            groups.append({"original": tid, "copies": [], "sig": signature(chapters[tid])})

    out = []
    for g in groups:
        orig_chs = chapters.get(g["original"], []) if g["original"] else []
        orig_by_root = {}
        for ch in orig_chs:
            orig_by_root.setdefault(ch.root, ch)
        copy_chs = [ch for sid in g["copies"] for ch in chapters[sid]]
        # chapter order: the original's, else the first copy's
        ordered, seen_roots = [], set()
        for ch in (orig_chs or copy_chs):
            if chapter_kind(cfg, ch) in ("homework", "exercise") and ch.root not in seen_roots:
                ordered.append(ch)
                seen_roots.add(ch.root)
        if not ordered:
            continue   # tournament games, free analysis… not class work

        exercises = []
        for n, base in enumerate(ordered, 1):
            orig = orig_by_root.get(base.root)
            candidates = [c for c in copy_chs if c.root == base.root]
            def added(c):
                return c.edges - orig.edges if orig else c.edges
            mine = max(candidates, key=lambda c: len(added(c)), default=None)
            moves = len(added(mine)) if mine else 0
            new_comments = (mine.comments - orig.comments if orig else mine.comments) if mine else set()
            done_marked = any(any(m.casefold() in t.casefold() for m in cfg.done_markers) for _, t in new_comments)
            sol = solutions.get(base.root)
            sol_moves = sol.mainline if sol else []
            if not g["copies"]:
                verdict = "not_cloned"
            elif not moves:
                verdict = "not_tried"
            else:
                verdict = grade(mine.mainline, added(mine), base.root, sol_moves)
            positions = set(base.positions).union(*(c.positions for c in candidates))
            practice = [gm for gm in games if fen_key_str(gm["initial_fen"]) in positions
                        and fen_key_str(gm["initial_fen"]) != STANDARD_START]
            wins = [gm for gm in practice if gm["winner"] == gm["color"]]
            tag = base.difficulty_tag
            difficulty = tag or (min(5, max(1, (len(sol_moves) + 1) // 2)) if sol_moves else None)
            exercises.append(ExerciseProgress(
                n, base.name, chapter_kind(cfg, base), moves, len(new_comments), verdict,
                san(base.tags, mine.mainline) if mine and moves else "", san(base.tags, sol_moves), len(sol_moves),
                difficulty, "teacher" if tag else ("solution" if sol_moves else ""),
                len(practice), len(wins), max((gm["ai_level"] for gm in wins), default=None), done_marked))

        first = studies[g["copies"][0]] if g["copies"] else studies[g["original"]]
        all_chs = orig_chs or copy_chs
        name = studies[g["original"]]["name"] if g["original"] else first["name"]
        out.append({
            "name": name, "track": track_of(cfg, name),
            "original": g["original"], "copies": g["copies"],
            "created_at": min((studies[s]["created_at"] or 0) for s in (g["copies"] or [g["original"]])),
            "updated_at": max((studies[s]["updated_at"] or 0) for s in (g["copies"] or [g["original"]])),
            "solutions": sum(1 for c in all_chs if chapter_kind(cfg, c) == "solution"),
            "games": sum(1 for c in all_chs if chapter_kind(cfg, c) == "game"),
            "exercises": exercises,
        })
    out.sort(key=lambda s: s["created_at"], reverse=True)
    return out


# --------------------------------------------------------------------------- report

VERDICT_CLASS = {"solved": "good", "partly": "mid", "seen": "mid", "missed": "bad",
                 "pending": "wait", "not_tried": "idle", "not_cloned": "bad"}


def track_summary(studies: list[dict]) -> str:
    allx = [x for s in studies for x in s["exercises"]]
    tried = [x for x in allx if x.verdict not in ("not_tried", "not_cloned")]
    graded = [x for x in tried if x.verdict != "pending"]
    solved = sum(1 for x in graded if x.verdict == "solved")
    good_start = sum(1 for x in graded if x.verdict in ("solved", "partly"))

    def pct(a, b):
        return f"{round(100 * a / b)}%" if b else "—"

    stats = [
        (f"{len(tried)}/{len(allx)}", "exercises tried"),
        (pct(solved, len(graded)), f"solved, of {len(graded)} with solutions"),
        (pct(good_start, len(graded)), "right first move"),
        (str(sum(1 for x in tried if x.verdict == "pending")), "no solution yet"),
    ]
    return "".join(f'<div class="stat"><b>{v}</b><span>{html.escape(l)}</span></div>' for v, l in stats)


def render_report(cfg: Config, db) -> str:
    studies = build_progress(cfg, db)
    last_sync = (db.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone() or [None])[0]
    e = html.escape

    def date(ms):
        return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%d %b %Y") if ms else ""

    cards: dict[str, list[str]] = {}
    for s in studies:
        xs = s["exercises"]
        n_tried = sum(1 for x in xs if x.verdict not in ("not_tried", "not_cloned"))
        n_solved = sum(1 for x in xs if x.verdict == "solved")
        links = []
        if s["original"]:
            links.append(f'<a href="{API}/study/{s["original"]}">teacher\'s study</a>')
        for i, c in enumerate(s["copies"]):
            links.append(f'<a href="{API}/study/{c}">{e(cfg.student)}\'s copy{" " + str(i + 1) if len(s["copies"]) > 1 else ""}</a>')
        extra = []
        if s["solutions"]:
            extra.append(f'{s["solutions"]} solution chapter{"s" * (s["solutions"] > 1)}')
        if s["games"]:
            extra.append(f'{s["games"]} game{"s" * (s["games"] > 1)}')
        rows = []
        for x in xs:
            check = f'<span class="pill {VERDICT_CLASS[x.verdict]}">{VERDICTS[x.verdict]}</span>'
            detail = []
            if x.answer:
                detail.append(f"played <b>{e(x.answer)}</b>")
            if x.solution and x.verdict in ("missed", "seen", "partly", "solved"):
                detail.append(f"solution <b>{e(x.solution)}</b>")
            practice = "—"
            if x.practice_games:
                practice = f"{x.practice_wins}/{x.practice_games} won"
                if x.best_ai_level_beaten:
                    practice += f" · lvl {x.best_ai_level_beaten}"
            dots = ("".join(f'<i class="{"on" if i < x.difficulty else ""}"></i>' for i in range(5))
                    if x.difficulty else '<small>—</small>')
            dtitle = {"teacher": "set by the teacher", "solution": f"solution is {x.solution_plies} moves long"}.get(
                x.difficulty_source, "no solution yet")
            kind = "Home" if x.kind == "homework" else "Class"
            rows.append(f"""<tr>
              <td class="num idx">{x.number}</td>
              <td><span class="kind {x.kind}">{kind}</span> {e(x.name)}{' ✅' if x.done_marked else ''}</td>
              <td class="num">{x.moves or '—'}</td>
              <td>{check}<div class="detail">{' · '.join(detail)}</div></td>
              <td>{practice}</td>
              <td><span class="dots" title="{e(dtitle)}">{dots}</span></td></tr>""")
        bar = round(100 * n_tried / len(xs)) if xs else 0
        cards.setdefault(s["track"], []).append(f"""<section class="card">
          <header><div><h2>{e(s['name'])}</h2>
            <p class="sub">{date(s['created_at'])} · {' · '.join(links)}{' · ' + ', '.join(extra) if extra else ''}</p></div>
            <div class="score"><b>{n_tried}</b>/{len(xs)} tried<br><small>{n_solved} solved</small></div></header>
          <div class="bar"><span style="width:{bar}%"></span></div>
          <div class="scroll"><table>
            <thead><tr><th class="num">#</th><th>Exercise</th><th class="num">Moves</th><th>Check</th>
              <th>Vs computer</th><th>Difficulty</th></tr></thead>
            <tbody>{''.join(rows)}</tbody></table></div></section>""")

    order = [t["name"] for t in cfg.tracks] + sorted(set(cards) - {t["name"] for t in cfg.tracks})
    nav = " · ".join(f'<a href="#t{i}">{e(name)}</a>' for i, name in enumerate(order) if name in cards)
    sections = (f'<nav class="sub">{nav}</nav>' if len(cards) > 1 else "") + "".join(
        f"""<h2 class="track" id="t{i}">{e(name)}</h2>
        <div class="stats">{track_summary([s for s in studies if s["track"] == name])}</div>{''.join(cards[name])}"""
        for i, name in enumerate(order) if name in cards)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{e(cfg.student)}'s Chess Studies</title>
<style>
:root{{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1b;--mut:#6b6a66;--line:#e6e4de;--acc:#2f6f4f;
  --good:#2f6f4f;--mid:#b7791f;--bad:#b23b3b;--wait:#3b6aa8;--idle:#8a8984}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--card:#1f1f1d;--ink:#ecebe7;--mut:#a3a29d;
  --line:#33322f;--acc:#6fbf94;--good:#6fbf94;--mid:#e2b04a;--bad:#e07070;--wait:#7fa8e0;--idle:#8f8e89}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1040px;margin:0 auto;padding:28px 16px 60px}}
h1{{margin:0 0 4px;font-size:26px}}h2{{margin:0;font-size:18px}}.sub{{margin:2px 0 0;color:var(--mut);font-size:13px}}
a{{color:var(--acc)}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:20px 0 26px}}
.stat{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}}
.stat b{{display:block;font-size:24px;font-variant-numeric:tabular-nums}}.stat span{{color:var(--mut);font-size:13px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:16px}}
.card header{{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}}
.score{{white-space:nowrap;color:var(--mut);text-align:right;line-height:1.2}}.score b{{color:var(--ink);font-size:20px}}
.bar{{height:6px;background:var(--line);border-radius:3px;margin:12px 0 8px;overflow:hidden}}
.bar span{{display:block;height:100%;background:var(--acc)}}
.scroll{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-size:14px;min-width:680px}}
th{{text-align:left;font-weight:600;color:var(--mut);font-size:12px;padding:8px 6px;border-bottom:1px solid var(--line)}}
td{{padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}}tr:last-child td{{border-bottom:0}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}.idx{{color:var(--mut);width:28px}}
.kind{{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);margin-right:4px}}
.kind.homework{{color:var(--acc)}}
.pill{{font-size:12px;padding:2px 8px;border-radius:99px;white-space:nowrap;border:1px solid currentColor}}
.pill.good{{color:var(--good)}}.pill.mid{{color:var(--mid)}}.pill.bad{{color:var(--bad)}}
.pill.wait{{color:var(--wait)}}.pill.idle{{color:var(--idle)}}
.detail{{font-size:12px;color:var(--mut);margin-top:3px}}.detail b{{color:var(--ink);font-weight:600}}
.dots i{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:3px;background:var(--line)}}
.dots i.on{{background:var(--ink)}}small{{color:var(--mut)}}
h2.track{{font-size:20px;margin:34px 0 0;padding-top:4px}}nav.sub{{margin-top:8px}}
footer{{color:var(--mut);font-size:12px;margin-top:24px;line-height:1.6}}
</style></head><body><main>
<h1>{e(cfg.student)}'s chess studies</h1>
<p class="sub">Classes with {e(cfg.teacher)} · last synced {e((last_sync or 'never')[:16].replace('T', ' '))} UTC</p>
{sections or '<p>No class studies yet — run <code>sync</code>.</p>'}
<footer>Exercises are the chapters that start from a set-up position (games and solution chapters are not counted).
<b>Moves</b> are the moves {e(cfg.student)} added in his copy. <b>Check</b> compares his main line with the teacher's
solution chapter for the same position, usually published in the next class: <i>Solved</i> = same moves as far as he went,
<i>Right start</i> = first move right, <i>Seen in analysis</i> = he looked at the right move but chose another.
<b>Difficulty</b> is the length of the solution (or a <code>[Difficulty "1-5"]</code> tag set by the teacher).</footer>
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
