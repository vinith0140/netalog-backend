#!/usr/bin/env python3
"""
scrape_photos.py

Fetches politician photos from three sources (in order):
  1. Wikidata P18 → Wikimedia Commons thumbnail
  2. English Wikipedia page thumbnail
  3. Regional language Wikipedia (mr/ta/te/kn/bn/hi) thumbnail

Saves image_url to verified_politicians table.
Only processes rows where image_url IS NULL unless --overwrite is set.

Usage:
    python scrape_photos.py                     # all 7 states
    python scrape_photos.py --state telangana   # one state
    python scrape_photos.py --all-positions     # include MLAs (slower)
    python scrape_photos.py --overwrite         # re-fetch even if image_url set
"""

import os
import re
import sys
import time
import argparse
from collections import defaultdict

import httpx

# --- env loading
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

# --- config
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

# State → regional Wikipedia language code
STATE_LANG = {
    24: "te",  # Telangana → Telugu
    14: "mr",  # Maharashtra → Marathi
    11: "kn",  # Karnataka → Kannada
    29: "hi",  # Delhi → Hindi
    23: "ta",  # Tamil Nadu → Tamil
    26: "hi",  # UP → Hindi
    28: "bn",  # West Bengal → Bengali
}

WIKI_HEADERS = {
    "User-Agent": (
        "NetaLog/1.0 (https://github.com/vinith0140/netalog-backend; "
        "vinith0140@gmail.com) python-httpx"
    )
}

POLITICIAN_SIGNALS = {
    # Explicit roles — sufficient to cover all Indian politicians
    "politician", "minister", "assembly", "mla", "lok sabha", "rajya sabha",
    "chief minister", "cabinet",
    # Major Indian parties (descriptions often name party not role)
    "congress", "bjp", "aitc", "tmc", "dravida", "samajwadi",
    # Intentionally excluded: "india"/"indian" → matches actors, cricketers, comedians
    # Intentionally excluded: state names → matches all celebrities from that state
}

STRIP_PREFIX = re.compile(
    r"^\s*(?:dr\.?|prof\.?|shri\.?|smt\.?|sri\.?|adv\.?|hon\.?|pt\.?)\s+",
    re.IGNORECASE,
)


# =============================================================================
# HTTP helper
# =============================================================================

def _get(url: str, params: dict | None = None) -> dict | None:
    try:
        with httpx.Client(headers=WIKI_HEADERS, timeout=15, follow_redirects=True) as c:
            r = c.get(url, params=params)
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


# =============================================================================
# Name helpers
# =============================================================================

def clean_name(raw: str) -> str:
    """Strip honorifics, trailing punctuation, normalize spaces."""
    name = STRIP_PREFIX.sub("", raw)
    name = re.sub(r"\s*\.\s*$", "", name)
    return re.sub(r"\s+", " ", name).strip()


def best_name(cleaned: str) -> str:
    """
    For "X Alias Y" names, return the longer of X or Y — the more specific form.
    E.g. "Sanjay Alias Sanjay Singh Patel" → "Sanjay Singh Patel"
         "Dhirendra Pratap Singh Alias Dhiru Singh" → "Dhirendra Pratap Singh"
    Also strips parenthetical nicknames like "Chandrakant (Dada) Bachhu Patil"
    → "Chandrakant Bachhu Patil" to avoid matching on "(Dada)".
    """
    name = re.sub(r"\s*\([^)]*\)", "", cleaned).strip()
    if re.search(r"\s+[Aa]lias\s+", name):
        parts = re.split(r"\s+[Aa]lias\s+", name, maxsplit=1)
        name = max(parts, key=len).strip()
    return re.sub(r"\s+", " ", name).strip()


def name_variants(cleaned: str, state_name: str) -> list[str]:
    """
    Return query variants from most to least specific.
    Uses best_name() to handle "Alias" and parenthetical nicknames.
    For 3-part names like 'Devendra Gangadhar Fadnavis', also tries
    the short form 'Devendra Fadnavis' (first + last word).
    """
    base = best_name(cleaned)
    parts = base.split()
    variants = [base]
    if len(parts) >= 3:
        short = f"{parts[0]} {parts[-1]}"
        variants.append(short)
        variants.append(f"{short} {state_name}")
    variants.append(f"{base} {state_name}")
    variants.append(f"{base} politician")
    return variants


