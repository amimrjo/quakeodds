"use strict";

/* ---------- helpers ---------- */
const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const debounce = (fn, ms) => {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
};

/* ---------- API ---------- */
class ApiClient {
  async suggest(query, signal) {
    const res = await fetch(`/api/autocomplete?q=${encodeURIComponent(query)}`, { signal });
    if (!res.ok) throw new Error("Place search is unavailable right now.");
    return (await res.json()).results;
  }

  async hazard({ latitude, longitude, name, detail }, radius) {
    const params = new URLSearchParams({ lat: latitude, lon: longitude, name: name || "", detail: detail || "", radius });
    const res = await fetch(`/api/hazard?${params}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status}).`);
    return data;
  }
}

/* ---------- Autocomplete dropdown ---------- */
class Autocomplete {
  constructor(input, list, api, onSelect) {
    this.input = input;
    this.list = list;
    this.api = api;
    this.onSelect = onSelect;
    this.items = [];
    this.active = -1;
    this.controller = null;
    this.selected = null;
    this.cache = new Map();  // query -> results, so retyping/backspacing is instant

    this.fetchDebounced = debounce((q) => this.fetch(q), 150);
    input.addEventListener("input", () => this.handleInput());
    input.addEventListener("keydown", (e) => this.handleKey(e));
    input.addEventListener("focus", () => this.items.length && this.open());
    list.addEventListener("mousedown", (e) => e.preventDefault()); // keep focus in the input
    list.addEventListener("click", (e) => {
      const li = e.target.closest("li[data-index]");
      if (li) this.choose(Number(li.dataset.index));
    });
    document.addEventListener("click", (e) => {
      if (!this.input.parentElement.contains(e.target)) this.close();
    });
  }

  handleInput() {
    this.selected = null;
    const q = this.input.value.trim();
    if (q.length < 2) { this.items = []; this.close(); return; }

    const key = q.toLowerCase();
    if (this.cache.has(key)) {           // seen before: show immediately
      this.items = this.cache.get(key);
      this.render(q);
      return;
    }
    // Show something right away while the request is in flight. Keep the
    // previous suggestions visible (they usually still fit) and add a status row.
    this.renderLoading();
    this.fetchDebounced(q);
  }

  async fetch(query) {
    this.controller?.abort();          // drop the older, slower request
    this.controller = new AbortController();
    try {
      const results = await this.api.suggest(query, this.controller.signal);
      this.cache.set(query.toLowerCase(), results);
      if (query !== this.input.value.trim()) return;  // user kept typing
      this.items = results;
      this.render(query);
    } catch (err) {
      if (err.name !== "AbortError") { this.items = []; this.renderMessage(err.message); }
    }
  }

  render(query) {
    this.active = -1;
    if (!this.items.length) { this.renderMessage(`No places match “${query}”.`); return; }
    const pattern = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "ig");
    this.list.innerHTML = this.items.map((loc, i) => {
      const name = escapeHtml(loc.name).replace(pattern, "<mark>$1</mark>");
      return `<li id="opt-${i}" role="option" aria-selected="false" data-index="${i}">
                ${name}<span class="sub">${escapeHtml(loc.detail || loc.kind)}</span></li>`;
    }).join("");
    this.open();
  }

  renderLoading() {
    this.list.querySelector(".loading")?.remove();
    if (!this.items.length) this.list.innerHTML = "";
    this.list.insertAdjacentHTML("beforeend", `<li class="empty loading" aria-disabled="true">Searching places…</li>`);
    this.open();
  }

  renderMessage(text) {
    this.list.innerHTML = `<li class="empty" aria-disabled="true">${escapeHtml(text)}</li>`;
    this.open();
  }

  handleKey(e) {
    const visible = !this.list.hidden && this.items.length;
    if (e.key === "ArrowDown" && visible) { e.preventDefault(); this.highlight(this.active + 1); }
    else if (e.key === "ArrowUp" && visible) { e.preventDefault(); this.highlight(this.active - 1); }
    else if (e.key === "Enter" && visible && this.active >= 0) { e.preventDefault(); this.choose(this.active); }
    else if (e.key === "Escape") { this.close(); }
  }

  highlight(index) {
    const n = this.items.length;
    this.active = (index + n) % n;
    [...this.list.children].forEach((li, i) => li.setAttribute("aria-selected", String(i === this.active)));
    const current = this.list.children[this.active];
    current?.scrollIntoView({ block: "nearest" });
    this.input.setAttribute("aria-activedescendant", current?.id || "");
  }

  choose(index) {
    const loc = this.items[index];
    if (!loc) return;
    this.selected = loc;
    this.input.value = loc.detail ? `${loc.name}, ${loc.detail}` : loc.name;
    this.close();
    this.onSelect(loc);
  }

  /** Best guess when the user presses "Check odds" without picking from the list. */
  firstMatch() { return this.selected || this.items[0] || null; }

  open() { this.list.hidden = false; this.input.setAttribute("aria-expanded", "true"); }
  close() {
    this.list.hidden = true;
    this.input.setAttribute("aria-expanded", "false");
    this.input.removeAttribute("aria-activedescendant");
  }
}

