#!/usr/bin/env python3
"""
dry_run_election_history.py
DRY RUN ONLY — no DB writes.

Scrapes Wikipedia election result pages (multiple years) for 7 states.
Extracts winner name, party, votes, margin per constituency per election.
Matches winners to verified_politicians by name + constituency.
Computes track record summary per matched politician.

Outputs:
  output/election_history_dry_run.csv
  output/track_record_summary_dry_run.csv

Usage:
    python dry_run_election_history.py
    python dry_run_election_history.py --state karnataka
    python dry_run_election_history.py --state karnataka --years 2023 2018
"""

import csv
import os
import re
import sys
import time
import argparse
from pathlib import Path
from collections import defaultdict

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# ── Constants ─────────────────────────────────────────────────────────────────

WIKI_API = "https://en.wikipedia.org/w/api.php"
UA       = "NetaLog/1.0 (vinith0140@gmail.com) python-httpx dry-run"
OUT_DIR  = Path("output")

# 7 states: state_id → (state_name, [(year, wikipedia_article)])
# Sources: Wikipedia state assembly election articles (public, no auth)
STATE_ELECTIONS = {
    11: ("Karnataka", [
        (2023, "2023_Karnataka_Legislative_Assembly_election"),
        (2018, "2018_Karnataka_Legislative_Assembly_election"),
        (2013, "2013_Karnataka_Legislative_Assembly_election"),
    ]),
    14: ("Maharashtra", [
        (2024, "2024_Maharashtra_Legislative_Assembly_election"),
        (2019, "2019_Maharashtra_Legislative_Assembly_election"),
        (2014, "2014_Maharashtra_Legislative_Assembly_election"),
    ]),
    23: ("Tamil Nadu", [
        (2026, "2026_Tamil_Nadu_Legislative_Assembly_election"),
        (2021, "2021_Tamil_Nadu_Legislative_Assembly_election"),
        (2016, "2016_Tamil_Nadu_Legislative_Assembly_election"),
    ]),
    24: ("Telangana", [
        (2023, "2023_Telangana_Legislative_Assembly_election"),
        (2018, "2018_Telangana_Legislative_Assembly_election"),
        # Telangana formed 2014 — only 2 state elections available
    ]),
    26: ("Uttar Pradesh", [
        (2022, "2022_Uttar_Pradesh_Legislative_Assembly_election"),
        (2017, "2017_Uttar_Pradesh_Legislative_Assembly_election"),
        (2012, "2012_Uttar_Pradesh_Legislative_Assembly_election"),
    ]),
    28: ("West Bengal", [
        (2026, "2026_West_Bengal_Legislative_Assembly_election"),
        (2021, "2021_West_Bengal_Legislative_Assembly_election"),
        (2016, "2016_West_Bengal_Legislative_Assembly_election"),
    ]),
    29: ("Delhi", [
        (2025, "2025_Delhi_Legislative_Assembly_election"),
        (2020, "2020_Delhi_Legislative_Assembly_election"),
        (2015, "2015_Delhi_Legislative_Assembly_election"),
    ]),
}

HISTORY_COLS = [
    "politician_id", "politician_name", "election_year", "election_type",
    "state", "constituency", "party", "result", "votes", "margin",
    "source_url", "source_name", "confidence",
]

SUMMARY_COLS = [
    "politician_id", "politician_name", "elections_contested", "elections_won",
    "win_rate_pct", "first_election_year", "years_in_politics",
    "all_constituencies", "all_parties", "source_note",
]

CURRENT_YEAR = 2026


# ── Wikipedia helpers ─────────────────────────────────────────────────────────

def fetch_sections(client: httpx.Client, article: str) -> list[dict]:
    r = client.get(WIKI_API, params={
        "action": "parse", "page": article,
        "prop": "sections", "format": "json",
    })
    r.raise_for_status()
    return r.json().get("parse", {}).get("sections", [])


def fetch_section_html(client: httpx.Client, article: str, section_idx: int) -> str:
    r = client.get(WIKI_API, params={
        "action": "parse", "page": article,
        "prop": "text", "section": section_idx, "format": "json",
    })
    r.raise_for_status()
    return r.json().get("parse", {}).get("text", {}).get("*", "")


def check_article_exists(client: httpx.Client, article: str) -> bool:
    r = client.get(WIKI_API, params={
        "action": "query", "titles": article, "format": "json",
    })
    r.raise_for_status()
    pages = r.json().get("query", {}).get("pages", {})
    return "-1" not in pages  # -1 means missing


def find_results_section(sections: list[dict]) -> int | None:
    """Find the constituency results section index."""
    best = None
    for s in sections:
        title = s["line"].lower()
        if "turnout" in title:
            continue
        if "constituency" in title:
            if best is None:
                best = int(s["index"])
            if "result" in title or title.startswith("by constituency"):
                best = int(s["index"])
    return best


def wiki_url(article: str) -> str:
    return f"https://en.wikipedia.org/wiki/{article}"


# ── Table parser ──────────────────────────────────────────────────────────────

