#!/usr/bin/env python3
"""
scrape_photos.py

For every Chief Minister and Cabinet Minister in verified_politicians
(7 target states), searches Wikipedia for their photo and saves the
image_url to the verified_politicians table.

Usage:
    python scrape_photos.py                     # all 7 states
    python scrape_photos.py --state telangana   # one state
    python scrape_photos.py --all-positions     # MLAs too (slow, lower hit rate)
    python scrape_photos.py --overwrite         # re-fetch even if image_url exists
"""

import os
import sys
import re
import time
import argparse
from collections import defaultdict

import httpx

# --- env loading -------------------------------------------------------------
for _env in [
    os.path.join(os.path.dirname(__file__), ".env"),
    os.path.join(os.path.dirname(__file__), "app", ".env"),
]:
    if os.path.exists(_env):
        with open(_env) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
        break

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

from supabase import create_client  # noqa: E402

# --- config ------------------------------------------------------------------
STATE_CONFIG = {
    24: ("Telangana",     "telangana"),
    14: ("Maharashtra",   "maharashtra"),
    11: ("Karnataka",     "karnataka"),
    29: ("Delhi",         "delhi"),
    23: ("Tamil Nadu",    "tamilnadu"),
    26: ("Uttar Pradesh", "up"),
    28: ("West Bengal",   "westbengal"),
}
STATE_ALIAS = {v[1]: k for k, v in STATE_CONFIG.items()}

WIKI_HEADERS = {
    "User-Agent": (
        "NetaLog/1.0 (https://github.com/vinith0140/netalog-backend; "
        "vinith0140@gmail.com) python-httpx"
    )
}

# Words that suggest a Wikipedia page IS about an Indian politician
POLITICIAN_SIGNALS = {
    "politician", "minister", "assembly", "mla", "lok sabha", "rajya sabha",
    "chief minister", "cabinet", "india", "indian", "party", "congress",
    "bjp", "aitc", "tmc", "dravida", "samajwadi", "telangana", "karnataka",
    "maharashtra", "delhi", "bengal", "pradesh",
}

# Honorifics to strip before searching
STRIP_PREFIX = re.compile(
    r"^\s*(?:dr\.?|prof\.?|shri\.?|smt\.?|sri\.?|adv\.?|hon\.?)\s+",
    re.IGNORECASE,
)


# =============================================================================
# Wikipedia helpers
# =============================================================================

def _wiki_get(url: str, params: dict | None = None) -> dict | None:
    try:
        with httpx.Client(headers=WIKI_HEADERS, timeout=15, follow_redirects=True) as c:
            r = c.get(url, params=params)
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return None


def clean_name(raw: str) -> str:
    """Strip honorifics, trailing punctuation, and normalize spaces."""
    name = STRIP_PREFIX.sub("", raw)
    # Remove trailing dots/special chars (e.g., "Shivakumar .M" → "Shivakumar M")
    name = re.sub(r"\s*\.\s*$", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def opensearch(query: str, limit: int = 5) -> list[str]:
    """Return list of Wikipedia page titles matching the query."""
    data = _wiki_get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "opensearch", "search": query,
            "limit": limit, "format": "json", "redirects": "resolve",
        },
    )
    if data and len(data) > 1:
        return data[1]  # list of titles
    return []


def get_summary(page_title: str) -> dict | None:
    """Fetch Wikipedia REST summary for a page title."""
    encoded = page_title.replace(" ", "_")
    return _wiki_get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}")


def is_indian_politician(summary: dict) -> bool:
    """Check if a Wikipedia summary is plausibly an Indian politician."""
    text = (
        (summary.get("description") or "")
        + " "
        + (summary.get("extract") or "")
    ).lower()
    return any(sig in text for sig in POLITICIAN_SIGNALS)


def find_photo(name: str, state_name: str) -> tuple[str | None, str | None]:
    """
    Look up a politician's Wikipedia photo.
    Returns (photo_url, matched_page_title) or (None, None).

    Strategy:
    1. Direct name search
    2. Name + state name (disambiguation)
    3. Name + "politician"
    """
    cleaned = clean_name(name)

    for query in [
        cleaned,
        f"{cleaned} {state_name}",
        f"{cleaned} politician",
        f"{cleaned} India",
    ]:
        titles = opensearch(query, limit=5)
        for title in titles:
            summary = get_summary(title)
            if not summary:
                continue
            if not is_indian_politician(summary):
                continue
            # Get photo from summary thumbnail or originalimage
            thumb = summary.get("thumbnail") or summary.get("originalimage")
            if thumb and thumb.get("source"):
                photo_url = thumb["source"]
                # Prefer a higher-res version if thumbnail is small
                if "thumb/" in photo_url:
                    # Replace width in thumbnail URL: .../200px-... → .../400px-...
                    photo_url = re.sub(r"/(\d+)px-", "/400px-", photo_url)
                return photo_url, title
            # Page exists but no photo — still mark as found (no photo)
            return None, title

        time.sleep(0.1)

    return None, None


