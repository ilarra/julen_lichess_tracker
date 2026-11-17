import shutil
import tempfile
import unittest
from pathlib import Path

import demo_data
from demo_data import DEMO_CONFIG
from tracker import (Config, analyse_positions, build_progress, connect, engine_difficulty, find_stockfish,
                     parse_study_pgn, render_report, study_id_from, sync, track_of)


class CountingLichess(demo_data.FakeLichess):
    def __init__(self):
        super().__init__()
        self.pgn_calls = 0

    def study_pgn(self, study_id):
        self.pgn_calls += 1
        return super().study_pgn(study_id)


class TrackerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(**{**DEMO_CONFIG.__dict__, "db_path": Path(self.tmp.name) / "t.db",
                             "report_path": Path(self.tmp.name) / "r.html"})
        self.db = connect(self.cfg.db_path)
        self.client = CountingLichess()
        sync(self.cfg, self.client, self.db, log=lambda *a: None)
        self.studies = {s["name"]: s for s in build_progress(self.cfg, self.db)}

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def ex(self, study):
        return self.studies[study]["exercises"]

    def test_only_class_work_is_shown(self):
        self.assertEqual(set(self.studies), {"Online Klase1", "Online Klase2", "Online Klase3",
                                             "TECNIFICACION G1 - Julen copy", "Julen XakeKlase3"})

    def test_copies_matched_by_positions_and_duplicates_merged(self):
        k1 = self.studies["Online Klase1"]
        self.assertEqual(k1["original"], "klase001")
        self.assertEqual(sorted(k1["copies"]), ["jklase01", "jklase1b"])
        self.assertEqual(self.studies["Online Klase2"]["copies"], ["jklase02"])

    def test_homework_checked_against_next_class_solutions(self):
        verdicts = [(x.kind, x.verdict) for x in self.ex("Online Klase1")]
        self.assertEqual(verdicts, [("exercise", "pending"), ("homework", "solved"),
                                    ("homework", "partly"), ("homework", "seen")])
        p3 = self.ex("Online Klase1")[3]
        self.assertEqual((p3.answer, p3.solution), ("Qa6", "Qb5+"))

    def test_only_students_moves_count_when_original_known(self):
        class_ex, p1 = self.ex("Online Klase1")[:2]
        self.assertEqual(class_ex.moves, 2)      # teacher's Ra5+ is not counted
        self.assertTrue(p1.done_marked)

    def test_not_tried_pending_and_not_copied(self):
        self.assertEqual([x.verdict for x in self.ex("Online Klase2")], ["pending", "not_tried"])
        self.assertEqual([x.verdict for x in self.ex("Online Klase3")], ["not_cloned"])

    def test_solution_and_game_chapters_are_not_exercises(self):
        k2 = self.studies["Online Klase2"]
        self.assertEqual((k2["solutions"], k2["games"], len(k2["exercises"])), (3, 1, 2))

    def test_tracks(self):
        tracks = {name: s["track"] for name, s in self.studies.items()}
        self.assertEqual(tracks["Online Klase2"], "Online · weekly class")
        self.assertEqual(tracks["TECNIFICACION G1 - Julen copy"], "Tecnificación · monthly")   # accents ignored
        self.assertEqual(tracks["Julen XakeKlase3"], "1-to-1")
        self.assertEqual(track_of(self.cfg, "Aloñamendi"), "1-to-1")

    def test_difficulty_from_solution_length(self):
        self.assertEqual([x.difficulty for x in self.ex("Online Klase1")], [None, 1, 2, 1])

    def test_games_vs_computer_linked_to_exercise_positions(self):
        t1 = self.ex("TECNIFICACION G1 - Julen copy")[0]
        self.assertEqual((t1.practice_games, t1.practice_wins, t1.best_ai_level_beaten), (2, 1, 3))
        self.assertEqual(self.ex("Online Klase1")[2].practice_games, 1)   # from a position inside P2's line

    def test_second_sync_skips_unchanged_student_studies(self):
        before = self.client.pgn_calls
        sync(self.cfg, self.client, self.db, log=lambda *a: None)
        self.assertEqual(self.client.pgn_calls - before, len(self.cfg.extra_studies))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM games").fetchone()[0], 4)

    def test_report_renders_tracks(self):
        page = render_report(self.cfg, self.db)
        for text in ("Online · weekly class", "Tecnificación · monthly", "1-to-1", "Seen in analysis", "Qb5+"):
            self.assertIn(text, page)
        self.assertNotIn("Tournament games", page)

    def test_parsing_helpers(self):
        self.assertEqual(study_id_from("https://lichess.org/study/AbCdEfGh/IjKlMnOp"), "AbCdEfGh")
        chs = parse_study_pgn("x", demo_data.student_studies()["jklase02"][1])
        self.assertEqual([c.is_game for c in chs], [False, False, False, True, False, False])


