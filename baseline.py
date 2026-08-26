"""Self-calibrating congestion scoring.

Absolute occupancy is not comparable between cameras: each region carries a
constant contribution from whatever fixed clutter sits inside it. But it *is*
comparable to the same region's own past, because the clutter is in every one
of those readings too. So instead of asking "is 0.36 busy?", which is
unanswerable, this asks "is 0.36 high for this region at this time of week?",
which the history answers.

Scoring compares against readings from a similar time of week first (rush hour
should be judged against rush hour), and falls back to the region's whole
history when there are not yet enough matching samples. Below MIN_SAMPLES it
returns "learning" rather than guessing, so the app never shows a confident
verdict it has not earned.

Pure functions plus a small JSON store, so it is fully testable offline.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

SGT = timezone(timedelta(hours=8))

# Readings needed before a region is scored at all.
MIN_SAMPLES = 12
# Preferred sample count from a matching time-of-week window before falling
# back to the region's whole history.
PREFERRED_SAMPLES = 8
# Hours either side of "now" that count as a similar time of day.
HOUR_WINDOW = 2
# How many readings to retain per store. At one sample every 10 minutes this is
# a bit over a fortnight.
MAX_SAMPLES = 2400

LEVEL_CLEAR = "clear"
LEVEL_MODERATE = "moderate"
LEVEL_HEAVY = "heavy"
LEVEL_LEARNING = "learning"
LEVEL_UNKNOWN = "unknown"

SEVERITY = {
    LEVEL_HEAVY: 3,
    LEVEL_MODERATE: 2,
    LEVEL_CLEAR: 1,
    LEVEL_LEARNING: 0,
    LEVEL_UNKNOWN: 0,
}

# Percentile boundaries against the region's own history.
CLEAR_BELOW = 0.40
HEAVY_ABOVE = 0.78

# Wording for the headline call to action.
CALL_TO_ACTION = {
    LEVEL_CLEAR: ("Go now", "Quieter than usual for this time. Good window."),
    LEVEL_MODERATE: ("Go, expect traffic", "About normal for this time of week."),
    LEVEL_HEAVY: ("Wait if you can", "Busier than usual for this time. Expect to queue."),
    LEVEL_LEARNING: ("Look at the frames", "Still learning what normal looks like here."),
    LEVEL_UNKNOWN: ("Look at the frames", "No usable reading right now."),
}


def parse_iso(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


class History:
    """Rolling per-region occupancy readings, persisted as JSON."""

    def __init__(self, samples=None):
        self.samples = list(samples or [])

    @classmethod
    def load(cls, path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                blob = json.load(fh)
        except (OSError, ValueError):
            return cls()
        if not isinstance(blob, dict):
            return cls()
        samples = blob.get("samples")
        return cls(samples if isinstance(samples, list) else None)

    def save(self, path):
        payload = {"version": 1, "samples": self.samples[-MAX_SAMPLES:]}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))

    def add(self, when, occupancies):
        """Record one round of readings. `occupancies` maps region key -> float."""
        clean = {k: round(float(v), 4) for k, v in occupancies.items() if v is not None}
        if not clean:
            return
        self.samples.append({"t": when.astimezone(SGT).isoformat(timespec="seconds"),
                             "occ": clean})
        del self.samples[:-MAX_SAMPLES]

    def series(self, region_key, now=None):
        """Past readings for a region as (datetime, value), oldest first."""
        out = []
        for sample in self.samples:
            value = (sample.get("occ") or {}).get(region_key)
            if value is None:
                continue
            when = parse_iso(sample.get("t"))
            if when is None:
                continue
            out.append((when, float(value)))
        return out

    def count(self, region_key):
        return sum(1 for s in self.samples if (s.get("occ") or {}).get(region_key) is not None)


def _similar_time(series, now):
    """Readings from a comparable hour of a comparable day type."""
    weekend = now.weekday() >= 5
    picked = []
    for when, value in series:
        if (when.weekday() >= 5) != weekend:
            continue
        delta = abs((when.hour * 60 + when.minute) - (now.hour * 60 + now.minute))
        delta = min(delta, 24 * 60 - delta)  # wrap around midnight
        if delta <= HOUR_WINDOW * 60:
            picked.append(value)
    return picked


def percentile_rank(values, current):
    """Mid-rank position of `current` within `values`, from 0.0 to 1.0.

    Ties count as half, which matters more than it sounds: a region whose
    readings barely vary would otherwise score 1.0 whenever it sat exactly at
    its own normal value, and be reported as unusually busy every single time.
    Splitting ties puts an unremarkable reading in the middle where it belongs.
    """
    if not values:
        return None
    below = sum(1 for v in values if v < current)
    equal = sum(1 for v in values if v == current)
    return (below + 0.5 * equal) / float(len(values))


def score(history, region_key, current, now, dark=False):
    """Grade one region's current occupancy against its own past."""
    if current is None:
        return {"level": LEVEL_UNKNOWN, "reason": "no reading", "samples": 0}
    if dark:
        return {"level": LEVEL_UNKNOWN, "reason": "too dark to measure", "samples": 0}

    series = history.series(region_key)
    if len(series) < MIN_SAMPLES:
        return {
            "level": LEVEL_LEARNING,
            "reason": "%d of %d readings collected" % (len(series), MIN_SAMPLES),
            "samples": len(series),
        }

    matching = _similar_time(series, now)
    if len(matching) >= PREFERRED_SAMPLES:
        values, basis = matching, "same time of week"
    else:
        values, basis = [v for _, v in series], "all hours"

    rank = percentile_rank(values, current)
    if rank is None:
        return {"level": LEVEL_UNKNOWN, "reason": "no comparable readings", "samples": 0}

    if rank <= CLEAR_BELOW:
        level = LEVEL_CLEAR
    elif rank >= HEAVY_ABOVE:
        level = LEVEL_HEAVY
    else:
        level = LEVEL_MODERATE

    return {
        "level": level,
        "reason": "busier than %d%% of readings (%s)" % (round(rank * 100), basis),
        "percentile": round(rank, 3),
        "samples": len(values),
        "basis": basis,
    }


