// UFCC scroll feed with virtualization + draggable year timeline.
// Renders only rows visible in the viewport (plus a buffer) over an absolute
// canvas the height of the full dataset, so 5437 items scroll buttery smooth.

const ROW_H = 92;           // total per-row height incl. gap (px), keep in sync with CSS
const BUFFER = 8;           // rows to render above/below visible area

const $ = (sel) => document.querySelector(sel);

const feedEl = $("#feed");
const contentEl = $("#feed-content");
const spacerEl = $("#feed-spacer");
const timelineEl = $("#timeline");
const trackEl = $("#timeline-track");
const thumbEl = $("#timeline-thumb");
const yearLabelEl = $("#timeline-year");
const heroEl = $("#hero");
const heroCrestEl = $("#hero-crest");
const heroNameEl = $("#hero-name");
const heroDaysEl = $("#hero-days");
const heroSinceEl = $("#hero-since");
const miniEl = $("#mini-champ");
const miniCrestEl = $("#mini-crest");
const miniNameEl = $("#mini-name");
const miniDaysEl = $("#mini-days-n");
const loadingEl = $("#loading");

// ---- Mode (clubs = UFCC | teams = UFWC) ----------------------------------
// The mode is set on <html data-mode> before paint by an inline script in
// index.html. It selects the data folder and how "crests" are rendered:
// clubs use image files, national teams use flag emoji.
const TEAMS = document.documentElement.dataset.mode === "teams";
const DATA_DIR = TEAMS ? "data-ufwc" : "data";
const UNIT = TEAMS ? "team" : "club";
const UNITS = TEAMS ? "teams" : "clubs";
const ACCENT = TEAMS ? "#34d399" : "#f6c050";

let matches = [];        // raw rows, DESC by match_no
let clubs = {};          // name -> crest filename (clubs) or flag emoji (teams)
let years = [];          // [{year, first_index, count}]
let rendered = new Map();// index -> element (recycle pool)

// Inner HTML for a crest box: image (clubs), flag emoji (teams) or initials.
function crestInnerVal(name, val) {
  if (TEAMS) return val ? `<span class="flag">${val}</span>` : initials(name);
  return val ? `<img loading="lazy" src="crests/${val}" alt="">` : initials(name);
}
function crestInner(name) { return crestInnerVal(name, clubs[name]); }
function hasCrest(name) { return !!clubs[name]; }

const fmtDate = (iso) => {
  if (!iso) return ["—", ""];
  const [y, m, d] = iso.split("-");
  const months = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AGO","SEP","OCT","NOV","DIC"];
  return [Number(d), `${months[Number(m)-1]} ${y}`];
};

const crestUrl = (name) => clubs[name] ? `crests/${clubs[name]}` : null;
const initials = (name) =>
  name.split(/\s+/).filter(Boolean).slice(0, 2).map(s => s[0]).join("").toUpperCase();