/* ---------- Seismogram drum ---------- */
class Seismogram {
  static WIDTH = 1200;
  static HEIGHT = 200;

  constructor(svg) {
    this.svg = svg;
    svg.setAttribute("viewBox", `0 0 ${Seismogram.WIDTH} ${Seismogram.HEIGHT}`);
    this.drawIdle();
  }

  drawIdle() {
    this.draw([], 30);
    this.svg.setAttribute("aria-label", "Idle seismogram trace");
  }

  /** Each quake becomes a decaying burst at its time position; bigger quakes swing wider. */
  draw(events, days) {
    const { WIDTH: W, HEIGHT: H } = Seismogram;
    const mid = H / 2;
    const now = Date.now();
    const span = days * 86400000;
    const bursts = events.map((q) => ({
      x: W * (1 - (now - new Date(q.time).getTime()) / span),
      amp: (mid - 6) * Math.min(1, Math.pow(Math.max(q.magnitude, 0) / 7.5, 1.7)),
      decay: 6 + q.magnitude * 5,
    }));

    let seed = 7;
    const noise = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647 - 0.5; };
    const pts = [];
    for (let x = 0; x <= W; x += 1.5) {
      let y = noise() * 2.2;
      for (const b of bursts) {
        const dx = x - b.x;
        if (dx >= 0 && dx < b.decay * 6) y += b.amp * Math.exp(-dx / b.decay) * Math.sin(dx * 1.9) * (0.7 + noise() * 0.6);
      }
      pts.push(`${x.toFixed(1)},${(mid - Math.max(-mid + 3, Math.min(mid - 3, y))).toFixed(1)}`);
    }
    this.svg.innerHTML = `<polyline class="trace" points="${pts.join(" ")}"/>`;
    const line = this.svg.firstChild;
    const len = line.getTotalLength?.() || 4000;
    line.style.setProperty("--len", len);
    line.classList.add("drawing");
    this.svg.setAttribute("aria-label", `Seismogram of ${events.length} earthquakes in the last ${days} days`);
  }
}

/* ---------- Map ---------- */
class QuakeMap {
  constructor(el) {
    this.el = el;
    this.map = null;
    this.layer = null;
  }

  ensure() {
    if (this.map || typeof L === "undefined") return;
    // Give the map a starting view right away; Leaflet can't place layers until it has one.
    this.map = L.map(this.el, { scrollWheelZoom: false }).setView([20, 0], 2);
    const dark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
    this.ink = dark ? "#e3e8e5" : "#1d2b36";
    // OpenStreetMap's standard tiles need no API key. In dark mode, CSS inverts them.
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(this.map);
    this.layer = L.layerGroup().addTo(this.map);
  }

