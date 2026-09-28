"""
verse_query.py — Read-only CLI for exploring how a Bible verse is cited in the corpus.

Built for Claude Code (see .claude/skills/verse-commentary/SKILL.md), but usable by hand.

Usage:
    python src/verse_query.py stats "John 1:14"
    python src/verse_query.py passages "John 1:14" --sample stratified --limit 40
    python src/verse_query.py passages "Rom 8" --category Patristics --max-density 3
    python src/verse_query.py context 4052474 --chars 2500

Every passage carries a "density": the number of other citations within ±400 chars
in the same work. High density means a proof-text list (confessions, catechisms,
indexes) rather than actual commentary.
"""

from __future__ import annotations

import argparse
import bisect
import json
import random
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bible_data import ABBREV_LOOKUP, BY_SLUG, roman_to_int
from db import DB_PATH

REPO_ROOT = Path(__file__).parent.parent
KJV_PATH = REPO_ROOT / "viewer" / "data" / "static" / "kjv.json.zst"

# Mirrors cmd/builder: expandToSentences(runes, offset, 2, 3) capped at maxPassageChars.
MAX_PASSAGE_CHARS = 1500
DENSITY_WINDOW = 400

MULTI_BLANK_RE = re.compile(r"\n{3,}")
SINGLE_NL_RE = re.compile(r"(?<!\n)[ \t]*\n[ \t]*(?!\n)")
REF_RE = re.compile(r"^\s*(.+?)\s*(\d+)(?:\s*:\s*(\d+)(?:\s*[-–]\s*(\d+))?)?\s*$")


class QueryError(Exception):
    pass


# ── Reference parsing ─────────────────────────────────────────────────────────

def resolve_book(raw: str) -> dict:
    s = re.sub(r"\s+", " ", raw.lower().replace(".", "")).strip()
    # Roman-numeral prefix: "ii cor" → "2 cor"
    m = re.match(r"^([ivx]+)\s+(.+)$", s)
    if m and roman_to_int(m.group(1)):
        s = f"{roman_to_int(m.group(1))} {m.group(2)}"
    # "1cor" → "1 cor"
    s = re.sub(r"^(\d)(?=[a-z])", r"\1 ", s)
    for cand in (s, s.replace(" ", "-")):
        if cand in BY_SLUG:
            return BY_SLUG[cand]
        if cand in ABBREV_LOOKUP:
            return ABBREV_LOOKUP[cand]
    prefix = [b for b in BY_SLUG.values() if b["name"].lower().startswith(s)]
    if len(prefix) == 1:
        return prefix[0]
    raise QueryError(f"unknown book: {raw!r}")


def parse_ref(ref: str) -> dict:
    m = REF_RE.match(ref)
    if not m:
        raise QueryError(f"cannot parse reference: {ref!r} (expected e.g. 'John 1:14', 'Rom 8', '1 Cor 13:4-7')")
    book = resolve_book(m.group(1))
    chapter = int(m.group(2))
    if not 1 <= chapter <= book["chapters"]:
        raise QueryError(f"{book['name']} has {book['chapters']} chapters, not {chapter}")
    vs = int(m.group(3)) if m.group(3) else None
    ve = int(m.group(4)) if m.group(4) else vs
    if vs is not None and ve < vs:
        raise QueryError(f"bad verse range in {ref!r}")
    return {"book": book, "chapter": chapter, "verse_start": vs, "verse_end": ve}


def ref_label(book: str, chapter: int, vs: int | None, ve: int | None) -> str:
    if vs is None:
        return f"{book} {chapter}"
    if ve is None or ve == vs:
        return f"{book} {chapter}:{vs}"
    return f"{book} {chapter}:{vs}-{ve}"


# ── Data access ───────────────────────────────────────────────────────────────

