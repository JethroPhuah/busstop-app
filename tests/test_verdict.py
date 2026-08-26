"""Offline tests for region geometry and direction routing.

No JPEG decoding here, so these run without pillow and without network.
"""
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import baseline  # noqa: E402
import jam  # noqa: E402
import verdict  # noqa: E402

SGT = timezone(timedelta(hours=8))
NOW = datetime(2026, 8, 26, 14, 0, tzinfo=SGT)
ROOT = Path(__file__).resolve().parent.parent


class TestRegions(unittest.TestCase):
    def test_all_coordinates_normalised(self):
        for cam, regions in jam.REGIONS.items():
            for direction, poly in regions.items():
                self.assertGreaterEqual(len(poly), 3, "%s %s" % (cam, direction))
                for x, y in poly:
                    self.assertTrue(0.0 <= x <= 1.0, "%s %s x=%s" % (cam, direction, x))
                    self.assertTrue(0.0 <= y <= 1.0, "%s %s y=%s" % (cam, direction, y))

    def test_directions_are_known(self):
        for cam, regions in jam.REGIONS.items():
            for direction in regions:
                self.assertIn(direction, jam.DIRECTIONS, "%s %s" % (cam, direction))

    def test_regions_belong_to_catalogued_cameras(self):
        import checkpoints
        for cam in jam.REGIONS:
            self.assertIn(cam, checkpoints.CAMERAS_BY_ID, cam)

    def test_region_keys_are_stable_and_complete(self):
        keys = jam.region_keys()
        self.assertEqual(len(keys), sum(len(v) for v in jam.REGIONS.values()))
        self.assertEqual(list(keys), sorted(keys))

    def test_opposing_regions_do_not_overlap(self):
        """A pixel cannot be both Johor-bound and Singapore-bound.

        The Causeway polygons share a midline, so an off-by-one here would
        double-count the same lane in both directions.
        """
        for cam, regions in jam.REGIONS.items():
            if len(regions) < 2:
                continue
            a = jam.mask_for(cam, jam.JOHOR)
            b = jam.mask_for(cam, jam.SINGAPORE)
            overlap = (a & b).sum()
            self.assertEqual(overlap, 0, "%s regions overlap by %d px" % (cam, overlap))

    def test_masks_are_non_trivial(self):
        # Too few pixels and the measure is noise; too many and the polygon has
        # escaped the carriageway.
        total = jam.WIDTH * jam.HEIGHT
        for cam, regions in jam.REGIONS.items():
            for direction in regions:
                n = int(jam.mask_for(cam, direction).sum())
                self.assertGreater(n, 300, "%s %s only %d px" % (cam, direction, n))
                self.assertLess(n, total * 0.25, "%s %s is %d px" % (cam, direction, n))


