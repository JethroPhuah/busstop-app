"""Local server for the Woodlands / Tuas checkpoint dashboard.

Why a server at all, rather than a single HTML file opened from disk:

  * data.gov.sg sends no CORS headers, so browser JavaScript cannot fetch the
    traffic-camera feed directly. The JSON has to be proxied.
  * images.data.gov.sg returns 403 to requests without a User-Agent header,
    so the image fetches have to be proxied too.

Standard library only - no pip install required.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen

import checkpoints

TRAFFIC_URL = "https://api.data.gov.sg/v1/transport/traffic-images"
RAINFALL_URL = "https://api-open.data.gov.sg/v2/real-time/api/rainfall"
FORECAST_URL = "https://api-open.data.gov.sg/v2/real-time/api/two-hr-forecast"

# images.data.gov.sg rejects the default urllib User-Agent with HTTP 403.
USER_AGENT = "busstop-app/1.0 (local checkpoint dashboard)"

STATIC_DIR = Path(__file__).parent / "static"

# The camera feed advances roughly once a minute; don't hammer it.
FEED_TTL = 20.0
# Snapshots kept per camera so the UI can show "is the queue building?".
HISTORY_LIMIT = 12

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


def fetch_json(url, timeout=15):
    """GET and parse JSON, returning None on any network or decode failure."""
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (URLError, HTTPError, ValueError, OSError):
        return None


def fetch_bytes(url, timeout=20):
    """GET raw bytes, returning None on failure."""
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (URLError, HTTPError, OSError):
        return None


class Store:
    """Caches the upstream feeds and keeps a short image history per camera."""

    def __init__(self):
        self._lock = threading.Lock()
        self._fetched_at = 0.0
        self._traffic = None
        self._rain = None
        self._forecast = None
        # camera_id -> list of {"timestamp": iso, "bytes": jpeg}, oldest first
        self._images = {}

    def refresh(self, force=False):
        """Refresh feeds if the TTL has expired, then pull any new frames."""
        with self._lock:
            fresh_enough = (time.monotonic() - self._fetched_at) < FEED_TTL
            if fresh_enough and not force and self._traffic is not None:
                return

        # Fetch the three feeds concurrently; each may independently fail.
        with ThreadPoolExecutor(max_workers=3) as pool:
            traffic_f = pool.submit(fetch_json, TRAFFIC_URL)
            rain_f = pool.submit(fetch_json, RAINFALL_URL)
            forecast_f = pool.submit(fetch_json, FORECAST_URL)
            traffic, rain, forecast = traffic_f.result(), rain_f.result(), forecast_f.result()

        new_frames = self._plan_image_fetches(traffic)
        fetched = {}
        if new_frames:
            with ThreadPoolExecutor(max_workers=len(new_frames)) as pool:
                futures = {
                    cam_id: pool.submit(fetch_bytes, url)
                    for cam_id, (url, _ts) in new_frames.items()
                }
                for cam_id, fut in futures.items():
                    blob = fut.result()
                    if blob:
                        fetched[cam_id] = (new_frames[cam_id][1], blob)

        with self._lock:
            self._fetched_at = time.monotonic()
            if traffic is not None:
                self._traffic = traffic
            if rain is not None:
                self._rain = rain
            if forecast is not None:
                self._forecast = forecast
            for cam_id, (ts, blob) in fetched.items():
                shots = self._images.setdefault(cam_id, [])
                if not any(s["timestamp"] == ts for s in shots):
                    shots.append({"timestamp": ts, "bytes": blob})
                    del shots[:-HISTORY_LIMIT]

    def _plan_image_fetches(self, traffic):
        """Which camera frames are new since what we already hold."""
        if not traffic:
            return {}
        try:
            cams = traffic["items"][0]["cameras"]
        except (KeyError, IndexError, TypeError):
            return {}
        with self._lock:
            known = {
                cam_id: {s["timestamp"] for s in shots}
                for cam_id, shots in self._images.items()
            }
        plan = {}
        for cam in cams:
            cam_id = cam.get("camera_id")
            if cam_id not in checkpoints.CAMERAS_BY_ID:
                continue
            ts = cam.get("timestamp")
            if ts and ts not in known.get(cam_id, set()):
                plan[cam_id] = (cam.get("image"), ts)
        return plan

    def state(self):
        with self._lock:
            traffic, rain, forecast = self._traffic, self._rain, self._forecast
            history = {
                cam_id: [s["timestamp"] for s in shots]
                for cam_id, shots in self._images.items()
            }
        now = datetime.now(timezone.utc)
        return checkpoints.build_state(traffic, rain, forecast, now, history)

    def image(self, cam_id, ts=None):
        """Newest frame for a camera, or a specific historical one."""
        with self._lock:
            shots = list(self._images.get(cam_id, []))
        if not shots:
            return None
        if ts:
            for shot in shots:
                if shot["timestamp"] == ts:
                    return shot
            return None
        return shots[-1]


STORE = Store()


class Handler(BaseHTTPRequestHandler):
    server_version = "busstop-app"

    def log_message(self, fmt, *args):  # quieter console
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)

        if route == "/":
            self._serve_static("index.html")
        elif route == "/api/state":
            self._serve_state()
        elif route.startswith("/img/"):
            self._serve_image(route[len("/img/"):], query.get("ts", [None])[0])
        elif route.startswith("/static/"):
            self._serve_static(route[len("/static/"):])
        else:
            self.send_error(404, "Not found")

    def _serve_state(self):
        try:
            STORE.refresh()
        except Exception:  # a scrape failure must not take the page down
            pass
        body = json.dumps(STORE.state()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_image(self, cam_id, ts):
        if cam_id not in checkpoints.CAMERAS_BY_ID:
            self.send_error(404, "Unknown camera")
            return
        shot = STORE.image(cam_id, ts)
        if shot is None:
            self.send_error(404, "No frame yet")
            return
        self.send_response(200)
        # Upstream serves these as application/octet-stream; label them
        # correctly so the browser renders rather than downloads.
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(shot["bytes"])))
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        self.wfile.write(shot["bytes"])

    def _serve_static(self, name):
        # Resolve inside STATIC_DIR only - no traversal out of the directory.
        target = (STATIC_DIR / name).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self.send_error(403, "Forbidden")
            return
        if not target.is_file():
            self.send_error(404, "Not found")
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser(description="Woodlands / Tuas checkpoint dashboard")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1",
                    help="use 0.0.0.0 to reach it from your phone on the same wifi")
    ap.add_argument("--no-open", action="store_true", help="do not open a browser")
    args = ap.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = "http://{}:{}/".format("localhost" if args.host == "127.0.0.1" else args.host, args.port)
    print("Checkpoint dashboard running at {}".format(url))
    print("Press Ctrl+C to stop.")
    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