def open_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise QueryError(f"database not found: {DB_PATH}")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_refs(conn, ref: dict, include_chapter_refs: bool) -> list[dict]:
    sql = """
        SELECT vr.id, vr.manuscript_id, vr.book, vr.chapter, vr.verse_start, vr.verse_end,
               vr.citation_offset, vr.ccel_anchor,
               m.filename, m.author, m.title, m.year, m.category, m.ccel_url
        FROM verse_refs vr JOIN manuscripts m ON m.id = vr.manuscript_id
        WHERE vr.book_slug = ? AND vr.chapter = ?
    """
    params: list = [ref["book"]["slug"], ref["chapter"]]
    if ref["verse_start"] is not None:
        cond = "(vr.verse_start <= ? AND COALESCE(vr.verse_end, vr.verse_start) >= ?)"
        if include_chapter_refs:
            cond = f"({cond} OR vr.verse_start IS NULL)"
        sql += f" AND {cond}"
        params += [ref["verse_end"], ref["verse_start"]]
    rows = [dict(r) for r in conn.execute(sql, params)]
    for r in rows:
        for k in ("author", "title"):
            if r[k]:
                r[k] = " ".join(r[k].split())
    # Several refs can share one citation (e.g. "John 1:14, 16" split in two); keep one.
    seen, out = set(), []
    for r in rows:
        k = (r["manuscript_id"], r["citation_offset"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def add_density(conn, refs: list[dict]) -> None:
    """Annotate each ref with the number of other citations within ±DENSITY_WINDOW chars."""
    ms_ids = sorted({r["manuscript_id"] for r in refs})
    offsets: dict[int, list[int]] = {}
    for i in range(0, len(ms_ids), 500):
        chunk = ms_ids[i:i + 500]
        q = f"""SELECT DISTINCT manuscript_id, citation_offset FROM verse_refs
                WHERE manuscript_id IN ({','.join('?' * len(chunk))})"""
        by_ms = defaultdict(list)
        for mid, off in conn.execute(q, chunk):
            by_ms[mid].append(off)
        for mid, offs in by_ms.items():
            offsets[mid] = sorted(offs)
    for r in refs:
        offs = offsets.get(r["manuscript_id"], [])
        c = r["citation_offset"]
        lo = bisect.bisect_left(offs, c - DENSITY_WINDOW)
        hi = bisect.bisect_right(offs, c + DENSITY_WINDOW)
        r["density"] = max(0, hi - lo - 1)


_file_cache: dict[str, str | None] = {}


def load_text(filename: str) -> str | None:
    """Resolve a DB filename the same way cmd/builder/main.go loadCache does."""
    if filename not in _file_cache:
        rel = filename if filename.startswith("manuscripts/") else f"manuscripts/{filename}"
        path = REPO_ROOT / rel
        try:
            _file_cache[filename] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            _file_cache[filename] = None
    return _file_cache[filename]


def ccel_page_url(ccel_url: str | None, anchor: str | None) -> str | None:
    """Port of ccelPageUrl in viewer/app.js."""
    if not ccel_url:
        return None
    if not anchor:
        return ccel_url
    book_id = ccel_url.rstrip("/").split("/")[-1]
    div_id = anchor.split("-p")[0]
    return f"{ccel_url}/{book_id}.{div_id}.html#fnf_{anchor}"


def load_kjv() -> dict | None:
    try:
        out = subprocess.run(["zstd", "-d", "-c", str(KJV_PATH)], capture_output=True, check=True)
        return json.loads(out.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        return None


# ── Passage extraction (port of cmd/builder/build.go) ─────────────────────────

CLOSING_PUNCT = set("\"')]}”’")
WS = set(" \n\t\r")


def _is_sentence_end(text: str, i: int) -> bool:
    if text[i] not in ".?!":
        return False
    j = i + 1
    n = len(text)
    while j < n and text[j] in CLOSING_PUNCT:
        j += 1
    return j >= n or text[j] in WS


def _sentence_start_before(text: str, pos: int, count: int) -> int:
    found = 0
    for i in range(pos - 1, -1, -1):
        if _is_sentence_end(text, i):
            found += 1
            if found == count:
                j = i + 1
                while j < pos and text[j] in WS:
                    j += 1
                return j
    return 0


def _sentence_end_after(text: str, pos: int, count: int) -> int:
    n = len(text)
    found = 0
    i = pos
    while i < n:
        if text[i] in ".?!":
            j = i + 1
            while j < n and text[j] in CLOSING_PUNCT:
                j += 1
            if j >= n or text[j] in WS:
                found += 1
                if found == count:
                    return j
            i = j
        else:
            i += 1
    return n


def tidy(raw: str) -> str:
    """Collapse line-wrap newlines into spaces; keep paragraph breaks."""
    raw = MULTI_BLANK_RE.sub("\n\n", raw.strip())
    return SINGLE_NL_RE.sub(" ", raw)


def extract_passage(text: str, offset: int, before: int, after: int, cap: int) -> str:
    offset = min(max(offset, 0), len(text))
    start = _sentence_start_before(text, offset, before)
    end = _sentence_end_after(text, offset, after)
    if end - start > cap:
        end = start + cap
    return tidy(text[start:end])


# ── Commands ──────────────────────────────────────────────────────────────────

def century_num(year: int | None) -> int | None:
    return (year - 1) // 100 + 1 if year and year > 0 else None


def century_label(c: int | None) -> str:
    if c is None:
        return "unknown"
    suffix = "th" if 10 <= c % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(c % 10, "th")
    return f"{c}{suffix} c."


def density_bucket(d: int) -> str:
    for hi, label in ((0, "0"), (2, "1-2"), (5, "3-5"), (10, "6-10")):
        if d <= hi:
            return label
    return ">10"


def cmd_stats(conn, args) -> dict:
    ref = parse_ref(args.ref)
    refs = fetch_refs(conn, ref, args.include_chapter_refs)
    add_density(conn, refs)
    b = ref["book"]
    verse_text = None
    kjv = load_kjv()
    if kjv and ref["verse_start"] is not None:
        ch = kjv.get(b["slug"], {}).get(str(ref["chapter"]), {})
        parts = [ch.get(str(v)) for v in range(ref["verse_start"], ref["verse_end"] + 1)]
        verse_text = " ".join(p for p in parts if p) or None
    works = Counter((r["author"] or "?", r["title"] or "?") for r in refs)
    by_century = Counter(century_num(r["year"]) for r in refs)
    return {
        "ref": ref_label(b["name"], ref["chapter"], ref["verse_start"], ref["verse_end"]),
        "kjv": verse_text,
        "total_citations": len(refs),
        "distinct_works": len(works),
        "distinct_authors": len({r["author"] for r in refs}),
        "by_category": Counter(r["category"] or "?" for r in refs).most_common(),
        "by_century": [(century_label(c), n) for c, n in
                       sorted(by_century.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))],
        "by_density": sorted(Counter(density_bucket(r["density"]) for r in refs).items(),
                             key=lambda kv: ["0", "1-2", "3-5", "6-10", ">10"].index(kv[0])),
        "top_authors": Counter(r["author"] or "?" for r in refs).most_common(15),
        "top_works": [(f"{a} — {t}", n) for (a, t), n in works.most_common(15)],
    }


def print_stats(s: dict) -> None:
    print(f"== {s['ref']} ==")
    if s["kjv"]:
        print(f"KJV: {s['kjv']}")
    print(f"{s['total_citations']} citations in {s['distinct_works']} works by {s['distinct_authors']} authors\n")
    for key, title in (("by_category", "By category"), ("by_century", "By century"),
                       ("by_density", "By density (other citations within ±400 chars)"),
                       ("top_authors", "Top authors"), ("top_works", "Top works")):
        print(f"{title}:")
        for name, n in s[key]:
            print(f"  {n:5d}  {name}")
        print()


def select_refs(conn, args) -> tuple[list[dict], int]:
    ref = parse_ref(args.ref)
    refs = fetch_refs(conn, ref, args.include_chapter_refs)
    cats = {c.lower() for c in args.category or []}
    excl = {c.lower() for c in (args.exclude_category if args.exclude_category is not None else ["Scripture"])}
    if cats:
        excl -= cats
    out = []
    for r in refs:
        cat = (r["category"] or "").lower()
        if cats and cat not in cats:
            continue
        if cat in excl:
            continue
        if args.author and args.author.lower() not in (r["author"] or "").lower():
            continue
        if args.before_year and not (r["year"] and r["year"] < args.before_year):
            continue
        if args.after_year and not (r["year"] and r["year"] >= args.after_year):
            continue
        if args.max_span is not None and r["verse_start"] is not None and \
                (r["verse_end"] or r["verse_start"]) - r["verse_start"] + 1 > args.max_span:
            continue
        out.append(r)
    add_density(conn, out)
    if args.max_density is not None:
        out = [r for r in out if r["density"] <= args.max_density]

    out.sort(key=lambda r: (r["year"] is None, r["year"] or 0, r["manuscript_id"], r["citation_offset"]))
    if args.sample == "stratified":
        rng = random.Random(args.seed)
        groups = defaultdict(list)
        for r in out:
            groups[r["author"] or "?"].append(r)
        authors = sorted(groups)
        rng.shuffle(authors)
        for a in authors:
            rng.shuffle(groups[a])
        ordered = []
        depth = 0
        while len(ordered) < len(out):
            for a in authors:
                if depth < len(groups[a]):
                    ordered.append(groups[a][depth])
            depth += 1
        out = ordered
    return out, len(out)


def cmd_passages(conn, args) -> dict:
    refs, total = select_refs(conn, args)
    page = refs[args.offset:]
    results, seen_text = [], set()
    consumed = 0
    for r in page:
        if len(results) >= args.limit:
            break
        consumed += 1
        text = load_text(r["filename"])
        if text is None:
            passage = f"[source file not found: {r['filename']}]"
        else:
            passage = extract_passage(text, r["citation_offset"], args.before, args.after, args.max_chars)
        if passage in seen_text:
            continue
        seen_text.add(passage)
        results.append({
            "id": r["id"],
            "author": r["author"],
            "title": r["title"],
            "year": r["year"],
            "category": r["category"],
            "cites": ref_label(r["book"], r["chapter"], r["verse_start"], r["verse_end"]),
            "density": r["density"],
            "link": ccel_page_url(r["ccel_url"], r["ccel_anchor"]),
            "text": passage,
        })
    return {"matched": total, "offset": args.offset, "next_offset": args.offset + consumed
            if args.offset + consumed < total else None, "results": results}


def print_passages(p: dict) -> None:
    print(f"{p['matched']} matching citations; showing {len(p['results'])} from offset {p['offset']}"
          + (f" (next: --offset {p['next_offset']})" if p["next_offset"] is not None else " (end)"))
    for r in p["results"]:
        yr = r["year"] if r["year"] else "n.d."
        print(f"\n#{r['id']} · {r['author']} · {r['title']} ({yr}) · {r['category']} · {r['cites']} · density={r['density']}")
        if r["link"]:
            print(r["link"])
        print(r["text"])


def cmd_context(conn, args) -> dict:
    row = conn.execute("""
        SELECT vr.*, m.filename, m.author, m.title, m.year, m.category, m.ccel_url
        FROM verse_refs vr JOIN manuscripts m ON m.id = vr.manuscript_id WHERE vr.id = ?
    """, (args.ref_id,)).fetchone()
    if row is None:
        raise QueryError(f"no verse_ref with id {args.ref_id}")
    r = dict(row)
    text = load_text(r["filename"])
    if text is None:
        raise QueryError(f"source file not found: {r['filename']}")
    c = min(max(r["citation_offset"], 0), len(text))
    start, end = max(0, c - args.chars), min(len(text), c + args.chars)
    # Snap to whitespace so the window doesn't open or close mid-word.
    while start > 0 and text[start - 1] not in WS:
        start -= 1
    while end < len(text) and text[end] not in WS:
        end += 1
    marked = text[start:c] + "⟪CITATION⟫" + text[c:end]
    return {
        "id": r["id"], "author": r["author"], "title": r["title"], "year": r["year"],
        "category": r["category"],
        "cites": ref_label(r["book"], r["chapter"], r["verse_start"], r["verse_end"]),
        "link": ccel_page_url(r["ccel_url"], r["ccel_anchor"]),
        "text": tidy(marked),
    }


def print_context(c: dict) -> None:
    yr = c["year"] if c["year"] else "n.d."
    print(f"#{c['id']} · {c['author']} · {c['title']} ({yr}) · {c['category']} · {c['cites']}")
    if c["link"]:
        print(c["link"])
    print("(⟪CITATION⟫ marks the citation position; it is not part of the text)\n")
    print(c["text"])


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("ref", help="e.g. 'John 1:14', 'Rom 8', '1 Cor 13:4-7'")
        p.add_argument("--include-chapter-refs", action="store_true",
                       help="also include chapter-only citations when querying specific verses")
        p.add_argument("--json", action="store_true")

    p_stats = sub.add_parser("stats", help="overview of who cites a verse, when, and how densely")
    common(p_stats)

    p_pass = sub.add_parser("passages", help="citing passages with surrounding sentences")
    common(p_pass)
    p_pass.add_argument("--category", action="append", help="only these categories (repeatable)")
    p_pass.add_argument("--exclude-category", action="append",
                        help="skip these categories (repeatable; default: Scripture)")
    p_pass.add_argument("--author", help="author substring (case-insensitive)")
    p_pass.add_argument("--before-year", type=int)
    p_pass.add_argument("--after-year", type=int)
    p_pass.add_argument("--max-density", type=int, help="drop passages with more nearby citations than this")
    p_pass.add_argument("--max-span", type=int,
                        help="drop citations of ranges wider than N verses (e.g. 'John 11:1-57')")
    p_pass.add_argument("--limit", type=int, default=40)
    p_pass.add_argument("--offset", type=int, default=0)
    p_pass.add_argument("--sample", choices=["chronological", "stratified"], default="chronological",
                        help="stratified = round-robin across authors")
    p_pass.add_argument("--seed", type=int, default=0)
    p_pass.add_argument("--before", type=int, default=2, help="sentences before the citation")
    p_pass.add_argument("--after", type=int, default=3, help="sentences after the citation")
    p_pass.add_argument("--max-chars", type=int, default=MAX_PASSAGE_CHARS)

    p_ctx = sub.add_parser("context", help="wide raw window around one citation")
    p_ctx.add_argument("ref_id", type=int)
    p_ctx.add_argument("--chars", type=int, default=2500, help="chars on each side")
    p_ctx.add_argument("--json", action="store_true")

    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        conn = open_db()
        cmd, printer = {"stats": (cmd_stats, print_stats),
                        "passages": (cmd_passages, print_passages),
                        "context": (cmd_context, print_context)}[args.cmd]
        result = cmd(conn, args)
    except QueryError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        printer(result)


if __name__ == "__main__":
    main()
