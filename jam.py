"""Measurement of LTA traffic camera frames, per direction.

This module only measures. It deliberately holds no thresholds and returns no
verdict, because absolute thresholds were tried and do not work: each camera
has its own floor of fixed clutter (lamp posts, guard rails, fences, lane
paint, tree shadows) and that floor is larger than the vehicle signal. Measured
on real frames, an empty Tuas carriageway scored 0.390 while a dense stationary
queue on the Causeway scored 0.363 - the empty road looked busier.

Scoring therefore happens in baseline.py, which compares a region against its
own history so the clutter cancels out.

Two measures per region of interest, on a downscaled greyscale frame:

  occupancy  fraction of the region departing from the road surface, using the
             region's own median intensity as the tarmac level. Includes a
             constant contribution from clutter, which is exactly why it is
             only ever interpreted relative to the same region's past.
  motion     mean absolute difference against an older frame of the same
             region, corrected for exposure drift. Weak here: the camera
             timestamps advance only every minute or two, so lighting shifts
             can outweigh traffic. Reported for context, not relied upon.

Regions of interest are hand-defined per camera as normalised polygons and were
checked by rendering them back over real frames (tools/roi_preview.py).
Direction assignment comes from the labels LTA burns into each frame, confirmed
against left-hand-drive geometry: traffic keeps left, so on the Causeway the
Johor-bound carriageway is the far one from the camera.
"""
from __future__ import annotations

import numpy as np

# Analysis resolution. Small enough to be fast, large enough that a car is
# still several pixels across.
WIDTH = 480
HEIGHT = 270

# Mean luminance below this and the frame is too dark to measure meaningfully.
DARK_LUMA = 55
# A frame pair further apart than this is not comparable.
MAX_PAIR_GAP_S = 240
# Motion needs at least this much separation between frames to mean anything.
MIN_PAIR_GAP_S = 15
# Pixels this far from the region's median count as "not road surface".
DEVIATION = 22.0

JOHOR = "johor"
SINGAPORE = "singapore"
DIRECTIONS = (JOHOR, SINGAPORE)

# Thin strips down the middle of a single carriageway, not whole road areas.
# A loose polygon that catches a guard rail, a lamp post or roadside trees
# makes the region's median stop being tarmac and ruins the measurement.
# Verified with tools/roi_preview.py, which reports each region's tarmac
# fraction. Only carriageways whose direction is unambiguous are included.
REGIONS = {
    "2702": {
        # Causeway-bound queue lane.
        JOHOR: [(0.485, 0.44), (0.535, 0.43), (0.655, 0.93), (0.595, 0.95)],
        # The BKE viaduct, heading back into Singapore.
        SINGAPORE: [(0.125, 0.46), (0.175, 0.45), (0.255, 0.90), (0.205, 0.92)],
    },
    # The Causeway deck runs diagonally across frame, both directions on one
    # deck split by a median. Traffic keeps left, so the far (upper) half is
    # Johor-bound and the near (lower) half returns to Woodlands.
    "2701": {
        JOHOR: [(0.40, 0.845), (0.66, 0.585), (0.88, 0.345), (0.89, 0.375),
                (0.67, 0.615), (0.41, 0.875)],
        SINGAPORE: [(0.40, 0.905), (0.66, 0.645), (0.88, 0.405), (0.89, 0.435),
                    (0.67, 0.675), (0.41, 0.935)],
    },
    "4713": {
        JOHOR: [(0.66, 0.28), (0.86, 0.13), (0.875, 0.175), (0.675, 0.325)],
        SINGAPORE: [(0.08, 0.58), (0.30, 0.38), (0.315, 0.425), (0.095, 0.625)],
    },
    "4703": {
        # Second Link deck. Only the Johor direction is legible here.
        JOHOR: [(0.14, 0.115), (0.48, 0.045), (0.49, 0.085), (0.15, 0.155)],
    },
}


