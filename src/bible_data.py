"""
Canonical Bible book data: Protestant canon + Deuterocanon.

Each entry:
  name   - canonical display name
  slug   - URL/path-safe identifier
  order  - canonical ordering (OT Protestant 1-39, Deuterocanon 40-54, NT 55-81)
  chapters - number of chapters
  abbrevs  - list of abbreviation patterns (lowercase, without trailing dots)
             The parser will match these case-insensitively and allow optional dots
             after each component.

Abbreviation notes:
  - Listed most-specific first (e.g. "1 cor" before "cor") to avoid greedy mismatch
  - Roman numeral book prefixes (I, II, III) are handled separately in the parser
  - "st " prefix handling (St John, St Luke) is handled in the parser
"""

from verse_counts import max_verse

BOOKS = [
    # ── Old Testament ────────────────────────────────────────────────────────
    {"name": "Genesis",          "slug": "genesis",          "order":  1, "chapters": 50,
     "abbrevs": ["gen", "gn"]},
    {"name": "Exodus",           "slug": "exodus",           "order":  2, "chapters": 40,
     "abbrevs": ["exod", "exo", "exod"]},
    {"name": "Leviticus",        "slug": "leviticus",        "order":  3, "chapters": 27,
     "abbrevs": ["lev", "lv"]},
    {"name": "Numbers",          "slug": "numbers",          "order":  4, "chapters": 36,
     "abbrevs": ["num", "numb"]},
    {"name": "Deuteronomy",      "slug": "deuteronomy",      "order":  5, "chapters": 34,
     "abbrevs": ["deut", "deu", "dt"]},
    {"name": "Joshua",           "slug": "joshua",           "order":  6, "chapters": 24,
     "abbrevs": ["josh", "jos"]},
    {"name": "Judges",           "slug": "judges",           "order":  7, "chapters": 21,
     "abbrevs": ["judg", "jdg", "jdgs"]},
    {"name": "Ruth",             "slug": "ruth",             "order":  8, "chapters":  4,
     "abbrevs": ["rth"]},
    {"name": "1 Samuel",         "slug": "1-samuel",         "order":  9, "chapters": 31,
     "abbrevs": ["1 sam", "1sam", "1 sa", "1sa", "i sam", "i sa"]},
    {"name": "2 Samuel",         "slug": "2-samuel",         "order": 10, "chapters": 24,
     "abbrevs": ["2 sam", "2sam", "2 sa", "2sa", "ii sam", "ii sa"]},
    {"name": "1 Kings",          "slug": "1-kings",          "order": 11, "chapters": 22,
     "abbrevs": ["1 kgs", "1kgs", "1 ki", "1ki", "i kgs", "i ki"]},
    {"name": "2 Kings",          "slug": "2-kings",          "order": 12, "chapters": 25,
     "abbrevs": ["2 kgs", "2kgs", "2 ki", "2ki", "ii kgs", "ii ki"]},
    {"name": "1 Chronicles",     "slug": "1-chronicles",     "order": 13, "chapters": 29,
     "abbrevs": ["1 chr", "1chr", "1 chron", "1chron", "i chr", "i chron"]},
    {"name": "2 Chronicles",     "slug": "2-chronicles",     "order": 14, "chapters": 36,
     "abbrevs": ["2 chr", "2chr", "2 chron", "2chron", "ii chr", "ii chron"]},
    {"name": "Ezra",             "slug": "ezra",             "order": 15, "chapters": 10,
     "abbrevs": ["ezra", "ezr"]},
    {"name": "Nehemiah",         "slug": "nehemiah",         "order": 16, "chapters": 13,
     "abbrevs": ["neh"]},
    {"name": "Esther",           "slug": "esther",           "order": 17, "chapters": 10,
     "abbrevs": ["esth", "est"]},
    {"name": "Job",              "slug": "job",              "order": 18, "chapters": 42,
     "abbrevs": ["job"]},
    {"name": "Psalms",           "slug": "psalms",           "order": 19, "chapters": 150,
     "abbrevs": ["ps", "pss", "psa", "psalm", "psalms"]},
    {"name": "Proverbs",         "slug": "proverbs",         "order": 20, "chapters": 31,
     "abbrevs": ["prov", "pro", "prv"]},
    {"name": "Ecclesiastes",     "slug": "ecclesiastes",     "order": 21, "chapters": 12,
     "abbrevs": ["eccl", "eccles", "ecc", "qoh", "qoheleth"]},
    {"name": "Song of Solomon",  "slug": "song-of-solomon",  "order": 22, "chapters":  8,
     "abbrevs": ["song of sol", "song of songs", "canticles", "cant"]},
    {"name": "Isaiah",           "slug": "isaiah",           "order": 23, "chapters": 66,
     "abbrevs": ["isa"]},
    {"name": "Jeremiah",         "slug": "jeremiah",         "order": 24, "chapters": 52,
     "abbrevs": ["jer", "jr"]},
    {"name": "Lamentations",     "slug": "lamentations",     "order": 25, "chapters":  5,
     "abbrevs": ["lam"]},
    {"name": "Ezekiel",          "slug": "ezekiel",          "order": 26, "chapters": 48,
     "abbrevs": ["ezek", "ezk"]},
    {"name": "Daniel",           "slug": "daniel",           "order": 27, "chapters": 12,
     "abbrevs": ["dan", "dn"]},
    {"name": "Hosea",            "slug": "hosea",            "order": 28, "chapters": 14,
     "abbrevs": ["hos"]},
    {"name": "Joel",             "slug": "joel",             "order": 29, "chapters":  3,
     "abbrevs": ["joel"]},
    {"name": "Amos",             "slug": "amos",             "order": 30, "chapters":  9,
     "abbrevs": ["amos", "amo"]},
    {"name": "Obadiah",          "slug": "obadiah",          "order": 31, "chapters":  1,
     "abbrevs": ["obad", "oba"]},
    {"name": "Jonah",            "slug": "jonah",            "order": 32, "chapters":  4,
     "abbrevs": ["jonah", "jon", "jnh"]},
    {"name": "Micah",            "slug": "micah",            "order": 33, "chapters":  7,
     "abbrevs": ["mic"]},
    {"name": "Nahum",            "slug": "nahum",            "order": 34, "chapters":  3,
     "abbrevs": ["nah"]},
    {"name": "Habakkuk",         "slug": "habakkuk",         "order": 35, "chapters":  3,
     "abbrevs": ["hab"]},
    {"name": "Zephaniah",        "slug": "zephaniah",        "order": 36, "chapters":  3,
     "abbrevs": ["zeph", "zep"]},
    {"name": "Haggai",           "slug": "haggai",           "order": 37, "chapters":  2,
     "abbrevs": ["hag"]},
    {"name": "Zechariah",        "slug": "zechariah",        "order": 38, "chapters": 14,
     "abbrevs": ["zech", "zec"]},
    {"name": "Malachi",          "slug": "malachi",          "order": 39, "chapters":  4,
     "abbrevs": ["mal"]},

    # ── Deuterocanon / Apocrypha ─────────────────────────────────────────────
    {"name": "Tobit",            "slug": "tobit",            "order": 40, "chapters": 14,
     "abbrevs": ["tob", "tobit"]},
    {"name": "Judith",           "slug": "judith",           "order": 41, "chapters": 16,
     "abbrevs": ["jdt", "judith"]},
    {"name": "1 Maccabees",      "slug": "1-maccabees",      "order": 42, "chapters": 16,
     "abbrevs": ["1 macc", "1macc", "1 mac", "1mac", "i macc", "i mac", "1 m"]},
    {"name": "2 Maccabees",      "slug": "2-maccabees",      "order": 43, "chapters": 15,
     "abbrevs": ["2 macc", "2macc", "2 mac", "2mac", "ii macc", "ii mac"]},
    {"name": "3 Maccabees",      "slug": "3-maccabees",      "order": 44, "chapters":  7,
     "abbrevs": ["3 macc", "3macc", "3 mac", "3mac", "iii macc", "iii mac"]},
    {"name": "4 Maccabees",      "slug": "4-maccabees",      "order": 45, "chapters": 18,
     "abbrevs": ["4 macc", "4macc", "4 mac", "4mac", "iv macc", "iv mac"]},
    {"name": "Wisdom of Solomon","slug": "wisdom",           "order": 46, "chapters": 19,
     "abbrevs": ["wis", "wisd", "wisdom of sol", "wisdom"]},
    {"name": "Sirach",           "slug": "sirach",           "order": 47, "chapters": 51,
     "abbrevs": ["sir", "sirach", "ecclus", "ecclesiasticus"]},
    {"name": "Baruch",           "slug": "baruch",           "order": 48, "chapters":  6,
     "abbrevs": ["bar", "baruch"]},
    {"name": "Letter of Jeremiah","slug": "letter-of-jeremiah","order": 49, "chapters": 1,
     "abbrevs": ["let jer", "ep jer", "epistle of jer"]},
    {"name": "Prayer of Azariah","slug": "prayer-of-azariah","order": 50, "chapters":  1,
     "abbrevs": ["pr azar", "sg three", "song of three"]},
    {"name": "Susanna",          "slug": "susanna",          "order": 51, "chapters":  1,
     "abbrevs": ["susanna"]},
    {"name": "Bel and the Dragon","slug": "bel",             "order": 52, "chapters":  1,
     "abbrevs": ["bel", "bel and dragon"]},
    {"name": "Prayer of Manasseh","slug": "prayer-of-manasseh","order": 53, "chapters": 1,
     "abbrevs": ["pr man", "prayer of man"]},
    {"name": "1 Esdras",         "slug": "1-esdras",         "order": 54, "chapters":  9,
     "abbrevs": ["1 esd", "1esd", "i esd", "3 ezra"]},
    {"name": "2 Esdras",         "slug": "2-esdras",         "order": 55, "chapters": 16,
     "abbrevs": ["2 esd", "2esd", "ii esd", "4 ezra"]},

    # ── New Testament ────────────────────────────────────────────────────────
    {"name": "Matthew",          "slug": "matthew",          "order": 56, "chapters": 28,
     "abbrevs": ["matt", "mat", "mt"]},
    {"name": "Mark",             "slug": "mark",             "order": 57, "chapters": 16,
     "abbrevs": ["mark", "mar", "mrk", "mk"]},
    {"name": "Luke",             "slug": "luke",             "order": 58, "chapters": 24,
     "abbrevs": ["luke", "luk", "lk"]},
    {"name": "John",             "slug": "john",             "order": 59, "chapters": 21,
     "abbrevs": ["john", "joh", "jhn", "jn"]},
    {"name": "Acts",             "slug": "acts",             "order": 60, "chapters": 28,
     "abbrevs": ["acts", "act"]},
    {"name": "Romans",           "slug": "romans",           "order": 61, "chapters": 16,
     "abbrevs": ["rom", "ro", "rm"]},
    {"name": "1 Corinthians",    "slug": "1-corinthians",    "order": 62, "chapters": 16,
     "abbrevs": ["1 cor", "1cor", "i cor", "1 co", "1co"]},
    {"name": "2 Corinthians",    "slug": "2-corinthians",    "order": 63, "chapters": 13,
     "abbrevs": ["2 cor", "2cor", "ii cor", "2 co", "2co"]},
    {"name": "Galatians",        "slug": "galatians",        "order": 64, "chapters":  6,
     "abbrevs": ["gal"]},
    {"name": "Ephesians",        "slug": "ephesians",        "order": 65, "chapters":  6,
     "abbrevs": ["eph", "ephes"]},
    {"name": "Philippians",      "slug": "philippians",      "order": 66, "chapters":  4,
     "abbrevs": ["phil", "php"]},
    {"name": "Colossians",       "slug": "colossians",       "order": 67, "chapters":  4,
     "abbrevs": ["col"]},
    {"name": "1 Thessalonians",  "slug": "1-thessalonians",  "order": 68, "chapters":  5,
     "abbrevs": ["1 thess", "1thess", "1 thes", "1thes", "i thess", "i thes", "1 th"]},
    {"name": "2 Thessalonians",  "slug": "2-thessalonians",  "order": 69, "chapters":  3,
     "abbrevs": ["2 thess", "2thess", "2 thes", "2thes", "ii thess", "ii thes", "2 th"]},
    {"name": "1 Timothy",        "slug": "1-timothy",        "order": 70, "chapters":  6,
     "abbrevs": ["1 tim", "1tim", "i tim", "1 ti", "1ti"]},
    {"name": "2 Timothy",        "slug": "2-timothy",        "order": 71, "chapters":  4,
     "abbrevs": ["2 tim", "2tim", "ii tim", "2 ti", "2ti"]},
    {"name": "Titus",            "slug": "titus",            "order": 72, "chapters":  3,
     "abbrevs": ["tit", "ti"]},
    {"name": "Philemon",         "slug": "philemon",         "order": 73, "chapters":  1,
     "abbrevs": ["phlm", "phm", "philem"]},
    {"name": "Hebrews",          "slug": "hebrews",          "order": 74, "chapters": 13,
     "abbrevs": ["heb"]},
    {"name": "James",            "slug": "james",            "order": 75, "chapters":  5,
     "abbrevs": ["jas", "jam", "jm"]},
    {"name": "1 Peter",          "slug": "1-peter",          "order": 76, "chapters":  5,
     "abbrevs": ["1 pet", "1pet", "1 pe", "1pe", "i pet", "i pe", "1 pt", "1pt"]},
    {"name": "2 Peter",          "slug": "2-peter",          "order": 77, "chapters":  3,
     "abbrevs": ["2 pet", "2pet", "2 pe", "2pe", "ii pet", "ii pe", "2 pt", "2pt"]},
    {"name": "1 John",           "slug": "1-john",           "order": 78, "chapters":  5,
     "abbrevs": ["1 john", "1john", "1 jn", "1jn", "i john", "i jn", "1 jo", "1jo"]},
    {"name": "2 John",           "slug": "2-john",           "order": 79, "chapters":  1,
     "abbrevs": ["2 john", "2john", "2 jn", "2jn", "ii john", "ii jn"]},
    {"name": "3 John",           "slug": "3-john",           "order": 80, "chapters":  1,
     "abbrevs": ["3 john", "3john", "3 jn", "3jn", "iii john", "iii jn"]},
    {"name": "Jude",             "slug": "jude",             "order": 81, "chapters":  1,
     "abbrevs": ["jude", "jud"]},
    {"name": "Revelation",       "slug": "revelation",       "order": 82, "chapters": 22,
     "abbrevs": ["rev", "the revelation", "apocalypse", "apoc"]},
]

