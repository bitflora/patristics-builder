"""
Parse ThML XML files (downloaded by fetch_thml.py) and insert citations into SQLite.

Only <scripRef> elements that carry a 'parsed' attribute are processed.
The 'parsed' attribute format is semicolon-delimited segments, each:
    version|BookName|fromChapter|fromVerse|toChapter|toVerse

A clean plain-text version of each work is saved alongside the XML so that the
Go builder can extract passage text using the stored character offsets.

Usage:
  python src/parse_thml.py                         # parse all files in manifest
  python src/parse_thml.py ccel_thml/kempis/imit.xml   # single file
  python src/parse_thml.py --stats                 # show DB stats after parsing
  python src/parse_thml.py --dry-run               # print citations, no DB write
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).parent))

from bible_data import (ABBREV_LOOKUP, BOOKS, BY_SLUG, greek_psalm_to_hebrew,
                        remap_versification, roman_to_int, validate_ref)
from verse_counts import max_verse
from db import (get_connection, create_schema, delete_refs_for_manuscript,
                delete_manuscripts_for_file, upsert_manuscript, DB_PATH)
from parser import extract_passage_offsets, _normalize_creator
from categorize import categorise_all

PROJECT_ROOT = Path(__file__).parent.parent
CCEL_THML_DIR = PROJECT_ROOT / "manuscripts" / "ccel_thml"
MANIFEST_PATH = CCEL_THML_DIR / "manifest.json"

# XML element local-names whose text content should be excluded from clean text.
# Their .tail (text after their closing tag) is still included — it belongs to
# the parent element's prose flow.
_SKIP_CONTENT_TAGS = frozenset({
    "ThML.head",
    "head",
    "note",       # footnotes / marginal notes
    "scripCom",   # scripture commentary marker (no prose content)
    "index",      # index entries
    "pb",         # page-break markers
    "milestone",
})

# Compiled once: matches numeric book prefix without a space, e.g. "1John" → "1 John"
_NUM_PREFIX_RE = re.compile(r"^([1-4])([A-Za-z])")

# Maps CCEL authorIDs found in ANF nested <ThML.head> blocks to (display_name, approx_year_CE).
# Year is the approximate historical writing date; None if unknown or modern.
ANF_AUTHOR_MAP: dict[str, tuple[str, int | None]] = {
    "alexander_alexandria": ("Alexander of Alexandria",  312),
    "alexander_capp":       ("Alexander of Cappadocia",  250),
    "alexander_lyc":        ("Alexander of Lycopolis",   300),
    "anatolius":            ("Anatolius of Laodicea",    270),
    "anonymous":            ("Anonymous",                None),
    "archelaus":            ("Archelaus of Carrhae",     278),
    "aristides":            ("Aristides of Athens",      125),
    "arnobius":             ("Arnobius of Sicca",        305),
    "asterius":             ("Asterius of Cappadocia",   225),
    "athenagoras":          ("Athenagoras of Athens",    177),
    "caius":                ("Gaius of Rome",            200),
    "clement_alex":         ("Clement of Alexandria",    200),
    "clement_rome":         ("Clement of Rome",           96),
    "commodianus":          ("Commodianus",              250),
    "cyprian":              ("Cyprian of Carthage",      250),
    "dionysius":            ("Dionysius of Alexandria",  265),
    "felix":                ("Minucius Felix",           200),
    "gregory_thau":         ("Gregory Thaumaturgus",     265),
    "hermas":               ("Hermas",                   155),
    "hippolytus":           ("Hippolytus of Rome",       215),
    "ignatius":             ("Ignatius of Antioch",      107),
    "irenaeus":             ("Irenaeus of Lyon",         180),
    "juliusafricanus":      ("Julius Africanus",         225),
    "justin_martyr":        ("Justin Martyr",            165),
    "lactantius":           ("Lactantius",               313),
    "malchion":             ("Malchion of Antioch",      268),
    "mathetes":             ("Mathetes",                 130),
    "methodius":            ("Methodius of Olympus",     311),
    "novatian":             ("Novatian of Rome",         258),
    "origen":               ("Origen of Alexandria",     250),
    "pamphilus":            ("Pamphilus of Caesarea",    310),
    "peter_alexandria":     ("Peter of Alexandria",      311),
    "phileas":              ("Phileas of Thmuis",        306),
    "pierus":               ("Pierius of Alexandria",    280),
    "polycarp":             ("Polycarp of Smyrna",       155),
    "rutherford_an":        ("W.G. Rutherford",          180),   # translator; Passion of the Scillitan Martyrs
    "schaff":               ("Philip Schaff",            1885),
    "tatian":               ("Tatian",                   175),
    "tertullian":           ("Tertullian",               200),
    "theodotus":            ("Theodotus of Byzantium",   200),
    "theognostus":          ("Theognostus of Alexandria", 265),
    "theonas":              ("Theonas of Alexandria",    300),
    "theophilus":           ("Theophilus of Antioch",    180),
    "venantius":            ("Venantius",                580),
    "victorinus":           ("Victorinus of Pettau",     303),
    "zosimus":              ("Zosimus of Panopolis",     300),
    # NPNF Series 1 & 2 authors
    "augustine":            ("Augustine of Hippo",       400),
    "chrysostom":           ("John Chrysostom",          400),
    "eusebius":             ("Eusebius of Caesarea",     313),
    "socrates":             ("Socrates Scholasticus",    439),
    "sozomen":              ("Sozomen",                  440),
    "theodoret":            ("Theodoret of Cyrrhus",     450),
    "jerome":               ("Jerome of Stridon",        420),
    "gennadius":            ("Gennadius of Marseilles",  495),
    "rufinus":              ("Rufinus of Aquileia",      411),
    "athanasius":           ("Athanasius of Alexandria", 373),
    "gregorynyssa":         ("Gregory of Nyssa",         394),
    "cyril_jer":            ("Cyril of Jerusalem",       386),
    "gregory_naz":          ("Gregory Nazianzen",        390),
    "basil":                ("Basil of Caesarea",        379),
    "hilary_poit":          ("Hilary of Poitiers",       368),
    "damascus":             ("John of Damascus",         749),
    "ambrose":              ("Ambrose of Milan",         397),
    "sulpiciusseverus":     ("Sulpicius Severus",        420),
    "vincent_lerins":       ("Vincent of Lérins",        445),
    "cassian":              ("John Cassian",             435),
    "leo":                  ("Leo the Great",            461),
    "gregory":              ("Gregory the Great",        604),
    "ephraim":              ("Ephrem the Syrian",        373),
    "aphrahat":             ("Aphrahat",                 345),
}


def _local(tag) -> str:
    """Strip XML namespace URI from a tag, returning just the local name.
    Returns empty string for non-element nodes (comments, PIs) whose tag is callable."""
    if callable(tag):
        return ""  # lxml Comment / ProcessingInstruction nodes
    return tag.split("}")[-1] if "}" in tag else tag


# ── Book name resolution ──────────────────────────────────────────────────────

# Build a normalised name → book dict lookup that covers full names and
# common CCEL variations ("Psalm" vs "Psalms", "1John" vs "1 John", etc.)
_NAME_LOOKUP: dict[str, dict] = {}
for _b in BOOKS:
    _NAME_LOOKUP[_b["name"].lower()] = _b
    # also register slug as lookup key ("1-corinthians" → same book)
    _NAME_LOOKUP[_b["slug"]] = _b
    # singular/plural variants for Psalms
    if _b["name"] == "Psalms":
        _NAME_LOOKUP["psalm"] = _b
    if _b["name"] == "Song of Solomon":
        _NAME_LOOKUP["song of songs"] = _b
        _NAME_LOOKUP["canticle of canticles"] = _b
    if _b["name"] == "Revelation":
        _NAME_LOOKUP["revelations"] = _b
        _NAME_LOOKUP["apocalypse"] = _b

# CCEL's own book codes (OSIS-style) that neither the names nor abbrevs cover.
# The Kingdoms books are the Greek/Vulgate names for Samuel and Kings.
for _code, _slug in {
    "song":   "song-of-solomon",
    "sus":    "susanna",
    "prazar": "prayer-of-azariah",
    "prman":  "prayer-of-manasseh",
    "1kgdms": "1-samuel",
    "2kgdms": "2-samuel",
    "3kgdms": "1-kings",
    "4kgdms": "2-kings",
}.items():
    _NAME_LOOKUP[_code] = BY_SLUG[_slug]

# Merge abbreviation lookup as well (lower priority)
for _k, _v in ABBREV_LOOKUP.items():
    _NAME_LOOKUP.setdefault(_k, _v)

# Roman-numeral book prefix: "iTim" → "1 tim", "iiJohn" → "2 john". Matched
# case-sensitively so that ordinary names like "Isa" are never split.
_ROMAN_PREFIX_RE = re.compile(r"^(iii|ii|i)(?=[A-Z])")
_ROMAN_PREFIX_NUM = {"i": "1", "ii": "2", "iii": "3"}


def _resolve_book_name(name: str) -> dict | None:
    """
    Map a book name from a ThML 'parsed' attribute segment to a canonical book dict.
    Returns None if the name cannot be resolved.
    """
    name = name.strip()
    lower = name.lower()

    if lower in _NAME_LOOKUP:
        return _NAME_LOOKUP[lower]

    # Handle "1John" → "1 john", "2Cor" → "2 cor", etc.
    spaced = _NUM_PREFIX_RE.sub(r"\1 \2", lower)
    if spaced != lower and spaced in _NAME_LOOKUP:
        return _NAME_LOOKUP[spaced]

    m = _ROMAN_PREFIX_RE.match(name)
    if m:
        arabic = f"{_ROMAN_PREFIX_NUM[m.group(1)]} {lower[m.end():]}"
        if arabic in _NAME_LOOKUP:
            return _NAME_LOOKUP[arabic]

    return None


# ── ThML XML parsing ──────────────────────────────────────────────────────────

def _load_xml(path: Path) -> etree._Element | None:
    """
    Parse a ThML file with lxml's recovery parser (handles missing DTD entities,
    malformed markup, etc.).  Returns the root element, or None on failure.
    """
    parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
    try:
        tree = etree.parse(str(path), parser)
        return tree.getroot()
    except Exception as exc:
        print(f"  [XML error] {path.name}: {exc}", file=sys.stderr)
        return None


def _extract_metadata(root: etree._Element) -> dict:
    """
    Extract author, title, year from <ThML.head>.
    Returns a dict with those keys (values may be None if not found).
    """
    result = {"author": None, "title": None, "year": None}

    def find_text(tag: str) -> str | None:
        # Search anywhere in document for the given local tag name
        for el in root.iter():
            if _local(el.tag) == tag and el.text:
                return el.text.strip()
        return None

    raw_title = find_text("DC.Title")
    if raw_title:
        result["title"] = raw_title

    # Prefer DC.Creator sub="Author" (actual author) over sub="Editor" (compiler/translator).
    # CCEL IDs (scheme="ccel") are mapped via ANF_AUTHOR_MAP; short-form display names used directly.
    # Multi-author volumes (e.g. npnf202 Socrates+Sozomen) produce joined names.
    # TODO: split multi-author NPNF volumes into sub-works (like ANF) once div1-based
    #       section boundaries are mapped per volume.
    author_ccel_ids: list[str] = []
    author_short_forms: list[str] = []
    for el in root.iter():
        if _local(el.tag) != "DC.Creator" or not el.text:
            continue
        sub = el.get("sub", "").lower()
        scheme = el.get("scheme", "").lower()
        if sub == "author":
            if scheme == "ccel":
                author_ccel_ids.append(el.text.strip())
            elif scheme == "short-form":
                author_short_forms.append(el.text.strip())

    if author_ccel_ids:
        display_names = [ANF_AUTHOR_MAP[a][0] if a in ANF_AUTHOR_MAP
                         else _normalize_creator(a)
                         for a in author_ccel_ids]
        result["author"] = ", ".join(display_names)
    elif author_short_forms:
        result["author"] = ", ".join(author_short_forms)
    else:
        raw_creator = find_text("DC.Creator")
        if raw_creator:
            result["author"] = _normalize_creator(raw_creator)

    # Year: prefer sub="Original"/"Written"/"Composed" (historical composition date);
    # fall back to sub="Published" only if it looks like a historical print date (< 1970);
    # ignore digitization dates (e.g. CCEL's 1999 "Published" dates).
    year_candidates = []  # (priority, year)
    for el in root.iter():
        if _local(el.tag) != "DC.Date" or not el.text:
            continue
        year_m = re.search(r"\b(1\d{3}|[2-9]\d{2})\b", el.text)
        if not year_m:
            continue
        candidate = int(year_m.group(1))
        sub = el.get("sub", "").lower()
        if sub in ("original", "written", "composed"):
            year_candidates.append((0, candidate))  # highest priority
        elif sub in ("published", "created") and candidate < 1970:
            year_candidates.append((1, candidate))  # plausible print/creation date
        elif not sub and candidate < 1970:
            year_candidates.append((2, candidate))  # untyped but historic
    # Fallback: extract death year from DC.Creator file-as field, e.g.
    # "Augustine, Saint, Bishop of Hippo (345-430)" → 430
    # "Origen (c. 185-c. 254)" → 254
    # Only used if no DC.Date candidate was found.
    if not year_candidates:
        for el in root.iter():
            if _local(el.tag) != "DC.Creator":
                continue
            if el.get("sub", "").lower() != "author":
                continue
            if el.get("scheme", "").lower() != "file-as":
                continue
            text = (el.text or "").strip()
            paren_m = re.search(r"\(([^)]+)\)", text)
            if paren_m:
                nums = re.findall(r"\d{3,4}", paren_m.group(1))
                if nums:
                    death_year = int(nums[-1])  # last number = death year
                    if death_year < 2000:
                        year_candidates.append((3, death_year))
            break  # only use the first Author file-as entry

    # Last resort: the known author's approximate writing date (e.g. NPNF volumes,
    # whose headers carry only CCEL's digitization date).
    if not year_candidates:
        for a in author_ccel_ids:
            if a in ANF_AUTHOR_MAP and ANF_AUTHOR_MAP[a][1] is not None:
                year_candidates.append((4, ANF_AUTHOR_MAP[a][1]))
                break

    if year_candidates:
        result["year"] = min(year_candidates, key=lambda x: x[0])[1]

    return result


class _TextBuilder:
    """
    Walks an lxml element tree in document order, concatenating text nodes into
    a clean string while recording the char offset of every <scripRef> element.

    Elements in _SKIP_CONTENT_TAGS have their text/children suppressed; their
    .tail is still included (it belongs to the parent's prose flow).
    """

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._offset: int = 0
        # list of (element, offset_at_start_of_scripRef_text)
        self.scripref_hits: list[tuple[etree._Element, int]] = []

    def _append(self, text: str) -> None:
        if text:
            self._parts.append(text)
            self._offset += len(text)

    def walk(self, el: etree._Element, in_skip: bool = False) -> None:
        if callable(el.tag):
            # Comment / processing instruction: its .text is markup noise
            # (e.g. CCEL's "added reason=AutoIndexing"), but its tail is prose.
            if not in_skip:
                self._append(el.tail)
            return

        tag = _local(el.tag)
        entering_skip = tag in _SKIP_CONTENT_TAGS
        skip_content = in_skip or entering_skip

        if not skip_content:
            if tag == "scripRef":
                self.scripref_hits.append((el, self._offset))
            self._append(el.text)
            for child in el:
                self.walk(child, in_skip=False)
        else:
            # scripRef inside a note/footnote: anchor to the current prose
            # offset (just before the note) — still a valid passage context.
            if tag == "scripRef":
                self.scripref_hits.append((el, self._offset))
            # Still recurse into children so their tails (belonging to this
            # skipped element) are also suppressed, but we must visit them to
            # handle deeply-nested tails correctly.
            for child in el:
                self.walk(child, in_skip=True)

        # Tail always belongs to the parent; include it unless the parent is skipped.
        if not in_skip:
            self._append(el.tail)

    @property
    def text(self) -> str:
        return "".join(self._parts)


# ── Parsed-attribute citation decoding ───────────────────────────────────────

# Ranges spanning more chapters than this are surveys ("Gen. 1-50"), not
# citations of each chapter; only their first chapter is recorded.
MAX_CHAPTER_SPAN = 5

# No book has this many chapters: such a number is a year, page or column
# ("Mar. 16, 1895", "Col. 1614"), so the whole scripRef is a false positive.
_IMPOSSIBLE_CHAPTER = 200
_DATE_RE = re.compile(r"^\s*Mar(?:ch)?\.?\s+\d{1,2},?\s+\d{4}\b")

# Roman numerals of 101+ in the human-readable 'passage' attribute. CCEL's
# tagger drops their leading "c" ("Ps. civ. 24" → parsed as Ps 4:24).
_HUNDREDS_ROMAN_RE = re.compile(r"\b(c[clxvi]+)\b", re.IGNORECASE)
_ANY_ROMAN_RE = re.compile(r"\b([clxvi]+)\b", re.IGNORECASE)

# Douay "1 Kings" is 1 Samuel; CCEL tags it vul|1Kgs without converting.
_DOUAY_1KINGS_RE = re.compile(r"^\s*(?:1|I)\s*K", re.IGNORECASE)


def _hundreds_fixups(passage: str | None) -> dict[int, int]:
    """
    Map each chapter number CCEL may have produced by dropping a leading "c"
    to the real chapter, e.g. {4: 104} for "Ps. civ. 24". Chapters that also
    appear as their own numeral in the passage ("Ps. iv. 4; civ. 4") are left
    alone, since we can't tell which segment is which.
    """
    if not passage:
        return {}
    plain = {roman_to_int(m) for m in _ANY_ROMAN_RE.findall(passage)}
    fixups = {}
    for numeral in _HUNDREDS_ROMAN_RE.findall(passage):
        value = roman_to_int(numeral)
        if value and 100 < value <= 150 and value - 100 not in plain:
            fixups[value - 100] = value
    return fixups


def _decode_segments(parsed: str) -> list[dict]:
    """Split a 'parsed' attribute into raw segments with the book resolved."""
    segs = []
    for segment in parsed.split(";"):
        parts = segment.strip().split("|")
        if len(parts) == 7 and parts[6] == "":
            parts.pop()  # stray trailing "|"
        if len(parts) != 6:
            continue
        version, book_name, *nums = parts
        book = _resolve_book_name(book_name)
        if book is None:
            continue
        try:
            fc, fv, tc, tv = (int(n) for n in nums)
        except ValueError:
            continue
        segs.append({"version": version.lower(), "book_name": book_name.lower(),
                     "book": book, "fc": fc, "fv": fv, "tc": tc, "tv": tv})
    return segs


def _is_chapter_only(seg: dict) -> bool:
    return seg["fv"] == seg["tc"] == seg["tv"] == 0


def _merge_comma_verses(segs: list[dict]) -> list[dict]:
    """
    "Mt 5,45" means Matthew 5:45, but CCEL tags it as chapters 5 and 45. A
    chapter-only segment past the book's last chapter that follows a segment
    of the same book is really a verse in that segment's chapter.
    """
    out: list[dict] = []
    for seg in segs:
        prev = out[-1] if out else None
        if (prev is not None and seg["book"] is prev["book"] and _is_chapter_only(seg)
                and seg["fc"] > seg["book"]["chapters"]):
            limit = max_verse(seg["book"]["slug"], prev["fc"])
            if limit is not None and seg["fc"] <= limit:
                if _is_chapter_only(prev):
                    out.pop()  # the "5" in "5,45" was the chapter, not a citation of it
                seg = {**seg, "fc": prev["fc"], "fv": seg["fc"]}
        out.append(seg)
    return out


def _expand_range(sc: int, sv: int | None, ec: int, ev: int | None,
                  slug: str) -> list[tuple[int, int | None, int | None]]:
    """
    Turn a start point (sc, sv) and end point (ec, ev) into per-chapter
    (chapter, verse_start, verse_end) tuples. A verse of None means
    "whole chapter" at the start, or "to the end of the chapter" at the end.
    """
    if ec == sc:
        return [(sc, sv, ev)]
    if ec < sc or ec - sc + 1 > MAX_CHAPTER_SPAN:
        return [(sc, sv, None)]
    rows = [(sc, sv, max_verse(slug, sc) if sv else None)]
    rows += [(ch, None, None) for ch in range(sc + 1, ec)]
    rows.append((ec, 1, ev) if ev else (ec, None, None))
    return rows


def _parse_parsed_attr(parsed: str, passage: str | None = None) -> list[dict]:
    """
    Decode a ThML 'parsed' attribute string into a list of citation dicts.

    Format: version|Book|fromChapter|fromVerse|toChapter|toVerse
    Multiple citations separated by semicolons. *passage* is the scripRef's
    human-readable 'passage' attribute, used to repair CCEL tagging errors.

    Returns a list of dicts with keys:
        book_entry, chapter, verse_start, verse_end
    Chapter and verse numbers are converted to KJV versification, and a range
    across chapters yields one dict per chapter. Segments that cannot be
    resolved, or that point at verses that don't exist, are skipped.
    """
    if passage and _DATE_RE.match(passage):
        return []
    segs = _decode_segments(parsed)
    if any(max(s["fc"], s["tc"]) >= _IMPOSSIBLE_CHAPTER for s in segs):
        return []
    segs = _merge_comma_verses(segs)
    fixups = _hundreds_fixups(passage)

    results = []
    for seg in segs:
        book = seg["book"]
        from_ch = seg["fc"]
        if from_ch < 1:
            continue  # whole-book reference; no useful chapter to store

        # End point of the range; (from_ch, None) means a single verse/chapter.
        to_ch = seg["tc"] if seg["tc"] >= 1 else from_ch
        start_v = seg["fv"] or None
        end_v = seg["tv"] or None
        if to_ch == from_ch and end_v == start_v:
            end_v = None
        single = to_ch == from_ch and end_v is None

        if from_ch in fixups:
            if to_ch == from_ch:
                to_ch = fixups[from_ch]
            from_ch = fixups[from_ch]

        if (seg["version"] == "vul" and seg["book_name"] == "1kgs"
                and _DOUAY_1KINGS_RE.match(passage or "")):
            book = BY_SLUG["1-samuel"]
        elif seg["version"] == "vul" and seg["book_name"] in ("1esd", "2esd"):
            # Vulgate 1 & 2 Esdras are Ezra and Nehemiah
            book = BY_SLUG["ezra" if seg["book_name"] == "1esd" else "nehemiah"]
        elif seg["version"].startswith("lxx") and seg["book_name"] == "2esd":
            # LXX Esdras B is Ezra (1-10) + Nehemiah (11-23)
            if from_ch > 10 and to_ch > 10:
                book, from_ch, to_ch = BY_SLUG["nehemiah"], from_ch - 10, to_ch - 10
            elif from_ch <= 10 and to_ch <= 10:
                book = BY_SLUG["ezra"]

        if book["slug"] == "psalms" and seg["version"].startswith(("vul", "lxx")):
            from_ch, start_v = greek_psalm_to_hebrew(from_ch, start_v)
            if single:
                to_ch = from_ch
            else:
                to_ch, end_v = greek_psalm_to_hebrew(to_ch, end_v)
        else:
            orig = book
            book, from_ch, start_v = remap_versification(orig, from_ch, start_v)
            if single:
                to_ch = from_ch
            else:
                end_book, to_ch, end_v = remap_versification(orig, to_ch, end_v)
                if end_book is not book:
                    to_ch, end_v = from_ch, None

        for ch, vs, ve in _expand_range(from_ch, start_v, to_ch, end_v, book["slug"]):
            checked = validate_ref(book, ch, vs, ve)
            if checked is None:
                continue
            results.append(
                {
                    "book_entry": book,
                    "chapter": ch,
                    "verse_start": checked[0],
                    "verse_end": checked[1],
                }
            )
    return results


# ── ANF compilation parsing ───────────────────────────────────────────────────

def _has_sub_works(root: etree._Element) -> bool:
    """True if this document has 2+ <ThML.head> blocks with <authorID> children.
    That pattern marks ANF compilation volumes containing works by multiple authors."""
    count = 0
    for el in root.iter():
        if _local(el.tag) != "ThML.head":
            continue
        for child in el.iter():
            if _local(child.tag) == "authorID" and child.text:
                count += 1
                break
        if count > 1:
            return True
    return False


def _parse_thml_subworks(
    root: etree._Element,
    builder: "_TextBuilder",
    conn,
    rel_filename: str,
    ccel_url: str,
    dry_run: bool,
    verbose: bool,
) -> int:
    """Parse an ANF compilation: one manuscript record per nested <ThML.head>/authorID section.

    Calls delete_manuscripts_for_file first for idempotency, then upserts one
    manuscript per distinct author_id and inserts verse_refs under the right record.
    """
    clean_text = builder.text

    # ── Pass 1: collect unique sections (author_id → title/name/year) ─────────
    seen_authors: dict[str, int] = {}       # author_id → manuscript_id
    author_info: dict[str, tuple[str, str, int | None]] = {}  # author_id → (title, display, year)

    for el in root.iter():
        if _local(el.tag) != "ThML.head":
            continue
        author_id_el = None
        title_el = None
        for child in el.iter():
            clocal = _local(child.tag)
            if clocal == "authorID" and child.text and author_id_el is None:
                author_id_el = child
            elif clocal == "DC.Title" and child.text and title_el is None:
                title_el = child
        if author_id_el is None or not author_id_el.text:
            continue
        author_id = author_id_el.text.strip()
        if author_id in author_info:
            continue  # same author in multiple sections → one record
        title_text = title_el.text.strip() if title_el is not None else None
        display_name, year = ANF_AUTHOR_MAP.get(author_id, (author_id, None))
        author_info[author_id] = (title_text or display_name, display_name, year)

    print(f"  Compilation: {len(author_info)} authors — "
          + ", ".join(author_info[a][1] for a in author_info))

    if not dry_run:
        delete_manuscripts_for_file(conn, rel_filename)
        for author_id, (title, display_name, year) in author_info.items():
            ms_id = upsert_manuscript(
                conn, rel_filename,
                work_key=author_id,
                author=display_name,
                title=title,
                year=year,
                ccel_url=ccel_url,
                category="Other",
                source_format="thml",
            )
            seen_authors[author_id] = ms_id

    # ── Pass 2: assign each scripRef to its section author ────────────────────
    ref_ids = {id(el) for el, _ in builder.scripref_hits}
    assignment: dict[int, str | None] = {}   # id(el) → author_id
    current_author_id: str | None = None

    for el in root.iter():
        local = _local(el.tag)
        if local == "ThML.head":
            for child in el.iter():
                if _local(child.tag) == "authorID" and child.text:
                    current_author_id = child.text.strip()
                    break
        elif id(el) in ref_ids:
            assignment[id(el)] = current_author_id

    # ── Pass 3: build and insert rows ─────────────────────────────────────────
    rows: list[tuple] = []

    for el, cite_offset in builder.scripref_hits:
        parsed_attr = el.get("parsed")
        if not parsed_attr:
            continue
        author_id = assignment.get(id(el))
        if not author_id or author_id not in author_info:
            continue
        ms_id = seen_authors.get(author_id, 0) if not dry_run else 0

        citations = _parse_parsed_attr(parsed_attr, el.get("passage"))
        if not citations:
            continue

        passage_start, passage_end = extract_passage_offsets(clean_text, cite_offset)
        ccel_anchor = el.get("id") or None

        for cit in citations:
            be = cit["book_entry"]
            if verbose or dry_run:
                display_name = author_info[author_id][1]
                ref_str = f"{be['name']} {cit['chapter']}"
                if cit["verse_start"]:
                    ref_str += f":{cit['verse_start']}"
                    if cit["verse_end"]:
                        ref_str += f"-{cit['verse_end']}"
                preview = clean_text[passage_start:passage_start + 80].replace("\n", " ")
                print(f"  [{display_name}] {ref_str:30s}  …{preview}…")
            rows.append((
                ms_id,
                be["name"],
                be["slug"],
                cit["chapter"],
                cit["verse_start"],
                cit["verse_end"],
                cite_offset,
                passage_start,
                passage_end,
                ccel_anchor,
            ))

    if not dry_run and rows:
        conn.executemany(
            """INSERT INTO verse_refs
               (manuscript_id, book, book_slug, chapter,
                verse_start, verse_end,
                citation_offset, passage_start_offset, passage_end_offset,
                ccel_anchor)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()

    print(f"  -> {len(rows)} citation rows (across {len(author_info)} authors)")
    return len(rows)


# ── Per-file parsing ──────────────────────────────────────────────────────────

def parse_thml_file(
    xml_path: Path,
    conn,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """
    Parse one ThML XML file, extract citations, and insert into the DB.
    Saves a clean .txt companion file alongside the .xml.
    Returns the number of citation rows inserted.
    """
    root = _load_xml(xml_path)
    if root is None:
        return 0

    meta = _extract_metadata(root)
    author = meta["author"]
    title = meta["title"]
    year = meta["year"]
    # The file was downloaded from ccel.org/ccel/{dir}/{stem}.xml, so the path
    # names the work. The in-file <bookID> can instead name a parent
    # collection (e.g. "morefathers" for Law's "A Practical Treatise").
    ccel_url = f"https://ccel.org/ccel/{xml_path.parent.name}/{xml_path.stem}"
    # Filename relative to project root — this is what the builder will read
    txt_path = xml_path.with_suffix(".txt")
    rel_filename = str(txt_path.relative_to(PROJECT_ROOT)).replace("\\", "/")

    print(f"\nParsing: {xml_path.relative_to(PROJECT_ROOT)}")
    if author or title:
        print(f"  {author or '?'}  |  {title or '?'}")

    # Build clean text and collect scripRef offsets
    builder = _TextBuilder()
    builder.walk(root)
    clean_text = builder.text

    # Save clean text file (builder uses this via stored offsets)
    if not dry_run:
        txt_path.write_text(clean_text, encoding="utf-8")

    # ANF compilation volumes: delegate to sub-works parser
    if _has_sub_works(root):
        return _parse_thml_subworks(root, builder, conn, rel_filename, ccel_url, dry_run, verbose)

    if not dry_run:
        # Conflict resolution: if a txt-sourced row exists for this ccel_url,
        # delete it so the ThML version takes priority.
        existing = conn.execute(
            "SELECT id, source_format FROM manuscripts WHERE ccel_url = ?", (ccel_url,)
        ).fetchone()
        if existing and existing["source_format"] == "txt":
            delete_refs_for_manuscript(conn, existing["id"])
            conn.execute("DELETE FROM manuscripts WHERE id = ?", (existing["id"],))

        manuscript_id = upsert_manuscript(
            conn, rel_filename,
            author=author, title=title, year=year,
            ccel_url=ccel_url, category="Other",
            source_format="thml",
        )
        delete_refs_for_manuscript(conn, manuscript_id)

    rows: list[tuple] = []

    for el, cite_offset in builder.scripref_hits:
        parsed_attr = el.get("parsed")
        if not parsed_attr:
            continue  # only process structurally-tagged citations

        citations = _parse_parsed_attr(parsed_attr, el.get("passage"))
        if not citations:
            continue

        passage_start, passage_end = extract_passage_offsets(clean_text, cite_offset)
        ccel_anchor = el.get("id") or None

        for cit in citations:
            be = cit["book_entry"]
            if verbose or dry_run:
                ref_str = f"{be['name']} {cit['chapter']}"
                if cit["verse_start"]:
                    ref_str += f":{cit['verse_start']}"
                    if cit["verse_end"]:
                        ref_str += f"-{cit['verse_end']}"
                preview = clean_text[passage_start:passage_start + 80].replace("\n", " ")
                print(f"  {ref_str:30s}  …{preview}…")

            rows.append((
                manuscript_id if not dry_run else 0,
                be["name"],
                be["slug"],
                cit["chapter"],
                cit["verse_start"],
                cit["verse_end"],
                cite_offset,
                passage_start,
                passage_end,
                ccel_anchor,
            ))

    if not dry_run and rows:
        conn.executemany(
            """INSERT INTO verse_refs
               (manuscript_id, book, book_slug, chapter,
                verse_start, verse_end,
                citation_offset, passage_start_offset, passage_end_offset,
                ccel_anchor)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()

    print(f"  -> {len(rows)} citation rows")
    return len(rows)


# ── Entry point ───────────────────────────────────────────────────────────────

def _paths_from_manifest() -> list[Path]:
    if not MANIFEST_PATH.exists():
        print(f"Manifest not found at {MANIFEST_PATH}. Run fetch_thml.py first.", file=sys.stderr)
        sys.exit(1)
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        entries = json.load(f)
    paths = []
    for entry in entries:
        if entry.get("status") not in ("downloaded", "cached"):
            continue
        author_id = entry.get("author_id")
        book_id = entry.get("book_id")
        if author_id and book_id:
            # Reconstruct path from IDs so Windows-origin local_path values work on Linux
            p = CCEL_THML_DIR / author_id / f"{book_id}.xml"
        elif entry.get("local_path"):
            p = Path(entry["local_path"])
        else:
            continue
        if p.exists():
            paths.append(p)
    return paths


def show_stats(conn) -> None:
    print("\n-- Database statistics ------------------------------------------")
    for fmt in ("thml", "txt"):
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM manuscripts WHERE source_format = ?", (fmt,)
        ).fetchone()
        print(f"  Manuscripts ({fmt}): {row['n']}")
    row = conn.execute("SELECT COUNT(*) AS n FROM verse_refs").fetchone()
    print(f"  Verse refs total: {row['n']}")
    print("\n  Top 20 most-referenced chapters:")
    rows = conn.execute("""
        SELECT book, chapter, COUNT(*) AS n
        FROM verse_refs GROUP BY book, chapter
        ORDER BY n DESC LIMIT 20
    """).fetchall()
    for r in rows:
        print(f"    {r['book']} {r['chapter']:>3}  —  {r['n']} refs")


def main() -> None:
    ap = argparse.ArgumentParser(description="Parse ThML XML files for Bible citations")
    ap.add_argument("files", nargs="*", metavar="FILE",
                    help="Specific .xml files to parse (default: all from manifest)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print citations without writing to DB")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="Print each citation found")
    ap.add_argument("--stats", action="store_true",
                    help="Show DB stats after parsing")
    args = ap.parse_args()

    create_schema()
    conn = get_connection()

    if args.files:
        paths = [Path(f).resolve() for f in args.files]
    else:
        paths = _paths_from_manifest()

    total = 0
    for path in paths:
        if not path.exists():
            print(f"File not found: {path}", file=sys.stderr)
            continue
        total += parse_thml_file(path, conn, dry_run=args.dry_run, verbose=args.verbose)

    print(f"\nTotal citation rows: {total}")

    if args.stats and not args.dry_run:
        show_stats(conn)

    conn.close()

    if not args.dry_run:
        print("\nRunning categorize...")
        categorise_all()


if __name__ == "__main__":
    main()
