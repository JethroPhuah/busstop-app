"""Turn per-region measurements into a call to action per travel direction.

The user's question is not "what is camera 2702 showing" but "should I set off
towards JB right now, and by which crossing". This maps regions onto routes,
scores each against its own history, and reduces each direction to one
recommendation.
"""
from __future__ import annotations

import baseline
import checkpoints
import jam

DIRECTION_LABELS = {
    jam.JOHOR: {
        "id": jam.JOHOR,
        "label": "To Johor",
        "sub": "Singapore to JB",
    },
    jam.SINGAPORE: {
        "id": jam.SINGAPORE,
        "label": "To Singapore",
        "sub": "JB back home",
    },
}

# Which regions sit on which route. A camera contributes to a direction only
# where its carriageway for that direction is unambiguous, which is why Tuas
# southbound has one region and not two.
ROUTES = {
    ("woodlands", jam.JOHOR): ("2702:johor", "2701:johor"),
    ("woodlands", jam.SINGAPORE): ("2702:singapore", "2701:singapore"),
    ("tuas", jam.JOHOR): ("4713:johor", "4703:johor"),
    ("tuas", jam.SINGAPORE): ("4713:singapore",),
}

CROSSING_NAMES = {c["id"]: c["name"] for c in checkpoints.CROSSINGS}


def occupancies(measurements):
    """Flatten {camera: {direction: m}} to {"camera:direction": occupancy}."""
    out = {}
    for camera_id, directions in (measurements or {}).items():
        for direction, m in directions.items():
            out["%s:%s" % (camera_id, direction)] = m.get("occupancy")
    return out


def _region_reading(measurements, region_key):
    camera_id, direction = region_key.split(":", 1)
    return (measurements.get(camera_id) or {}).get(direction)


def build(measurements, history, now):
    """Build the direction-level verdicts shown at the top of the page."""
    measurements = measurements or {}
    directions = []

    for direction in jam.DIRECTIONS:
        per_crossing = {}
        crossing_rows = []

        for crossing in checkpoints.CROSSINGS:
            keys = ROUTES.get((crossing["id"], direction), ())
            if not keys:
                continue

            region_rows = []
            scores = []
            for key in keys:
                reading = _region_reading(measurements, key)
                current = reading.get("occupancy") if reading else None
                dark = bool(reading.get("dark")) if reading else False
                s = baseline.score(history, key, current, now, dark=dark)
                scores.append(s)
                camera_id = key.split(":", 1)[0]
                region_rows.append({
                    "camera": camera_id,
                    "region": key,
                    "level": s["level"],
                    "reason": s.get("reason", ""),
                    "occupancy": current,
                    "percentile": s.get("percentile"),
                    "samples": s.get("samples", 0),
                    "motion": reading.get("motion") if reading else None,
                })

            combined = baseline.combine(scores)
            per_crossing[crossing["id"]] = combined
            crossing_rows.append({
                "id": crossing["id"],
                "name": crossing["name"],
                "road": crossing["road"],
                "level": combined["level"],
                "reason": combined.get("reason", ""),
                "regions": region_rows,
            })

        rec = baseline.recommend(per_crossing)
        if rec.get("crossing"):
            rec["crossing_name"] = CROSSING_NAMES.get(rec["crossing"], rec["crossing"])
        if rec.get("avoid"):
            rec["avoid"]["crossing_name"] = CROSSING_NAMES.get(
                rec["avoid"]["crossing"], rec["avoid"]["crossing"])

        meta = DIRECTION_LABELS[direction]
        directions.append({
            "id": meta["id"],
            "label": meta["label"],
            "sub": meta["sub"],
            "recommendation": rec,
            "crossings": crossing_rows,
        })

    return directions