def _is_num(s: str) -> bool:
    return bool(re.match(r"^\d+$", s.strip()))


def _is_pct(s: str) -> bool:
    return bool(re.match(r"^\d+[\.,]\d+%?$", s.strip()))


def _looks_like_name(s: str) -> bool:
    s = s.strip()
    if len(s) < 3 or not s[0].isupper():
        return False
    if _is_num(s) or _is_pct(s):
        return False
    return s.lower() not in ("candidate", "winner", "name", "party", "constituency")


def _parse_votes(s: str) -> int | None:
    s = re.sub(r"[,\s]", "", s.strip())
    if re.match(r"^\d+$", s):
        return int(s)
    return None


def parse_election_results(html: str) -> list[dict]:
    """
    Parse constituency results table from Wikipedia section HTML.
    Returns list of {constituency, winner, party, votes, margin}.
    Reuses the same flexible parser logic from verify_mlas.py.
    """
    soup  = BeautifulSoup(html, "html.parser")
    found = []

    for table in soup.find_all("table", class_=re.compile("wikitable")):
        if "constituency" not in table.get_text(" ", strip=True).lower():
            continue

        for row in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if len(cells) < 5:
                continue

            # Find constituency number
            num_idx = None
            if _is_num(cells[0]):
                num_idx = 0
            elif len(cells) > 1 and _is_num(cells[1]):
                num_idx = 1

            if num_idx is None:
                continue

            const_idx = num_idx + 1
            if const_idx >= len(cells):
                continue
            constituency = cells[const_idx]

            # Find winner name
            winner = party = None
            for i in range(const_idx + 1, min(const_idx + 5, len(cells))):
                if _looks_like_name(cells[i]):
                    winner = cells[i]
                    # Party: next short non-numeric cell
                    for j in range(i + 1, min(i + 4, len(cells))):
                        c = cells[j]
                        if c and not _is_num(c) and not _is_pct(c) and len(c) <= 60:
                            party = c
                            break
                    # Votes: look for a large number after party
                    votes  = None
                    margin = None
                    for k in range(i + 1, len(cells)):
                        v = _parse_votes(cells[k])
                        if v and v > 100:  # plausible vote count
                            if votes is None:
                                votes = v
                            elif margin is None:
                                margin = v
                                break
                    found.append({
                        "constituency": constituency,
                        "winner":       winner,
                        "party":        party or "",
                        "votes":        votes,
                        "margin":       margin,
                    })
                    break

    return found


# ── Name matching ─────────────────────────────────────────────────────────────

