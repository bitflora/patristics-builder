/**
 * Patristics Viewer — static SPA
 *
 * Expects data files at:
 *   ../data/static/index.json.zst
 *   ../data/static/bible/{book-slug}/{chapter}.json.zst
 *   ../data/static/manuscripts/{id}.json.zst
 *
 * To serve locally:
 *   python -m http.server 8000 --directory .   (from project root)
 *   then open http://localhost:8000/viewer/
 */

const DATA_ROOT = "../data/static";

// ── State ─────────────────────────────────────────────────────────────────────
let index = null;         // loaded from index.json.zst
let worksById = new Map(); // manuscript id → work entry (from index)
const bookCache = new Map(); // book slug → parsed book payload
let activeBook = null;    // slug
let activeChapter = null; // number
let activeMode = "viz";  // "scripture" | "works" | "viz"
let activeWorkId = null;       // numeric manuscript id

// ── DOM refs ──────────────────────────────────────────────────────────────────
const bookListEl       = document.getElementById("book-list");
const searchEl         = document.getElementById("search");
const welcomeEl        = document.getElementById("welcome");
const statsEl          = document.getElementById("stats");
const chapterViewEl    = document.getElementById("chapter-view");
const chapterTitle     = document.getElementById("chapter-title");
const refsListEl       = document.getElementById("refs-list");
const authorFilter     = document.getElementById("author-filter");
// Works mode DOM refs
const sidebarEl        = document.getElementById("sidebar");
const scripturePanelEl = document.getElementById("scripture-panel");
const worksPanelEl     = document.getElementById("works-panel");
const worksListEl      = document.getElementById("works-list");
const worksSearchEl    = document.getElementById("works-search");
const workViewEl       = document.getElementById("work-view");
const workTitleEl      = document.getElementById("work-title");
const workMetaEl       = document.getElementById("work-meta");
const workRefsListEl   = document.getElementById("work-refs-list");
const bookFilterEl     = document.getElementById("book-filter");
const categoryFiltersEl = document.getElementById("category-filters");
const modeTabEls       = document.querySelectorAll(".mode-tab");
// Visualizations mode DOM ref
const vizViewEl        = document.getElementById("viz-view");

// ── Fetch helpers ─────────────────────────────────────────────────────────────
async function fetchJSON(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`Failed to fetch ${url}: ${resp.status}`);
  // If the server set Content-Encoding: zstd the browser already decompressed;
  // otherwise (e.g. simple static servers) decompress the raw zstd stream manually.
  if (url.endsWith('.zst') && !resp.headers.get('Content-Encoding')) {
    const buf = await resp.arrayBuffer();
    const decompressed = fzstd.decompress(new Uint8Array(buf));
    return JSON.parse(new TextDecoder().decode(decompressed));
  }
  return resp.json();
}

// ── Heatmap ───────────────────────────────────────────────────────────────────
function heatLevel(count, max) {
  if (count === 0) return 0;
  const ratio = count / max;
  if (ratio < 0.15) return 1;
  if (ratio < 0.40) return 2;
  if (ratio < 0.70) return 3;
  return 4;
}

// Return the ref count for a chapter entry, limited to the checked categories.
function filteredCount(ch, cats) {
  if (!ch.by_cat) return ch.count; // graceful fallback for old JSON format
  let total = 0;
  for (const [cat, n] of Object.entries(ch.by_cat)) {
    if (cats.has(cat)) total += n;
  }
  return total;
}