# ── Lookup structures ─────────────────────────────────────────────────────────

# slug → book info
BY_SLUG: dict[str, dict] = {b["slug"]: b for b in BOOKS}

# canonical name (lowercase) → book info
BY_NAME: dict[str, dict] = {b["name"].lower(): b for b in BOOKS}

# abbreviation (lowercase, no dots) → book info
# Built from all abbrevs lists; longer matches are preferred
_abbrev_map: dict[str, dict] = {}
for _book in BOOKS:
    for _abbr in _book["abbrevs"]:
        _abbrev_map[_abbr] = _book
    # also register the full name
    _abbrev_map[_book["name"].lower()] = _book

# Sort by length descending so the regex prefers longer matches
ABBREV_LOOKUP: dict[str, dict] = dict(
    sorted(_abbrev_map.items(), key=lambda kv: len(kv[0]), reverse=True)
)

# Sorted abbrev list for building the citation regex (longest first)
ABBREV_LIST: list[str] = list(ABBREV_LOOKUP.keys())


def roman_to_int(s: str) -> int | None:
    """
    Convert a Roman numeral string to an integer.
    Returns None if the string is not a valid Roman numeral.
    Handles values 1-3999.
    """
    s = s.upper().strip()
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    if not s or not all(c in vals for c in s):
        return None
    total = 0
    prev = 0
    for ch in reversed(s):
        v = vals[ch]
        if v < prev:
            total -= v
        else:
            total += v
        prev = v
    return total


