"""Made-up studies and games in the formats the Lichess API returns, for `tracker.py demo`
and the tests. They mirror the real setup: a weekly class whose homework is solved in the
next class's EBAZPENA chapters, a monthly gathering, 1-to-1 lessons, and some noise
(old default-named studies, a tournament study) that must be ignored."""
from __future__ import annotations

import chess
import chess.pgn

from tracker import ROOT, Config

TEACHER, STUDENT = "DemoCoach", "Julen"
DAY = 86_400_000
T0 = 1_762_000_000_000

# positions (all legal; moves are illustrative, not deep tactics)
CLASS_EX = "8/8/8/4k3/8/8/8/R3K3 w - - 0 1"
P1 = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"        # back-rank mate
P2 = "r3k3/8/8/8/8/8/3N4/4K3 w - - 0 1"
P3 = "4k3/8/8/8/8/8/4Q3/4K3 w - - 0 1"
Q1 = "7k/8/8/8/8/8/6R1/6K1 w - - 0 1"
Q2 = "k7/8/8/8/8/8/1R6/1K6 w - - 0 1"
Q3 = "2k5/8/8/8/8/8/8/2R1K3 w - - 0 1"
T1 = "8/8/8/8/8/2k5/8/2K1R3 w - - 0 1"
T2 = "3k4/8/8/8/8/8/8/3QK3 w - - 0 1"
O1 = "6k1/8/8/8/8/8/8/R3K3 w - - 0 1"

SOLUTIONS = {P1: ["Ra8#"], P2: ["Nc4", "Ra4", "Nd6+"], P3: ["Qb5+", "Kd8"]}


def chapter(study_id, study_name, chapter_id, chapter_name, moves=(), fen=None, extra_tags=None,
            variations=None, comments=None) -> str:
    """One chapter as Lichess exports it. variations: {ply: [san...]} alternative to that ply."""
    game = chess.pgn.Game()
    game.headers.clear()
    game.headers.update({"Event": f"{study_name}: {chapter_name}", "Result": "*", "StudyName": study_name,
                         "ChapterName": chapter_name,
                         "ChapterURL": f"https://lichess.org/study/{study_id}/{chapter_id}"})
    if fen:
        game.headers.update({"FEN": fen, "SetUp": "1"})
        game.setup(fen)
    game.headers.update(extra_tags or {})
    node = game
    for i, san in enumerate(moves):
        parent = node
        node = node.add_variation(node.board().parse_san(san))
        if comments and i in comments:
            node.comment = comments[i]
        if variations and i in variations:
            v = parent
            for vsan in variations[i]:
                v = v.add_variation(v.board().parse_san(vsan))
    return str(game)


def study(sid, name, created_day, updated_day, chapters):
    """chapters: list of (chapter_name, kwargs)."""
    pgn = "\n\n".join(chapter(sid, name, f"{sid[:4]}{i:04d}", cname, **kw) for i, (cname, kw) in enumerate(chapters))
    return {"id": sid, "name": name, "createdAt": T0 + created_day * DAY, "updatedAt": T0 + updated_day * DAY}, pgn


GAME = {"moves": ["e4", "e5", "Nf3"], "extra_tags": {"White": "Larrañaga, Julen", "Black": "Someone"}}


def teacher_studies():
    return dict((s[0]["id"], s) for s in [
        study("klase001", "Online Klase1", 0, 0, [
            ("KLASEAN EGINDAKO ARIKETA", {"fen": CLASS_EX, "moves": ["Ra5+"]}),
            ("ETXERAKO PROBLEMA", {"fen": P1}),
            ("ETXERAKO PROBLEMA", {"fen": P2}),
            ("ETXERAKO PROBLEMA", {"fen": P3}),
        ]),
        study("klase002", "Online Klase2", 7, 7, [
            ("EBAZPENA", {"fen": P1, "moves": SOLUTIONS[P1]}),
            ("EBAZPENA", {"fen": P2, "moves": SOLUTIONS[P2]}),
            ("EBAZPENA", {"fen": P3, "moves": SOLUTIONS[P3]}),
            ("Alex - Ibai", GAME),
            ("ETXERAKO PROBLEMA", {"fen": Q1}),
            ("ETXERAKO PROBLEMA", {"fen": Q2}),
        ]),
        study("klase003", "Online Klase3", 14, 14, [   # shared, not copied yet
            ("ETXERAKO PROBLEMA", {"fen": Q3}),
        ]),
        study("oldstudy", "DemoCoach's Study", -400, -400, [("Chapter 1", {})]),   # noise
    ])