function buildRow(idx) {
  const m = matches[idx];
  // shape: [match_no, date_iso, home, score, away, result, comp, venue, champion]
  const [no, date, home, score, away, result, comp, venue, champ] = m;
  const [d, my] = fmtDate(date);

  const prev = matches[idx + 1]; // older match -> previous champion
  const prevChamp = prev ? prev[8] : null;
  const newChampion = champ !== prevChamp;
  const homeWon = result === "H" || result === "W";
  const awayWon = result === "A";

  const el = document.createElement("article");
  el.className = "match" + (newChampion ? " has-new-champion" : "") +
    (newChampion && champ === home ? " is-home" : "") +
    (newChampion && champ === away ? " is-away" : "");
  el.style.transform = `translateY(${idx * ROW_H}px)`;

  el.innerHTML = `
    <div class="date">
      <div class="d">${d}</div>
      <div class="my">${my}</div>
      <div class="no">#${no}</div>
    </div>
    <div class="body">
      <div class="fixture">
        <div class="team home ${homeWon ? "winner" : awayWon ? "loser" : ""}">
          <div class="crest ${hasCrest(home) ? "" : "placeholder"}">
            ${crestInner(home)}
          </div>
          <div class="name">${escapeHtml(home)}</div>
        </div>
        <div class="score ${result === "D" ? "draw" : ""}">${escapeHtml(score)}</div>
        <div class="team away ${awayWon ? "winner" : homeWon ? "loser" : ""}">
          <div class="name">${escapeHtml(away)}</div>
          <div class="crest ${hasCrest(away) ? "" : "placeholder"}">
            ${crestInner(away)}
          </div>
        </div>
      </div>
      <div class="meta">
        ${comp ? `<span class="pill">${escapeHtml(comp)}</span>` : ""}
        ${venue ? `<span class="venue">${escapeHtml(venue)}</span>` : ""}
        ${newChampion
          ? `<span class="crown">→ ${escapeHtml(champ)}</span>`
          : `<span>retained: ${escapeHtml(champ)}</span>`}
      </div>
    </div>
  `;
  return el;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

let raf = 0;
function scheduleRender() {
  if (raf) return;
  raf = requestAnimationFrame(() => { raf = 0; renderVisible(); });
}

function renderVisible() {
  const feedTop = contentEl.getBoundingClientRect().top + window.scrollY;
  const scrollTop = Math.max(0, window.scrollY - feedTop);
  const viewportH = window.innerHeight;
  const firstIdx = Math.max(0, Math.floor(scrollTop / ROW_H) - BUFFER);
  const lastIdx  = Math.min(matches.length - 1,
                            Math.ceil((scrollTop + viewportH) / ROW_H) + BUFFER);

  // Remove rows outside window
  for (const [idx, el] of rendered) {
    if (idx < firstIdx || idx > lastIdx) {
      el.remove();
      rendered.delete(idx);
    }
  }
  // Add missing rows
  const frag = document.createDocumentFragment();
  for (let i = firstIdx; i <= lastIdx; i++) {
    if (!rendered.has(i)) {
      const el = buildRow(i);
      rendered.set(i, el);
      frag.appendChild(el);
    }
  }
  contentEl.appendChild(frag);
  updateYearLabel();
}

function updateYearLabel() {
  if (!years.length) return;
  const feedTop = contentEl.getBoundingClientRect().top + window.scrollY;
  const scrollTop = Math.max(0, window.scrollY - feedTop);
  const topRowIdx = Math.floor(scrollTop / ROW_H);
  let lo = 0, hi = years.length - 1, ans = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (years[mid].first_index <= topRowIdx) { ans = mid; lo = mid + 1; }
    else hi = mid - 1;
  }
  yearLabelEl.textContent = years[ans].year;
  const totalH = matches.length * ROW_H;
  const ratio = totalH > 0 ? scrollTop / totalH : 0;
  const trackH = timelineEl.clientHeight;
  const y = Math.max(8, Math.min(trackH - 8, ratio * trackH));
  thumbEl.style.top = `${y}px`;
}

function buildTimelineTicks() {
  // Pick ~12 evenly spaced year labels along the track.
  const N = Math.min(12, years.length);
  const step = Math.floor(years.length / N) || 1;
  const ticks = [];
  for (let i = 0; i < years.length; i += step) ticks.push(years[i]);
  if (ticks[ticks.length - 1] !== years[years.length - 1]) ticks.push(years[years.length - 1]);
  trackEl.innerHTML = ticks
    .map(t => `<div class="tick">${t.year}</div>`).join("");
}

function scrollToIndex(idx, smooth) {
  const feedTop = contentEl.getBoundingClientRect().top + window.scrollY;
  const top = feedTop + idx * ROW_H;
  window.scrollTo({ top, behavior: smooth ? "smooth" : "auto" });
}

