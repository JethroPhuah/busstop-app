"""Pure data-shaping logic for the checkpoint dashboard.

Deliberately free of network and HTTP-server concerns so that every rule in
here (camera catalogue, nearest-station matching, staleness thresholds,
degraded-feed handling) can be unit tested offline in CI.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

SGT = timezone(timedelta(hours=8))

# Freshness thresholds, in seconds, applied to a camera frame's own timestamp.
FRESH_UNDER = 180
AGING_UNDER = 600

# Camera captions were verified on 2026-08-26 by actually looking at the
# frames: LTA burns direction labels ("JOHOR", "CAUSEWAY", "AYE") into every
# image, so these read off the pictures rather than off remembered camera IDs.
# `step` orders each crossing from furthest-upstream to the bridge itself.
CAMERAS = (
    {
        "id": "2704", "crossing": "woodlands", "step": 1,
        "title": "BKE approach",
        "detail": "Woodlands Ave 3 / Woodlands Checkpoint",
        "hint": "Early warning. Traffic only backs up this far when the checkpoint is badly stuck.",
    },
    {
        "id": "2702", "crossing": "woodlands", "step": 2,
        "title": "Checkpoint approach",
        "detail": "BKE / Causeway",
        "hint": "The frame that matters. Causeway-bound queue forms on the right-hand carriageway.",
    },
    {
        "id": "2701", "crossing": "woodlands", "step": 3,
        "title": "Causeway bridge",
        "detail": "Woodlands / Johor",
        "hint": "Bridge moving but approach stuck means the delay is at immigration, not on the road.",
    },
    {
        "id": "4712", "crossing": "tuas", "step": 1,
        "title": "AYE approach",
        "detail": "Johor / City",
        "hint": "Early warning for the Second Link.",
    },
    {
        "id": "4713", "crossing": "tuas", "step": 2,
        "title": "Checkpoint approach",
        "detail": "AYE / Johor",
        "hint": "The frame that matters for Tuas.",
    },
    {
        "id": "4703", "crossing": "tuas", "step": 3,
        "title": "Second Link bridge",
        "detail": "Towards Johor",
        "hint": "The bridge itself. Often clear even when Woodlands is not.",
    },
)

CROSSINGS = (
    {
        "id": "woodlands", "name": "Woodlands", "road": "Causeway",
        "lat": 1.4451, "lon": 103.7695,
        "note": "Shorter drive from most of Singapore, far busier.",
    },
    {
        "id": "tuas", "name": "Tuas", "road": "Second Link",
        "lat": 1.3482, "lon": 103.6362,
        "note": "Longer drive, usually the quieter crossing.",
    },
)

CAMERAS_BY_ID = {c["id"]: c for c in CAMERAS}


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in kilometres."""
    radius = 6371.0
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def parse_ts(value):
    """Parse an ISO-8601 timestamp from data.gov.sg, or return None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def freshness(age_seconds):
    """Bucket a frame age into a label the UI can colour-code."""
    if age_seconds is None:
        return "unknown"
    if age_seconds < FRESH_UNDER:
        return "fresh"
    if age_seconds < AGING_UNDER:
        return "aging"
    return "stale"


def _nearest(entries, lat, lon):
    """Nearest entry to a point, as (entry, distance_km) or (None, None)."""
    best, best_km = None, None
    for entry in entries:
        km = haversine_km(lat, lon, entry["lat"], entry["lon"])
        if best_km is None or km < best_km:
            best, best_km = entry, km
    return best, best_km


def nearest_rainfall(rain, lat, lon):
    """Rain reading in mm from the station closest to a point.

    Returns None when the feed is missing or shaped unexpectedly, so a failed
    weather call degrades the panel instead of breaking the page.
    """
    try:
        data = rain["data"]
        reading = data["readings"][0]
        values = {d["stationId"]: d["value"] for d in reading["data"]}
        stations = [
            {
                "lat": s["location"]["latitude"],
                "lon": s["location"]["longitude"],
                "name": s["name"],
                "value": values.get(s["id"]),
            }
            for s in data["stations"]
            if s["id"] in values
        ]
    except (KeyError, IndexError, TypeError):
        return None
    station, km = _nearest(stations, lat, lon)
    if station is None:
        return None
    return {
        "station": station["name"],
        "mm": station["value"],
        "km": round(km, 1),
        "timestamp": reading.get("timestamp"),
    }


def nearest_forecast(forecast, lat, lon):
    """Two-hour forecast for the named area closest to a point."""
    try:
        data = forecast["data"]
        item = data["items"][0]
        texts = {f["area"]: f["forecast"] for f in item["forecasts"]}
        areas = [
            {
                "lat": a["label_location"]["latitude"],
                "lon": a["label_location"]["longitude"],
                "name": a["name"],
            }
            for a in data["area_metadata"]
            if a["name"] in texts
        ]
    except (KeyError, IndexError, TypeError):
        return None
    area, km = _nearest(areas, lat, lon)
    if area is None:
        return None
    return {
        "area": area["name"],
        "forecast": texts[area["name"]],
        "km": round(km, 1),
        "period": (item.get("valid_period") or {}).get("text"),
    }


def is_wet(rainfall, forecast):
    """True when rain is falling now or named in the two-hour forecast.

    Used only to raise a "rain makes the queue worse" note, never to invent a
    congestion figure the cameras cannot actually support.
    """
    if rainfall and isinstance(rainfall.get("mm"), (int, float)) and rainfall["mm"] > 0:
        return True
    if forecast:
        text = (forecast.get("forecast") or "").lower()
        return any(w in text for w in ("rain", "shower", "thunder"))
    return False


def build_state(traffic, rain, forecast, now, history=None):
    """Shape the three upstream feeds into the payload the browser renders.

    Any feed may be None (a failed fetch); the corresponding section is
    omitted and a warning is attached rather than raising.
    """
    history = history or {}
    warnings = []

    frames = {}
    feed_ts = None
    try:
        item = traffic["items"][0]
        feed_ts = item.get("timestamp")
        for cam in item["cameras"]:
            frames[cam["camera_id"]] = cam
    except (KeyError, IndexError, TypeError):
        warnings.append("Traffic camera feed unavailable - showing last known frames.")

    if rain is None:
        warnings.append("Rainfall feed unavailable.")
    if forecast is None:
        warnings.append("Weather forecast feed unavailable.")

    crossings = []
    for crossing in CROSSINGS:
        rainfall = nearest_rainfall(rain, crossing["lat"], crossing["lon"]) if rain else None
        fc = nearest_forecast(forecast, crossing["lat"], crossing["lon"]) if forecast else None

        cams = []
        for meta in sorted(
            (c for c in CAMERAS if c["crossing"] == crossing["id"]),
            key=lambda c: c["step"],
        ):
            frame = frames.get(meta["id"])
            ts = frame.get("timestamp") if frame else None
            parsed = parse_ts(ts)
            age = int((now - parsed).total_seconds()) if parsed else None
            snapshots = history.get(meta["id"], [])
            cams.append({
                "id": meta["id"],
                "title": meta["title"],
                "detail": meta["detail"],
                "hint": meta["hint"],
                "step": meta["step"],
                "timestamp": ts,
                "age_seconds": age,
                "freshness": freshness(age),
                "available": frame is not None or bool(snapshots),
                "history": snapshots,
            })

        crossings.append({
            "id": crossing["id"],
            "name": crossing["name"],
            "road": crossing["road"],
            "note": crossing["note"],
            "rainfall": rainfall,
            "forecast": fc,
            "wet": is_wet(rainfall, fc),
            "cameras": cams,
        })

    return {
        "generated_at": now.astimezone(SGT).isoformat(timespec="seconds"),
        "feed_timestamp": feed_ts,
        "crossings": crossings,
        "warnings": warnings,
    }
