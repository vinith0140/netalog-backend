#!/usr/bin/env python3
"""
dry_run_district_mapping.py
DRY RUN ONLY — no DB writes.

Scrapes Wikipedia "List of constituencies" pages for 7 states.
Extracts: district → constituency → constituency_number → reservation.
Joins MLA name + party from verified_politicians (fuzzy name match).
Outputs: output/district_mapping_dry_run.csv

Usage:
    python dry_run_district_mapping.py
    python dry_run_district_mapping.py --state karnataka
"""

import csv
import os
import re
import sys
import time
import argparse
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# ── Constants ─────────────────────────────────────────────────────────────────

WIKI_API = "https://en.wikipedia.org/w/api.php"
UA       = "NetaLog/1.0 (vinith0140@gmail.com) python-httpx dry-run"
OUT_DIR  = Path("output")

# 7 target states: state_id → (state_name, wikipedia_article_title)
STATES = {
    11: ("Karnataka",      "List_of_constituencies_of_the_Karnataka_Legislative_Assembly"),
    14: ("Maharashtra",    "List_of_constituencies_of_the_Maharashtra_Legislative_Assembly"),
    23: ("Tamil Nadu",     "List_of_constituencies_of_the_Tamil_Nadu_Legislative_Assembly"),
    24: ("Telangana",      "List_of_constituencies_of_the_Telangana_Legislative_Assembly"),
    26: ("Uttar Pradesh",  "List_of_constituencies_of_the_Uttar_Pradesh_Legislative_Assembly"),
    28: ("West Bengal",    "List_of_constituencies_of_the_West_Bengal_Legislative_Assembly"),
    29: ("Delhi",          "List_of_constituencies_of_the_Delhi_Legislative_Assembly"),
}

OUTPUT_COLS = [
    "state_id", "state_name", "district_name", "constituency_name",
    "constituency_number", "reservation", "mla_name", "mla_party",
    "politician_id", "match_type", "source_url", "source_name", "confidence",
]


# ── Wikipedia fetch ───────────────────────────────────────────────────────────

def fetch_article_html(client: httpx.Client, article: str) -> str:
    r = client.get(WIKI_API, params={
        "action": "parse", "page": article,
        "prop": "text", "format": "json",
    })
    r.raise_for_status()
    return r.json().get("parse", {}).get("text", {}).get("*", "")


def wiki_article_url(article: str) -> str:
    return f"https://en.wikipedia.org/wiki/{article}"


# ── Table parser ──────────────────────────────────────────────────────────────

def _clean(s: str) -> str:
    return re.sub(r"\[.*?\]", "", s).strip()


def parse_constituency_list(html: str, state_name: str, source_url: str) -> list[dict]:
    """
    Parse a Wikipedia 'List of constituencies' article.
    Returns list of dicts with district, constituency_name, number, reservation.

    Handles 2 common layouts:
      Layout A: columns include "No.", "Constituency", "District", "Reservation"
      Layout B: grouped by district (district appears as a header row or merged cell)
    """
    soup = BeautifulSoup(html, "html.parser")
    rows_out = []

    for table in soup.find_all("table", class_=re.compile("wikitable")):
        headers = [_clean(th.get_text()) for th in table.find_all("th")]
        header_text = " ".join(h.lower() for h in headers)

        # Skip tables that don't look like constituency lists
        if "constituency" not in header_text and "no" not in header_text:
            continue

        # Detect column positions
        hdr_lower = [h.lower() for h in headers]
        def col(*keys):
            for k in keys:
                for i, h in enumerate(hdr_lower):
                    if k in h:
                        return i
            return None

        no_idx    = col("no", "number", "s.no")
        name_idx  = col("constituency", "name")
        dist_idx  = col("district")
        res_idx   = col("reservation", "category", "type")

        if name_idx is None:
            continue

        current_district = "Unknown"

        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue

            texts = [_clean(c.get_text()) for c in cells]

            # District header row (single bold/th cell spanning columns)
            if len(texts) == 1 and texts[0] and all(c.name == "th" for c in cells):
                current_district = texts[0]
                continue

            # Skip pure header rows
            if all(c.name == "th" for c in cells):
                continue

            if len(texts) < 2:
                continue

            # Extract fields by column index
            def get(idx):
                if idx is not None and idx < len(texts):
                    return texts[idx]
                return ""

            number_raw  = get(no_idx) if no_idx is not None else ""
            const_name  = get(name_idx)
            district    = get(dist_idx) if dist_idx is not None else current_district
            reservation = get(res_idx) if res_idx is not None else ""

            if not const_name or const_name.lower() in ("constituency", "name", "no."):
                continue

            # Parse constituency number
            num = None
            if number_raw:
                m = re.search(r"\d+", number_raw)
                if m:
                    num = int(m.group())

            # Normalise reservation
            res = ""
            res_up = reservation.upper()
            if "SC" in res_up:
                res = "SC"
            elif "ST" in res_up:
                res = "ST"
            elif "GEN" in res_up or "UR" in res_up or "GENERAL" in res_up:
                res = "GEN"

            if district.strip() == "" or district == "Unknown":
                district = current_district

            rows_out.append({
                "district_name":      district.strip(),
                "constituency_name":  const_name,
                "constituency_number": num,
                "reservation":        res,
                "source_url":         source_url,
                "source_name":        "Wikipedia",
                "confidence":         "confirmed",
            })

        if rows_out:
            break  # found the right table; stop scanning

    return rows_out