def is_roman(s: str) -> bool:
    return roman_to_int(s) is not None


# How far a verse number may run past the KJV's last verse before we call it
# bogus. Hebrew/Vulgate Psalms count the superscription as verse 1, and other
# versifications differ from the KJV by a verse here and there.
_VERSE_SLACK = {"psalms": 2}
_DEFAULT_VERSE_SLACK = 1


def greek_psalm_to_hebrew(ch: int, v: int | None) -> tuple[int, int | None]:
    """
    Convert an LXX/Vulgate Psalm reference to Hebrew/English numbering.
    Verse numbers are only shifted where Psalms were split or merged; the
    usual off-by-one from counted superscriptions is left alone.
    """
    if ch == 9:
        return (10, v - 21) if v and v >= 22 else (9, v)
    if ch == 113:
        return (115, v - 8) if v and v >= 9 else (114, v)
    if ch == 114:
        return 116, v
    if ch == 115:
        return 116, (v + 9 if v else v)
    if ch == 146:
        return 147, v
    if ch == 147:
        return 147, (v + 11 if v else v)
    if 10 <= ch <= 145:
        return ch + 1, v
    return ch, v


def remap_versification(book: dict, ch: int, v: int | None) -> tuple[dict, int, int | None]:
    """
    Translate a reference that cannot exist in KJV versification, but can in
    the Greek/Latin/Hebrew ones the fathers and their translators used, into
    its KJV equivalent. References that fit the KJV are returned unchanged.
    """
    slug = book["slug"]
    if slug == "psalms" and v:
        limit = max_verse(slug, ch)
        if limit is not None and v > limit + _VERSE_SLACK["psalms"]:
            # "Ps. 118:105" only fits as Vulgate 118 = Hebrew 119.
            hch, hv = greek_psalm_to_hebrew(ch, v)
            hlimit = max_verse(slug, hch)
            if hlimit is not None and hv <= hlimit + _VERSE_SLACK["psalms"]:
                return book, hch, hv
    elif slug == "daniel":
        if ch == 13:
            return BY_SLUG["susanna"], 1, v
        if ch == 14:
            return BY_SLUG["bel"], 1, v
        if ch == 3 and v and v > 30:
            # Greek Daniel 3:24-90 is the Prayer of Azariah / Song of the Three;
            # 3:91-97 is KJV 3:24-30.
            if v <= 90:
                return BY_SLUG["prayer-of-azariah"], 1, v - 23
            if v <= 97:
                return book, 3, v - 67
    elif slug == "ecclesiastes" and 12 < ch <= BY_SLUG["sirach"]["chapters"]:
        return BY_SLUG["sirach"], ch, v  # "Eccles. xxiv." can only be Ecclesiasticus
    elif slug == "malachi" and ch == 3 and v and v >= 19:
        return book, 4, v - 18  # Hebrew/LXX Mal 3:19-24 = KJV 4:1-6
    elif slug == "joel" and ch == 4:
        return book, 3, v       # Hebrew Joel 4 = KJV 3
    return book, ch, v


