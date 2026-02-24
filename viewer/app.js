/**
 * Patristics Viewer — static SPA
 *
 * Expects data files at:
 *   ../data/static/index.json.gz
 *   ../data/static/{book-slug}/{chapter}.json.gz
 *   ../data/static/works/{id}.json.gz
 *
 * To serve locally:
 *   python -m http.server 8000 --directory .   (from project root)
 *   then open http://localhost:8000/viewer/
 */

const DATA_ROOT = "../data/static";

// ── State ─────────────────────────────────────────────────────────────────────
let index = null;         // loaded from index.json.gz
let activeBook = null;    // slug
let activeChapter = null; // number
let activeMode = "scripture";  // "scripture" | "works"
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
const scripturePanelEl = document.getElementById("scripture-panel");
const worksPanelEl     = document.getElementById("works-panel");
const worksListEl      = document.getElementById("works-list");
const worksSearchEl    = document.getElementById("works-search");
const workViewEl       = document.getElementById("work-view");
const workTitleEl      = document.getElementById("work-title");
const workMetaEl       = document.getElementById("work-meta");
const workRefsListEl   = document.getElementById("work-refs-list");
const bookFilterEl     = document.getElementById("book-filter");
const modeTabEls       = document.querySelectorAll(".mode-tab");

// ── Fetch helpers ─────────────────────────────────────────────────────────────
async function fetchJSON(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`Failed to fetch ${url}: ${resp.status}`);
  // If the server set Content-Encoding: gzip the browser already decompressed;
  // otherwise (e.g. simple static servers) decompress the raw gzip stream manually.
  if (url.endsWith('.gz') && !resp.headers.get('Content-Encoding')) {
    const ds = new DecompressionStream('gzip');
    const text = await new Response(resp.body.pipeThrough(ds)).text();
    return JSON.parse(text);
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

// ── Mode switching ────────────────────────────────────────────────────────────
function setMode(mode) {
  activeMode = mode;
  for (const tab of modeTabEls)
    tab.classList.toggle("active", tab.dataset.mode === mode);

  const isScripture = mode === "scripture";
  scripturePanelEl.hidden = !isScripture;
  worksPanelEl.hidden = isScripture;

  if (isScripture) {
    workViewEl.hidden = true;
    if (activeChapter !== null) {
      welcomeEl.hidden = true;
      chapterViewEl.hidden = false;
    } else {
      showWelcome();
    }
  } else {
    chapterViewEl.hidden = true;
    if (activeWorkId !== null) {
      welcomeEl.hidden = true;
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
  bookListEl.innerHTML = "";

  for (const book of index.books) {
    if (term && !book.name.toLowerCase().includes(term)) continue;

    const totalRefs = book.chapters.reduce((s, c) => s + c.count, 0);
    const maxCount  = Math.max(...book.chapters.map(c => c.count));

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

    for (const ch of book.chapters) {
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

  let data;
  try {
    data = await fetchJSON(`${DATA_ROOT}/${bookSlug}/${chapter}.json.gz`);
  } catch (err) {
    refsListEl.innerHTML = `<p class="no-refs">Could not load chapter data. Have you run builder.py?</p>`;
    return;
  }

  renderChapter(data);
}

function renderChapter(data) {
  // Populate author filter
  const authors = [...new Set(data.works.map(w => w.author))].sort();
  authorFilter.innerHTML = `<option value="">All authors</option>`;
  for (const a of authors) {
    const opt = document.createElement("option");
    opt.value = a;
    opt.textContent = a;
    authorFilter.appendChild(opt);
  }

  refsListEl.innerHTML = "";

  if (!data.refs.length) {
    refsListEl.innerHTML = `<p class="no-refs">No references found for this chapter.</p>`;
    return;
  }

  for (const ref of data.refs) {
    const work = data.works[ref.w];

    const card = document.createElement("article");
    card.className = "ref-card";
    card.dataset.author = work.author;

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
      <div class="ref-text">${esc(ref.text)}</div>
    `;
    refsListEl.appendChild(card);
  }

  applyAuthorFilter();
}

function applyAuthorFilter() {
  const val = authorFilter.value;
  for (const card of refsListEl.querySelectorAll(".ref-card")) {
    card.hidden = val && card.dataset.author !== val;
  }
}

authorFilter.addEventListener("change", applyAuthorFilter);

// ── Works sidebar ─────────────────────────────────────────────────────────────
function renderWorksList(filter = "") {
  const term = filter.toLowerCase();
  worksListEl.innerHTML = "";

  for (const work of index.works) {
    if (term && !`${work.author} ${work.title}`.toLowerCase().includes(term)) continue;

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
  activeWorkId = workId;
  renderWorksList(worksSearchEl.value);

  welcomeEl.hidden = true;
  chapterViewEl.hidden = true;
  workViewEl.hidden = false;
  workRefsListEl.innerHTML = `<p class="loading">Loading…</p>`;

  let data;
  try {
    data = await fetchJSON(`${DATA_ROOT}/works/${workId}.json.gz`);
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
      <div class="ref-text">${esc(ref.text)}</div>
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

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  try {
    index = await fetchJSON(`${DATA_ROOT}/index.json.gz`);
  } catch (err) {
    bookListEl.innerHTML = `<p style="padding:.75rem;color:var(--muted);font-size:.85rem">
      Could not load index.json.<br>Run <code>python src/parser.py</code> then
      <code>python src/builder.py</code> first.
    </p>`;
    return;
  }

  renderSidebar();
  renderWorksList();
  showWelcome();
}

searchEl.addEventListener("input", () => renderSidebar(searchEl.value));

init();
