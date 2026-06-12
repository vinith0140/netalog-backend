#!/usr/bin/env python3
"""
scrape_achievements.py

Scrapes PIB press releases from state-specific bureaus, matches politician
names from verified_politicians, and saves matches to the achievements table.

Usage:
    python scrape_achievements.py                    # all 7 states
    python scrape_achievements.py --state telangana  # one state test
    python scrape_achievements.py --backfill 180     # backfill N days via PRID scan
    python scrape_achievements.py --prid-from 2250000  # start PRID for backfill

Run daily via Task Scheduler / cron to accumulate achievements over time.
"""

import os
import sys
import re
import time
import argparse
from datetime import date, datetime
from collections import defaultdict

import httpx
from bs4 import BeautifulSoup

# --- Supabase env (loaded before DB import) ----------------------------------
for env_candidate in [
    os.path.join(os.path.dirname(__file__), ".env"),
    os.path.join(os.path.dirname(__file__), "app", ".env"),
]:
    if os.path.exists(env_candidate):
        with open(env_candidate) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
        break

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

from supabase import create_client  # noqa: E402 (needs env first)

# --- Target state config -----------------------------------------------------
# Maps state_id -> (state name, PIB bureau reg code, short alias)
STATE_BUREAU_MAP = {
    24: ("Telangana",     5,  "telangana"),
    14: ("Maharashtra",   1,  "maharashtra"),
    11: ("Karnataka",     20, "karnataka"),
    29: ("Delhi",         3,  "delhi"),
    23: ("Tamil Nadu",    6,  "tamilnadu"),
    26: ("Uttar Pradesh", 37, "up"),
    28: ("West Bengal",   19, "westbengal"),
}
NATIONAL_REG = 48  # PIB National bureau (also scrape this)

STATE_ALIAS_MAP = {v[2]: k for k, v in STATE_BUREAU_MAP.items()}  # alias -> state_id

# --- Category detection ------------------------------------------------------
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "infrastructure": ["road", "highway", "bridge", "airport", "metro", "railway",
                       "construction", "infrastructure", "power", "energy", "port",
                       "expressway", "flyover", "pipeline", "broadband"],
    "health":         ["health", "hospital", "medical", "vaccine", "ayushman",
                       "healthcare", "disease", "drug", "dispensary", "clinic",
                       "pharmaceutical", "cancer", "diabetes"],
    "education":      ["education", "school", "university", "college", "student",
                       "scholarship", "skill", "literacy", "training", "navodaya"],
    "agriculture":    ["agriculture", "farmer", "crop", "irrigation", "kisan",
                       "farm", "msp", "rural", "horticulture", "fisheries",
                       "agri", "seed", "fertilizer"],
    "welfare":        ["welfare", "scheme", "benefit", "social", "women", "youth",
                       "poor", "relief", "pension", "subsidy", "ration", "antyodaya",
                       "divyang", "tribal", "sc/st", "backward"],
    "finance":        ["budget", "tax", "revenue", "economy", "finance", "bank",
                       "loan", "fund", "investment", "gst", "fiscal", "export",
                       "import", "trade", "startup"],
}

TITLE_NOISE = {
    "shri", "smt", "dr", "prof", "sri", "adv", "hon", "the", "and", "for",
    "of", "in", "is", "to", "at", "an", "a",
}

PIB_BASE = "https://pib.gov.in"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# =============================================================================
# Database helpers
# =============================================================================

def get_db():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL and SUPABASE_KEY environment variables must be set.")
        sys.exit(1)
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def load_politicians(db, state_ids: list[int]) -> list[dict]:
    """Load verified politicians for the given state IDs."""
    all_pols = []
    for sid in state_ids:
        state_name, _, _ = STATE_BUREAU_MAP.get(sid, ("Unknown", 0, ""))
        resp = (
            db.table("verified_politicians")
            .select("id,name,state_id,position,party")
            .eq("state_id", sid)
            .execute()
        )
        pols = resp.data or []
        all_pols.extend(pols)
        print(f"  {state_name} ({sid}): {len(pols)} politicians")
    return all_pols


def _extract_prid(url: str) -> str:
    """Extract PRID value from a PIB press release URL."""
    if "PRID=" in url:
        return url.split("PRID=")[-1].split("&")[0]
    return url  # fallback: use full URL


