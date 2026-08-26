"""Dev tool: draw jam.REGIONS over live camera frames so they can be checked.

The verdict is only as good as these polygons, and a polygon that covers the
wrong carriageway produces a confident, wrong answer. Run this after touching
jam.REGIONS and actually look at the output.

    python tools/roi_preview.py [output_dir]

Requires pillow (see requirements-dev.txt). Not imported by the app.
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

import jam  # noqa: E402

FEED = "https://api.data.gov.sg/v1/transport/traffic-images"
UA = {"User-Agent": "busstop-app/1.0 (roi preview)"}
COLOURS = {jam.JOHOR: (255, 60, 60), jam.SINGAPORE: (60, 160, 255)}


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "roi-preview")
    out.mkdir(parents=True, exist_ok=True)

    feed = json.load(urllib.request.urlopen(urllib.request.Request(FEED, headers=UA), timeout=20))
    frames = {c["camera_id"]: c["image"] for c in feed["items"][0]["cameras"]}

    for cam_id, regions in jam.REGIONS.items():
        url = frames.get(cam_id)
        if not url:
            print("  %s not in feed" % cam_id)
            continue
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25).read()
        path = out / ("roi-%s.png" % cam_id)
        render(raw, regions, path)

        # Report what the analyser currently measures in each region, so the
        # numbers can be sanity-checked against what the picture shows.
        readings = jam.analyse_camera(cam_id, [(0.0, raw)])
        bits = ["%s occ=%.3f" % (d, r["occupancy"]) for d, r in sorted(readings.items())]
        print("  %s -> %s  (%s)" % (cam_id, path.name, ", ".join(bits)))


def render(raw, regions, path, width=960):
    from io import BytesIO

    with Image.open(BytesIO(raw)) as im:
        im = im.convert("RGB")
        height = int(width * im.height / im.width)
        im = im.resize((width, height))

    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for direction, poly in regions.items():
        pts = [(x * width, y * height) for x, y in poly]
        colour = COLOURS.get(direction, (0, 255, 0))
        draw.polygon(pts, fill=colour + (90,), outline=colour + (255,))
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        draw.text((cx - 24, cy), direction.upper(), fill=(255, 255, 255, 255))

    Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB").save(path)


if __name__ == "__main__":
    main()
