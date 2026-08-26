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

## The call to action

The top of the page answers the two questions directly, one card each:

* **To Johor** — go now, go expecting traffic, or wait; and which crossing.
* **To Singapore** — the same, for coming home.

Each card names the crossing to use, the one to avoid, and why. Below it sits the
per-crossing breakdown and the frames themselves, so the headline is always
traceable to a picture you can check.

### How the verdict is reached, and why it is not a threshold

Congestion is measured per carriageway, not per camera. Each direction has a
polygon drawn down the middle of its own lane, so "towards the Causeway" and
"towards BKE" are scored separately instead of being averaged into one useless
number for the whole frame.

The measure is the fraction of that strip departing from the road surface —
roughly, how much metal is sitting on the tarmac. That number is **not**
comparable between cameras. Every region carries a constant contribution from
whatever fixed clutter is in it: lamp posts, guard rails, fences, lane paint,
tree shadows. Measured on real frames, an empty Tuas carriageway scored 0.390
while a dense stationary queue on the Causeway scored 0.363 — the empty road
looked busier. An absolute threshold cannot work.

So each region is scored against **its own history** instead, because the lamp
post is in every one of those readings too and cancels out. The question becomes
"is this high for this region at this time of week", which history can answer,
rather than "is 0.36 busy", which it cannot. Comparison prefers readings from a
similar hour and day type, so rush hour is judged against rush hour, falling
back to all hours when there are not yet enough.

Until a region has enough readings it reports **learning** rather than guessing.
That is the honest state on a fresh clone, and it resolves itself as the
scheduled snapshot accumulates history.

### What it still will not do

It will not claim a verdict on a dark frame. After sunset these cameras are
mostly headlights and the measure means nothing, so it says so instead.

## Published page

<https://jethrophuah.github.io/busstop-app/>

A snapshot rebuilt on a schedule, showing the verdict beside the exact frames it
was computed from. It lags real time by roughly ten to twenty minutes, because
GitHub's scheduled runners are rate limited and routinely late; the page states
its own build time so a stale run is visible rather than hidden.

It is a snapshot rather than a live page for a concrete reason. The JSON feeds
send `Access-Control-Allow-Origin: *`, so a static page can fetch those. The
camera images send no CORS headers at all, so while a browser will happily
display them, reading their pixels through a canvas throws a security error. A
static page therefore cannot analyse the frames. The scheduled job does the
analysis server-side and publishes the result.

For a live read, run it locally.

## Run it locally

Python 3.8 or newer, plus pillow and numpy for the frame analysis.

```
pip install -r requirements.txt
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

`app.py` is a proxy as well as a static file server, for reasons found by
testing the endpoints directly:

* The **camera images send no CORS headers**, so their pixels cannot be read
  from a canvas. Proxying them makes them same-origin and lets the analysis run
  in Python, shared with the published snapshot.
* `images.data.gov.sg` **rejects some User-Agent strings with HTTP 403** —
  including urllib's default — and serves the JPEGs as
  `application/octet-stream`, which the proxy relabels as `image/jpeg` so the
  browser renders them.
* The JSON feeds *do* send `Access-Control-Allow-Origin: *`. Those headers only
  appear when a request carries an `Origin`, which is why a plain `curl -I`
  makes it look as though they are missing.

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
| `app.py` | Local HTTP server, feed caching, image proxy and frame history |
| `checkpoints.py` | Camera catalogue, nearest-station matching, freshness, payload shaping |
| `jam.py` | Measurement only: direction polygons, masking, occupancy, motion |
| `baseline.py` | Self-calibrating scoring against a region's own history |
| `verdict.py` | Maps regions onto routes and words the call to action |
| `snapshot.py` | Builds the published site into `build/` and grows the baseline |
| `static/` | The local page — `index.html`, `style.css`, `app.js` |
| `web/index.html` | The published page's shell; shares the same JS and CSS |
| `data/history.json` | The committed baseline, appended to by each snapshot run |
| `tools/` | `roi_preview.py` draws the regions; `calibrate.py` prints raw measures |
| `tests/` | Offline unit tests, no network |

`checkpoints.py` holds no network code and `jam.py` holds no thresholds: it
measures, and `baseline.py` judges. Both splits are what make the logic testable
offline. Fixtures in the tests are trimmed from real API responses, so the field
names under test match the live feeds.

### Checking the regions

The verdict is only as good as the polygons, and one covering the wrong
carriageway gives a confident wrong answer. After changing `jam.REGIONS`, render
them over live frames and actually look:

```
python tools/roi_preview.py roi-preview
```

```
python -m unittest discover -s tests -v
```

CI runs the same suite on every push and pull request, on Python 3.9, 3.11 and
3.12, plus a server smoke test. The tests cover the scoring rules, the region
geometry (including that opposing directions never overlap), degraded feeds, and
two browser-only mistakes that Python tests miss.

## Data sources

* LTA traffic images via data.gov.sg — `/v1/transport/traffic-images`
* NEA rainfall via data.gov.sg — `/v2/real-time/api/rainfall`
* NEA two-hour forecast via data.gov.sg — `/v2/real-time/api/two-hr-forecast`

Camera images are © Land Transport Authority, served through data.gov.sg under
the [Singapore Open Data Licence](https://data.gov.sg/open-data-licence).
