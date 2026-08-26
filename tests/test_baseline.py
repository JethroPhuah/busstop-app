"""Offline tests for the self-calibrating scorer.

The point of scoring against a region's own history is that fixed clutter
cancels out. These tests pin that behaviour, including the case that broke the
absolute-threshold approach: two regions with very different clutter floors
must both be judged correctly.
"""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import baseline  # noqa: E402

SGT = timezone(timedelta(hours=8))


def at(day, hour, minute=0):
    return datetime(2026, 8, day, hour, minute, tzinfo=SGT)


def history_with(region_key, values, start=at(10, 14), step_minutes=15):
    h = baseline.History()
    when = start
    for v in values:
        h.add(when, {region_key: v})
        when += timedelta(minutes=step_minutes)
    return h


class TestHistoryStore(unittest.TestCase):
    def test_add_and_count(self):
        h = history_with("2702:johor", [0.1, 0.2, 0.3])
        self.assertEqual(h.count("2702:johor"), 3)
        self.assertEqual(h.count("nope:johor"), 0)

    def test_none_values_are_dropped(self):
        h = baseline.History()
        h.add(at(10, 14), {"a:johor": None, "b:johor": 0.4})
        self.assertEqual(h.count("a:johor"), 0)
        self.assertEqual(h.count("b:johor"), 1)

    def test_all_none_adds_nothing(self):
        h = baseline.History()
        h.add(at(10, 14), {"a:johor": None})
        self.assertEqual(h.samples, [])

    def test_round_trip_through_disk(self):
        h = history_with("2702:johor", [0.11, 0.22])
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "history.json")
            h.save(path)
            again = baseline.History.load(path)
        self.assertEqual(again.count("2702:johor"), 2)

    def test_missing_or_corrupt_file_loads_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "nope.json")
            self.assertEqual(baseline.History.load(missing).samples, [])
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            self.assertEqual(baseline.History.load(str(bad)).samples, [])
            wrong = Path(tmp) / "wrong.json"
            wrong.write_text("[1,2,3]", encoding="utf-8")
            self.assertEqual(baseline.History.load(str(wrong)).samples, [])

    def test_retention_cap(self):
        h = baseline.History()
        when = at(10, 0)
        for i in range(baseline.MAX_SAMPLES + 50):
            h.add(when + timedelta(minutes=i), {"a:johor": 0.1})
        self.assertEqual(len(h.samples), baseline.MAX_SAMPLES)


class TestPercentileRank(unittest.TestCase):
    def test_rank(self):
        values = [0.1, 0.2, 0.3, 0.4]
        self.assertEqual(baseline.percentile_rank(values, 0.05), 0.0)
        self.assertEqual(baseline.percentile_rank(values, 0.25), 0.5)
        self.assertEqual(baseline.percentile_rank(values, 0.9), 1.0)

    def test_ties_count_as_half(self):
        # A flat history must put its own usual value mid-scale, not at the top.
        self.assertEqual(baseline.percentile_rank([0.2] * 10, 0.2), 0.5)
        self.assertEqual(baseline.percentile_rank([0.2] * 10, 0.3), 1.0)
        self.assertEqual(baseline.percentile_rank([0.2] * 10, 0.1), 0.0)

    def test_unvarying_region_reads_as_normal(self):
        h = history_with("2702:johor", [0.2] * 40,
                         start=at(10, 14), step_minutes=7 * 24 * 60)
        s = baseline.score(h, "2702:johor", 0.2, at(10, 14))
        self.assertEqual(s["level"], baseline.LEVEL_MODERATE)

    def test_empty(self):
        self.assertIsNone(baseline.percentile_rank([], 0.3))