class TestPolygonMask(unittest.TestCase):
    def test_axis_aligned_square(self):
        mask = jam.polygon_mask([(0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0)],
                                width=10, height=10)
        self.assertEqual(mask.shape, (10, 10))
        self.assertEqual(int(mask.sum()), 50)
        self.assertTrue(mask[0][0])
        self.assertFalse(mask[0][9])

    def test_triangle_area_is_about_half(self):
        mask = jam.polygon_mask([(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
                                width=100, height=100)
        self.assertAlmostEqual(int(mask.sum()) / 10000.0, 0.5, delta=0.03)

    def test_degenerate_polygon_is_empty(self):
        mask = jam.polygon_mask([(0.2, 0.2), (0.8, 0.2)], width=20, height=20)
        self.assertEqual(int(mask.sum()), 0)


class TestRoutes(unittest.TestCase):
    def test_routes_reference_real_regions(self):
        known = set(jam.region_keys())
        for (crossing, direction), keys in verdict.ROUTES.items():
            self.assertIn(direction, jam.DIRECTIONS)
            for key in keys:
                self.assertIn(key, known, "%s/%s -> %s" % (crossing, direction, key))

    def test_routes_cover_both_crossings_and_directions(self):
        import checkpoints
        for crossing in checkpoints.CROSSINGS:
            for direction in jam.DIRECTIONS:
                self.assertIn((crossing["id"], direction), verdict.ROUTES)

    def test_a_route_never_mixes_directions(self):
        for (_crossing, direction), keys in verdict.ROUTES.items():
            for key in keys:
                self.assertTrue(key.endswith(":" + direction), key)

    def test_every_region_is_used_by_some_route(self):
        used = {k for keys in verdict.ROUTES.values() for k in keys}
        self.assertEqual(set(jam.region_keys()) - used, set())


class TestBuild(unittest.TestCase):
    def _measurements(self, occ):
        out = {}
        for key in jam.region_keys():
            cam, direction = key.split(":", 1)
            out.setdefault(cam, {})[direction] = {
                "occupancy": occ, "motion": None, "luma": 120.0,
                "dark": False, "frames_used": 1, "compared_gap_s": None,
            }
        return out

    def test_shape_with_empty_history(self):
        directions = verdict.build(self._measurements(0.3), baseline.History(), NOW)
        self.assertEqual([d["id"] for d in directions], list(jam.DIRECTIONS))
        for d in directions:
            self.assertIn("recommendation", d)
            self.assertEqual(d["recommendation"]["level"], baseline.LEVEL_LEARNING)
            self.assertTrue(d["crossings"])
            for c in d["crossings"]:
                self.assertTrue(c["regions"])

    def test_occupancies_flattening(self):
        flat = verdict.occupancies(self._measurements(0.42))
        self.assertEqual(set(flat), set(jam.region_keys()))
        self.assertEqual(set(flat.values()), {0.42})

    def test_missing_measurements_degrade(self):
        directions = verdict.build({}, baseline.History(), NOW)
        for d in directions:
            self.assertIn(d["recommendation"]["level"],
                          (baseline.LEVEL_UNKNOWN, baseline.LEVEL_LEARNING))

    def test_recommendation_names_the_quieter_crossing(self):
        # Give every region a long, tight history, then make the Woodlands
        # Johor-bound regions read far above their own norm.
        history = baseline.History()
        when = NOW - timedelta(days=30)
        for _ in range(40):
            history.add(when, {k: 0.20 for k in jam.region_keys()})
            when += timedelta(days=7)  # same weekday and hour each time

        measurements = self._measurements(0.20)
        for cam in ("2702", "2701"):
            measurements[cam][jam.JOHOR]["occupancy"] = 0.95

        directions = {d["id"]: d for d in verdict.build(measurements, history, NOW)}
        johor = directions[jam.JOHOR]["recommendation"]
        self.assertEqual(johor["crossing"], "tuas")
        self.assertEqual(johor["avoid"]["crossing"], "woodlands")
        self.assertEqual(johor["crossing_name"], "Tuas")

    def test_dark_regions_are_not_scored(self):
        measurements = self._measurements(0.3)
        for cam in measurements:
            for direction in measurements[cam]:
                measurements[cam][direction]["dark"] = True
        directions = verdict.build(measurements, baseline.History(), NOW)
        for d in directions:
            for c in d["crossings"]:
                for r in c["regions"]:
                    self.assertEqual(r["level"], baseline.LEVEL_UNKNOWN)


class TestPublishedShell(unittest.TestCase):
    """The Pages shell must point at the committed snapshot, not the live API."""

    def setUp(self):
        self.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    def test_targets_the_static_snapshot(self):
        self.assertIn("data/state.json", self.html)
        self.assertIn("frames/", self.html)
        self.assertRegex(self.html, r"live\s*:\s*false")

    def test_does_not_reference_the_local_server(self):
        self.assertNotIn("/api/state", self.html)
        self.assertNotIn('"/img/', self.html)

    def test_uses_relative_asset_paths(self):
        # Pages serves from a subpath on project sites, so absolute /static
        # links would 404.
        for src in re.findall(r'(?:src|href)="([^"]+)"', self.html):
            if src.startswith(("http", "#", "mailto:")):
                continue
            self.assertFalse(src.startswith("/"), "absolute asset path: %s" % src)


if __name__ == "__main__":
    unittest.main()