function attachTimelineDrag() {
  let dragging = false;
  const setFromEvent = (e) => {
    const rect = timelineEl.getBoundingClientRect();
    const y = (e.touches ? e.touches[0].clientY : e.clientY) - rect.top;
    const ratio = Math.max(0, Math.min(1, y / rect.height));
    const targetIdx = Math.floor(ratio * (matches.length - 1));
    scrollToIndex(targetIdx, false);
  };
  const onDown = (e) => {
    dragging = true;
    timelineEl.classList.add("active");
    setFromEvent(e);
    e.preventDefault();
  };
  const onMove = (e) => { if (dragging) setFromEvent(e); };
  const onUp = () => { dragging = false; timelineEl.classList.remove("active"); };

  timelineEl.addEventListener("mousedown", onDown);
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
  timelineEl.addEventListener("touchstart", onDown, { passive: false });
  window.addEventListener("touchmove", onMove, { passive: false });
  window.addEventListener("touchend", onUp);
}

async function main() {
  applyModeChrome();
  const [m, c, y] = await Promise.all([
    fetch(`${DATA_DIR}/matches.json`).then(r => r.json()),
    fetch(`${DATA_DIR}/clubs.json`).then(r => r.json()),
    fetch(`${DATA_DIR}/years.json`).then(r => r.json()),
  ]);
  matches = m;
  clubs = c;
  years = y;

  spacerEl.style.height = "0";
  contentEl.style.height = `${matches.length * ROW_H}px`;

  setupChampionHero();
  buildTimelineTicks();
  attachTimelineDrag();

  window.addEventListener("scroll", scheduleRender, { passive: true });
  window.addEventListener("resize", scheduleRender);
  window.addEventListener("scroll", updateMiniChamp, { passive: true });
  miniEl.addEventListener("click", scrollToTopSmooth);
  updateMiniChamp();
  renderVisible();

  loadingEl.classList.add("hidden");
}

function setupChampionHero() {
  if (!matches.length) return;
  const champion = matches[0][8];
  // Walk forward (older matches) until champion changes -> that's the reign start.
  let reignStartIdx = 0;
  for (let i = 0; i < matches.length; i++) {
    if (matches[i][8] !== champion) break;
    reignStartIdx = i;
  }
  const reignStartMatch = matches[reignStartIdx];
  const startDateIso = reignStartMatch[1];
  const days = startDateIso ? daysSince(startDateIso) : 0;

  const crestHTML = crestInner(champion);
  heroCrestEl.innerHTML = crestHTML;
  if (!hasCrest(champion)) heroCrestEl.classList.add("placeholder");
  heroNameEl.textContent = champion;
  heroDaysEl.textContent = days.toLocaleString();

  if (startDateIso) {
    const won = reignStartMatch; // [no, date, home, score, away, result, ...]
    const opp = won[2] === champion ? won[4] : won[2];
    heroSinceEl.innerHTML =
      `Champion since <strong>${formatDateLong(startDateIso)}</strong> ` +
      `(${escapeHtml(won[3])} vs ${escapeHtml(opp)})`;
  }

  miniCrestEl.innerHTML = crestHTML;
  miniNameEl.textContent = champion;
  miniDaysEl.textContent = days.toLocaleString();

  loadNextMatch(champion);
}

async function loadNextMatch(champion) {
  const el = document.getElementById("hero-next");
  if (!el) return;
  let data;
  try {
    const res = await fetch(`${DATA_DIR}/next_match.json`, { cache: "no-cache" });
    if (!res.ok) { el.hidden = true; return; }
    data = await res.json();
  } catch (_) { el.hidden = true; return; }
  if (!data || data.champion !== champion) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }
  const kickoff = new Date(data.kickoff_utc);
  if (isNaN(kickoff.getTime())) return;
  const dateTxt = kickoff.toLocaleDateString(undefined, {
    weekday: "short", day: "numeric", month: "short",
  });
  const timeTxt = kickoff.toLocaleTimeString(undefined, {
    hour: "2-digit", minute: "2-digit",
  });
  const oppCrestHTML = TEAMS
    ? (hasCrest(data.opponent) ? crestInner(data.opponent) : "")
    : (data.opponent_crest ? `<img src="${data.opponent_crest}" alt="">` : "");
  const venueTxt = data.is_home ? "Home" : "Away";
  el.hidden = false;
  el.innerHTML = `
    <span class="next-label">Next match</span>
    <span class="next-vs">${venueTxt} vs</span>
    <span class="next-opp-crest">${oppCrestHTML}</span>
    <strong>${escapeHtml(data.opponent)}</strong>
    <span class="next-meta">${escapeHtml(dateTxt)} · ${escapeHtml(timeTxt)} · ${escapeHtml(data.competition || "TBD")}${data.venue ? " · " + escapeHtml(data.venue) : ""}</span>
  `;
}