def is_politician(text_or_summary) -> bool:
    """Check if text (or Wikipedia/Wikidata summary dict) looks like an Indian politician."""
    if isinstance(text_or_summary, dict):
        text = (
            (text_or_summary.get("description") or "")
            + " "
            + (text_or_summary.get("extract") or "")
        )
    else:
        text = text_or_summary or ""
    return any(sig in text.lower() for sig in POLITICIAN_SIGNALS)


def _name_match_ok(pol_name: str, candidate_title: str) -> bool:
    """
    Require meaningful word overlap between the politician name and the Wikipedia/
    Wikidata page title, to prevent first-name-only false positives.

    Uses best_name() so that "Alias" and parenthetical nicknames don't create
    spurious extra matches of the same word appearing twice.

    Rules:
    - If first name matches: at least one additional word must also match.
    - If first name doesn't match: at least two other words must match.
    - Single-significant-word names: that word must appear in the title.

    Only considers words of 4+ characters (filters out initials and noise).
    """
    base = best_name(clean_name(pol_name))
    pol_parts = [w.lower() for w in re.split(r"\W+", base) if len(w) >= 4]
    title_words = {w.lower() for w in re.split(r"\W+", candidate_title) if len(w) >= 4}

    if not pol_parts:
        return False
    if len(pol_parts) == 1:
        return pol_parts[0] in title_words

    first = pol_parts[0]
    first_matches = first in title_words
    non_first_overlap = {w for w in pol_parts[1:] if w in title_words}

    if first_matches:
        return len(non_first_overlap) >= 1
    else:
        return len(non_first_overlap) >= 2


def _thumb_from_summary(summary: dict) -> str | None:
    """Extract and upscale thumbnail URL from a Wikipedia REST summary."""
    thumb = summary.get("thumbnail") or summary.get("originalimage")
    if thumb and thumb.get("source"):
        return re.sub(r"/(\d+)px-", "/400px-", thumb["source"])
    return None


# =============================================================================
# Source 1: Wikidata → Commons thumbnail
# =============================================================================

def wikidata_search(query: str, limit: int = 5) -> list[dict]:
    """Return Wikidata entity search results (each has id, label, description)."""
    data = _get(
        "https://www.wikidata.org/w/api.php",
        params={
            "action": "wbsearchentities", "search": query,
            "language": "en", "type": "item", "limit": limit, "format": "json",
        },
    )
    return (data or {}).get("search", [])


def wikidata_p18(qid: str) -> str | None:
    """Fetch the P18 (image) filename from a Wikidata entity, or None."""
    data = _get(
        "https://www.wikidata.org/w/api.php",
        params={"action": "wbgetentities", "ids": qid, "props": "claims", "format": "json"},
    )
    claims = (data or {}).get("entities", {}).get(qid, {}).get("claims", {})
    try:
        return claims["P18"][0]["mainsnak"]["datavalue"]["value"]
    except (KeyError, IndexError, TypeError):
        return None


def commons_thumb(filename: str, width: int = 400) -> str | None:
    """Query the Commons API for a thumbnail URL given a file name."""
    data = _get(
        "https://commons.wikimedia.org/w/api.php",
        params={
            "action": "query", "titles": f"File:{filename}",
            "prop": "imageinfo", "iiprop": "url",
            "iiurlwidth": width, "format": "json",
        },
    )
    for page in (data or {}).get("query", {}).get("pages", {}).values():
        ii = page.get("imageinfo", [])
        if ii:
            return ii[0].get("thumburl") or ii[0].get("url")
    return None


def find_photo_wikidata(name: str, state_name: str) -> tuple[str | None, str | None]:
    """
    Search Wikidata for the politician's P18 image.
    Returns (photo_url, label) on success, (None, None) otherwise.
    Only returns a non-None result if an actual photo URL is found.
    """
    cleaned = clean_name(name)
    for query in name_variants(cleaned, state_name):
        for ent in wikidata_search(query):
            if not is_politician(ent.get("description", "")):
                continue
            if not _name_match_ok(cleaned, ent.get("label", "")):
                continue
            qid = ent["id"]
            filename = wikidata_p18(qid)
            time.sleep(0.15)
            if filename:
                url = commons_thumb(filename)
                if url:
                    return url, f"wd:{qid} {ent.get('label', '')}"
        time.sleep(0.1)
    return None, None


