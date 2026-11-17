import tempfile
import unittest
from pathlib import Path

import demo_data
from demo_data import DEMO_CONFIG
from tracker import Config, build_progress, connect, parse_study_pgn, render_report, study_id_from, sync, track_of


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


if __name__ == "__main__":
    unittest.main()
