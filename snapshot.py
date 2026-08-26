"""Build the published static snapshot under build/.

Run on a schedule by .github/workflows/snapshot.yml. Fetches the feeds, scores
each region against the committed baseline, appends this round's readings to
that baseline, and writes everything the static page needs:

    build/data/state.json    the rendered verdict and camera metadata
    build/frames/<cam>.jpg   the exact frames the verdict was computed from
    data/history.json        the rolling baseline, committed so it accumulates

The site under build/ is uploaded straight to Pages as an artifact and is never
committed: the six frames come to roughly 900 KB a run, which at a fifteen
minute cadence would add tens of megabytes a day to the repository. Only the
baseline, which is small text, is committed back.

The frames are published alongside the verdict on purpose. A static page can
display live frames from data.gov.sg, but it cannot read their pixels (no CORS
headers on the image host), so it cannot compute anything. Publishing the
judged frames keeps the picture and the verdict in agreement instead of showing
a live photo next to a verdict from ten minutes ago.

    python snapshot.py [--out build] [--history data/history.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import baseline
import checkpoints
import jam
import verdict
from app import (FORECAST_URL, RAINFALL_URL, TRAFFIC_URL, fetch_bytes,
                 fetch_json)


def collect_frames(traffic):
    """Download the current frame for every analysed camera."""
    frames = {}
    try:
        cameras = traffic["items"][0]["cameras"]
    except (KeyError, IndexError, TypeError):
        return frames
    for cam in cameras:
        cam_id = cam.get("camera_id")
        if cam_id not in checkpoints.CAMERAS_BY_ID:
            continue
        blob = fetch_bytes(cam.get("image"))
        if blob:
            frames[cam_id] = (cam.get("timestamp"), blob)
    return frames


def shrink(raw, max_width):
    """Re-encode a frame smaller to keep the published payload light.

    Full 1920x1080 frames are ~220 KB each; the page shows six of them and is
    rebuilt every quarter of an hour.
    """
    try:
        from io import BytesIO

        from PIL import Image
    except ImportError:
        return raw
    try:
        with Image.open(BytesIO(raw)) as im:
            if im.width <= max_width:
                return raw
            height = int(max_width * im.height / im.width)
            im = im.convert("RGB").resize((max_width, height), Image.LANCZOS)
            buf = BytesIO()
            im.save(buf, format="JPEG", quality=78, optimize=True)
            return buf.getvalue()
    except OSError:
        return raw


def main():
    ap = argparse.ArgumentParser(description="Build the published snapshot")
    ap.add_argument("--out", default=Path("build"), type=Path,
                    help="site output directory, uploaded to Pages")
    ap.add_argument("--history", default=Path("data") / "history.json", type=Path,
                    help="rolling baseline, committed between runs")
    ap.add_argument("--max-width", default=1280, type=int)
    args = ap.parse_args()

    data_dir = args.out / "data"
    frames_dir = args.out / "frames"
    data_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)

    # The published page shares the local app's renderer and stylesheet, and
    # takes its own shell from web/index.html. Copying every run means the two
    # front ends cannot drift apart.
    here = Path(__file__).parent
    for source, name in (
        (here / "static" / "app.js", "app.js"),
        (here / "static" / "style.css", "style.css"),
        (here / "web" / "index.html", "index.html"),
    ):
        if source.is_file():
            (args.out / name).write_bytes(source.read_bytes())

    traffic = fetch_json(TRAFFIC_URL)
    rain = fetch_json(RAINFALL_URL)
    forecast = fetch_json(FORECAST_URL)

    if traffic is None:
        print("traffic feed unavailable; leaving the previous snapshot in place")
        return 1

    frames = collect_frames(traffic)
    if not frames:
        print("no frames downloaded; leaving the previous snapshot in place")
        return 1

    now = datetime.now(timezone.utc)

    # Measure from the single frame we just fetched. Motion needs a pair and is
    # therefore unavailable here; the verdict does not depend on it.
    measurements = {}
    for cam_id, (ts, blob) in frames.items():
        if cam_id not in jam.REGIONS:
            continue
        parsed = checkpoints.parse_ts(ts)
        stamp = parsed.timestamp() if parsed else 0.0
        measurements[cam_id] = jam.measure_camera(cam_id, [(stamp, blob)])

    history_path = args.history
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history = baseline.History.load(str(history_path))

    # Score against the baseline as it stands BEFORE adding this round, so a
    # reading is never compared against itself.
    directions = verdict.build(measurements, history, now)

    history.add(now, verdict.occupancies(measurements))
    history.save(str(history_path))

    state = checkpoints.build_state(
        traffic, rain, forecast, now,
        history={cam_id: [ts] for cam_id, (ts, _blob) in frames.items()},
    )
    state["directions"] = directions
    state["snapshot"] = {
        "built_at": now.astimezone(baseline.SGT).isoformat(timespec="seconds"),
        "baseline_samples": len(history.samples),
        "region_samples": {k: history.count(k) for k in jam.region_keys()},
    }

    for cam_id, (_ts, blob) in frames.items():
        (frames_dir / ("%s.jpg" % cam_id)).write_bytes(shrink(blob, args.max_width))

    with open(data_dir / "state.json", "w", encoding="utf-8") as fh:
        json.dump(state, fh, separators=(",", ":"))

    levels = ", ".join(
        "%s=%s" % (d["label"], d["recommendation"]["level"]) for d in directions)
    print("snapshot written: %s" % levels)
    print("baseline now holds %d samples" % len(history.samples))
    return 0


if __name__ == "__main__":
    sys.exit(main())