def validate_ref(book: dict, chapter: int, verse_start: int | None,
                 verse_end: int | None) -> tuple[int | None, int | None] | None:
    """
    Sanity-check a citation against the book's real shape.

    Returns the (possibly cleaned) (verse_start, verse_end), or None if the
    citation cannot be right and should be dropped: the chapter does not exist,
    or the starting verse is past the end of the chapter. A shorthand
    verse_end ("21-6") is expanded; an overlong or backwards one is trimmed.
    """
    if not 1 <= chapter <= book["chapters"]:
        return None
    if verse_start is None:
        return None, None
    if verse_start < 1:
        return None

    if verse_end is not None and verse_end < verse_start:
        # Shorthand range: "21-6" means 21-26, "134-7" means 134-137
        start_s, end_s = str(verse_start), str(verse_end)
        expanded = int(start_s[:-len(end_s)] + end_s) if len(end_s) < len(start_s) else 0
        verse_end = expanded if expanded > verse_start else None

    limit = max_verse(book["slug"], chapter)
    if limit is not None:
        slack = _VERSE_SLACK.get(book["slug"], _DEFAULT_VERSE_SLACK)
        if verse_start > limit + slack:
            return None
        if verse_end is not None and verse_end > limit + slack:
            verse_end = max(limit, verse_start)

    if verse_end is not None and verse_end <= verse_start:
        verse_end = None
    return verse_start, verse_end