def names_match(a: str, b: str) -> bool:
    a, b = a.lower().strip(), b.lower().strip()
    if a == b or a in b or b in a:
        return True
    wa = [w for w in a.split() if len(w) > 3]
    return bool(wa) and sum(1 for w in wa if w in b) >= max(1, (len(wa) + 1) // 2)


def const_match(a: str, b: str) -> bool:
    a, b = a.lower().strip(), b.lower().strip()
    if not a or not b:
        return True
    if a == b or a in b or b in a:
        return True
    wa = [w for w in a.split() if len(w) > 3]
    return bool(wa) and all(w in b for w in wa[:2])


def match_politician(winner_name: str, constituency: str, db_pols: list[dict]):
    """Match a Wikipedia winner to verified_politicians. Returns (pol, match_type)."""
    # Round 1: name + constituency
    for pol in db_pols:
        if names_match(winner_name, pol["name"]) and const_match(constituency, pol.get("constituency") or ""):
            return pol, "name+constituency"
    # Round 2: name only
    for pol in db_pols:
        if names_match(winner_name, pol["name"]):
            return pol, "name_only"
    return None, "no_match"


# ── Track record computation ──────────────────────────────────────────────────

def compute_summary(pol_id: int, pol_name: str, history_rows: list[dict]) -> dict:
    contested  = len(history_rows)
    won        = sum(1 for r in history_rows if r["result"] == "Won")
    win_rate   = round(won / contested * 100, 1) if contested else 0.0
    years      = sorted(set(r["election_year"] for r in history_rows))
    first_year = years[0] if years else None
    yip        = (CURRENT_YEAR - first_year) if first_year else None
    consts     = list(dict.fromkeys(r["constituency"] for r in history_rows))
    parties    = list(dict.fromkeys(r["party"] for r in history_rows if r["party"]))
    return {
        "politician_id":       pol_id,
        "politician_name":     pol_name,
        "elections_contested": contested,
        "elections_won":       won,
        "win_rate_pct":        win_rate,
        "first_election_year": first_year or "",
        "years_in_politics":   yip or "",
        "all_constituencies":  " | ".join(consts),
        "all_parties":         " | ".join(parties),
        "source_note":         "Wikipedia election results only — losers not included",
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", help="One state name filter (e.g. karnataka)")
    parser.add_argument("--years", nargs="+", type=int, help="Limit to specific years")
    args = parser.parse_args()

    target = {
        sid: info for sid, info in STATE_ELECTIONS.items()
        if not args.state or args.state.lower() in info[0].lower()
    }
    if not target:
        sys.exit(f"No state matched '{args.state}'")

    # Load verified_politicians
    print("Loading verified_politicians from DB...")
    from app.database import get_db
    db   = get_db()
    pols = db.table("verified_politicians").select(
        "id,name,party,constituency,state_id"
    ).execute().data or []
    pols_by_state: dict[int, list] = defaultdict(list)
    for p in pols:
        pols_by_state[p["state_id"]].append(p)
    print(f"  Loaded {len(pols)} politicians\n")

    OUT_DIR.mkdir(exist_ok=True)
    hist_path    = OUT_DIR / "election_history_dry_run.csv"
    summary_path = OUT_DIR / "track_record_summary_dry_run.csv"

    # politician_id → list of history rows (for summary computation)
    history_by_pol: dict[int, list] = defaultdict(list)
    pol_names: dict[int, str] = {}

    grand_total   = 0
    grand_matched = 0
    grand_skipped = 0  # articles not found / not parseable

    with (
        httpx.Client(headers={"User-Agent": UA}, timeout=30, follow_redirects=True) as client,
        open(hist_path, "w", newline="", encoding="utf-8") as hf,
    ):
        writer = csv.DictWriter(hf, fieldnames=HISTORY_COLS)
        writer.writeheader()

        for state_id, (state_name, elections) in target.items():
            print(f"══ {state_name} ({'|'.join(str(y) for y,_ in elections)}) ══")
            db_pols = pols_by_state.get(state_id, [])

            for year, article in elections:
                if args.years and year not in args.years:
                    continue

                url = wiki_url(article)
                print(f"  {year}  {url}")

                # Check article exists
                try:
                    if not check_article_exists(client, article):
                        print(f"        → article not found on Wikipedia, skipping")
                        grand_skipped += 1
                        time.sleep(0.5)
                        continue
                    time.sleep(0.5)
                except Exception as exc:
                    print(f"        → check failed: {exc}")
                    grand_skipped += 1
                    continue

                # Find results section
                try:
                    sections    = fetch_sections(client, article)
                    section_idx = find_results_section(sections)
                    time.sleep(0.5)
                    if section_idx is None:
                        print(f"        → no constituency results section found")
                        grand_skipped += 1
                        continue
                    html = fetch_section_html(client, article, section_idx)
                    time.sleep(1)
                except Exception as exc:
                    print(f"        → fetch error: {exc}")
                    grand_skipped += 1
                    continue

                results = parse_election_results(html)
                print(f"        → {len(results)} constituency results parsed")

                matched_this = 0
                for r in results:
                    pol, match_type = match_politician(r["winner"], r["constituency"], db_pols)
                    confidence  = "confirmed" if match_type == "name+constituency" else (
                                  "uncertain"  if match_type == "name_only" else "uncertain"
                    )
                    pol_id   = pol["id"]   if pol else None
                    pol_name = pol["name"] if pol else r["winner"]

                    row = {
                        "politician_id":   pol_id or "",
                        "politician_name": pol_name,
                        "election_year":   year,
                        "election_type":   "State Assembly",
                        "state":           state_name,
                        "constituency":    r["constituency"],
                        "party":           r["party"],
                        "result":          "Won",  # Wikipedia only has winners
                        "votes":           r["votes"] or "",
                        "margin":          r["margin"] or "",
                        "source_url":      url,
                        "source_name":     "Wikipedia",
                        "confidence":      confidence,
                    }
                    writer.writerow(row)
                    grand_total += 1

                    if pol_id:
                        matched_this      += 1
                        grand_matched     += 1
                        pol_names[pol_id]  = pol_name
                        history_by_pol[pol_id].append(row)

                unmatched = len(results) - matched_this
                print(f"        → matched {matched_this} | unmatched {unmatched}")

            print()

    # ── Track record summary ──────────────────────────────────────────────────
    print(f"Computing track record summaries for {len(history_by_pol)} politicians...")
    with open(summary_path, "w", newline="", encoding="utf-8") as sf:
        writer2 = csv.DictWriter(sf, fieldnames=SUMMARY_COLS)
        writer2.writeheader()
        for pol_id, rows in sorted(history_by_pol.items()):
            summary = compute_summary(pol_id, pol_names[pol_id], rows)
            writer2.writerow(summary)

    print()
    print("=" * 60)
    print(f"Total election records : {grand_total}")
    print(f"Matched to verified DB : {grand_matched}")
    print(f"Articles skipped       : {grand_skipped}")
    print(f"Unmatched winners      : {grand_total - grand_matched}")
    print()
    print(f"  History  → {hist_path.resolve()}")
    print(f"  Summary  → {summary_path.resolve()}")
    print()
    print("NOTE: Only election WINNERS are in Wikipedia results.")
    print("      Losers' histories require MyNeta scraping (not done here).")
    print()
    print("DRY RUN COMPLETE — no DB writes were made.")


if __name__ == "__main__":
    main()
