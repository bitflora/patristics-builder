"""
build_kjv.py — Download public-domain KJV text and write viewer/data/static/kjv.json.zst

Usage:
    python src/build_kjv.py

Requires: pip install zstandard
"""

import json
import sys
import urllib.request
import zstandard

BASE_URL = "https://raw.githubusercontent.com/aruljohn/Bible-kjv/master"
BOOKS_URL = f"{BASE_URL}/Books.json"

OUT_PATH = "viewer/data/static/kjv.json.zst"

# Map canonical book names → slugs used by this project
SLUG_MAP = {
    "Genesis": "genesis",
    "Exodus": "exodus",
    "Leviticus": "leviticus",
    "Numbers": "numbers",
    "Deuteronomy": "deuteronomy",
    "Joshua": "joshua",
    "Judges": "judges",
    "Ruth": "ruth",
    "1 Samuel": "1-samuel",
    "2 Samuel": "2-samuel",
    "1 Kings": "1-kings",
    "2 Kings": "2-kings",
    "1 Chronicles": "1-chronicles",
    "2 Chronicles": "2-chronicles",
    "Ezra": "ezra",
    "Nehemiah": "nehemiah",
    "Esther": "esther",
    "Job": "job",
    "Psalms": "psalms",
    "Proverbs": "proverbs",
    "Ecclesiastes": "ecclesiastes",
    "Song of Solomon": "song-of-solomon",
    "Isaiah": "isaiah",
    "Jeremiah": "jeremiah",
    "Lamentations": "lamentations",
    "Ezekiel": "ezekiel",
    "Daniel": "daniel",
    "Hosea": "hosea",
    "Joel": "joel",
    "Amos": "amos",
    "Obadiah": "obadiah",
    "Jonah": "jonah",
    "Micah": "micah",
    "Nahum": "nahum",
    "Habakkuk": "habakkuk",
    "Zephaniah": "zephaniah",
    "Haggai": "haggai",
    "Zechariah": "zechariah",
    "Malachi": "malachi",
    "Matthew": "matthew",
    "Mark": "mark",
    "Luke": "luke",
    "John": "john",
    "Acts": "acts",
    "Romans": "romans",
    "1 Corinthians": "1-corinthians",
    "2 Corinthians": "2-corinthians",
    "Galatians": "galatians",
    "Ephesians": "ephesians",
    "Philippians": "philippians",
    "Colossians": "colossians",
    "1 Thessalonians": "1-thessalonians",
    "2 Thessalonians": "2-thessalonians",
    "1 Timothy": "1-timothy",
    "2 Timothy": "2-timothy",
    "Titus": "titus",
    "Philemon": "philemon",
    "Hebrews": "hebrews",
    "James": "james",
    "1 Peter": "1-peter",
    "2 Peter": "2-peter",
    "1 John": "1-john",
    "2 John": "2-john",
    "3 John": "3-john",
    "Jude": "jude",
    "Revelation": "revelation",
}


def fetch(url):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


def main():
    print(f"Fetching book list from {BOOKS_URL} ...")
    book_names = fetch(BOOKS_URL)

    out = {}
    for book_name in book_names:
        slug = SLUG_MAP.get(book_name)
        if not slug:
            print(f"  Skipping unmapped book: {book_name}")
            continue

        encoded = book_name.replace(" ", "")
        url = f"{BASE_URL}/{encoded}.json"
        print(f"  Fetching {book_name} ...")
        try:
            book_data = fetch(url)
        except Exception as e:
            print(f"  WARNING: failed to fetch {book_name}: {e}", file=sys.stderr)
            continue

        ch_map = {}
        for ch in book_data.get("chapters", []):
            ch_num = str(ch["chapter"])
            v_map = {}
            for v in ch.get("verses", []):
                v_map[str(v["verse"])] = v["text"]
            ch_map[ch_num] = v_map
        out[slug] = ch_map

    json_bytes = json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    print(f"\nUncompressed size: {len(json_bytes) / 1024:.1f} KB")

    cctx = zstandard.ZstdCompressor(level=19)
    compressed = cctx.compress(json_bytes)
    print(f"Compressed size:   {len(compressed) / 1024:.1f} KB")

    with open(OUT_PATH, "wb") as f:
        f.write(compressed)
    print(f"Written to {OUT_PATH}")


if __name__ == "__main__":
    main()