def region_keys():
    """Stable "camera:direction" keys for every measured region."""
    return tuple(
        "%s:%s" % (cam, direction)
        for cam in sorted(REGIONS)
        for direction in sorted(REGIONS[cam])
    )


def decode(raw):
    """JPEG bytes -> greyscale float array at analysis resolution."""
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(raw)) as im:
        im = im.convert("L").resize((WIDTH, HEIGHT), Image.BILINEAR)
        return np.asarray(im, dtype=np.float32)


def polygon_mask(points, width=WIDTH, height=HEIGHT):
    """Boolean mask for a normalised polygon, by even-odd ray crossing."""
    xs = np.arange(width, dtype=np.float32) + 0.5
    ys = np.arange(height, dtype=np.float32) + 0.5
    gx, gy = np.meshgrid(xs, ys)
    inside = np.zeros((height, width), dtype=bool)
    pts = [(x * width, y * height) for x, y in points]
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if y1 == y2:
            continue
        straddles = (gy >= np.minimum(y1, y2)) & (gy < np.maximum(y1, y2))
        xint = x1 + (gy - y1) * (x2 - x1) / (y2 - y1)
        inside ^= straddles & (gx < xint)
    return inside


_MASK_CACHE = {}


def mask_for(camera_id, direction):
    key = (camera_id, direction)
    if key not in _MASK_CACHE:
        poly = REGIONS.get(camera_id, {}).get(direction)
        _MASK_CACHE[key] = polygon_mask(poly) if poly else None
    return _MASK_CACHE[key]


def occupancy(frame, mask):
    """Fraction of the region departing from its own road-surface level."""
    inside = frame[mask]
    if inside.size == 0:
        return None
    road = np.median(inside)
    return float(np.mean(np.abs(inside - road) > DEVIATION))


def motion(frame, older, mask):
    """Mean absolute change in the region, corrected for exposure drift."""
    if older is None:
        return None
    a, b = frame[mask], older[mask]
    if a.size == 0:
        return None
    # Cameras adjust exposure between frames; remove the global shift so a
    # brightness change is not read as traffic movement.
    return float(np.mean(np.abs((a - a.mean()) - (b - b.mean()))))


_DECODE_CACHE = {}
_DECODE_CACHE_MAX = 96


def decode_cached(camera_id, ts, raw):
    """Decode with memoisation: every request re-reads the same held frames."""
    key = (camera_id, ts)
    hit = _DECODE_CACHE.get(key)
    if hit is None:
        hit = decode(raw)
        if len(_DECODE_CACHE) >= _DECODE_CACHE_MAX:
            for stale in list(_DECODE_CACHE)[: _DECODE_CACHE_MAX // 2]:
                del _DECODE_CACHE[stale]
        _DECODE_CACHE[key] = hit
    return hit


def measure_camera(camera_id, frames):
    """Measure one camera's regions.

    `frames` is a list of (timestamp_seconds, jpeg_bytes), oldest first.
    Returns {direction: raw measurements}. No verdict - see baseline.score.
    """
    regions = REGIONS.get(camera_id)
    if not regions or not frames:
        return {}

    decoded = [(ts, decode_cached(camera_id, ts, raw)) for ts, raw in frames]
    newest_ts, current = decoded[-1]
    luma = float(current.mean())

    older = None
    older_ts = None
    for ts, frame in reversed(decoded[:-1]):
        gap = newest_ts - ts
        if MIN_PAIR_GAP_S <= gap <= MAX_PAIR_GAP_S:
            older, older_ts = frame, ts
            break

    out = {}
    for direction in regions:
        mask = mask_for(camera_id, direction)
        if mask is None:
            continue
        occ = occupancy(current, mask)
        mot = motion(current, older, mask)
        out[direction] = {
            "occupancy": None if occ is None else round(occ, 4),
            "motion": None if mot is None else round(mot, 3),
            "luma": round(luma, 1),
            "dark": luma < DARK_LUMA,
            "frames_used": len(decoded),
            "compared_gap_s": None if older_ts is None else int(newest_ts - older_ts),
        }
    return out