// ── Mode switching ────────────────────────────────────────────────────────────
function setMode(mode) {
  activeMode = mode;
  for (const tab of modeTabEls)
    tab.classList.toggle("active", tab.dataset.mode === mode);

  const isScripture = mode === "scripture";
  const isWorks     = mode === "works";
  const isViz       = mode === "viz";

  scripturePanelEl.hidden = !isScripture;
  worksPanelEl.hidden     = !isWorks;
  sidebarEl.hidden        = isViz;
  vizViewEl.hidden        = !isViz;

  if (isViz) {
    welcomeEl.hidden    = true;
    chapterViewEl.hidden = true;
    workViewEl.hidden   = true;
    renderVizTab();
  } else if (isScripture) {
    workViewEl.hidden = true;
    if (activeChapter !== null) {
      welcomeEl.hidden    = true;
      chapterViewEl.hidden = false;
    } else {
      showWelcome();
    }
  } else {
    chapterViewEl.hidden = true;
    if (activeWorkId !== null) {
      welcomeEl.hidden  = true;
      workViewEl.hidden = false;
    } else {
      showWelcome();
    }
  }
}

for (const tab of modeTabEls)
  tab.addEventListener("click", () => setMode(tab.dataset.mode));

// ── Sidebar rendering ─────────────────────────────────────────────────────────
function renderSidebar(filter = "") {
  const term = filter.toLowerCase();
  const cats = checkedCategories();
  bookListEl.innerHTML = "";

  for (const book of index.books) {
    if (term && !book.name.toLowerCase().includes(term)) continue;

    // Build a filtered chapter list, computing counts relative to checked categories.
    const filteredChs = book.chapters
      .map(ch => ({ ch: ch.ch, count: filteredCount(ch, cats), by_cat: ch.by_cat }))
      .filter(ch => ch.count > 0);

    if (!filteredChs.length) continue;

    const totalRefs = filteredChs.reduce((s, c) => s + c.count, 0);
    const maxCount  = Math.max(...filteredChs.map(c => c.count));

    const entry = document.createElement("div");
    entry.className = "book-entry" + (book.slug === activeBook ? " open" : "");
    entry.dataset.slug = book.slug;

    // Book button
    const btn = document.createElement("button");
    btn.className = "book-btn" + (book.slug === activeBook ? " active" : "");
    btn.setAttribute("aria-expanded", book.slug === activeBook ? "true" : "false");
    btn.innerHTML = `<span>${book.name}</span><span class="total-badge">${totalRefs}</span>`;
    btn.addEventListener("click", () => toggleBook(book.slug));
    entry.appendChild(btn);

    // Chapter dots row
    const row = document.createElement("div");
    row.className = "chapter-row";
    row.setAttribute("role", "list");

    for (const ch of filteredChs) {
      const dot = document.createElement("button");
      dot.className = "ch-dot" + (book.slug === activeBook && ch.ch === activeChapter ? " active" : "");
      dot.dataset.heat = heatLevel(ch.count, maxCount);
      dot.title = `${book.name} ${ch.ch} — ${ch.count} reference${ch.count !== 1 ? "s" : ""}`;
      dot.textContent = ch.ch;
      dot.setAttribute("role", "listitem");
      dot.addEventListener("click", (e) => {
        e.stopPropagation();
        loadChapter(book.slug, ch.ch);
      });
      row.appendChild(dot);
    }
    entry.appendChild(row);
    bookListEl.appendChild(entry);
  }
}

function toggleBook(slug) {
  if (activeBook === slug) {
    // Collapse
    activeBook = null;
    activeChapter = null;
    showWelcome();
  } else {
    activeBook = slug;
    activeChapter = null;
  }
  renderSidebar(searchEl.value);
}

// ── Welcome / stats ───────────────────────────────────────────────────────────
function showWelcome() {
  welcomeEl.hidden = false;
  chapterViewEl.hidden = true;
  workViewEl.hidden = true;
  if (!index) return;
  const totalBooks = index.books.length;
  const totalRefs  = index.books.reduce((s, b) => s + b.chapters.reduce((s2, c) => s2 + c.count, 0), 0);
  const totalWorks = index.works.length;
  statsEl.textContent =
    `${totalRefs.toLocaleString()} references across ${totalBooks} books, from ${totalWorks} works.`;
}