def load_existing_keys(db) -> set[tuple]:
    """
    Load (politician_id, prid) pairs already in achievements.
    PRID is extracted from source_url so the key survives URL format changes.
    """
    resp = (
        db.table("achievements")
        .select("politician_id,source_url")
        .execute()
    )
    return {
        (row["politician_id"], _extract_prid(row["source_url"]))
        for row in (resp.data or [])
        if row.get("source_url")
    }


# =============================================================================
# Name matching
# =============================================================================

def build_name_index(politicians: list[dict]) -> list[dict]:
    """
    Build a matching index from politician records.
    Each entry has:
      - politician: the raw dict
      - full_lower: full name normalized (no punctuation, lowercase)
      - sig_parts: significant name tokens (len >= 4, not noise words)
    """
    index = []
    for pol in politicians:
        raw_name = pol.get("name", "").strip()
        if not raw_name:
            continue
        # Normalize: remove titles, punctuation
        cleaned = re.sub(r"\b(shri|smt|dr|prof|sri|adv)\b", "", raw_name, flags=re.I)
        cleaned = re.sub(r"[^a-zA-Z\s]", " ", cleaned)
        full_lower = " ".join(cleaned.lower().split())

        parts = full_lower.split()
        sig = [p for p in parts if len(p) >= 4 and p not in TITLE_NOISE]

        if not sig:
            sig = [p for p in parts if len(p) >= 3 and p not in TITLE_NOISE]

        if sig:
            index.append({
                "politician": pol,
                "full_lower": full_lower,
                "sig_parts": sig,
                "raw_name": raw_name,
            })
    return index


def match_politician(title: str, name_index: list[dict]) -> dict | None:
    """
    Return the first politician whose name appears in the press release title.

    Matching rules (priority order):
    1. Full cleaned name is a substring of the title
    2. Last two sig_parts appear consecutively (handles "Revanth Reddy" in
       "Anumula Revanth Reddy")
    3. Any two consecutive sig_parts both appear as title words
    4. Single distinctive name (5+ chars) appears as a title word
    """
    title_lower = title.lower()
    title_clean = re.sub(r"[^a-z\s]", " ", title_lower)
    title_words = set(title_clean.split())

    for entry in name_index:
        full_lower = entry["full_lower"]
        sig_parts = entry["sig_parts"]

        # Rule 1: full name substring
        if full_lower and full_lower in title_lower:
            return entry["politician"]

        if len(sig_parts) >= 2:
            # Rule 2: last two parts appear together (common in Indian names:
            # "Revanth Reddy" from "Anumula Revanth Reddy")
            last_two = " ".join(sig_parts[-2:])
            if last_two in title_clean:
                return entry["politician"]

            # Rule 3: any two consecutive sig_parts both in title words
            for i in range(len(sig_parts) - 1):
                if sig_parts[i] in title_words and sig_parts[i + 1] in title_words:
                    return entry["politician"]

        elif len(sig_parts) == 1 and len(sig_parts[0]) >= 5:
            # Rule 4: single rare name (5+ chars) as a title word
            if sig_parts[0] in title_words:
                return entry["politician"]

    return None


# =============================================================================
# Category + date detection
# =============================================================================

def detect_category(text: str) -> str:
    lower = text.lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return cat
    return "government"


# =============================================================================
# PIB scraping
# =============================================================================