# =============================================================================
# Source 2: English Wikipedia
# =============================================================================

def en_opensearch(query: str, limit: int = 5) -> list[str]:
    data = _get(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "opensearch", "search": query, "limit": limit,
                "format": "json", "redirects": "resolve"},
    )
    return data[1] if (data and len(data) > 1) else []


def en_summary(title: str) -> dict | None:
    return _get(
        f"https://en.wikipedia.org/api/rest_v1/page/summary/{title.replace(' ', '_')}"
    )


def find_photo_wikipedia_en(name: str, state_name: str) -> tuple[str | None, str | None]:
    """
    Search English Wikipedia.
    Returns (photo_url, page_title) if photo found.
    Returns (None, page_title) if correct page found but no photo (stops search).
    Returns (None, None) if no relevant page found.
    """
    cleaned = clean_name(name)
    for query in name_variants(cleaned, state_name):
        for title in en_opensearch(query):
            s = en_summary(title)
            if not s or not is_politician(s):
                continue
            if not _name_match_ok(cleaned, title):
                continue
            url = _thumb_from_summary(s)
            if url:
                return url, title
            return None, title  # correct page found, no photo — stop searching
        time.sleep(0.1)
    return None, None


# =============================================================================
# Source 3: Regional language Wikipedia
# =============================================================================

def reg_opensearch(query: str, lang: str, limit: int = 3) -> list[str]:
    data = _get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={"action": "opensearch", "search": query, "limit": limit,
                "format": "json", "redirects": "resolve"},
    )
    return data[1] if (data and len(data) > 1) else []


def reg_summary(title: str, lang: str) -> dict | None:
    return _get(
        f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title.replace(' ', '_')}"
    )


def find_photo_wikipedia_regional(name: str, state_id: int) -> tuple[str | None, str | None]:
    """
    Search the state's regional Wikipedia (mr/ta/te/kn/bn/hi).
    The REST summary API returns a Wikidata-derived description in English,
    so is_politician() still works. Falls back gracefully if no results.
    """
    lang = STATE_LANG.get(state_id)
    if not lang:
        return None, None

    cleaned = clean_name(name)
    parts = cleaned.split()
    queries = [cleaned]
    if len(parts) >= 3:
        queries.append(f"{parts[0]} {parts[-1]}")  # short form helps regional search

    for query in queries:
        for title in reg_opensearch(query, lang, limit=3):
            s = reg_summary(title, lang)
            if not s or not is_politician(s):
                continue
            url = _thumb_from_summary(s)
            if url:
                return url, f"{lang}:{title}"
            return None, f"{lang}:{title}"  # right page, no photo
        time.sleep(0.1)
    return None, None


# =============================================================================
# Orchestrator
# =============================================================================

def find_photo(
    name: str, state_name: str, state_id: int
) -> tuple[str | None, str | None, str]:
    """
    Try Wikidata → English Wikipedia → regional Wikipedia in order.
    Returns (photo_url, source_label, method) where method is one of:
      "wikidata" | "en" | "regional" | "no_photo" | "not_found"
    """
    # 1. Wikidata (returns only if photo found)
    url, label = find_photo_wikidata(name, state_name)
    if url:
        return url, label, "wikidata"

    # 2. English Wikipedia
    en_url, en_label = find_photo_wikipedia_en(name, state_name)
    if en_url:
        return en_url, en_label, "en"

    # 3. Regional Wikipedia (only tried if EN had no photo)
    reg_url, reg_label = find_photo_wikipedia_regional(name, state_id)
    if reg_url:
        return reg_url, reg_label, "regional"

    # Report best "found but no photo" label for logging
    found_label = en_label or reg_label
    if found_label:
        return None, found_label, "no_photo"
    return None, None, "not_found"


# =============================================================================
# DB helpers
# =============================================================================

