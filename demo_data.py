"""Made-up studies and games in the formats the Lichess API returns, for `tracker.py demo`
and the tests. Nothing here is real data."""
from __future__ import annotations

import chess
import chess.pgn

from tracker import ROOT, Config

TEACHER, STUDENT = "DemoCoach", "Julen"
DAY = 86_400_000
T0 = 1_762_000_000_000  # Nov 2025


def chapter_pgn(study_id, study_name, chapter_id, chapter_name, moves, fen=None, extra_tags=None,
                variations=None, comments=None) -> str:
    """Build a chapter PGN the way Lichess exports it.
    variations: {ply_index: [san, ...]} alternative line starting at that ply.
    comments: {ply_index: text} comment after that ply."""
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


ITALIAN = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "c3", "Nf6", "d4", "exd4", "cxd4", "Bb4+"]
TWO_KNIGHTS = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6", "Ng5", "d5", "exd5", "Na5"]
LUCENA_FEN = "1K1k4/1P6/8/8/8/8/r7/2R5 w - - 0 1"
LUCENA = ["Rd1+", "Ke7", "Rd4", "Rb2", "Kc7", "Rc2+", "Kb6", "Rb2+", "Kc6", "Rc2+", "Kb5", "Rb2+", "Rb4"]
VANCURA_FEN = "8/8/8/1P6/8/6k1/r7/1R2K3 b - - 0 1"
FORK_FEN = "r3k3/8/8/8/8/8/3N4/4K3 w - - 0 1"
FORK2_FEN = "6k1/5ppp/8/3q4/8/2N5/5PPP/6K1 w - - 0 1"

S_ITALIAN, S_ENDGAME, S_FORKS = "itAL1an0", "endGame1", "forKs001"
C_ITALIAN, C_FORKS = "juLITal1", "juLFork1"


def teacher_studies():
    italian = "\n\n".join([
        chapter_pgn(S_ITALIAN, "Italian Game ideas", "ch000001", "Giuoco Piano main line", ITALIAN,
                    variations={7: ["d6"]}, comments={6: "Prepare d4."}),
        chapter_pgn(S_ITALIAN, "Italian Game ideas", "ch000002", "Two Knights: Ng5", TWO_KNIGHTS),
        chapter_pgn(S_ITALIAN, "Italian Game ideas", "ch000003", "Ideas for Black", ITALIAN[:6]),
    ])
    endgame = "\n\n".join([
        chapter_pgn(S_ENDGAME, "Rook endgames", "ch000011", "Lucena position", LUCENA, fen=LUCENA_FEN,
                    extra_tags={"Difficulty": "4"}),
        chapter_pgn(S_ENDGAME, "Rook endgames", "ch000012", "Vancura defence", ["Rb2", "Rxb2"],
                    fen=VANCURA_FEN, extra_tags={"Difficulty": "5"}),
    ])
    forks = "\n\n".join([
        chapter_pgn(S_FORKS, "Tactics: forks", "ch000021", "Knight fork 1", ["Nc4"], fen=FORK_FEN),
        chapter_pgn(S_FORKS, "Tactics: forks", "ch000022", "Knight fork 2", ["Ne4"], fen=FORK2_FEN),
    ])
    return {
        S_ITALIAN: ({"id": S_ITALIAN, "name": "Italian Game ideas", "createdAt": T0, "updatedAt": T0}, italian),
        S_ENDGAME: ({"id": S_ENDGAME, "name": "Rook endgames", "createdAt": T0 + 7 * DAY,
                     "updatedAt": T0 + 7 * DAY}, endgame),
        S_FORKS: ({"id": S_FORKS, "name": "Tactics: forks", "createdAt": T0 + 10 * DAY,
                   "updatedAt": T0 + 10 * DAY}, forks),
    }


