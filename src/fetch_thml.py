"""
Download ThML XML files from CCEL.

Crawls https://ccel.org/index/format/ThML to discover all works available in
ThML format, then downloads each one to ccel_thml/{authorID}/{bookID}.xml.
A manifest.json records the status of every attempted download.

Usage:
  python src/fetch_thml.py                    # download all (resumable)
  python src/fetch_thml.py --limit 10         # first 10 only (for testing)
  python src/fetch_thml.py --delay 1.0        # polite crawl delay in seconds
  python src/fetch_thml.py --force            # re-download even cached files
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://ccel.org"
INDEX_URL = "https://ccel.org/index/format/ThML"

PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "manuscripts" / "ccel_thml"
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"

# Work URL pattern on CCEL: /ccel/{authorID}/{bookID}[.html]
_WORK_PATH_RE = re.compile(r"^/ccel/([^/]+)/([^/]+?)(?:\.[a-z]+)?$")


def fetch_index(session: requests.Session) -> list[dict]:
    """Scrape the CCEL ThML index page and return a list of work dicts."""
    print(f"Fetching index: {INDEX_URL}")
    resp = session.get(INDEX_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    seen: set[str] = set()
    works: list[dict] = []

    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0].split("#")[0].rstrip("/")
        m = _WORK_PATH_RE.match(href)
        if not m:
            continue
        author_id, book_id = m.group(1), m.group(2)
        key = f"{author_id}/{book_id}"
        if key in seen:
            continue
        seen.add(key)
        works.append(
            {
                "author_id": author_id,
                "book_id": book_id,
                "title": a.get_text(strip=True),
                "work_url": BASE_URL + href,
            }
        )

    return works


def _try_download(session: requests.Session, urls: list[str], out_path: Path) -> str | None:
    """
    Try each URL in order; on HTTP 200 with XML-looking content, save and return
    the URL that succeeded.  Returns None if all candidates fail.
    """
    for url in urls:
        try:
            resp = session.get(url, timeout=60)
            if resp.status_code == 200 and len(resp.content) > 500:
                # Minimal sanity check: should look like XML
                head = resp.content[:200].lstrip()
                if b"<" in head:
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(resp.content)
                    return url
        except requests.RequestException:
            continue
    return None


def download_work(
    work: dict,
    session: requests.Session,
    delay: float,
    force: bool,
) -> dict:
    """
    Attempt to download a ThML XML file for *work*.
    Returns an updated work dict with 'status' and 'local_path' keys.
    """
    author_id = work["author_id"]
    book_id = work["book_id"]
    out_path = OUTPUT_DIR / author_id / f"{book_id}.xml"

    if not force and out_path.exists() and out_path.stat().st_size > 500:
        return {**work, "status": "cached", "local_path": str(out_path)}

    time.sleep(delay)

    # Candidate URL patterns in preference order
    candidates = [
        f"{BASE_URL}/ccel/{author_id}/{book_id}.xml",
        f"{BASE_URL}/ccel/{author_id}/{book_id}.thml",
        # Some CCEL works nest the XML under the work directory
        f"{BASE_URL}/ccel/{author_id}/{book_id}/{book_id}.xml",
    ]

    source_url = _try_download(session, candidates, out_path)
    if source_url:
        return {**work, "status": "downloaded", "local_path": str(out_path), "source_url": source_url}
    return {**work, "status": "failed", "local_path": None}


def load_manifest() -> dict[str, dict]:
    """Load existing manifest keyed by 'authorID/bookID'."""
    if not MANIFEST_PATH.exists():
        return {}
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        entries = json.load(f)
    return {f"{e['author_id']}/{e['book_id']}": e for e in entries}


def save_manifest(results: list[dict]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


def main() -> None:
    ap = argparse.ArgumentParser(description="Download ThML XML files from CCEL")
    ap.add_argument("--limit", type=int, default=0, metavar="N",
                    help="Stop after N works (0 = all)")
    ap.add_argument("--delay", type=float, default=0.5, metavar="SECS",
                    help="Seconds to wait between HTTP requests (default: 0.5)")
    ap.add_argument("--force", action="store_true",
                    help="Re-download files that are already cached")
    args = ap.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cached_manifest = load_manifest()

    session = requests.Session()
    session.headers["User-Agent"] = (
        "PatristicsResearchBot/1.0 (academic scripture citation research; "
        "contact: see github.com/patristics)"
    )

    works = fetch_index(session)
    print(f"Found {len(works)} works in ThML index")

    if args.limit:
        works = works[: args.limit]

    results: list[dict] = []
    n = len(works)

    for i, work in enumerate(works, 1):
        key = f"{work['author_id']}/{work['book_id']}"
        cached = cached_manifest.get(key)

        if not args.force and cached and cached.get("status") in ("downloaded", "cached"):
            result = {**cached, "status": "cached"}
            results.append(result)
            print(f"  [{i:4d}/{n}] cached      {key}")
            continue

        result = download_work(work, session, delay=args.delay, force=args.force)
        results.append(result)
        flag = "OK " if result["status"] == "downloaded" else "FAIL"
        print(f"  [{i:4d}/{n}] {flag}         {key}")

    save_manifest(results)

    downloaded = sum(1 for r in results if r["status"] in ("downloaded", "cached"))
    failed = sum(1 for r in results if r["status"] == "failed")
    print(f"\nSummary: {downloaded}/{len(results)} downloaded, {failed} failed")
    print(f"Manifest written to {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
