"use strict";

const REFRESH_MS = 30000;

const board = document.getElementById("board");
const warnBox = document.getElementById("warnings");
const clock = document.getElementById("clock");
const lightbox = document.getElementById("lightbox");
const lightboxImg = document.getElementById("lightbox-img");
const lightboxCap = document.getElementById("lightbox-cap");

// Cameras the user has expanded into before/after comparison.
const comparing = new Set();
let timer = null;

function timeOf(iso) {
  if (!iso) return "unknown time";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleTimeString("en-SG", { hour12: false });
}

function ageText(seconds) {
  if (seconds === null || seconds === undefined) return "age unknown";
  if (seconds < 90) return seconds + "s ago";
  return Math.round(seconds / 60) + " min ago";
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function weatherChips(crossing) {
  const wrap = el("div", "weather");

  const rain = crossing.rainfall;
  if (rain) {
    const mm = typeof rain.mm === "number" ? rain.mm : null;
    const label = mm === null
      ? "rain: no reading"
      : (mm > 0 ? "raining now: " + mm + " mm" : "dry now");
    const chip = el("span", "chip" + (mm > 0 ? " wet" : ""), label);
    chip.title = rain.station + " station, " + rain.km + " km away";
    wrap.appendChild(chip);
  }

  const fc = crossing.forecast;
  if (fc) {
    const wet = /rain|shower|thunder/i.test(fc.forecast || "");
    const chip = el("span", "chip" + (wet ? " wet" : ""), fc.area + ": " + fc.forecast);
    chip.title = fc.period ? "Forecast for " + fc.period : "Two-hour forecast";
    wrap.appendChild(chip);
  }

  return wrap;
}

function cameraCard(cam) {
  const card = el("section", "cam");

  const head = el("div", "cam-head");
  head.appendChild(el("span", "step", String(cam.step)));
  head.appendChild(el("span", "cam-title", cam.title));
  head.appendChild(el("span", "cam-detail", cam.detail));
  head.appendChild(el("span", "chip " + cam.freshness, ageText(cam.age_seconds)));
  card.appendChild(head);

  if (!cam.available) {
    card.appendChild(el("p", "cam-missing",
      "No frame retrieved yet for camera " + cam.id + ". It will appear on the next refresh."));
    return card;
  }

  const history = cam.history || [];
  const isComparing = comparing.has(cam.id) && history.length > 1;

  const shots = el("div", "shots" + (isComparing ? " compare" : ""));
  const newest = history.length ? history[history.length - 1] : cam.timestamp;

  if (isComparing) {
    shots.appendChild(shotFigure(cam, history[0], "earliest held frame"));
  }
  shots.appendChild(shotFigure(cam, newest, "now"));
  card.appendChild(shots);

  const foot = el("div", "cam-foot");
  foot.appendChild(el("p", "hint", cam.hint));

  const btn = el("button", "link-btn");
  btn.type = "button";
  if (history.length > 1) {
    btn.textContent = isComparing ? "Hide comparison" : "Compare with earlier";
    btn.addEventListener("click", () => {
      if (comparing.has(cam.id)) comparing.delete(cam.id);
      else comparing.add(cam.id);
      load();
    });
  } else {
    btn.textContent = "Comparison builds as it runs";
    btn.disabled = true;
  }
  foot.appendChild(btn);
  card.appendChild(foot);

  return card;
}

function shotFigure(cam, ts, label) {
  const fig = el("figure", "shot");
  const img = el("img");
  img.src = "/img/" + cam.id + (ts ? "?ts=" + encodeURIComponent(ts) : "");
  img.alt = cam.title + ", " + cam.detail;
  img.loading = "lazy";
  const caption = cam.title + " — " + label + " · " + timeOf(ts);
  img.addEventListener("click", () => openLightbox(img.src, caption));
  fig.appendChild(img);
  fig.appendChild(el("figcaption", null, label + " · " + timeOf(ts)));
  return fig;
}

function crossingPanel(crossing) {
  const panel = el("section", "crossing");

  const head = el("div", "crossing-head");
  const title = el("div", "crossing-title");
  title.appendChild(el("h2", null, crossing.name));
  title.appendChild(el("span", "road", crossing.road));
  head.appendChild(title);
  head.appendChild(el("p", "crossing-note", crossing.note));
  head.appendChild(weatherChips(crossing));

  if (crossing.wet) {
    head.appendChild(el("p", "rain-note",
      "Rain in the area — expect the queue to move slower than it looks."));
  }
  panel.appendChild(head);

  const cams = el("div", "cams");
  crossing.cameras.forEach((cam) => cams.appendChild(cameraCard(cam)));
  panel.appendChild(cams);

  return panel;
}

function openLightbox(src, caption) {
  lightboxImg.src = src;
  lightboxCap.textContent = caption;
  lightbox.hidden = false;
}

function closeLightbox() {
  lightbox.hidden = true;
  lightboxImg.src = "";
}

function render(state) {
  board.replaceChildren(...state.crossings.map(crossingPanel));

  if (state.warnings && state.warnings.length) {
    warnBox.replaceChildren(...state.warnings.map((w) => el("p", null, w)));
    warnBox.hidden = false;
  } else {
    warnBox.hidden = true;
  }

  clock.textContent = "feed " + timeOf(state.feed_timestamp);
}

async function load() {
  try {
    const resp = await fetch("/api/state", { cache: "no-store" });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    render(await resp.json());
  } catch (err) {
    warnBox.replaceChildren(el("p", null,
      "Could not reach the local server (" + err.message + "). Is app.py still running?"));
    warnBox.hidden = false;
  }
}

function schedule() {
  if (timer) clearInterval(timer);
  timer = setInterval(() => {
    if (!document.hidden) load();
  }, REFRESH_MS);
}

document.getElementById("refresh").addEventListener("click", load);
lightbox.querySelector(".lightbox-close").addEventListener("click", closeLightbox);
lightbox.addEventListener("click", (e) => {
  if (e.target === lightbox) closeLightbox();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeLightbox();
  if (e.key === "r" && !e.metaKey && !e.ctrlKey) load();
});
// Catch up immediately when the tab comes back into focus.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) load();
});

load();
schedule();