function daysSince(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const start = Date.UTC(y, m - 1, d);
  const now = Date.now();
  return Math.max(0, Math.floor((now - start) / 86400000));
}

function formatDateLong(iso) {
  const [y, m, d] = iso.split("-");
  const months = ["January","February","March","April","May","June",
                  "July","August","September","October","November","December"];
  return `${Number(d)} ${months[Number(m)-1]} ${y}`;
}

function updateMiniChamp() {
  // Show mini-champ once user has scrolled past the hero block.
  const heroBottom = heroEl.offsetTop + heroEl.offsetHeight;
  const threshold = heroBottom - 80; // start the swap a bit before hero is fully out
  const past = window.scrollY > threshold;
  miniEl.classList.toggle("visible", past);
  miniEl.setAttribute("aria-hidden", past ? "false" : "true");
}

let scrollAnim = 0;
function currentScrollY() {
  const se = document.scrollingElement || document.documentElement;
  return window.pageYOffset || se.scrollTop || 0;
}
function setScrollY(y) {
  // window.scrollTo always targets the actual scrolling element across
  // browsers; writing scrollTop directly on documentElement/body is
  // unreliable on mobile Chromium where the scrolling element may differ.
  window.scrollTo(0, y);
}
function scrollToTopSmooth(e) {
  if (e) { e.preventDefault(); e.stopPropagation(); }
  const start = currentScrollY();
  if (start <= 0) return;
  if (scrollAnim) cancelAnimationFrame(scrollAnim);
  const duration = 450;
  const t0 = performance.now();
  const ease = (t) => 1 - Math.pow(1 - t, 3);
  function step(now) {
    const k = Math.min(1, (now - t0) / duration);
    const y = Math.round(start * (1 - ease(k)));
    setScrollY(y);
    if (k < 1) {
      scrollAnim = requestAnimationFrame(step);
    } else {
      scrollAnim = 0;
    }
  }
  scrollAnim = requestAnimationFrame(step);
}

main().catch((e) => {
  console.error(e);
  loadingEl.textContent = "Failed to load data.";
});

/* ============================================================
   VIEW ROUTING + SECTIONS
   ============================================================ */

const viewLoaders = {
  feed:      () => {},  // already initialised by main()
  rankings:  loadRankingsView,
  reigns:    loadReignsView,
  countries: loadCountriesView,
  map:       loadMapView,
  stats:     loadStatsView,
  search:    loadSearchView,
};
const viewLoaded = new Set(["feed"]);

function switchView(view) {
  document.querySelectorAll(".view").forEach(v => {
    const on = v.dataset.view === view;
    v.classList.toggle("active", on);
    if (on) v.removeAttribute("hidden");
    else v.setAttribute("hidden", "");
  });
  document.querySelectorAll(".ab-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.view === view);
  });
  if (!viewLoaded.has(view)) {
    viewLoaded.add(view);
    Promise.resolve().then(() => viewLoaders[view]?.());
  }
  // When leaving feed, the topbar mini-champ shouldn't show; bring back when returning.
  if (view !== "feed") {
    miniEl.classList.remove("visible");
    miniEl.setAttribute("aria-hidden", "true");
  } else {
    updateMiniChamp();
  }
  // Scroll to top on view change for predictability.
  window.scrollTo(0, 0);
  // Persist last view.
  try { localStorage.setItem("ufcc.view", view); } catch (_) {}
}