def _http_get(url: str, timeout: int = 25) -> httpx.Response | None:
    try:
        with httpx.Client(headers=HEADERS, timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp
    except Exception as exc:
        print(f"  HTTP error [{url[-60:]}]: {exc}")
        return None


def _prib_english_url(prid: str) -> str:
    """Return the English press release page URL for a given PRID."""
    return f"{PIB_BASE}/PressReleasePage.aspx?PRID={prid}"


def fetch_bureau_listings(reg: int) -> list[dict]:
    """
    Fetch all press release listings from a PIB regional bureau.
    Returns list of {title, prid, url} dicts.
    """
    url = f"{PIB_BASE}/indexd.aspx?reg={reg}&lang=1"
    resp = _http_get(url)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    releases = []
    for a in soup.select("ul.release_list li a"):
        title = (a.get("title", "") or a.get_text(strip=True)).strip()
        href = a.get("href", "")
        if not title or "PRID=" not in href:
            continue
        prid = href.split("PRID=")[-1]
        # Use the English PressReleasePage URL, not the Hindi PressReleaseDetail URL
        full_url = _prib_english_url(prid)
        releases.append({"title": title, "prid": prid, "url": full_url})
    return releases


def fetch_pib_detail(prid: str) -> dict:
    """
    Fetch description and published date from a PIB English press release page.
    Content is in div#PdfDiv on PressReleasePage.aspx.
    Returns {description, published_date} (both may be None).
    """
    url = _prib_english_url(prid)
    resp = _http_get(url, timeout=20)
    if not resp:
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")

    # English content lives in div#PdfDiv
    description = None
    pdf_div = soup.find("div", id="PdfDiv")
    if pdf_div:
        text = pdf_div.get_text(separator=" ", strip=True)
        ascii_ratio = sum(1 for c in text if ord(c) < 128) / max(len(text), 1)
        if ascii_ratio > 0.75 and len(text) > 40:
            description = text[:1500]

    # Extract date from page — look for common date patterns
    published_date = None
    full_text = soup.get_text()
    for pattern, fmt in [
        (r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+20\d{2})\b", None),
        (r"\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+20\d{2})\b", None),
        (r"\b(\d{2}/\d{2}/20\d{2})\b", "%d/%m/%Y"),
    ]:
        m = re.search(pattern, full_text, re.I)
        if m:
            ds = m.group(1).strip()
            try:
                if fmt:
                    published_date = datetime.strptime(ds, fmt).date()
                else:
                    for f in ["%d %B %Y", "%d %b %Y", "%B %d %Y", "%B %d, %Y"]:
                        try:
                            published_date = datetime.strptime(ds, f).date()
                            break
                        except ValueError:
                            pass
            except ValueError:
                pass
            if published_date:
                break

    return {"description": description, "published_date": published_date}


def fetch_prid_english_content(prid: str) -> tuple[str | None, str | None]:
    """
    Fetch English title and full body from a PIB press release.
    Returns (title, body) where either may be None.
    Returns (None, None) if the page has no English content.
    Uses PressReleasePage.aspx which serves the English version.
    """
    url = _prib_english_url(prid)
    resp = _http_get(url, timeout=15)
    if not resp:
        return None, None

    soup = BeautifulSoup(resp.text, "html.parser")

    # English content is in div#PdfDiv
    pdf_div = soup.find("div", id="PdfDiv")
    if pdf_div:
        text = pdf_div.get_text(separator=" ", strip=True)
        ascii_ratio = sum(1 for c in text if ord(c) < 128) / max(len(text), 1)
        if ascii_ratio < 0.70 or len(text) < 20:
            return None, None  # Non-English or empty
        # Title: first sentence (up to 200 chars)
        first_line = text.split(".")[0].strip()[:200]
        title = first_line if len(first_line) > 10 else None
        return title, text[:2000]

    # Fallback: h2 heading
    for tag in soup.find_all(["h2", "h3"], limit=3):
        text = tag.get_text(strip=True)
        if not text:
            continue
        ascii_ratio = sum(1 for c in text if ord(c) < 128) / max(len(text), 1)
        if ascii_ratio > 0.85 and len(text) > 10:
            return text, None

    return None, None


# =============================================================================
# Core scraping logic
# =============================================================================

def process_releases(
    releases: list[dict],
    name_index: list[dict],
    existing_keys: set[tuple],
    db,
    saved_count: dict,
    politician_names: dict,
    fetch_details: bool = True,
) -> int:
    """
    Match and save achievements from a list of {title, prid, url} releases.
    Returns number of new achievements saved.
    """
    new_saved = 0
    for rel in releases:
        title = rel["title"]
        url = rel["url"]
        prid = rel.get("prid", "")

        match = match_politician(title, name_index)
        if not match:
            continue

        pol_id = match["id"]
        if (pol_id, prid) in existing_keys:
            continue

        # Fetch detail page for description + date
        detail = {}
        if fetch_details and prid:
            detail = fetch_pib_detail(prid)
            time.sleep(0.4)

        description = detail.get("description") or title
        published_date = detail.get("published_date") or date.today().isoformat()
        if hasattr(published_date, "isoformat"):
            published_date = published_date.isoformat()

        category = detect_category(title + " " + (detail.get("description") or ""))

        record = {
            "politician_id": pol_id,
            "title": title[:500],
            "description": description[:2000],
            "source_url": url,
            "published_date": published_date,
            "category": category,
        }

        try:
            db.table("achievements").insert(record).execute()
            saved_count[pol_id] += 1
            existing_keys.add((pol_id, prid))
            pol_name = politician_names.get(pol_id, str(pol_id))
            print(f"  SAVED [{category}] {pol_name}: {title[:65].encode('ascii', 'replace').decode()}...")
            new_saved += 1
        except Exception as exc:
            print(f"  DB error (PRID {prid}): {str(exc)[:80]}")

    return new_saved


def scrape_bureaus(
    db,
    state_ids: list[int],
    name_index: list[dict],
    existing_keys: set[tuple],
    saved_count: dict,
    politician_names: dict,
) -> int:
    """
    Scrape press releases from all regional PIB bureaus for the given states.
    Returns total new achievements saved.
    """
    # Build list of bureaus to scrape (state bureaus + national)
    bureau_regs: list[tuple[str, int]] = []
    seen_regs: set[int] = set()
    for sid in state_ids:
        if sid in STATE_BUREAU_MAP:
            name, reg, _ = STATE_BUREAU_MAP[sid]
            if reg not in seen_regs:
                bureau_regs.append((name, reg))
                seen_regs.add(reg)
    # Always include national
    if NATIONAL_REG not in seen_regs:
        bureau_regs.append(("National", NATIONAL_REG))
    # Always include Delhi national for major announcements
    if 3 not in seen_regs:
        bureau_regs.append(("Delhi National", 3))

    total_new = 0
    for bureau_name, reg in bureau_regs:
        print(f"\nScraping PIB {bureau_name} (reg={reg})...")
        releases = fetch_bureau_listings(reg)
        print(f"  Found {len(releases)} releases")
        if releases:
            new = process_releases(
                releases, name_index, existing_keys, db,
                saved_count, politician_names
            )
            total_new += new
            print(f"  -> {new} new achievements saved")
        time.sleep(0.5)

    return total_new


def backfill_prid_range(
    db,
    prid_start: int,
    prid_end: int,
    name_index: list[dict],
    existing_keys: set[tuple],
    saved_count: dict,
    politician_names: dict,
    step: int = 5,
) -> int:
    """
    Scan a PRID range to find historical English press releases.
    Checks every `step`-th PRID to keep runtime manageable.

    For 6-month backfill: prid_start ~ current_PRID - 25000
    """
    total_prids = (prid_end - prid_start) // step
    print(f"\nBackfill: scanning PRID {prid_start} to {prid_end} "
          f"(step={step}, ~{total_prids} checks)...")

    total_new = 0
    checked = 0
    skipped_nonenglish = 0

    for prid_int in range(prid_end, prid_start, -step):
        prid = str(prid_int)
        checked += 1

        if checked % 200 == 0:
            print(f"  [{checked}/{total_prids}] checked, {total_new} saved, "
                  f"{skipped_nonenglish} non-English skipped")

        # Fetch English content (title + full body)
        title, body = fetch_prid_english_content(prid)
        if not title and not body:
            skipped_nonenglish += 1
            time.sleep(0.15)
            continue

        # Match against full content (title first, then body) for broader coverage
        search_text = (title or "") + " " + (body or "")
        url = _prib_english_url(prid)
        match = match_politician(search_text, name_index)
        if not match:
            time.sleep(0.15)
            continue

        pol_id = match["id"]
        if (pol_id, prid) in existing_keys:
            time.sleep(0.15)
            continue

        # We already have the content; just need the date
        detail = fetch_pib_detail(prid)
        time.sleep(0.3)

        # Use the listing title if we have one; otherwise use first line of body
        save_title = (title or "").strip() or (body or "")[:200].strip()
        description = body or detail.get("description") or save_title
        published_date = detail.get("published_date") or None
        if hasattr(published_date, "isoformat"):
            published_date = published_date.isoformat()

        category = detect_category(save_title + " " + (description or ""))

        record = {
            "politician_id": pol_id,
            "title": save_title[:500],
            "description": description[:2000],
            "source_url": url,
            "published_date": published_date,
            "category": category,
        }

        try:
            db.table("achievements").insert(record).execute()
            saved_count[pol_id] += 1
            existing_keys.add((pol_id, prid))
            pol_name = politician_names.get(pol_id, str(pol_id))
            print(f"  SAVED [{category}] {pol_name}: "
                  f"{save_title[:65].encode('ascii', 'replace').decode()}...")
            total_new += 1
        except Exception as exc:
            print(f"  DB error (PRID {prid}): {str(exc)[:80]}")

        time.sleep(0.15)

    print(f"\nBackfill done: {checked} PRIDs checked, {skipped_nonenglish} non-English, "
          f"{total_new} new achievements saved")
    return total_new


# =============================================================================
# Entry point
# =============================================================================

def parse_args():
    p = argparse.ArgumentParser(description="Scrape PIB press releases for politicians.")
    p.add_argument(
        "--state",
        help=(
            "Single state alias to test: "
            + " | ".join(v[2] for v in STATE_BUREAU_MAP.values())
        ),
        default=None,
    )
    p.add_argument(
        "--backfill",
        type=int,
        metavar="DAYS",
        help="Also scan PRID range for last N days of historical data",
        default=0,
    )
    p.add_argument(
        "--prid-from",
        type=int,
        metavar="PRID",
        help="Explicit start PRID for backfill (overrides --backfill estimate)",
        default=None,
    )
    p.add_argument(
        "--step",
        type=int,
        help="PRID step size for backfill scan (default 5; smaller = more coverage, slower)",
        default=5,
    )
    p.add_argument(
        "--no-details",
        action="store_true",
        help="Skip fetching detail pages (faster but description = title only)",
    )
    return p.parse_args()


def main():
    args = parse_args()

    # Determine which states to run
    if args.state:
        alias = args.state.lower().replace("-", "").replace(" ", "")
        if alias not in STATE_ALIAS_MAP:
            print(f"Unknown state: {args.state}")
            print("Valid aliases:", list(STATE_ALIAS_MAP.keys()))
            sys.exit(1)
        state_ids = [STATE_ALIAS_MAP[alias]]
    else:
        state_ids = list(STATE_BUREAU_MAP.keys())

    db = get_db()

    print("Loading verified politicians...")
    politicians = load_politicians(db, state_ids)
    if not politicians:
        print("No politicians found. Check state IDs and database connection.")
        sys.exit(1)
    print(f"Total: {len(politicians)} politicians across {len(state_ids)} states")

    name_index = build_name_index(politicians)
    print(f"Name index: {len(name_index)} entries")

    print("Loading existing achievements...")
    existing_keys = load_existing_keys(db)
    print(f"Existing achievements: {len(existing_keys)}")

    saved_count: dict[int, int] = defaultdict(int)
    politician_names = {pol["id"]: pol["name"] for pol in politicians}

    # --- Current listings from regional bureaus ---
    total_new = scrape_bureaus(
        db, state_ids, name_index, existing_keys,
        saved_count, politician_names
    )

    # --- Optional PRID backfill ---
    if args.backfill > 0 or args.prid_from is not None:
        # Estimate current (max) PRID from national listing
        current_listings = fetch_bureau_listings(3)  # Delhi national
        if current_listings:
            current_max_prid = max(
                int(r["prid"]) for r in current_listings if r.get("prid", "").isdigit()
            )
        else:
            current_max_prid = 2272021  # fallback if listing fails

        if args.prid_from is not None:
            prid_start = args.prid_from
        else:
            # PIB publishes ~150 total PRIDs/day across all regions/languages
            prid_start = max(current_max_prid - (args.backfill * 150), current_max_prid - 30000)

        backfill_new = backfill_prid_range(
            db,
            prid_start=prid_start,
            prid_end=current_max_prid,
            name_index=name_index,
            existing_keys=existing_keys,
            saved_count=saved_count,
            politician_names=politician_names,
            step=args.step,
        )
        total_new += backfill_new

    # --- Summary ---
    print(f"\n{'=' * 60}")
    print(f"Total new achievements saved: {total_new}")

    if saved_count:
        print("\nPer-politician breakdown:")
        for pol_id, count in sorted(saved_count.items(), key=lambda x: -x[1]):
            pos = next(
                (p.get("position", "") for p in politicians if p["id"] == pol_id), ""
            )
            name = politician_names.get(pol_id, str(pol_id))
            pos_str = f" ({pos})" if pos else ""
            print(f"  {name}{pos_str}: {count}")
    else:
        print("\nNo new achievements found.")
        print("Tip: politician names in PIB titles may not match exactly.")
        print("     Consider checking a few titles manually at pib.gov.in")


if __name__ == "__main__":
    main()