// ── Chapter loading ───────────────────────────────────────────────────────────
async function loadChapter(bookSlug, chapter) {
  activeMode = "scripture";
  for (const tab of modeTabEls) tab.classList.toggle("active", tab.dataset.mode === "scripture");

  activeBook = bookSlug;
  activeChapter = chapter;
  renderSidebar(searchEl.value);

  welcomeEl.hidden = true;
  chapterViewEl.hidden = false;
  workViewEl.hidden = true;
  refsListEl.innerHTML = `<p class="loading">Loading…</p>`;

  const bookInfo = index.books.find(b => b.slug === bookSlug);
  chapterTitle.textContent = bookInfo
    ? `${bookInfo.name} ${chapter}`
    : `${bookSlug} ${chapter}`;

  let bookData;
  try {
    if (!bookCache.has(bookSlug)) {
      bookCache.set(bookSlug, await fetchJSON(`${DATA_ROOT}/bible/${bookSlug}.json.zst`));
    }
    bookData = bookCache.get(bookSlug);
  } catch (err) {
    refsListEl.innerHTML = `<p class="no-refs">Could not load chapter data. Have you run the builder?</p>`;
    return;
  }

  const chData = bookData.chapters.find(c => c.ch === chapter);
  renderChapter(bookData, chData);
}

function renderChapter(bookData, chData) {
  if (!chData || !chData.refs.length) {
    refsListEl.innerHTML = `<p class="no-refs">No references found for this chapter.</p>`;
    return;
  }

  // Populate author filter from the refs in this chapter.
  const authors = [...new Set(chData.refs.map(r => worksById.get(r.w)?.author ?? "Unknown"))].sort();
  authorFilter.innerHTML = `<option value="">All authors</option>`;
  for (const a of authors) {
    const opt = document.createElement("option");
    opt.value = a;
    opt.textContent = a;
    authorFilter.appendChild(opt);
  }

  refsListEl.innerHTML = "";

  for (const ref of chData.refs) {
    const work = worksById.get(ref.w);
    if (!work) continue;

    const card = document.createElement("article");
    card.className = "ref-card";
    card.dataset.author = work.author;
    card.dataset.category = work.category || "Other";

    const verseTag = ref.v
      ? `<span class="ref-verse-tag">v. ${ref.v}</span>`
      : `<span class="ref-verse-tag">whole chapter</span>`;

    const yearStr = work.year ? ` (${work.year})` : "";

    card.innerHTML = `
      <div class="ref-meta">
        <div>
          <span class="ref-author">${esc(work.author)}</span>
          <span class="ref-work"> — ${esc(work.title)}${esc(yearStr)}</span>
        </div>
        ${verseTag}
      </div>
      <div class="ref-text">${esc(bookData.passages[ref.p])}</div>
    `;
    refsListEl.appendChild(card);
  }

  applyCombinedFilter();
}

// Hide/show chapter-view ref cards based on both author dropdown and category checkboxes.
function applyCombinedFilter() {
  const authorVal = authorFilter.value;
  const cats = checkedCategories();
  for (const card of refsListEl.querySelectorAll(".ref-card")) {
    const hiddenByAuthor = authorVal && card.dataset.author !== authorVal;
    const hiddenByCat   = !cats.has(card.dataset.category || "Other");
    card.hidden = hiddenByAuthor || hiddenByCat;
  }
}

function applyAuthorFilter() { applyCombinedFilter(); }

authorFilter.addEventListener("change", applyCombinedFilter);

// ── Category filters ──────────────────────────────────────────────────────────
function renderCategoryFilters() {
  const categories = [...new Set(index.works.map(w => w.category || "Other"))].sort();
  categoryFiltersEl.innerHTML = "";
  for (const cat of categories) {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = cat;
    cb.checked = true;
    cb.addEventListener("change", applyFilters);
    label.appendChild(cb);
    label.appendChild(document.createTextNode(" " + cat));
    categoryFiltersEl.appendChild(label);
  }
}