def student_studies():
    return dict((s[0]["id"], s) for s in [
        # copy of Klase1: class exercise continued, P1 solved, P2 right start, P3 solution only in a side line
        study("jklase01", "Online Klase1 - julen", 1, 3, [
            ("KLASEAN EGINDAKO ARIKETA", {"fen": CLASS_EX, "moves": ["Ra5+", "Kd4", "Kd2"]}),
            ("ETXERAKO PROBLEMA", {"fen": P1, "moves": ["Ra8#"], "comments": {0: "✅"}}),
            ("ETXERAKO PROBLEMA", {"fen": P2, "moves": ["Nc4", "Ra4", "Nb6"]}),
            ("ETXERAKO PROBLEMA", {"fen": P3, "moves": ["Qa6"], "variations": {0: ["Qb5+"]}}),
        ]),
        # a second, older copy of the same class with less work: must be merged, not double-counted
        study("jklase1b", "Online Klase1", 1, 1, [
            ("KLASEAN EGINDAKO ARIKETA", {"fen": CLASS_EX, "moves": ["Ra5+"]}),
            ("ETXERAKO PROBLEMA", {"fen": P1, "moves": ["Ra7"]}),
            ("ETXERAKO PROBLEMA", {"fen": P2}),
            ("ETXERAKO PROBLEMA", {"fen": P3}),
        ]),
        study("jklase02", "Online Klase2 - julen", 8, 9, [
            ("EBAZPENA", {"fen": P1, "moves": SOLUTIONS[P1]}),
            ("EBAZPENA", {"fen": P2, "moves": SOLUTIONS[P2]}),
            ("EBAZPENA", {"fen": P3, "moves": SOLUTIONS[P3]}),
            ("Alex - Ibai", GAME),
            ("ETXERAKO PROBLEMA", {"fen": Q1, "moves": ["Rh2+"]}),
            ("ETXERAKO PROBLEMA", {"fen": Q2}),
        ]),
        study("jtecni01", "TECNIFICACION G1 - Julen copy", 4, 5, [
            ("1 kapitulua", {"fen": T1, "moves": ["Re3+"]}),
            ("ARIKETA-ETXERAKO", {"fen": T2, "moves": ["Qd7+"]}),
        ]),
        study("jxake003", "Julen XakeKlase3", 10, 10, [
            ("ETXERAKO PROBLEMA", {"fen": O1, "moves": ["Ra8+"]}),
            ("FM DemoCoach - julen", GAME),
        ]),
        study("jtourn01", "Tournament games", 2, 2, [("game 1", GAME), ("game 2", GAME)]),   # noise
        study("jstudy01", "Julen's Study", 3, 3, [("Chapter 1", {})]),                     # noise
    ])


def fen_after(moves, fen):
    b = chess.Board(fen)
    for m in moves:
        b.push_san(m)
    return b.fen()


def ai_game(gid, day, fen, ai_level, winner, julen_color="white"):
    ai = {"aiLevel": ai_level}
    me = {"user": {"name": STUDENT, "id": STUDENT.lower()}, "rating": 1500}
    return {"id": gid, "rated": False, "variant": "fromPosition" if fen != chess.STARTING_FEN else "standard",
            "createdAt": T0 + day * DAY, "lastMoveAt": T0 + day * DAY + 600_000, "status": "mate",
            "players": {"white": me if julen_color == "white" else ai,
                        "black": ai if julen_color == "white" else me},
            "initialFen": fen, "winner": winner, "moves": "e4 e5"}


GAMES = [
    ai_game("gameAAA1", 4, T1, 3, "white"),
    ai_game("gameAAA2", 5, T1, 5, "black"),
    ai_game("gameBBB1", 2, fen_after(["Nc4"], P2), 4, "white"),
    ai_game("gameCCC1", 12, chess.STARTING_FEN, 2, "white"),   # normal game, not linked to a study
]


class FakeLichess:
    def __init__(self):
        self.teacher, self.student = teacher_studies(), student_studies()

    def list_studies(self, username):
        # like the real API: the teacher's class studies are unlisted, only old ones show up
        if username == TEACHER:
            return [dict(self.teacher["oldstudy"][0])]
        if username == STUDENT:
            return [dict(meta) for meta, _ in self.student.values()]
        return []

    def study_pgn(self, study_id):
        return {**self.teacher, **self.student}[study_id][1]

    def ai_games(self, username, since_ms):
        return [g for g in GAMES if g["createdAt"] >= since_ms]


DEMO_CONFIG = Config(
    student=STUDENT, teacher=TEACHER, db_path=ROOT / "data" / "demo.db",
    report_path=ROOT / "data" / "demo-report.html",
    extra_studies=["klase001", "klase002", "klase003"],
    tracks=[{"name": "Online · weekly class", "match": ["Online Klase"]},
            {"name": "Tecnificación · monthly", "match": ["Tecnificación"]},
            {"name": "1-to-1", "match": []}])