class DifficultyTest(unittest.TestCase):
    """engine_difficulty on hand-written engine lines: (uci, centipawns, mate, pv)."""

    def test_simple_capture_with_several_good_moves_is_easy(self):
        fen = "4k3/8/8/3q4/8/8/3R4/4K3 w - - 0 1"      # Rxd5 wins the queen
        lines = [("d2d5", 900, None, ["d2d5"]), ("d2d4", 880, None, ["d2d4"])]
        self.assertEqual(engine_difficulty(fen, lines)[0], 1)

    def test_quiet_only_move_sacrifice_is_hard(self):
        fen = "6k1/5ppp/8/8/8/8/1Q3PPP/6K1 w - - 0 1"
        lines = [("b2b7", 300, None, ["b2b7", "g8f8", "b7b8"]), ("b2b3", 0, None, ["b2b3"])]
        rating, why = engine_difficulty(fen, lines, solution_plies=5)
        self.assertGreaterEqual(rating, 4)
        self.assertIn("quiet move", why)
        self.assertIn("only one move works", why)

    def test_mate_depth_counts(self):
        fen = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"
        _, why = engine_difficulty(fen, [("a1a8", 9999, 1, ["a1a8"])])
        self.assertIn("mate in 1", why)


class EngineVerdictTest(unittest.TestCase):
    def test_answer_rated_good_by_engine_is_not_missed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(**{**DEMO_CONFIG.__dict__, "db_path": Path(tmp) / "t.db"})
            db = connect(cfg.db_path)
            sync(cfg, demo_data.FakeLichess(), db, log=lambda *a: None)
            # pretend Stockfish likes Julen's Qa6 in P3 as much as the teacher's Qb5+
            from demo_data import P3
            from tracker import fen_key_str
            import json
            db.execute("INSERT INTO position_analysis VALUES (?,?,?,?,?)",
                       (fen_key_str(P3), P3, json.dumps([["e2b5", 500, None, ["e2b5"]], ["e2a6", 480, None, ["e2a6"]]]),
                        18, "x"))
            k1 = next(s for s in build_progress(cfg, db) if s["name"] == "Online Klase1")
            p3 = k1["exercises"][3]
            self.assertEqual(p3.verdict, "alternative")
            self.assertEqual(p3.difficulty_source, "engine")
            db.close()

    @unittest.skipUnless(find_stockfish(DEMO_CONFIG), "Stockfish not installed")
    def test_real_stockfish_finds_back_rank_mate(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(**{**DEMO_CONFIG.__dict__, "db_path": Path(tmp) / "t.db", "engine_seconds": 0.2,
                            "engine_depth": 10})
            db = connect(cfg.db_path)
            sync(cfg, demo_data.FakeLichess(), db, log=lambda *a: None)
            self.assertGreater(analyse_positions(cfg, db, log=lambda *a: None), 0)
            self.assertEqual(analyse_positions(cfg, db, log=lambda *a: None), 0)   # cached
            p1 = next(s for s in build_progress(cfg, db) if s["name"] == "Online Klase1")["exercises"][1]
            self.assertEqual(p1.engine_move, "Ra8#")
            self.assertIn("mate in 1", p1.difficulty_why)
            db.close()


if __name__ == "__main__":
    unittest.main()
