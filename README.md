# Go JB?

A local dashboard that answers one question: **is the jam at the checkpoint bad
enough that I should not go to Johor right now?**

It puts the Woodlands and Tuas crossings side by side, using the live LTA
traffic cameras from [data.gov.sg](https://data.gov.sg), plus rainfall and the
two-hour forecast for each checkpoint. No account, no API key.

## Why this exists

Checking this today means opening one camera at a time on a site that shows you
every camera in Singapore, working out which of them point at the checkpoint,
and remembering which direction each faces. The decision you actually want to
make is a comparison — *Woodlands or Tuas, or not at all* — and nothing presents
it that way.

So this app does three things:

1. **Shows only the six cameras that matter**, grouped by crossing and ordered
   from furthest upstream to the bridge itself. If the queue reaches the
   upstream camera, it is genuinely bad.
2. **Adds rain**, because a wet queue moves slower than it looks. Rainfall now
   and the two-hour forecast are matched to the nearest station and forecast
   area by distance from each checkpoint.
3. **Keeps recent frames** so you can see whether the queue is building or
   clearing, rather than guessing from one snapshot.

## What it deliberately does not do

It does not score the jam or tell you to go. Judging congestion from a traffic
photo is something you do better than a heuristic — especially at night, when
the camera view is mostly headlights. Inventing a "congestion: 7/10" number
from these images would look authoritative and be wrong. The app's job is to
put the right evidence in front of you fast.

## Run it

Python 3.8 or newer. No dependencies — standard library only.

```
python app.py
```

Then open <http://localhost:8765> (it opens a browser for you). On Windows you
can double-click `run.bat`.

Useful flags:

```
python app.py --port 9000        # different port
python app.py --host 0.0.0.0     # reach it from your phone on the same wifi
python app.py --no-open          # don't launch a browser
```

In the UI: click any frame to enlarge it, press `r` to refresh, and use
**Compare with earlier** on a camera to see its oldest held frame beside the
current one. The page refreshes every 30 seconds and pauses while the tab is in
the background.

## The cameras

Camera captions were verified by looking at the frames rather than trusting
camera IDs: LTA burns direction labels into every image, so the captions match
what you actually see.

| Crossing | Upstream | Checkpoint approach | Bridge |
| --- | --- | --- | --- |
| Woodlands / Causeway | `2704` BKE, Woodlands Ave 3 | `2702` BKE / Causeway | `2701` Woodlands / Johor |
| Tuas / Second Link | `4712` AYE, Johor / City | `4713` AYE / Johor | `4703` towards Johor |

The `2702` frame is the one that usually decides it — the Causeway-bound queue
forms on the right-hand carriageway. If that is stuck but `2701` (the bridge) is
moving, the delay is at immigration rather than on the road, which tends to mean
a long wait regardless of how clear the tarmac looks.

## How it works

```
browser  ->  app.py  ->  api.data.gov.sg      (camera list + weather, JSON)
                     ->  images.data.gov.sg   (the JPEG frames)
```

`app.py` is a proxy as well as a static file server, for two reasons found by
testing the endpoints directly:

* data.gov.sg sends **no CORS headers**, so browser JavaScript cannot fetch the
  camera feed itself.
* `images.data.gov.sg` returns **HTTP 403 without a `User-Agent` header**, and
  serves the JPEGs as `application/octet-stream`, which the proxy relabels as
  `image/jpeg` so the browser renders them.

The camera feed publishes a new frame set roughly every 20 seconds, about 23
seconds behind real time, so frames under three minutes old are labelled fresh.
Feeds are cached for 20 seconds and up to 12 frames per camera are held in
memory for the comparison view.

Each feed can fail independently: if the weather calls fail you still get the
cameras, with a warning banner, and cached frames keep showing if the camera
feed drops.

## Layout

| Path | What it is |
| --- | --- |
| `app.py` | Local HTTP server, feed caching, image proxy and history |
| `checkpoints.py` | Pure logic: camera catalogue, nearest-station matching, freshness, payload shaping |
| `static/` | The page — `index.html`, `style.css`, `app.js` |
| `tests/` | Offline unit tests over `checkpoints.py` |

`checkpoints.py` holds no network code so the whole data-shaping layer is
testable offline. Fixtures in the tests are trimmed from real API responses, so
the field names under test match the live feeds.

```
python -m unittest discover -s tests -v
```

CI runs the same suite on every push and pull request.

## Data sources

* LTA traffic images via data.gov.sg — `/v1/transport/traffic-images`
* NEA rainfall via data.gov.sg — `/v2/real-time/api/rainfall`
* NEA two-hour forecast via data.gov.sg — `/v2/real-time/api/two-hr-forecast`

Camera images are © Land Transport Authority, served through data.gov.sg under
the [Singapore Open Data Licence](https://data.gov.sg/open-data-licence).
