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

let matches = [];        // raw rows, DESC by match_no
let clubs = {};          // name -> crest filename
let years = [];          // [{year, first_index, count}]
let rendered = new Map();// index -> element (recycle pool)

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

  const crestH = crestUrl(home);
  const crestA = crestUrl(away);

  el.innerHTML = `
    <div class="date">
      <div class="d">${d}</div>
      <div class="my">${my}</div>
      <div class="no">#${no}</div>
    </div>
    <div class="body">
      <div class="fixture">
        <div class="team home ${homeWon ? "winner" : awayWon ? "loser" : ""}">
          <div class="crest ${crestH ? "" : "placeholder"}">
            ${crestH ? `<img loading="lazy" src="${crestH}" alt="">` : initials(home)}
          </div>
          <div class="name">${escapeHtml(home)}</div>
        </div>
        <div class="score ${result === "D" ? "draw" : ""}">${escapeHtml(score)}</div>
        <div class="team away ${awayWon ? "winner" : homeWon ? "loser" : ""}">
          <div class="name">${escapeHtml(away)}</div>
          <div class="crest ${crestA ? "" : "placeholder"}">
            ${crestA ? `<img loading="lazy" src="${crestA}" alt="">` : initials(away)}
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
  const [m, c, y] = await Promise.all([
    fetch("data/matches.json").then(r => r.json()),
    fetch("data/clubs.json").then(r => r.json()),
    fetch("data/years.json").then(r => r.json()),
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
  const crest = crestUrl(champion);

  const crestHTML = crest
    ? `<img src="${crest}" alt="${escapeHtml(champion)} crest">`
    : initials(champion);
  heroCrestEl.innerHTML = crestHTML;
  if (!crest) heroCrestEl.classList.add("placeholder");
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

main().catch((e) => {
  console.error(e);
  loadingEl.textContent = "Failed to load data.";
});