def student_studies():
    # Clone of the Italian study: Julen continued chapter 1 and marked it done,
    # added moves in chapter 2, left chapter 3 alone.
    italian = "\n\n".join([
        chapter_pgn(C_ITALIAN, "Italian Game ideas", "jc000001", "Giuoco Piano main line",
                    ITALIAN + ["Bd2", "Bxd2+", "Nbxd2", "d5"], variations={7: ["d6"]},
                    comments={6: "Prepare d4.", 15: "✅ done, black hits the centre"}),
        chapter_pgn(C_ITALIAN, "Italian Game ideas", "jc000002", "Two Knights: Ng5",
                    TWO_KNIGHTS + ["Bb5+", "c6", "dxc6", "bxc6"]),
        chapter_pgn(C_ITALIAN, "Italian Game ideas", "jc000003", "Ideas for Black", ITALIAN[:6]),
    ])
    # Clone of the forks study that Julen renamed; matched by chapter names.
    forks = "\n\n".join([
        chapter_pgn(C_FORKS, "My fork homework", "jf000001", "Knight fork 1", ["Nc4"], fen=FORK_FEN),
        chapter_pgn(C_FORKS, "My fork homework", "jf000002", "Knight fork 2", ["Ne4"], fen=FORK2_FEN),
    ])
    return {
        C_ITALIAN: ({"id": C_ITALIAN, "name": "Italian Game ideas", "createdAt": T0 + DAY,
                     "updatedAt": T0 + 3 * DAY}, italian),
        C_FORKS: ({"id": C_FORKS, "name": "My fork homework", "createdAt": T0 + 11 * DAY,
                   "updatedAt": T0 + 11 * DAY}, forks),
    }


def fen_after(moves, fen=chess.STARTING_FEN):
    b = chess.Board(fen)
    for m in moves:
        b.push_san(m)
    return b.fen()


def ai_game(gid, day, fen, ai_level, winner, julen_color="white", moves="e4 e5 Nf3"):
    ai = {"aiLevel": ai_level}
    me = {"user": {"name": STUDENT, "id": STUDENT.lower()}, "rating": 1500}
    return {"id": gid, "rated": False, "variant": "fromPosition" if fen != chess.STARTING_FEN else "standard",
            "createdAt": T0 + day * DAY, "lastMoveAt": T0 + day * DAY + 600_000, "status": "mate",
            "players": {"white": me if julen_color == "white" else ai,
                        "black": ai if julen_color == "white" else me},
            "initialFen": fen, "winner": winner, "moves": moves}


GAMES = [
    ai_game("gameAAA1", 8, LUCENA_FEN, 3, "white"),
    ai_game("gameAAA2", 9, LUCENA_FEN, 5, "black"),
    ai_game("gameAAA3", 9, LUCENA_FEN, 5, "white"),
    ai_game("gameBBB1", 2, fen_after(ITALIAN[:8]), 4, "black", julen_color="black"),
    ai_game("gameCCC1", 12, chess.STARTING_FEN, 2, "white"),  # normal game, not linked to a study
    {"id": "humanGm1", "createdAt": T0 + 5 * DAY, "players": {"white": {"user": {"name": STUDENT}},
                                                              "black": {"user": {"name": "someone"}}}},
]


class FakeLichess:
    def __init__(self):
        self.teacher, self.student = teacher_studies(), student_studies()

    def list_studies(self, username):
        src = self.teacher if username == TEACHER else self.student if username == STUDENT else {}
        return [dict(meta) for meta, _ in src.values()]

    def study_pgn(self, study_id):
        return {**self.teacher, **self.student}[study_id][1]

    def ai_games(self, username, since_ms):
        return [g for g in GAMES if g["createdAt"] >= since_ms
                and any("aiLevel" in p for p in g["players"].values())]


DEMO_CONFIG = Config(student=STUDENT, teacher=TEACHER, db_path=ROOT / "data" / "demo.db",
                     report_path=ROOT / "data" / "demo-report.html")