  show(report) {
    this.ensure();
    if (!this.map) return;
    this.layer.clearLayers();
    const { latitude: lat, longitude: lon } = report.location;
    const center = L.latLng(lat, lon);
    const radiusM = report.radius_km * 1000;

    // Size the map to its box and zoom to the search area BEFORE adding layers.
    this.map.invalidateSize();
    this.map.fitBounds(center.toBounds(radiusM * 2), { padding: [12, 12] });

    L.circle(center, { radius: radiusM, color: this.ink, weight: 2, fill: false, dashArray: "6 6" }).addTo(this.layer);
    L.circleMarker(center, { radius: 6, color: this.ink, fillColor: this.ink, fillOpacity: 1 })
      .bindTooltip(escapeHtml(report.location.name)).addTo(this.layer);

    for (const q of report.recent_events) {
      L.circleMarker([q.latitude, q.longitude], {
        radius: 3 + Math.max(q.magnitude, 0) ** 1.5,
        color: "#1d2b36", weight: 1, fillColor: ReportView.magColor(q.magnitude), fillOpacity: 0.85,
      }).bindPopup(`<strong>M${q.magnitude.toFixed(1)}</strong> ${escapeHtml(q.place)}<br>${new Date(q.time).toLocaleString()}<br>
          ${q.depth_km.toFixed(0)} km deep, ${q.distance_km.toFixed(0)} km away${q.url ? `<br><a href="${escapeHtml(q.url)}" target="_blank" rel="noopener">USGS event page</a>` : ""}`)
        .addTo(this.layer);
    }
    // The page layout can still be settling on first show; re-measure once it has.
    requestAnimationFrame(() => this.map.invalidateSize());
  }
}

/* ---------- Report rendering ---------- */
class ReportView {
  static RISK_COLORS = { "Very high": "var(--h4)", High: "var(--h3)", Moderate: "var(--h2)", Low: "var(--h1)", "Very low": "var(--grid)" };
  static MAG_LABELS = { 4: "felt widely", 5: "can crack walls", 6: "can be destructive", 7: "major" };

  static probColor(p) {
    if (p < 0.01) return ["var(--h0)", "var(--ink)"];
    if (p < 0.10) return ["var(--h1)", "#1d2b36"];
    if (p < 0.35) return ["var(--h2)", "#1d2b36"];
    if (p < 0.70) return ["var(--h3)", "#fff"];
    return ["var(--h4)", "#fff"];
  }

  static magColor(m) {
    if (m < 2.5) return "#f3e37c";
    if (m < 4) return "#f0a14b";
    if (m < 5.5) return "#d9462e";
    return "#7a2350";
  }

  static pct(p) {
    if (p <= 0) return "0%";
    if (p < 0.001) return "<0.1%";
    if (p > 0.995) return ">99%";
    return p < 0.1 ? `${(p * 100).toFixed(1)}%` : `${Math.round(p * 100)}%`;
  }

  static period(years) {
    if (!years) return "none on record";
    if (years < 1) return `about ${Math.round(1 / years)}× a year`;
    if (years > 10000) return "rarer than once in 10,000 yrs";
    return `about every ${years < 10 ? years.toFixed(1) : Math.round(years).toLocaleString()} yrs`;
  }

  static relTime(iso) {
    const mins = (Date.now() - new Date(iso).getTime()) / 60000;
    if (mins < 60) return `${Math.max(1, Math.round(mins))} min ago`;
    if (mins < 1440) return `${Math.round(mins / 60)} h ago`;
    return `${Math.round(mins / 1440)} d ago`;
  }

  constructor(root, map) {
    this.root = root;
    this.map = map;
    this.$ = (id) => root.querySelector(`#${id}`);
  }

  render(r) {
    this.root.hidden = false;
    this.$("place-name").textContent = r.location.name;
    this.$("place-detail").textContent = r.location.detail || `${r.location.latitude.toFixed(3)}, ${r.location.longitude.toFixed(3)}`;
    this.$("risk-level").textContent = `${r.risk_level} risk`;
    this.$("risk-level").style.setProperty("--risk", ReportView.RISK_COLORS[r.risk_level] || "var(--ink)");
    this.$("risk-summary").textContent = r.risk_summary;
    this.$("radius-label").textContent = `${r.radius_km} km`;
    this.renderMatrix(r);
    this.renderRecent(r.recent_events);
    this.renderMethod(r);
    this.map.show(r);
  }

  renderMatrix(r) {
    const horizons = Object.keys(r.estimates[0]?.probabilities || {});
    const head = `<thead><tr><th>Magnitude</th>${horizons.map((h) => `<th scope="col">next ${h === "1" ? "year" : `${h} years`}</th>`).join("")}</tr></thead>`;
    const rows = r.estimates.map((e) => {
      const cells = horizons.map((h) => {
        const p = e.probabilities[h];
        const [bg, fg] = ReportView.probColor(p);
        return `<td><span class="cell" style="--c:${bg};--t:${fg}" title="${(p * 100).toFixed(2)}%">${ReportView.pct(p)}</span></td>`;
      }).join("");
      const label = ReportView.MAG_LABELS[Math.round(e.magnitude)] || "";
      return `<tr><th scope="row">M${e.magnitude}+<small>${label}; ${ReportView.period(e.return_period_years)}</small></th>${cells}</tr>`;
    }).join("");
    this.$("matrix").innerHTML = head + `<tbody>${rows}</tbody>`;
  }