def get_db():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL and SUPABASE_KEY must be set.")
        sys.exit(1)
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def load_politicians(
    db, state_ids: list[int], positions: list[str], overwrite: bool
) -> list[dict]:
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
        rows = resp.data or []
        has_photo = sum(1 for p in rows if p.get("image_url"))
        pols = rows if overwrite else [p for p in rows if not p.get("image_url")]
        all_pols.extend(pols)
        print(f"  {sname}: {len(rows)} total ({has_photo} have photo), {len(pols)} to process")
    return all_pols


# =============================================================================
# Main
# =============================================================================

def parse_args():
    p = argparse.ArgumentParser(
        description="Scrape politician photos from Wikidata, EN Wikipedia, and regional wikis."
    )
    p.add_argument("--state", default=None, help="Single state alias (e.g. telangana)")
    p.add_argument("--all-positions", action="store_true", help="Include MLAs (slower)")
    p.add_argument("--overwrite", action="store_true", help="Re-fetch existing image_urls")
    return p.parse_args()


def main():
    args = parse_args()

    if args.state:
        alias = args.state.lower().replace("-", "").replace(" ", "")
        if alias not in STATE_ALIAS:
            print(f"Unknown state: {args.state}. Valid: {list(STATE_ALIAS.keys())}")
            sys.exit(1)
        state_ids = [STATE_ALIAS[alias]]
    else:
        state_ids = list(STATE_CONFIG.keys())

    positions = ["Chief Minister", "Cabinet Minister"]
    if args.all_positions:
        positions.append("MLA")

    db = get_db()
    print("Loading politicians...")
    politicians = load_politicians(db, state_ids, positions, args.overwrite)
    print(f"Total to process: {len(politicians)}\n")

    if not politicians:
        print("Nothing to do.")
        return

    saved = 0
    no_photo = 0
    not_found = 0
    per_state: dict[int, int] = defaultdict(int)
    method_counts: dict[str, int] = defaultdict(int)
    per_politician: list[tuple] = []

    for i, pol in enumerate(politicians, 1):
        pid = pol["id"]
        name = pol["name"]
        sid = pol["state_id"]
        pos = pol["position"]
        sname = STATE_CONFIG[sid][0]
        safe_name = name.encode("ascii", "replace").decode()

        print(f"[{i}/{len(politicians)}] {safe_name} ({pos}, {sname})...", end=" ", flush=True)

        photo_url, label, method = find_photo(name, sname, sid)
        safe_label = (label or "").encode("ascii", "replace").decode()

        if method == "not_found":
            print("not found")
            not_found += 1
            per_politician.append((name, pos, None, "not_found"))

        elif photo_url is None:
            print(f"no photo ({safe_label[:45]})")
            no_photo += 1
            per_politician.append((name, pos, None, "no_photo"))

        else:
            try:
                db.table("verified_politicians").update({"image_url": photo_url}).eq("id", pid).execute()
                saved += 1
                per_state[sid] += 1
                method_counts[method] += 1
                per_politician.append((name, pos, photo_url, method))
                short = photo_url[-50:] if len(photo_url) > 50 else photo_url
                print(f"SAVED [{method}] {safe_label[:35]} -> ...{short}")
            except Exception as exc:
                print(f"DB error: {str(exc)[:60]}")
                per_politician.append((name, pos, None, "db_error"))

        time.sleep(0.3)

    print(f"\n{'='*60}")
    print(f"Photos saved:    {saved}")
    print(f"Found, no photo: {no_photo}")
    print(f"Not found:       {not_found}")
    print(f"By method:       {dict(method_counts)}")
    print()
    print("Per-state (new photos this run):")
    for sid in state_ids:
        sname = STATE_CONFIG[sid][0]
        c = per_state.get(sid, 0)
        t = sum(1 for p in politicians if p["state_id"] == sid)
        print(f"  {sname}: +{c}/{t}")
    print()
    print("Per-politician:")
    for name, pos, url, status in per_politician:
        tag = "OK" if url else status.upper()
        safe = name.encode("ascii", "replace").decode()
        print(f"  [{tag}] {safe} ({pos})")


if __name__ == "__main__":
    main()
