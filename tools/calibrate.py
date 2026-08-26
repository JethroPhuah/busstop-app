"""Dev tool: sample the live feed and print the raw measures per region.

Use this to set jam.BUSY_OCCUPANCY and jam.MOVING_MOTION against real traffic
rather than by guesswork. Watch a region you can see is empty and one you can
see is queued, and put the thresholds between them.

    python tools/calibrate.py [samples] [interval_seconds]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jam  # noqa: E402

FEED = "https://api.data.gov.sg/v1/transport/traffic-images"
UA = {"User-Agent": "busstop-app/1.0 (calibrate)"}


def fetch():
    feed = json.load(urllib.request.urlopen(urllib.request.Request(FEED, headers=UA), timeout=20))
    item = feed["items"][0]
    out = {}
    for cam in item["cameras"]:
        cid = cam["camera_id"]
        if cid in jam.REGIONS:
            raw = urllib.request.urlopen(
                urllib.request.Request(cam["image"], headers=UA), timeout=25).read()
            out[cid] = (cam["timestamp"], raw)
    return out


def main():
    samples = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 25.0

    history = {}
    seen = {}
    for n in range(samples):
        for cid, (ts, raw) in fetch().items():
            if seen.get(cid) == ts:
                continue  # feed has not advanced for this camera
            seen[cid] = ts
            history.setdefault(cid, []).append((n * interval, raw))
        print("sample %d/%d" % (n + 1, samples))
        if n < samples - 1:
            time.sleep(interval)

    print()
    print("%-6s %-10s %8s %8s %7s  %s" % ("cam", "direction", "occupancy", "motion", "luma", "status"))
    for cid in sorted(history):
        for direction, r in sorted(jam.analyse_camera(cid, history[cid]).items()):
            occ = "-" if r["occupancy"] is None else "%.4f" % r["occupancy"]
            mot = "-" if r["motion"] is None else "%.3f" % r["motion"]
            print("%-6s %-10s %8s %8s %7.1f  %s (%s)" % (
                cid, direction, occ, mot, r["luma"], r["status"], r["reason"]))
    print()
    print("frames held per camera:", {c: len(v) for c, v in history.items()})


if __name__ == "__main__":
    main()