# ── MLA matching ──────────────────────────────────────────────────────────────

def names_match(a: str, b: str) -> bool:
    a, b = a.lower().strip(), b.lower().strip()
    if a == b or a in b or b in a:
        return True
    wa = [w for w in a.split() if len(w) > 3]
    return bool(wa) and sum(1 for w in wa if w in b) >= max(1, (len(wa) + 1) // 2)


def const_match(a: str, b: str) -> bool:
    a, b = a.lower().strip(), b.lower().strip()
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    wa = [w for w in a.split() if len(w) > 3]
    return bool(wa) and all(w in b for w in wa[:2])


def match_mla(constituency: str, state_id: int, db_pols: list[dict]) -> tuple:
    """Return (mla_name, mla_party, politician_id, match_type) or empty strings."""
    # Try constituency exact / fuzzy match
    for pol in db_pols:
        if const_match(constituency, pol.get("constituency") or ""):
            return pol["name"], pol["party"], pol["id"], "constituency"
    return "", "", None, "no_match"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", help="Run one state only (e.g. karnataka)")
    args = parser.parse_args()

    # Filter states if --state given
    target = {
        sid: info for sid, info in STATES.items()
        if not args.state or args.state.lower() in info[0].lower()
    }
    if not target:
        sys.exit(f"No state matched '{args.state}'")

    # Load verified_politicians from DB
    print("Loading verified_politicians from DB...")
    from app.database import get_db
    db = get_db()
    all_pols = db.table("verified_politicians").select(
        "id,name,party,constituency,state_id"
    ).execute().data or []
    pols_by_state: dict[int, list] = {}
    for p in all_pols:
        pols_by_state.setdefault(p["state_id"], []).append(p)
    print(f"  Loaded {len(all_pols)} politicians across {len(pols_by_state)} states\n")

    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / "district_mapping_dry_run.csv"

    total_rows   = 0
    matched_rows = 0

    with (
        httpx.Client(headers={"User-Agent": UA}, timeout=30, follow_redirects=True) as client,
        open(out_path, "w", newline="", encoding="utf-8") as f,
    ):
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLS)
        writer.writeheader()

        for state_id, (state_name, article) in target.items():
            print(f"── {state_name} ──────────────────────────────")
            url = wiki_article_url(article)
            print(f"  Fetching: {url}")

            try:
                html = fetch_article_html(client, article)
                time.sleep(1)
            except Exception as exc:
                print(f"  ERROR: {exc}")
                continue

            rows = parse_constituency_list(html, state_name, url)
            print(f"  Parsed {len(rows)} constituencies")

            if not rows:
                print(f"  WARNING: No rows parsed — article structure may differ")
                writer.writerow({
                    "state_id": state_id, "state_name": state_name,
                    "district_name": "PARSE_FAILED", "constituency_name": "",
                    "constituency_number": "", "reservation": "",
                    "mla_name": "", "mla_party": "", "politician_id": "",
                    "match_type": "no_parse", "source_url": url,
                    "source_name": "Wikipedia", "confidence": "uncertain",
                })
                continue

            db_pols = pols_by_state.get(state_id, [])
            state_matched = 0

            for row in rows:
                mla_name, mla_party, pol_id, match_type = match_mla(
                    row["constituency_name"], state_id, db_pols
                )
                confidence = row["confidence"] if match_type != "no_match" else "uncertain"

                writer.writerow({
                    "state_id":            state_id,
                    "state_name":          state_name,
                    "district_name":       row["district_name"],
                    "constituency_name":   row["constituency_name"],
                    "constituency_number": row["constituency_number"] or "",
                    "reservation":         row["reservation"],
                    "mla_name":            mla_name,
                    "mla_party":           mla_party,
                    "politician_id":       pol_id or "",
                    "match_type":          match_type,
                    "source_url":          row["source_url"],
                    "source_name":         row["source_name"],
                    "confidence":          confidence,
                })

                total_rows   += 1
                if match_type != "no_match":
                    matched_rows += 1
                    state_matched += 1

            print(f"  MLA matched: {state_matched}/{len(rows)}")
            print()

    print("=" * 60)
    print(f"TOTAL constituencies : {total_rows}")
    print(f"MLA matched          : {matched_rows}")
    print(f"Unmatched            : {total_rows - matched_rows}")
    print(f"Output               : {out_path.resolve()}")
    print()
    print("DRY RUN COMPLETE — no DB writes were made.")


if __name__ == "__main__":
    main()