# =============================================================================
# DB helpers
# =============================================================================

def get_db():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL and SUPABASE_KEY must be set.")
        sys.exit(1)
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def load_politicians(db, state_ids: list[int], positions: list[str], overwrite: bool) -> list[dict]:
    """Load politicians matching position filters. Skip those with image_url unless overwrite."""
    all_pols = []
    for sid in state_ids:
        sname = STATE_CONFIG[sid][0]
        resp = (
            db.table("verified_politicians")
            .select("id,name,position,image_url,state_id")
            .eq("state_id", sid)
            .in_("position", positions)
            .execute()
        )
        pols = resp.data or []
        if not overwrite:
            pols = [p for p in pols if not p.get("image_url")]
        all_pols.extend(pols)
        total = len(resp.data or [])
        need = len(pols)
        print(f"  {sname} ({sid}): {total} politicians, {need} need photos")
    return all_pols


# =============================================================================
# Main
# =============================================================================

def parse_args():
    p = argparse.ArgumentParser(description="Scrape Wikipedia photos for politicians.")
    p.add_argument("--state", help="Single state alias", default=None)
    p.add_argument(
        "--all-positions", action="store_true",
        help="Include MLAs (slower, lower Wikipedia hit rate)"
    )
    p.add_argument(
        "--overwrite", action="store_true",
        help="Re-fetch even if image_url already set"
    )
    return p.parse_args()


def main():
    args = parse_args()

    if args.state:
        alias = args.state.lower().replace("-", "").replace(" ", "")
        if alias not in STATE_ALIAS:
            print(f"Unknown state: {args.state}")
            print("Valid:", list(STATE_ALIAS.keys()))
            sys.exit(1)
        state_ids = [STATE_ALIAS[alias]]
    else:
        state_ids = list(STATE_CONFIG.keys())

    positions = ["Chief Minister", "Cabinet Minister"]
    if args.all_positions:
        positions.append("MLA")

    db = get_db()

    print(f"Loading politicians (positions: {positions})...")
    politicians = load_politicians(db, state_ids, positions, args.overwrite)
    print(f"Total to process: {len(politicians)}\n")

    if not politicians:
        print("Nothing to do.")
        return

    # Stats
    saved = 0
    skipped_no_photo = 0
    skipped_no_wiki = 0
    per_state: dict[int, int] = defaultdict(int)
    per_politician: list[tuple[str, str, str | None]] = []

    for i, pol in enumerate(politicians, 1):
        pid = pol["id"]
        name = pol["name"]
        sid = pol["state_id"]
        pos = pol["position"]
        sname = STATE_CONFIG[sid][0]

        print(f"[{i}/{len(politicians)}] {name} ({pos}, {sname})...", end=" ")

        photo_url, wiki_title = find_photo(name, sname)

        if wiki_title is None:
            print(f"no Wikipedia page found")
            skipped_no_wiki += 1
            per_politician.append((name, pos, None))
            time.sleep(0.2)
            continue

        if photo_url is None:
            print(f"Wikipedia: '{wiki_title}' — no photo")
            skipped_no_photo += 1
            per_politician.append((name, pos, None))
            time.sleep(0.2)
            continue

        # Save to DB
        try:
            db.table("verified_politicians").update({"image_url": photo_url}).eq("id", pid).execute()
            saved += 1
            per_state[sid] += 1
            per_politician.append((name, pos, photo_url))
            short_url = photo_url[-55:] if len(photo_url) > 55 else photo_url
            print(f"SAVED ({wiki_title[:40]}) -> ...{short_url}")
        except Exception as exc:
            print(f"DB error: {str(exc)[:60]}")
            per_politician.append((name, pos, None))

        time.sleep(0.3)

    # Summary
    print(f"\n{'='*60}")
    print(f"Photos saved:             {saved}")
    print(f"Wikipedia page not found: {skipped_no_wiki}")
    print(f"Found but no photo:       {skipped_no_photo}")
    print()
    print("Per-state breakdown:")
    for sid in state_ids:
        sname = STATE_CONFIG[sid][0]
        count = per_state.get(sid, 0)
        total = sum(1 for p in politicians if p["state_id"] == sid)
        print(f"  {sname}: {count}/{total} photos saved")
    print()
    print("Per-politician results:")
    for name, pos, url in per_politician:
        status = "OK" if url else "NO_PHOTO"
        print(f"  [{status}] {name} ({pos})")


if __name__ == "__main__":
    main()