class TestScore(unittest.TestCase):
    KEY = "2702:johor"

    def test_learning_until_enough_samples(self):
        h = history_with(self.KEY, [0.2] * (baseline.MIN_SAMPLES - 1))
        s = baseline.score(h, self.KEY, 0.5, at(11, 14))
        self.assertEqual(s["level"], baseline.LEVEL_LEARNING)
        self.assertIn(str(baseline.MIN_SAMPLES), s["reason"])

    def test_heavy_when_above_its_own_history(self):
        h = history_with(self.KEY, [0.20 + i * 0.001 for i in range(40)])
        s = baseline.score(h, self.KEY, 0.95, at(11, 14))
        self.assertEqual(s["level"], baseline.LEVEL_HEAVY)

    def test_clear_when_below_its_own_history(self):
        h = history_with(self.KEY, [0.20 + i * 0.001 for i in range(40)])
        s = baseline.score(h, self.KEY, 0.01, at(11, 14))
        self.assertEqual(s["level"], baseline.LEVEL_CLEAR)

    def test_moderate_in_the_middle(self):
        # All samples at the same weekday and hour, so the time-of-week window
        # covers the whole history and the rank is predictable.
        h = history_with(self.KEY, [i / 100.0 for i in range(40)],
                         start=at(10, 14), step_minutes=7 * 24 * 60)
        s = baseline.score(h, self.KEY, 0.20, at(10, 14))
        self.assertEqual(s["basis"], "same time of week")
        self.assertEqual(s["level"], baseline.LEVEL_MODERATE)

    def test_clutter_floor_cancels_out(self):
        """The failure that killed absolute thresholds.

        A cluttered region sits around 0.39 when empty; a clean one around
        0.10. Judged absolutely, the cluttered empty road outranks the clean
        busy one. Judged against their own histories, both come out right.
        """
        cluttered = history_with("4713:singapore", [0.38 + (i % 5) * 0.002 for i in range(40)])
        clean = history_with("2702:johor", [0.09 + (i % 5) * 0.002 for i in range(40)])

        # Cluttered region at its usual empty value -> not busy.
        s1 = baseline.score(cluttered, "4713:singapore", 0.385, at(11, 14))
        self.assertIn(s1["level"], (baseline.LEVEL_CLEAR, baseline.LEVEL_MODERATE))

        # Clean region well above its own usual value -> busy, despite being a
        # much smaller absolute number than the cluttered region's baseline.
        s2 = baseline.score(clean, "2702:johor", 0.30, at(11, 14))
        self.assertEqual(s2["level"], baseline.LEVEL_HEAVY)

    def test_dark_frames_are_not_scored(self):
        h = history_with(self.KEY, [0.2] * 40)
        s = baseline.score(h, self.KEY, 0.9, at(11, 2), dark=True)
        self.assertEqual(s["level"], baseline.LEVEL_UNKNOWN)

    def test_missing_reading(self):
        h = history_with(self.KEY, [0.2] * 40)
        self.assertEqual(baseline.score(h, self.KEY, None, at(11, 14))["level"],
                         baseline.LEVEL_UNKNOWN)

    def test_time_of_week_basis_is_preferred(self):
        # Quiet at 03:00, busy at 18:00, across enough weekdays to qualify.
        h = baseline.History()
        for day in (10, 11, 12, 13, 14):
            for i in range(3):
                h.add(at(day, 3, i * 5), {self.KEY: 0.10})
                h.add(at(day, 18, i * 5), {self.KEY: 0.60})
        # 0.35 at 18:00 is quiet *for rush hour*, though high overall.
        s = baseline.score(h, self.KEY, 0.35, at(17, 18, 0))
        self.assertEqual(s["basis"], "same time of week")
        self.assertEqual(s["level"], baseline.LEVEL_CLEAR)

    def test_falls_back_to_all_hours(self):
        # Readings only ever at 14:00, scored at 04:00 -> no matching window.
        h = history_with(self.KEY, [0.2] * 40, start=at(10, 14), step_minutes=1440)
        s = baseline.score(h, self.KEY, 0.9, at(20, 4))
        self.assertEqual(s["basis"], "all hours")

    def test_weekend_and_weekday_do_not_mix(self):
        h = baseline.History()
        for day in (10, 11, 12, 13, 14):          # Mon-Fri
            for i in range(3):
                h.add(at(day, 18, i * 5), {self.KEY: 0.60})
        for day in (15, 16, 22, 23):              # two full weekends
            for i in range(3):
                h.add(at(day, 18, i * 5), {self.KEY: 0.10})
        # Saturday evening compared against weekend evenings only: 0.55 is high
        # for a weekend even though it is low for a weekday rush hour.
        s = baseline.score(h, self.KEY, 0.55, at(29, 18))
        self.assertEqual(s["basis"], "same time of week")
        self.assertEqual(s["level"], baseline.LEVEL_HEAVY)