function checkedCategories() {
  const checked = new Set();
  for (const cb of categoryFiltersEl.querySelectorAll("input[type=checkbox]")) {
    if (cb.checked) checked.add(cb.value);
  }
  return checked;
}

// Re-render all views whenever the category filter changes.
function applyFilters() {
  renderSidebar(searchEl.value);
  if (activeChapter !== null) applyCombinedFilter();
  renderWorksList(worksSearchEl.value);
  if (activeMode === "viz") renderVizTab();
}

// ── Works sidebar ─────────────────────────────────────────────────────────────
function renderWorksList(filter = "") {
  const term = filter.toLowerCase();
  const cats = checkedCategories();
  worksListEl.innerHTML = "";

  for (const work of index.works) {
    if (term && !`${work.author} ${work.title}`.toLowerCase().includes(term)) continue;
    if (cats.size && !cats.has(work.category || "Other")) continue;

    const btn = document.createElement("button");
    btn.className = "work-btn" + (work.id === activeWorkId ? " active" : "");

    const yearStr = work.year ? ` (${work.year})` : "";
    const badge = work.ref_count != null
      ? `<span class="work-ref-badge">${work.ref_count.toLocaleString()}</span>`
      : "";

    btn.innerHTML = `
      <span class="work-btn-text">
        <span class="work-author">${esc(work.author)}</span>
        <span class="work-title-sm"> — ${esc(work.title)}${esc(yearStr)}</span>
      </span>
      ${badge}
    `;
    btn.addEventListener("click", () => loadWork(work.id));
    worksListEl.appendChild(btn);
  }
}

worksSearchEl.addEventListener("input", () => renderWorksList(worksSearchEl.value));

// ── Work loading ──────────────────────────────────────────────────────────────
async function loadWork(workId) {
  activeMode = "works";
  for (const tab of modeTabEls) tab.classList.toggle("active", tab.dataset.mode === "works");

  activeWorkId = workId;
  renderWorksList(worksSearchEl.value);

  welcomeEl.hidden = true;
  chapterViewEl.hidden = true;
  workViewEl.hidden = false;
  workRefsListEl.innerHTML = `<p class="loading">Loading…</p>`;

  let data;
  try {
    data = await fetchJSON(`${DATA_ROOT}/manuscripts/${workId}.json.zst`);
  } catch (err) {
    workRefsListEl.innerHTML = `<p class="no-refs">Could not load work data. Have you run builder.py?</p>`;
    return;
  }

  renderWork(data);
}

function renderWork(data) {
  workTitleEl.textContent = data.title;
  workMetaEl.textContent = `${data.author}${data.year ? ` (${data.year})` : ""}`;

  // Book filter dropdown (books in the order they appear in refs)
  const seenBooks = [];
  for (const ref of data.refs)
    if (!seenBooks.includes(ref.book)) seenBooks.push(ref.book);

  bookFilterEl.innerHTML = `<option value="">All books</option>`;
  for (const book of seenBooks) {
    const opt = document.createElement("option");
    opt.value = book;
    opt.textContent = book;
    bookFilterEl.appendChild(opt);
  }

  workRefsListEl.innerHTML = "";

  if (!data.refs.length) {
    workRefsListEl.innerHTML = `<p class="no-refs">No references found for this work.</p>`;
    return;
  }

  for (const ref of data.refs) {
    const card = document.createElement("article");
    card.className = "ref-card";
    card.dataset.book = ref.book;

    const locTag = ref.v
      ? `<span class="ref-verse-tag">${esc(ref.book)} ${ref.chapter}:${esc(ref.v)}</span>`
      : `<span class="ref-verse-tag">${esc(ref.book)} ${ref.chapter}</span>`;

    card.innerHTML = `
      <div class="ref-meta">
        <div>
          <span class="ref-author">${esc(data.author)}</span>
          <span class="ref-work"> — ${esc(data.title)}</span>
        </div>
        ${locTag}
      </div>
      <div class="ref-text">${esc(data.passages[ref.p])}</div>
    `;
    workRefsListEl.appendChild(card);
  }

  applyBookFilter();
}

