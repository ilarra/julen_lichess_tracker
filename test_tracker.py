import tempfile
import unittest
from pathlib import Path

import demo_data
from tracker import Config, build_progress, connect, parse_study_pgn, render_report, study_id_from, sync


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
        self.cfg = Config(student=demo_data.STUDENT, teacher=demo_data.TEACHER,
                          db_path=Path(self.tmp.name) / "t.db", report_path=Path(self.tmp.name) / "r.html")
        self.db = connect(self.cfg.db_path)
        self.client = CountingLichess()
        sync(self.cfg, self.client, self.db, log=lambda *a: None)
        self.progress = {s["name"]: {c.name: c for c in s["chapters"]} for s in build_progress(self.cfg, self.db)}

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_statuses(self):
        it = self.progress["Italian Game ideas"]
        self.assertEqual(it["Giuoco Piano main line"].status, "done")
        self.assertEqual(it["Two Knights: Ng5"].status, "in_progress")
        self.assertEqual(it["Ideas for Black"].status, "untouched")
        self.assertEqual(self.progress["Rook endgames"]["Vancura defence"].status, "not_cloned")

    def test_only_student_additions_count(self):
        ch = self.progress["Italian Game ideas"]["Giuoco Piano main line"]
        self.assertEqual(ch.moves_added, 4)       # teacher's variation and moves are not counted
        self.assertEqual(ch.comments_added, 1)    # teacher's "Prepare d4." is not counted

    def test_renamed_clone_matched_by_chapter_names(self):
        self.assertEqual(self.progress["Tactics: forks"]["Knight fork 1"].status, "untouched")

    def test_games_vs_computer_linked_to_chapter_positions(self):
        lucena = self.progress["Rook endgames"]["Lucena position"]
        self.assertEqual((lucena.practice_games, lucena.practice_wins, lucena.best_ai_level_beaten), (3, 2, 5))
        self.assertEqual(lucena.status, "in_progress")   # practised without cloning
        self.assertEqual(self.progress["Italian Game ideas"]["Giuoco Piano main line"].practice_games, 1)
        # the game from the normal start position is not attributed to any chapter
        self.assertEqual(sum(c.practice_games for s in self.progress.values() for c in s.values()), 4)

    def test_difficulty_from_tag_or_estimate(self):
        self.assertEqual(self.progress["Rook endgames"]["Vancura defence"].difficulty, 5)
        self.assertEqual(self.progress["Rook endgames"]["Vancura defence"].difficulty_source, "teacher")
        self.assertEqual(self.progress["Tactics: forks"]["Knight fork 1"].difficulty_source, "estimate")

    def test_second_sync_skips_unchanged_studies_and_games(self):
        before = self.client.pgn_calls
        sync(self.cfg, self.client, self.db, log=lambda *a: None)
        self.assertEqual(self.client.pgn_calls, before)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM games").fetchone()[0], 5)

    def test_report_renders(self):
        page = render_report(self.cfg, self.db)
        self.assertIn("Giuoco Piano main line", page)
        self.assertIn("2/3 won · beat level 5", page)

    def test_parsing_helpers(self):
        self.assertEqual(study_id_from("https://lichess.org/study/AbCdEfGh/IjKlMnOp"), "AbCdEfGh")
        chs = parse_study_pgn("x", demo_data.student_studies()[demo_data.C_ITALIAN][1])
        self.assertEqual([c.chapter_id for c in chs], ["jc000001", "jc000002", "jc000003"])


if __name__ == "__main__":
    unittest.main()