class TestCombine(unittest.TestCase):
    def test_worst_wins(self):
        got = baseline.combine([
            {"level": baseline.LEVEL_CLEAR, "reason": "a"},
            {"level": baseline.LEVEL_HEAVY, "reason": "b"},
            {"level": baseline.LEVEL_MODERATE, "reason": "c"},
        ])
        self.assertEqual(got["level"], baseline.LEVEL_HEAVY)

    def test_learning_only_stays_learning(self):
        got = baseline.combine([
            {"level": baseline.LEVEL_LEARNING, "samples": 3},
            {"level": baseline.LEVEL_LEARNING, "samples": 7},
        ])
        self.assertEqual(got["level"], baseline.LEVEL_LEARNING)
        self.assertEqual(got["samples"], 7)

    def test_a_real_reading_beats_learning(self):
        got = baseline.combine([
            {"level": baseline.LEVEL_LEARNING, "samples": 3},
            {"level": baseline.LEVEL_CLEAR, "reason": "x"},
        ])
        self.assertEqual(got["level"], baseline.LEVEL_CLEAR)

    def test_nothing_usable(self):
        self.assertEqual(baseline.combine([])["level"], baseline.LEVEL_UNKNOWN)
        self.assertEqual(
            baseline.combine([{"level": baseline.LEVEL_UNKNOWN}])["level"],
            baseline.LEVEL_UNKNOWN)


class TestRecommend(unittest.TestCase):
    def test_picks_the_quieter_crossing_and_names_the_other(self):
        rec = baseline.recommend({
            "woodlands": {"level": baseline.LEVEL_HEAVY, "reason": "busy"},
            "tuas": {"level": baseline.LEVEL_CLEAR, "reason": "quiet"},
        })
        self.assertEqual(rec["crossing"], "tuas")
        self.assertEqual(rec["level"], baseline.LEVEL_CLEAR)
        self.assertEqual(rec["avoid"]["crossing"], "woodlands")
        self.assertEqual(rec["action"], baseline.CALL_TO_ACTION[baseline.LEVEL_CLEAR][0])

    def test_no_avoid_when_both_equal(self):
        rec = baseline.recommend({
            "woodlands": {"level": baseline.LEVEL_MODERATE, "reason": "x"},
            "tuas": {"level": baseline.LEVEL_MODERATE, "reason": "y"},
        })
        self.assertIsNone(rec["avoid"])

    def test_learning_gives_a_hedged_action(self):
        rec = baseline.recommend({
            "woodlands": {"level": baseline.LEVEL_LEARNING, "reason": "2 of 12"},
            "tuas": {"level": baseline.LEVEL_LEARNING, "reason": "2 of 12"},
        })
        self.assertEqual(rec["level"], baseline.LEVEL_LEARNING)
        self.assertIsNone(rec["crossing"])
        self.assertEqual(rec["action"], baseline.CALL_TO_ACTION[baseline.LEVEL_LEARNING][0])

    def test_every_level_has_wording(self):
        for level in (baseline.LEVEL_CLEAR, baseline.LEVEL_MODERATE, baseline.LEVEL_HEAVY,
                      baseline.LEVEL_LEARNING, baseline.LEVEL_UNKNOWN):
            action, detail = baseline.CALL_TO_ACTION[level]
            self.assertTrue(action and detail, level)


if __name__ == "__main__":
    unittest.main()