function applyModeChrome() {
  const set = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  if (themeMeta) themeMeta.setAttribute("content", TEAMS ? "#0a0f0d" : "#0b0d10");
  if (TEAMS) {
    set("brand-title", "Unofficial Football World Championship");
    set("brand-sub", "A single title that passes to whichever national team beats the current holder. Lineage tracked match by match since 1872.");
  }
  const eyebrow = document.querySelector("#hero .hero-eyebrow");
  if (eyebrow) eyebrow.textContent = TEAMS ? "Current UFWC champion" : "Current UFCC champion";
  if (TEAMS) {
    const cl = document.querySelector('.ab-btn[data-view="countries"] .ab-label');
    if (cl) cl.textContent = "Confeds";
    const ch = document.querySelector('#view-countries .view-header h2');
    if (ch) ch.textContent = "By confederation";
    const cp = document.querySelector('#view-countries .view-header p');
    if (cp) cp.textContent = "How long the championship has stayed in each confederation.";
    const mp = document.querySelector('#view-map .view-header p');
    if (mp) mp.textContent = "Each circle is a nation that has held the championship. Size reflects total days.";
    document.querySelectorAll('#rankings-search, #reigns-search').forEach(i => { i.placeholder = "Filter teams…"; });
  }
  // Mode switch state + wiring.
  document.querySelectorAll(".mode-btn").forEach(btn => {
    const on = (btn.dataset.mode === "teams") === TEAMS;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.addEventListener("click", () => {
      const target = btn.dataset.mode;
      if ((target === "teams") === TEAMS) return;
      try { localStorage.setItem("ufcc.mode", target); } catch (_) {}
      location.reload();
    });
  });
}

function initActivityBar() {
  document.querySelectorAll(".ab-btn").forEach(btn => {
    btn.addEventListener("click", () => switchView(btn.dataset.view));
  });
  const toggle = document.getElementById("sidebar-toggle");
  toggle.addEventListener("click", () => {
    document.body.classList.toggle("sidebar-collapsed");
    try {
      localStorage.setItem(
        "ufcc.sidebar",
        document.body.classList.contains("sidebar-collapsed") ? "collapsed" : "open"
      );
    } catch (_) {}
  });
  try {
    if (localStorage.getItem("ufcc.sidebar") === "collapsed") {
      document.body.classList.add("sidebar-collapsed");
    }
    const last = localStorage.getItem("ufcc.view");
    if (last && viewLoaders[last] && last !== "feed") switchView(last);
  } catch (_) {}
}

/* ---------- Shared helpers ---------- */

function crestCellHTML(name, crestFile, size = 36) {
  return `<div class="rank-crest">${crestInnerVal(name, crestFile)}</div>`;
}

function fmtNum(n) { return Number(n).toLocaleString(); }