  renderRecent(events) {
    const list = this.$("recent-list");
    if (!events.length) { list.innerHTML = `<li><span></span><span class="none">No earthquakes recorded nearby in the last 30 days.</span></li>`; return; }
    list.innerHTML = events.slice(0, 40).map((q) => `
      <li><span class="mag">${q.magnitude.toFixed(1)}</span>
        <span><span class="where">${escapeHtml(q.place)}</span>
        <span class="when">${ReportView.relTime(q.time)}, ${q.distance_km.toFixed(0)} km away, ${q.depth_km.toFixed(0)} km deep</span></span></li>`).join("");
  }

  renderMethod(r) {
    const m = r.model;
    const big = r.largest_event;
    const facts = [
      ["Quakes analysed", `${r.catalog_size.toLocaleString()} over ${r.effective_years.toFixed(1)} years`],
      ["Reliably recorded from", m.completeness_magnitude != null ? `M${m.completeness_magnitude.toFixed(1)}` : "n/a"],
      ["Magnitude falloff (b-value)", `${m.b_value.toFixed(2)}${m.b_uncertainty ? ` ± ${m.b_uncertainty.toFixed(2)}` : ""}${m.fitted_from_data ? "" : " (global default)"}`],
      ["Largest on record", big ? `M${big.magnitude.toFixed(1)}, ${new Date(big.time).getFullYear()}, ${big.distance_km.toFixed(0)} km away` : "none"],
      ["Data fetched", new Date(r.generated_at).toLocaleString()],
    ];
    this.$("model-facts").innerHTML = facts.map(([k, v]) => `<dt>${k}</dt><dd>${escapeHtml(v)}</dd>`).join("");
    this.$("notes").innerHTML = r.notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("");
  }
}

/* ---------- App controller ---------- */
class QuakeOddsApp {
  constructor() {
    this.api = new ApiClient();
    this.input = document.getElementById("place-input");
    this.radius = document.getElementById("radius-select");
    this.button = document.getElementById("check-btn");
    this.status = document.getElementById("status");
    this.seismogram = new Seismogram(document.getElementById("seismogram"));
    this.view = new ReportView(document.getElementById("report"), new QuakeMap(document.getElementById("map")));
    this.autocomplete = new Autocomplete(this.input, document.getElementById("suggestions"), this.api, (loc) => this.check(loc));
    this.current = null;

    document.getElementById("search-form").addEventListener("submit", (e) => { e.preventDefault(); this.submit(); });
    document.getElementById("locate-btn").addEventListener("click", () => this.locate());
    this.radius.addEventListener("change", () => this.current && this.check(this.current));
  }

  submit() {
    const loc = this.autocomplete.firstMatch();
    if (!loc) { this.setStatus("Type a place and pick it from the list.", true); return; }
    this.autocomplete.close();
    this.check(loc);
  }

  locate() {
    if (!navigator.geolocation) { this.setStatus("Your browser can't share its location.", true); return; }
    this.setStatus("Finding your location…");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const loc = { name: "Your location", detail: "", latitude: pos.coords.latitude, longitude: pos.coords.longitude };
        this.input.value = "Your location";
        this.check(loc);
      },
      () => this.setStatus("Location access was blocked. Search for a place instead.", true),
      { timeout: 10000 },
    );
  }

  async check(loc) {
    this.current = loc;
    this.button.disabled = true;
    this.setStatus(`Reading ${this.radius.value} km of quake history around ${loc.name} from the USGS…`);
    try {
      const report = await this.api.hazard(loc, this.radius.value);
      if (this.current !== loc) return;  // a newer search started meanwhile
      this.view.render(report);
      this.seismogram.draw(report.recent_events, 30);
      this.setStatus("");
    } catch (err) {
      this.setStatus(err.message, true);
    } finally {
      this.button.disabled = false;
    }
  }

  setStatus(text, isError = false) {
    this.status.textContent = text;
    this.status.classList.toggle("error", isError);
  }
}

document.addEventListener("DOMContentLoaded", () => new QuakeOddsApp());