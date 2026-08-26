"""Offline tests for the checkpoint dashboard logic.

Every fixture below is trimmed from a real data.gov.sg response captured on
2026-08-26, so the field names under test match the live feeds. No network.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import checkpoints  # noqa: E402

SGT = timezone(timedelta(hours=8))
NOW = datetime(2026, 8, 26, 13, 32, 0, tzinfo=SGT)

TRAFFIC = {
    "items": [{
        "timestamp": "2026-08-26T13:30:30+08:00",
        "cameras": [
            {"camera_id": "2701", "timestamp": "2026-08-26T13:30:30+08:00",
             "image": "https://images.data.gov.sg/a.jpg",
             "location": {"latitude": 1.447023728, "longitude": 103.7716543}},
            {"camera_id": "2702", "timestamp": "2026-08-26T13:30:30+08:00",
             "image": "https://images.data.gov.sg/b.jpg",
             "location": {"latitude": 1.44555, "longitude": 103.76834}},
            {"camera_id": "2704", "timestamp": "2026-08-26T13:20:00+08:00",
             "image": "https://images.data.gov.sg/c.jpg",
             "location": {"latitude": 1.42959, "longitude": 103.76931}},
            {"camera_id": "4703", "timestamp": "2026-08-26T13:30:30+08:00",
             "image": "https://images.data.gov.sg/d.jpg",
             "location": {"latitude": 1.3487, "longitude": 103.63504}},
            {"camera_id": "4712", "timestamp": "2026-08-26T13:30:30+08:00",
             "image": "https://images.data.gov.sg/e.jpg",
             "location": {"latitude": 1.34124, "longitude": 103.64391}},
            {"camera_id": "4713", "timestamp": "2026-08-26T13:30:30+08:00",
             "image": "https://images.data.gov.sg/f.jpg",
             "location": {"latitude": 1.34765, "longitude": 103.6367}},
            # A camera outside the two corridors: must be ignored.
            {"camera_id": "4799", "timestamp": "2026-08-26T13:30:30+08:00",
             "image": "https://images.data.gov.sg/g.jpg",
             "location": {"latitude": 1.26028, "longitude": 103.82389}},
        ],
    }]
}

RAIN = {
    "code": 0,
    "data": {
        "stations": [
            {"id": "S121", "name": "Old Choa Chu Kang Rd",
             "location": {"latitude": 1.37288, "longitude": 103.72244}},
            {"id": "S60", "name": "Sentosa",
             "location": {"latitude": 1.25, "longitude": 103.8279}},
            {"id": "S104", "name": "Woodlands Ave 9",
             "location": {"latitude": 1.44387, "longitude": 103.78538}},
            {"id": "S117", "name": "Banyan Road",
             "location": {"latitude": 1.256, "longitude": 103.679}},
        ],
        "readings": [{
            "timestamp": "2026-08-26T13:15:00+08:00",
            "data": [
                {"stationId": "S121", "value": 0},
                {"stationId": "S60", "value": 0},
                {"stationId": "S104", "value": 1.4},
                {"stationId": "S117", "value": 0},
            ],
        }],
    },
}

FORECAST = {
    "code": 0,
    "data": {
        "area_metadata": [
            {"name": "Woodlands", "label_location": {"latitude": 1.4382, "longitude": 103.7891}},
            {"name": "Tuas", "label_location": {"latitude": 1.294947, "longitude": 103.635024}},
            {"name": "Bedok", "label_location": {"latitude": 1.321, "longitude": 103.924}},
        ],
        "items": [{
            "valid_period": {"text": "1.00 pm to 3.00 pm"},
            "forecasts": [
                {"area": "Woodlands", "forecast": "Thundery Showers"},
                {"area": "Tuas", "forecast": "Partly Cloudy (Day)"},
                {"area": "Bedok", "forecast": "Cloudy"},
            ],
        }],
    },
}


class TestGeometry(unittest.TestCase):
    def test_checkpoints_are_about_18km_apart(self):
        w, t = checkpoints.CROSSINGS
        km = checkpoints.haversine_km(w["lat"], w["lon"], t["lat"], t["lon"])
        # Measured against the live camera coordinates: ~18.2 km.
        self.assertAlmostEqual(km, 18.2, delta=0.6)

    def test_zero_distance(self):
        self.assertEqual(checkpoints.haversine_km(1.3, 103.8, 1.3, 103.8), 0.0)


class TestCatalogue(unittest.TestCase):
    def test_six_cameras_three_per_crossing(self):
        self.assertEqual(len(checkpoints.CAMERAS), 6)
        for crossing in ("woodlands", "tuas"):
            cams = [c for c in checkpoints.CAMERAS if c["crossing"] == crossing]
            self.assertEqual(len(cams), 3, crossing)
            self.assertEqual(sorted(c["step"] for c in cams), [1, 2, 3])

    def test_ids_match_the_live_feed(self):
        feed_ids = {c["camera_id"] for c in TRAFFIC["items"][0]["cameras"]}
        self.assertTrue(set(checkpoints.CAMERAS_BY_ID).issubset(feed_ids))


class TestFreshness(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(checkpoints.freshness(30), "fresh")
        self.assertEqual(checkpoints.freshness(300), "aging")
        self.assertEqual(checkpoints.freshness(1200), "stale")
        self.assertEqual(checkpoints.freshness(None), "unknown")

    def test_boundaries_are_exclusive(self):
        self.assertEqual(checkpoints.freshness(checkpoints.FRESH_UNDER), "aging")
        self.assertEqual(checkpoints.freshness(checkpoints.AGING_UNDER), "stale")


class TestParseTs(unittest.TestCase):
    def test_offset_timestamp(self):
        parsed = checkpoints.parse_ts("2026-08-26T13:30:30+08:00")
        self.assertEqual(parsed.hour, 13)
        self.assertIsNotNone(parsed.tzinfo)

    def test_garbage_returns_none(self):
        for bad in ("", None, "not-a-date", 42):
            self.assertIsNone(checkpoints.parse_ts(bad))


class TestNearestMatching(unittest.TestCase):
    def test_rainfall_picks_the_closest_station(self):
        w = checkpoints.CROSSINGS[0]
        got = checkpoints.nearest_rainfall(RAIN, w["lat"], w["lon"])
        self.assertEqual(got["station"], "Woodlands Ave 9")
        self.assertEqual(got["mm"], 1.4)

    def test_forecast_area_matches_each_crossing(self):
        w, t = checkpoints.CROSSINGS
        self.assertEqual(checkpoints.nearest_forecast(FORECAST, w["lat"], w["lon"])["area"], "Woodlands")
        self.assertEqual(checkpoints.nearest_forecast(FORECAST, t["lat"], t["lon"])["area"], "Tuas")

    def test_station_without_a_reading_is_skipped(self):
        rain = {"data": {
            "stations": RAIN["data"]["stations"],
            "readings": [{"timestamp": "x", "data": [{"stationId": "S121", "value": 0}]}],
        }}
        w = checkpoints.CROSSINGS[0]
        # S104 is nearest but has no reading, so the next-nearest wins.
        self.assertEqual(checkpoints.nearest_rainfall(rain, w["lat"], w["lon"])["station"],
                         "Old Choa Chu Kang Rd")

    def test_malformed_feeds_return_none(self):
        for bad in ({}, {"data": {}}, {"data": {"readings": []}}, None):
            self.assertIsNone(checkpoints.nearest_rainfall(bad or {}, 1.4, 103.7))
            self.assertIsNone(checkpoints.nearest_forecast(bad or {}, 1.4, 103.7))


class TestIsWet(unittest.TestCase):
    def test_rain_now(self):
        self.assertTrue(checkpoints.is_wet({"mm": 0.2}, None))
        self.assertFalse(checkpoints.is_wet({"mm": 0}, None))

    def test_forecast_wording(self):
        for text in ("Light Rain", "Thundery Showers", "Passing Showers"):
            self.assertTrue(checkpoints.is_wet(None, {"forecast": text}), text)
        for text in ("Partly Cloudy (Day)", "Fair", "Hazy"):
            self.assertFalse(checkpoints.is_wet(None, {"forecast": text}), text)

    def test_missing_reading_is_not_wet(self):
        self.assertFalse(checkpoints.is_wet({"mm": None}, None))
        self.assertFalse(checkpoints.is_wet(None, None))


class TestBuildState(unittest.TestCase):
    def setUp(self):
        self.state = checkpoints.build_state(TRAFFIC, RAIN, FORECAST, NOW)

    def test_two_crossings_in_order(self):
        self.assertEqual([c["id"] for c in self.state["crossings"]], ["woodlands", "tuas"])

    def test_cameras_ordered_upstream_to_bridge(self):
        woodlands = self.state["crossings"][0]
        self.assertEqual([c["id"] for c in woodlands["cameras"]], ["2704", "2702", "2701"])

    def test_unlisted_camera_is_excluded(self):
        shown = {c["id"] for x in self.state["crossings"] for c in x["cameras"]}
        self.assertNotIn("4799", shown)

    def test_age_and_freshness_from_frame_timestamp(self):
        woodlands = self.state["crossings"][0]
        by_id = {c["id"]: c for c in woodlands["cameras"]}
        self.assertEqual(by_id["2702"]["age_seconds"], 90)
        self.assertEqual(by_id["2702"]["freshness"], "fresh")
        # 2704's frame is 12 minutes old in the fixture.
        self.assertEqual(by_id["2704"]["age_seconds"], 720)
        self.assertEqual(by_id["2704"]["freshness"], "stale")

    def test_weather_attached_per_crossing(self):
        woodlands, tuas = self.state["crossings"]
        self.assertTrue(woodlands["wet"])
        self.assertEqual(woodlands["forecast"]["forecast"], "Thundery Showers")
        self.assertFalse(tuas["wet"])

    def test_no_warnings_when_all_feeds_present(self):
        self.assertEqual(self.state["warnings"], [])

    def test_history_is_passed_through(self):
        state = checkpoints.build_state(
            TRAFFIC, RAIN, FORECAST, NOW, history={"2702": ["t1", "t2"]})
        by_id = {c["id"]: c for c in state["crossings"][0]["cameras"]}
        self.assertEqual(by_id["2702"]["history"], ["t1", "t2"])
        self.assertEqual(by_id["2701"]["history"], [])


class TestDegradedFeeds(unittest.TestCase):
    def test_missing_weather_still_renders_cameras(self):
        state = checkpoints.build_state(TRAFFIC, None, None, NOW)
        self.assertEqual(len(state["crossings"]), 2)
        self.assertEqual(len(state["crossings"][0]["cameras"]), 3)
        self.assertIsNone(state["crossings"][0]["rainfall"])
        self.assertEqual(len(state["warnings"]), 2)

    def test_missing_traffic_feed_warns_but_does_not_raise(self):
        state = checkpoints.build_state({}, RAIN, FORECAST, NOW)
        self.assertTrue(any("camera feed" in w for w in state["warnings"]))
        cams = state["crossings"][0]["cameras"]
        self.assertEqual(len(cams), 3)
        self.assertFalse(cams[0]["available"])
        self.assertIsNone(cams[0]["age_seconds"])

    def test_cached_history_keeps_a_camera_available(self):
        state = checkpoints.build_state({}, RAIN, FORECAST, NOW, history={"2702": ["t1"]})
        by_id = {c["id"]: c for c in state["crossings"][0]["cameras"]}
        self.assertTrue(by_id["2702"]["available"])
        self.assertFalse(by_id["2701"]["available"])

    def test_all_feeds_down(self):
        state = checkpoints.build_state(None, None, None, NOW)
        self.assertEqual(len(state["warnings"]), 3)
        self.assertEqual(len(state["crossings"]), 2)


if __name__ == "__main__":
    unittest.main()