function applyBookFilter() {
  const val = bookFilterEl.value;
  for (const card of workRefsListEl.querySelectorAll(".ref-card"))
    card.hidden = val && card.dataset.book !== val;
}

bookFilterEl.addEventListener("change", applyBookFilter);

// ── Utility ───────────────────────────────────────────────────────────────────
function esc(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Visualizations ────────────────────────────────────────────────────────────

// Earthy palette that complements the app's warm brown theme
const CAT_PALETTE = ['#7a5c38','#4a8c6a','#5c7aa8','#9a6b4b','#7a4a6a','#5c8a5c','#8a7a4a','#6a5c8a'];
let _catColorMap = null;

function getCatColors() {
  if (!_catColorMap) {
    const cats = [...new Set(index.works.map(w => w.category || 'Other'))].sort();
    _catColorMap = new Map(cats.map((c, i) => [c, CAT_PALETTE[i % CAT_PALETTE.length]]));
  }
  return _catColorMap;
}

function renderVizTab() {
  vizViewEl.innerHTML = '';
  const cats = checkedCategories();
  if (!cats.size) {
    vizViewEl.innerHTML = '<p class="no-refs" style="padding:.5rem 0">No categories selected.</p>';
    return;
  }
  renderBibleHeatmap(cats);
  renderTopBooksChart(cats);
  renderWorksTimeline(cats);
  renderCategoryDonut(cats);
}

// Helper: append a new viz section to vizViewEl, return the element
function makeVizSection(title) {
  const sec = document.createElement('section');
  sec.className = 'viz-section';
  const h = document.createElement('h3');
  h.className = 'viz-heading';
  h.textContent = title;
  sec.appendChild(h);
  vizViewEl.appendChild(sec);
  return sec;
}

// Navigate from viz to a book in Scripture mode
function navigateToBook(slug) {
  activeBook = slug;
  activeChapter = null;
  setMode('scripture');
  renderSidebar(searchEl.value);
}

// Fixed-point helper for SVG coords
function f(n) { return n.toFixed(2); }

// ── 1. Bible Coverage Heatmap ─────────────────────────────────────────────────
function renderBibleHeatmap(cats) {
  const sec = makeVizSection('Bible Coverage');
  const desc = document.createElement('p');
  desc.className = 'viz-desc';
  desc.textContent = 'Each block is a Bible book. Color intensity reflects citation density. Click to browse.';
  sec.appendChild(desc);

  const maxTotal = Math.max(1, ...index.books.map(b =>
    b.chapters.reduce((s, ch) => s + filteredCount(ch, cats), 0)
  ));

  const grid = document.createElement('div');
  grid.className = 'viz-heatmap';

  for (const book of index.books) {
    const total = book.chapters.reduce((s, ch) => s + filteredCount(ch, cats), 0);
    const cell = document.createElement('button');
    cell.className = 'viz-book-cell';
    cell.dataset.heat = heatLevel(total, maxTotal);
    cell.title = `${book.name}: ${total} reference${total !== 1 ? 's' : ''}`;
    cell.innerHTML = `<span class="vbc-name">${esc(book.name)}</span>${total ? `<span class="vbc-count">${total}</span>` : ''}`;
    if (total > 0) {
      cell.addEventListener('click', () => navigateToBook(book.slug));
    } else {
      cell.disabled = true;
    }
    grid.appendChild(cell);
  }
  sec.appendChild(grid);
}

// ── 2. Top Books Bar Chart ────────────────────────────────────────────────────
function renderTopBooksChart(cats) {
  const sec = makeVizSection('Most Referenced Books');
  const colors = getCatColors();

  const bookData = index.books.map(book => {
    const byCat = {};
    let total = 0;
    for (const ch of book.chapters) {
      for (const [cat, n] of Object.entries(ch.by_cat || {})) {
        if (cats.has(cat)) { byCat[cat] = (byCat[cat] || 0) + n; total += n; }
      }
    }
    return { name: book.name, slug: book.slug, total, byCat };
  }).filter(b => b.total > 0).sort((a, b) => b.total - a.total).slice(0, 20);

  if (!bookData.length) { sec.innerHTML += '<p class="no-refs">No data.</p>'; return; }

  const maxVal = bookData[0].total;
  const allCats = [...cats].sort();
  const ROW_H = 28, LBL_W = 145, BAR_MAX = 380, SVG_W = LBL_W + BAR_MAX + 55;
  const SVG_H = bookData.length * ROW_H + 8;

  let s = [`<svg class="viz-svg" viewBox="0 0 ${SVG_W} ${SVG_H}">`];

  for (let i = 0; i < bookData.length; i++) {
    const book = bookData[i];
    const y = i * ROW_H + 4;
    const label = book.name.length > 20 ? book.name.slice(0, 19) + '…' : book.name;
    s.push(`<text x="${LBL_W - 6}" y="${y + 14}" class="viz-bar-label" text-anchor="end">${esc(label)}</text>`);

    let xOff = LBL_W;
    for (const cat of allCats) {
      const n = book.byCat[cat] || 0;
      if (!n) continue;
      const w = Math.max(1, (n / maxVal) * BAR_MAX);
      const col = colors.get(cat) || '#7a5c38';
      s.push(`<rect x="${f(xOff)}" y="${y}" width="${f(w)}" height="18" fill="${col}" rx="2"><title>${esc(cat)}: ${n}</title></rect>`);
      xOff += w;
    }
    // Invisible hit target for click-to-navigate
    const totalW = Math.max(1, (book.total / maxVal) * BAR_MAX);
    s.push(`<rect x="${LBL_W}" y="${y}" width="${f(totalW)}" height="18" fill="transparent" class="viz-bar-hit" data-slug="${esc(book.slug)}"/>`);
    s.push(`<text x="${f(LBL_W + (book.total / maxVal) * BAR_MAX + 5)}" y="${y + 14}" class="viz-bar-count">${book.total}</text>`);
  }
  s.push('</svg>');

  const wrap = document.createElement('div');
  wrap.className = 'viz-chart-wrap';
  wrap.innerHTML = s.join('');
  wrap.querySelector('svg').addEventListener('click', e => {
    const hit = e.target.closest('[data-slug]');
    if (hit) navigateToBook(hit.getAttribute('data-slug'));
  });
  sec.appendChild(wrap);
  sec.appendChild(buildCatLegend(allCats, colors));
}

// ── 3. Works Timeline ─────────────────────────────────────────────────────────
function renderWorksTimeline(cats) {
  const sec = makeVizSection('Works by Date');
  const colors = getCatColors();

  const withYear = index.works
    .filter(w => w.year != null && cats.has(w.category || 'Other'))
    .sort((a, b) => a.year - b.year);
  const noYear = index.works.filter(w => w.year == null && cats.has(w.category || 'Other'));

  if (!withYear.length && !noYear.length) { sec.innerHTML += '<p class="no-refs">No data.</p>'; return; }

  if (withYear.length) {
    const minYear = withYear[0].year;
    const maxYear = withYear[withYear.length - 1].year;
    const yearSpan = Math.max(maxYear - minYear, 1);
    const maxRefs  = Math.max(...withYear.map(w => w.ref_count || 0), 1);

    // Category lanes (only cats with dated works)
    const catList = [...new Set(withYear.map(w => w.category || 'Other'))].sort();
    const SVG_W = 680, PAD_L = 10, PAD_R = 10, PAD_T = 12, PAD_B = 32;
    const PLOT_W = SVG_W - PAD_L - PAD_R;
    const PLOT_H = Math.max(80, catList.length * 40);
    const SVG_H  = PLOT_H + PAD_T + PAD_B;
    const bandH  = PLOT_H / catList.length;

    const xOf = yr => PAD_L + ((yr - minYear) / yearSpan) * PLOT_W;
    const rOf = r  => Math.max(3, Math.min(13, 3 + ((r || 0) / maxRefs) * 10));

    // Pick nice year tick step
    const rawStep = yearSpan / 5;
    const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const tickStep = [1, 2, 5, 10].map(n => n * mag).find(s => yearSpan / s <= 8 && yearSpan / s >= 2) || mag * 10;

    let s = [`<svg class="viz-svg" viewBox="0 0 ${SVG_W} ${SVG_H}">`];

    // Alternating band backgrounds
    for (let i = 0; i < catList.length; i++) {
      if (i % 2 === 0) continue;
      const by = PAD_T + i * bandH;
      s.push(`<rect x="${PAD_L}" y="${f(by)}" width="${PLOT_W}" height="${f(bandH)}" fill="rgba(0,0,0,0.03)"/>`);
    }

    // Axis
    s.push(`<line x1="${PAD_L}" y1="${SVG_H - PAD_B}" x2="${SVG_W - PAD_R}" y2="${SVG_H - PAD_B}" stroke="var(--border)" stroke-width="1"/>`);

    // Year ticks
    const firstTick = Math.ceil(minYear / tickStep) * tickStep;
    for (let yr = firstTick; yr <= maxYear; yr += tickStep) {
      const tx = xOf(yr);
      s.push(`<line x1="${f(tx)}" y1="${SVG_H - PAD_B}" x2="${f(tx)}" y2="${SVG_H - PAD_B + 4}" stroke="var(--muted)" stroke-width="1"/>`);
      s.push(`<text x="${f(tx)}" y="${SVG_H - PAD_B + 14}" class="viz-axis-label" text-anchor="middle">${yr}</text>`);
    }

    // Dots per category lane
    for (const work of withYear) {
      const catIdx = catList.indexOf(work.category || 'Other');
      const x = xOf(work.year);
      const r = rOf(work.ref_count);
      const y = PAD_T + (catIdx + 0.5) * bandH;
      const col = colors.get(work.category || 'Other') || '#7a5c38';
      const tip = `${work.author} — ${work.title} (${work.year}) · ${work.ref_count || 0} refs`;
      s.push(`<circle cx="${f(x)}" cy="${f(y)}" r="${r}" fill="${col}" opacity="0.75" stroke="rgba(255,255,255,0.5)" stroke-width="0.5"><title>${esc(tip)}</title></circle>`);
    }

    s.push('</svg>');
    const wrap = document.createElement('div');
    wrap.className = 'viz-chart-wrap';
    wrap.innerHTML = s.join('');
    sec.appendChild(wrap);
    sec.appendChild(buildCatLegend(catList, colors));
  }

  if (noYear.length) {
    const p = document.createElement('p');
    p.className = 'viz-timeline-unknown';
    p.textContent = `${noYear.length} work${noYear.length !== 1 ? 's' : ''} without a known date not shown.`;
    sec.appendChild(p);
  }
}

// ── 4. Category Donut ─────────────────────────────────────────────────────────
function renderCategoryDonut(cats) {
  const sec = makeVizSection('Corpus by Category');
  const colors = getCatColors();

  const catTotals = new Map();
  for (const book of index.books) {
    for (const ch of book.chapters) {
      for (const [cat, n] of Object.entries(ch.by_cat || {})) {
        if (cats.has(cat)) catTotals.set(cat, (catTotals.get(cat) || 0) + n);
      }
    }
  }
  const entries = [...catTotals.entries()].sort((a, b) => b[1] - a[1]);
  const total   = entries.reduce((s, [, n]) => s + n, 0);

  if (!total) { sec.innerHTML += '<p class="no-refs">No data.</p>'; return; }

  const CX = 110, CY = 110, R = 88, INNER_R = 52;

  let s = [`<svg class="viz-donut" viewBox="0 0 220 220">`];

  if (entries.length === 1) {
    const col = colors.get(entries[0][0]) || '#7a5c38';
    s.push(`<circle cx="${CX}" cy="${CY}" r="${R}" fill="${col}"/>`);
    s.push(`<circle cx="${CX}" cy="${CY}" r="${INNER_R}" fill="var(--bg-card)"/>`);
  } else {
    let angle = -Math.PI / 2;
    for (const [cat, n] of entries) {
      const slice = (n / total) * 2 * Math.PI;
      const end = angle + slice;
      const x1 = CX + R * Math.cos(angle), y1 = CY + R * Math.sin(angle);
      const x2 = CX + R * Math.cos(end),   y2 = CY + R * Math.sin(end);
      const ix1 = CX + INNER_R * Math.cos(end),   iy1 = CY + INNER_R * Math.sin(end);
      const ix2 = CX + INNER_R * Math.cos(angle), iy2 = CY + INNER_R * Math.sin(angle);
      const large = slice > Math.PI ? 1 : 0;
      const col = colors.get(cat) || '#7a5c38';
      const path = `M ${f(x1)} ${f(y1)} A ${R} ${R} 0 ${large} 1 ${f(x2)} ${f(y2)} L ${f(ix1)} ${f(iy1)} A ${INNER_R} ${INNER_R} 0 ${large} 0 ${f(ix2)} ${f(iy2)} Z`;
      s.push(`<path d="${path}" fill="${col}" stroke="var(--bg-card)" stroke-width="2"><title>${esc(cat)}: ${n.toLocaleString()} (${Math.round(n / total * 100)}%)</title></path>`);
      angle = end;
    }
  }

  // Center label
  s.push(`<text x="${CX}" y="${CY - 6}" class="viz-donut-num" text-anchor="middle">${total.toLocaleString()}</text>`);
  s.push(`<text x="${CX}" y="${CY + 13}" class="viz-donut-lbl" text-anchor="middle">total refs</text>`);
  s.push('</svg>');

  const wrap = document.createElement('div');
  wrap.className = 'viz-donut-wrap';
  wrap.innerHTML = s.join('');
  sec.appendChild(wrap);

  // Stats legend
  const stats = document.createElement('div');
  stats.className = 'viz-legend viz-donut-stats';
  for (const [cat, n] of entries) {
    const col = colors.get(cat) || '#888';
    const pct = Math.round(n / total * 100);
    const item = document.createElement('span');
    item.className = 'viz-legend-item';
    item.innerHTML = `<span class="viz-legend-swatch" style="background:${col}"></span>${esc(cat)}: <strong>${n.toLocaleString()}</strong> (${pct}%)`;
    stats.appendChild(item);
  }
  sec.appendChild(stats);
}

// Shared colored category legend row
function buildCatLegend(allCats, colors) {
  const div = document.createElement('div');
  div.className = 'viz-legend';
  for (const cat of allCats) {
    const item = document.createElement('span');
    item.className = 'viz-legend-item';
    item.innerHTML = `<span class="viz-legend-swatch" style="background:${colors.get(cat) || '#888'}"></span>${esc(cat)}`;
    div.appendChild(item);
  }
  return div;
}

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  try {
    index = await fetchJSON(`${DATA_ROOT}/index.json.zst`);
  } catch (err) {
    bookListEl.innerHTML = `<p style="padding:.75rem;color:var(--muted);font-size:.85rem">
      Could not load index.json.<br>Run <code>python src/parser.py</code> then
      <code>python src/builder.py</code> first.
    </p>`;
    return;
  }

  for (const w of index.works) worksById.set(w.id, w);

  renderCategoryFilters();
  renderSidebar();
  renderWorksList();
  setMode('viz');
}

searchEl.addEventListener("input", () => renderSidebar(searchEl.value));

init();