function fmtShortDate(iso) {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-");
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${Number(d)} ${months[Number(m)-1]} ${y}`;
}

async function loadJSON(name) {
  const r = await fetch(`${DATA_DIR}/${name}`);
  if (!r.ok) throw new Error(`Fetch ${name} ${r.status}`);
  return r.json();
}

/* ---------- Rankings ---------- */

async function loadRankingsView() {
  const body = document.getElementById("rankings-body");
  const meta = document.getElementById("rankings-meta");
  const input = document.getElementById("rankings-search");
  body.innerHTML = `<p style="color: var(--text-dim)">Loading rankings…</p>`;
  let data;
  try { data = await loadJSON("rankings.json"); }
  catch (e) { body.innerHTML = `<p>Failed to load.</p>`; return; }

  function render(query) {
    const q = (query || "").trim().toLowerCase();
    const filtered = q
      ? data.filter(r => r.name.toLowerCase().includes(q))
      : data;
    const limit = q ? filtered.length : 200;
    const slice = filtered.slice(0, limit);
    if (meta) {
      meta.textContent = q
        ? `${fmtNum(filtered.length)} ${filtered.length === 1 ? UNIT : UNITS} match “${query.trim()}”.`
        : `Showing top ${Math.min(200, data.length)} of ${fmtNum(data.length)} ${UNITS}.`;
    }
    const frag = document.createDocumentFragment();
    slice.forEach((row, i) => {
      const rank = data.indexOf(row) + 1;
      const isTop3 = !q && i < 3;
      const el = document.createElement("article");
      el.className = "rank-row" + (isTop3 ? " gold" : "");
      el.innerHTML = `
        <div class="rank-pos">${rank}</div>
        ${crestCellHTML(row.name, row.crest)}
        <div class="rank-info">
          <div class="rank-name">${escapeHtml(row.name)}</div>
          <div class="rank-sub">${row.reigns} reign${row.reigns === 1 ? "" : "s"} · ${fmtNum(row.days)} days</div>
        </div>
        <div class="rank-metric">
          <span class="big">${fmtNum(row.matches)}</span>
          <span class="lbl">matches</span>
        </div>
      `;
      frag.appendChild(el);
    });
    body.innerHTML = "";
    if (slice.length === 0) {
      body.innerHTML = `<p style="color: var(--text-dim)">No ${UNITS} match.</p>`;
    } else {
      body.appendChild(frag);
    }
  }

  render("");
  if (input && !input.dataset.wired) {
    input.dataset.wired = "1";
    input.addEventListener("input", () => render(input.value));
  }
}

/* ---------- Longest reigns ---------- */

async function loadReignsView() {
  const body = document.getElementById("reigns-body");
  const meta = document.getElementById("reigns-meta");
  const input = document.getElementById("reigns-search");
  body.innerHTML = `<p style="color: var(--text-dim)">Loading reigns…</p>`;
  let data;
  try { data = await loadJSON("longest_reigns.json"); }
  catch (e) { body.innerHTML = `<p>Failed to load.</p>`; return; }

  function render(query) {
    const q = (query || "").trim().toLowerCase();
    const filtered = q
      ? data.filter(r => r.club.toLowerCase().includes(q))
      : data;
    if (meta) {
      meta.textContent = q
        ? `${fmtNum(filtered.length)} reign${filtered.length === 1 ? "" : "s"} match “${query.trim()}”.`
        : `Top ${fmtNum(data.length)} uninterrupted reigns.`;
    }
    const frag = document.createDocumentFragment();
    filtered.forEach((row) => {
      const rank = data.indexOf(row) + 1;
      const isTop3 = !q && rank <= 3;
      const el = document.createElement("article");
      el.className = "rank-row" + (isTop3 ? " gold" : "");
      const range = `${fmtShortDate(row.started_on)} → ${row.is_current ? "today" : fmtShortDate(row.ended_on)}`;
      const wartimeNote = row.wartime_days
        ? ` <span style="color:var(--text-dim);font-size:11px;">(${fmtNum(row.wartime_days)}d wartime excluded)</span>`
        : "";
      el.innerHTML = `
        <div class="rank-pos">${rank}</div>
        ${crestCellHTML(row.club, row.crest)}
        <div class="rank-info">
          <div class="rank-name">${escapeHtml(row.club)}${row.is_current ? ' <span style="color:var(--gold);font-size:11px;">· current</span>' : ""}</div>
          <div class="rank-sub">${range} · ${fmtNum(row.days)} days${wartimeNote}</div>
        </div>
        <div class="rank-metric">
          <span class="big">${fmtNum(row.matches)}</span>
          <span class="lbl">matches</span>
        </div>
      `;
      frag.appendChild(el);
    });
    body.innerHTML = "";
    if (filtered.length === 0) {
      body.innerHTML = `<p style="color: var(--text-dim)">No reigns match.</p>`;
    } else {
      body.appendChild(frag);
    }
  }

  render("");
  if (input && !input.dataset.wired) {
    input.dataset.wired = "1";
    input.addEventListener("input", () => render(input.value));
  }
}

/* ---------- Countries ---------- */

async function loadCountriesView() {
  const body = document.getElementById("countries-body");
  body.innerHTML = `<p style="color: var(--text-dim)">Loading countries…</p>`;
  let data;
  try { data = await loadJSON("countries.json"); }
  catch (e) { body.innerHTML = `<p>Failed to load.</p>`; return; }

  const frag = document.createDocumentFragment();
  data.forEach(row => {
    const el = document.createElement("article");
    el.className = "country-card";
    const chips = row.top_clubs.map(c =>
      `<span class="country-chip">
        <span class="mini-c">${crestInnerVal(c.name, c.crest)}</span>
        ${escapeHtml(c.name)}
        <span class="d">${fmtNum(c.days)}d</span>
      </span>`
    ).join("");
    el.innerHTML = `
      <div class="country-card-head">
        <div>
          <div class="name">${escapeHtml(row.country)}</div>
          <div class="sub">${row.clubs_total} ${row.clubs_total === 1 ? UNIT : UNITS} · ${fmtNum(row.matches)} matches</div>
        </div>
        <div class="days">${fmtNum(row.days)}d</div>
      </div>
      <div class="country-clubs">${chips}</div>
    `;
    frag.appendChild(el);
  });
  body.innerHTML = "";
  body.appendChild(frag);
}

/* ---------- Stats ---------- */

async function loadStatsView() {
  const body = document.getElementById("stats-body");
  body.innerHTML = `<p style="color: var(--text-dim)">Loading stats…</p>`;
  let s;
  try { s = await loadJSON("stats.json"); }
  catch (e) { body.innerHTML = `<p>Failed to load.</p>`; return; }

  document.getElementById("stats-first-date").textContent = fmtShortDate(s.first_date);
  document.getElementById("stats-last-date").textContent = fmtShortDate(s.last_date);

  const cards = [
    { lbl: "Total matches",   val: fmtNum(s.total_matches), extra: "Every game in the lineage" },
    { lbl: TEAMS ? "Teams involved" : "Clubs involved",  val: fmtNum(s.total_clubs),   extra: `Distinct ${UNITS} in any match` },
    { lbl: "Different champions", val: fmtNum(s.total_champions), extra: `${TEAMS ? "Teams" : "Clubs"} that held the title` },
    { lbl: "Reign changes",   val: fmtNum(s.total_reigns),  extra: "Times the belt swapped hands" },
    { lbl: "Current champion", val: s.current_champion || "—", extra: "Holding it right now", gold: true },
    { lbl: "Longest reign", val: `${fmtNum(s.longest_reign.matches)}`, extra: `${escapeHtml(s.longest_reign.club)} · ${fmtNum(s.longest_reign.days)} days`, gold: true },
    { lbl: "Shortest reign", val: `${s.shortest_reign.matches}`, extra: `${escapeHtml(s.shortest_reign.club)} · ${fmtShortDate(s.shortest_reign.started_on)}` },
    { lbl: "Year with most changes", val: s.most_changes_year ? s.most_changes_year.year : "—", extra: s.most_changes_year ? `${s.most_changes_year.changes} reign changes` : "" },
  ];
  const html = `<div class="stats-grid">${
    cards.map(c => `
      <div class="stat-card${c.gold ? " gold" : ""}">
        <div class="lbl">${c.lbl}</div>
        <div class="val">${c.val}</div>
        ${c.extra ? `<div class="extra">${c.extra}</div>` : ""}
      </div>
    `).join("")
  }</div>`;
  body.innerHTML = html;
}

/* ---------- Search ---------- */

let searchIndex = null;
function buildSearchIndex() {
  if (searchIndex) return;
  // Lowercase concatenation for quick filter.
  searchIndex = matches.map(m => {
    const [no, date, home, score, away, , comp, venue, champ] = m;
    return `${no} ${date} ${home} ${away} ${comp} ${venue} ${champ}`.toLowerCase();
  });
}
function loadSearchView() {
  buildSearchIndex();
  const input = document.getElementById("search-input");
  const body = document.getElementById("search-body");
  const meta = document.getElementById("search-meta");
  meta.textContent = `Search across ${fmtNum(matches.length)} matches.`;

  let raf = 0;
  function render() {
    const q = input.value.trim().toLowerCase();
    if (!q) { body.innerHTML = ""; meta.textContent = `Search across ${fmtNum(matches.length)} matches.`; return; }
    const terms = q.split(/\s+/);
    const limit = 200;
    const out = [];
    for (let i = 0; i < searchIndex.length && out.length < limit; i++) {
      const hay = searchIndex[i];
      let ok = true;
      for (const t of terms) { if (!hay.includes(t)) { ok = false; break; } }
      if (ok) out.push(i);
    }
    meta.textContent = `${out.length === limit ? out.length + "+" : out.length} result${out.length === 1 ? "" : "s"}`;
    body.innerHTML = out.map(i => {
      const m = matches[i];
      const [no, date, home, score, away, result, comp, venue, champ] = m;
      const [d, my] = fmtDate(date);
      return `
        <div class="search-result" data-idx="${i}">
          <div class="date"><strong>${d}</strong>${my}<br>#${no}</div>
          <div class="desc">
            ${escapeHtml(home)} vs ${escapeHtml(away)}
            <div class="meta">${escapeHtml(comp || "")}${venue ? " · " + escapeHtml(venue) : ""} · → ${escapeHtml(champ)}</div>
          </div>
          <div class="score">${escapeHtml(score)}</div>
        </div>
      `;
    }).join("");
  }
  function schedule() { if (raf) return; raf = requestAnimationFrame(() => { raf = 0; render(); }); }
  input.addEventListener("input", schedule);
  body.addEventListener("click", (e) => {
    const card = e.target.closest(".search-result");
    if (!card) return;
    const idx = Number(card.dataset.idx);
    switchView("feed");
    requestAnimationFrame(() => scrollToIndex(idx, false));
  });
  input.focus();
}

/* ---------- Map (lazy-load Leaflet) ---------- */

let mapInstance = null;
async function loadMapView() {
  await ensureLeaflet();
  const container = document.getElementById("map-container");
  if (mapInstance) { mapInstance.invalidateSize(); return; }
  let geo;
  try { geo = await loadJSON("champions_geo.json"); }
  catch (e) { container.innerHTML = "<p style='padding:20px;color:var(--text-dim)'>Failed to load map data.</p>"; return; }

  const map = L.map(container, { worldCopyJump: true, scrollWheelZoom: true });
  mapInstance = map;
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    maxZoom: 12,
    attribution: '&copy; OpenStreetMap, &copy; CARTO',
    subdomains: "abcd",
  }).addTo(map);

  // Compute marker sizes by days (sqrt scale).
  const maxDays = Math.max(...geo.map(g => g.days));
  geo.forEach(c => {
    const r = 4 + 22 * Math.sqrt(c.days / maxDays);
    const marker = L.circleMarker([c.lat, c.lon], {
      radius: r,
      color: ACCENT,
      weight: 1,
      fillColor: ACCENT,
      fillOpacity: 0.55,
    }).addTo(map);
    marker.bindTooltip(c.name, { direction: "top", offset: [0, -2] });
    const crestHTML = crestInnerVal(c.name, c.crest);
    marker.bindPopup(`
      <div class="map-popup">
        <div class="crest">${crestHTML}</div>
        <div>
          <div class="name">${escapeHtml(c.name)}</div>
          <div class="meta"><b>${fmtNum(c.days)}d</b> · ${fmtNum(c.matches)} matches · ${c.reigns} reign${c.reigns === 1 ? "" : "s"}</div>
        </div>
      </div>
    `);
  });

  map.fitBounds(L.latLngBounds(geo.map(g => [g.lat, g.lon])).pad(0.1));
  setTimeout(() => map.invalidateSize(), 50);
}

function ensureLeaflet() {
  if (window.L) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
    document.head.appendChild(css);
    const js = document.createElement("script");
    js.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
    js.onload = () => resolve();
    js.onerror = () => reject(new Error("Leaflet failed to load"));
    document.head.appendChild(js);
  });
}

// Wire up the activity bar after main() has initialised the feed.
initActivityBar();