if __name__ == "__main__":
    print(f"Total books: {len(BOOKS)}")
    print(f"Total abbreviations registered: {len(ABBREV_LOOKUP)}")
    # Quick sanity checks
    assert BY_SLUG["romans"]["name"] == "Romans"
    assert ABBREV_LOOKUP["rom"]["name"] == "Romans"
    assert ABBREV_LOOKUP["jam"]["name"] == "James"
    assert ABBREV_LOOKUP["sir"]["name"] == "Sirach"
    assert roman_to_int("viii") == 8
    assert roman_to_int("xiii") == 13
    assert roman_to_int("cl") == 150
    ps = BY_SLUG["psalms"]
    assert validate_ref(ps, 4, 8, None) == (8, None)
    assert validate_ref(ps, 4, 9, None) == (9, None)       # Hebrew/Vulgate numbering
    assert validate_ref(ps, 4, 141, None) is None
    assert validate_ref(ps, 151, 1, None) is None
    assert validate_ref(ps, 4, 6, 40) == (6, 8)
    assert validate_ref(ps, 4, 6, 2) == (6, None)
    assert validate_ref(BY_SLUG["matthew"], 5, 21, 6) == (21, 26)    # shorthand range
    assert validate_ref(ps, 119, 134, 7) == (134, 137)
    assert validate_ref(ps, 119, 21, 19) == (21, None)
    assert validate_ref(BY_SLUG["tobit"], 1, 99, None) == (99, None)  # no verse data
    print("All assertions passed.")
