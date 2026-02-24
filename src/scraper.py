"""
CCEL scraper: downloads works as plain text from ccel.org.

Usage:
  python src/scraper.py               # download all works from the CCEL txt index
  python src/scraper.py --list        # print works list without downloading

Works are discovered dynamically from https://www.ccel.org/index/format/txt.
Files are saved to manuscripts/{filename}.
Already-downloaded files are always skipped, so the run can be safely resumed.
"""
import argparse
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

MANUSCRIPTS_DIR = Path(__file__).parent.parent / "manuscripts"

CCEL_INDEX_URL = "https://www.ccel.org/index/format/txt"

# Local-only works that exist in manuscripts/ but are not available on CCEL.
LOCAL_WORKS = [
    {
        "author": "Meister Eckhart",
        "title": "Sermons",
        "filename": "sermons.txt",
        "txt_url": None,
    },
    {
        "author": "John Owen",
        "title": "Of the Mortification of Sin in Believers",
        "filename": "mort.txt",
        "txt_url": None,
    },
    {
        "author": "Richard Allestree",
        "title": "The Government of the Tongue",
        "filename": "government.txt",
        "txt_url": None,
    },
    {
        "author": "Alvin Plantinga",
        "title": "Warranted Christian Belief",
        "filename": "warrant3.txt",
        "txt_url": None,
    },
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PatristicsBot/1.0; "
        "+https://github.com/patristics-viewer) "
        "Gecko/20100101 Firefox/120.0"
    )
}
REQUEST_DELAY = 2.0  # seconds between requests


def fetch_index_works(session: requests.Session) -> list[dict]:
    """
    Fetch the CCEL plain-text index page and return a list of work dicts.

    Each entry in the index has an HTML page URL of the form:
        /ccel/{author_slug}/{work_slug}.html
    which maps to a .txt file at:
        https://ccel.org/ccel/{author_slug}/{work_slug}/{work_slug}.txt
    """
    print(f"Fetching work list from {CCEL_INDEX_URL} …")
    try:
        resp = session.get(CCEL_INDEX_URL, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"ERROR: Could not fetch index: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    works = []
    seen_urls: set[str] = set()

    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        # Match links like /ccel/author/work.html
        if not href.startswith("/ccel/") or not href.endswith(".html"):
            continue
        # Strip leading slash and split: ['ccel', author_slug, 'work.html']
        parts = href.lstrip("/").split("/")
        if len(parts) != 3:
            continue
        _, author_slug, work_file = parts
        work_slug = work_file[:-5]  # remove .html
        txt_url = f"https://ccel.org/ccel/{author_slug[0]}/{author_slug}/{work_slug}/cache/{work_slug}.txt"
        if txt_url in seen_urls:
            continue
        seen_urls.add(txt_url)
        title = a.get_text(strip=True) or f"{author_slug}/{work_slug}"
        works.append(
            {
                "author": author_slug,
                "title": title,
                "filename": f"{author_slug}_{work_slug}.txt",
                "txt_url": txt_url,
            }
        )

    print(f"Found {len(works)} works in index.")
    return works


def discover_txt_url(work_url: str, session: requests.Session) -> str | None:
    """
    Given a CCEL work page URL, try to find a link to the .txt download.
    Returns the URL string or None.
    """
    try:
        resp = session.get(work_url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  Failed to fetch work page {work_url}: {e}", file=sys.stderr)
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    # Look for a link ending in .txt
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.endswith(".txt"):
            if href.startswith("http"):
                return href
            return f"https://ccel.org{href}"
    return None


def download_work(work: dict, session: requests.Session) -> bool:
    """
    Download a single work to manuscripts/. Returns True on success.
    Skips if the file already exists.
    """
    dest = MANUSCRIPTS_DIR / work["filename"]
    if dest.exists():
        print(f"  [skip] {work['filename']} already exists")
        return True

    txt_url = work.get("txt_url")
    if not txt_url:
        print(f"  [skip] {work['filename']} — no URL configured, expected in manuscripts/")
        return True

    print(f"  Downloading {work['author']} — {work['title']}")
    print(f"    URL: {txt_url}")

    try:
        resp = session.get(txt_url, timeout=60, stream=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    size_kb = dest.stat().st_size // 1024
    print(f"    Saved {dest.name} ({size_kb} KB)")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Download CCEL works as plain text")
    parser.add_argument("--list", action="store_true", help="List works without downloading")
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update(HEADERS)

    works = fetch_index_works(session) + LOCAL_WORKS

    if args.list:
        print(f"{'Author':<35} {'Title':<45} {'File'}")
        print("-" * 100)
        for w in works:
            has_url = "[url]" if w.get("txt_url") else "(local)"
            print(f"{w['author']:<35} {w['title']:<45} {w['filename']}  {has_url}")
        return

    already = sum(1 for w in works if (MANUSCRIPTS_DIR / w["filename"]).exists())
    print(f"Downloading {len(works)} works to {MANUSCRIPTS_DIR}/ ({already} already present, will skip)")

    ok = 0
    fail = 0
    for i, work in enumerate(works):
        if i > 0:
            time.sleep(REQUEST_DELAY)
        success = download_work(work, session)
        if success:
            ok += 1
        else:
            fail += 1

    print(f"\nDone. {ok} succeeded, {fail} failed.")


if __name__ == "__main__":
    main()