def combine(scores):
    """Reduce several regions on one route to a single reading, worst first.

    A clear bridge does not redeem a stuck approach, so the worst confident
    reading wins.
    """
    usable = [s for s in scores if s and s["level"] not in (LEVEL_UNKNOWN, LEVEL_LEARNING)]
    if not usable:
        learning = [s for s in scores if s and s["level"] == LEVEL_LEARNING]
        if learning:
            return max(learning, key=lambda s: s.get("samples", 0))
        return {"level": LEVEL_UNKNOWN, "reason": "no usable reading", "samples": 0}
    return max(usable, key=lambda s: SEVERITY[s["level"]])


def recommend(per_crossing):
    """Pick the better crossing for one direction and word the call to action.

    `per_crossing` maps crossing id -> combined reading.
    """
    known = [(cid, r) for cid, r in per_crossing.items()
             if r["level"] not in (LEVEL_UNKNOWN, LEVEL_LEARNING)]
    if not known:
        level = LEVEL_LEARNING if any(
            r["level"] == LEVEL_LEARNING for r in per_crossing.values()) else LEVEL_UNKNOWN
        action, detail = CALL_TO_ACTION[level]
        return {"crossing": None, "level": level, "action": action, "detail": detail,
                "reason": next((r.get("reason") for r in per_crossing.values() if r.get("reason")), "")}

    known.sort(key=lambda kv: SEVERITY[kv[1]["level"]])
    best_id, best = known[0]
    action, detail = CALL_TO_ACTION[best["level"]]

    avoid = None
    if len(known) > 1:
        other_id, other = known[-1]
        if SEVERITY[other["level"]] > SEVERITY[best["level"]]:
            avoid = {"crossing": other_id, "level": other["level"]}

    return {
        "crossing": best_id,
        "level": best["level"],
        "action": action,
        "detail": detail,
        "reason": best.get("reason", ""),
        "avoid": avoid,
    }
