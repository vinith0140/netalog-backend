"""
verify_mlas.py
Scrapes official election winner data from Wikipedia (MediaWiki API) for each state.
Matches winners against politicians table by name + constituency.
Copies verified matches to verified_politicians with verified_at = now().
"""

import re
import sys
import time
import httpx
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

WIKI_API   = "https://en.wikipedia.org/w/api.php"
WIKI_UA    = "NetaLog/1.0 (vinith0140@gmail.com) python-httpx"
BATCH_SIZE = 500

# Most recent state assembly election Wikipedia article titles.
STATE_ELECTIONS = {
    "Andhra Pradesh":    "2024_Andhra_Pradesh_Legislative_Assembly_election",
    "Arunachal Pradesh": "2024_Arunachal_Pradesh_Legislative_Assembly_election",
    "Assam":             "2021_Assam_Legislative_Assembly_election",
    "Bihar":             "2020_Bihar_Legislative_Assembly_election",
    "Chhattisgarh":      "2023_Chhattisgarh_Legislative_Assembly_election",
    "Delhi":             "2025_Delhi_Legislative_Assembly_election",
    "Goa":               "2022_Goa_Legislative_Assembly_election",
    "Gujarat":           "2022_Gujarat_Legislative_Assembly_election",
    "Haryana":           "2024_Haryana_Legislative_Assembly_election",
    "Himachal Pradesh":  "2022_Himachal_Pradesh_Legislative_Assembly_election",
    "Jammu & Kashmir":   "2024_Jammu_and_Kashmir_Legislative_Assembly_election",
    "Jharkhand":         "2024_Jharkhand_Legislative_Assembly_election",
    "Karnataka":         "2023_Karnataka_Legislative_Assembly_election",
    "Kerala":            "2021_Kerala_Legislative_Assembly_election",
    "Madhya Pradesh":    "2023_Madhya_Pradesh_Legislative_Assembly_election",
    "Maharashtra":       "2024_Maharashtra_Legislative_Assembly_election",
    "Manipur":           "2022_Manipur_Legislative_Assembly_election",
    "Meghalaya":         "2023_Meghalaya_Legislative_Assembly_election",
    "Mizoram":           "2023_Mizoram_Legislative_Assembly_election",
    "Nagaland":          "2023_Nagaland_Legislative_Assembly_election",
    "Odisha":            "2024_Odisha_Legislative_Assembly_election",
    "Punjab":            "2022_Punjab_Legislative_Assembly_election",
    "Rajasthan":         "2023_Rajasthan_Legislative_Assembly_election",
    "Sikkim":            "2024_Sikkim_Legislative_Assembly_election",
    "Tamil Nadu":        "2021_Tamil_Nadu_Legislative_Assembly_election",
    "Telangana":         "2023_Telangana_Legislative_Assembly_election",
    "Tripura":           "2023_Tripura_Legislative_Assembly_election",
    "Uttar Pradesh":     "2022_Uttar_Pradesh_Legislative_Assembly_election",
    "Uttarakhand":       "2022_Uttarakhand_Legislative_Assembly_election",
    "West Bengal":       "2021_West_Bengal_Legislative_Assembly_election",
}


# ── Wikipedia helpers ─────────────────────────────────────────────────────────

def wiki_sections(client, article):
    r = client.get(WIKI_API, params={
        "action": "parse", "page": article,
        "prop": "sections", "format": "json",
    })
    r.raise_for_status()
    return r.json().get("parse", {}).get("sections", [])


def wiki_section_html(client, article, section_idx):
    r = client.get(WIKI_API, params={
        "action": "parse", "page": article,
        "prop": "text", "section": section_idx, "format": "json",
    })
    r.raise_for_status()
    return r.json().get("parse", {}).get("text", {}).get("*", "")


def find_constituency_section(sections):
    """Return index of the results-by-constituency section.
    Prefers sections with 'result' + 'constituency'; skips 'turnout' sections.
    """
    best = None
    for s in sections:
        title = s["line"].lower()
        if "turnout" in title:
            continue
        if "constituency" in title:
            if best is None:
                best = int(s["index"])
            # Stronger match: explicitly a results section
            if "result" in title or title.startswith("by constituency"):
                best = int(s["index"])
    return best


# ── Table parser ──────────────────────────────────────────────────────────────

def _is_num(s):
    return bool(re.match(r"^\d+$", s.strip()))


def _is_pct(s):
    return bool(re.match(r"^\d+[\.,]\d+%?$", s.strip()))


def _looks_like_name(s):
    """True when s resembles a person's name: capitalised, 2+ words, not a number/pct."""
    s = s.strip()
    if len(s) < 3 or not s[0].isupper():
        return False
    if _is_num(s) or _is_pct(s):
        return False
    # Reject header-row text
    if s.lower() in ("candidate", "winner", "name", "party", "constituency", "district"):
        return False
    return True


def parse_winners(html):
    """
    Parse constituency results tables from Wikipedia section HTML.

    Works across multiple table layouts by:
      1. Finding the constituency number (first numeric cell in a row).
      2. Taking the next cell as the constituency name.
      3. Scanning subsequent cells for the first name-like cell → winner.
      4. Taking the next non-empty, non-numeric cell after winner → party.
    This handles extra turnout-% columns, empty colour-swatch cells, and
    district-header rows automatically.
    """
    soup = BeautifulSoup(html, "html.parser")
    winners = []

    for table in soup.find_all("table", class_=re.compile("wikitable")):
        if "constituency" not in table.get_text(" ", strip=True).lower():
            continue

        for row in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if len(cells) < 5:
                continue

            # Find the constituency number
            num_idx = None
            if _is_num(cells[0]):
                num_idx = 0
            elif len(cells) > 1 and _is_num(cells[1]):
                num_idx = 1          # first cell is district name

            if num_idx is None:
                continue

            const_idx = num_idx + 1
            if const_idx >= len(cells):
                continue
            constituency = cells[const_idx]

            # Find winner: first name-like cell after the constituency name
            winner = party = None
            for i in range(const_idx + 1, min(const_idx + 5, len(cells))):
                if _looks_like_name(cells[i]):
                    winner = cells[i]
                    # Party: next non-empty, non-number, reasonably short cell
                    for j in range(i + 1, min(i + 4, len(cells))):
                        c = cells[j]
                        if c and not _is_num(c) and not _is_pct(c) and len(c) <= 60:
                            party = c
                            break
                    break

            if not winner or not constituency or len(winner) < 3:
                continue

            winners.append({
                "constituency": constituency,
                "winner":       winner,
                "party":        party or "",
            })

    return winners


# ── Name / constituency matching ──────────────────────────────────────────────

def names_match(a, b):
    a, b = a.lower().strip(), b.lower().strip()
    if a == b or a in b or b in a:
        return True
    wa = [w for w in a.split() if len(w) > 3]
    if not wa:
        return False
    return sum(1 for w in wa if w in b) >= max(1, (len(wa) + 1) // 2)


def const_match(a, b):
    a, b = a.lower().strip(), b.lower().strip()
    if not a or not b:
        return True   # no constituency data — don't penalise
    if a == b or a in b or b in a:
        return True
    wa = [w for w in a.split() if len(w) > 3]
    return bool(wa) and all(w in b for w in wa[:2])


# ── DB helpers ────────────────────────────────────────────────────────────────

def bulk_copy_to_verified(db, rows):
    if not rows:
        return
    now = datetime.now(timezone.utc).isoformat()
    payload = [{**r, "verified_at": now} for r in rows]
    for i in range(0, len(payload), BATCH_SIZE):
        db.table("verified_politicians").upsert(payload[i:i + BATCH_SIZE], on_conflict="id").execute()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    from app.database import get_db
    db = get_db()

    states_raw = db.table("states").select("id,name").execute().data or []
    if not states_raw:
        sys.exit("ERROR: No states found in Supabase")
    states_by_name = {s["name"]: s["id"] for s in states_raw}

    grand_verified = 0
    grand_flagged  = 0

    print("MLA Verification  |  Wikipedia election results\n")
    print(f"{'State':<22} {'ECI winners':>11} {'Verified':>9} {'Flagged':>8}")
    print("-" * 56)

    with httpx.Client(headers={"User-Agent": WIKI_UA}, timeout=30, follow_redirects=True) as client:

        for state_name, article in STATE_ELECTIONS.items():
            state_id = states_by_name.get(state_name)
            if not state_id:
                print(f"{state_name:<22} state not in DB")
                continue

            try:
                sections    = wiki_sections(client, article)
                section_idx = find_constituency_section(sections)
                if section_idx is None:
                    print(f"{state_name:<22} {'no constituency section':>11}")
                    time.sleep(1)
                    continue

                html = wiki_section_html(client, article, section_idx)
                time.sleep(1)
            except Exception as exc:
                print(f"{state_name:<22} WIKI ERR: {str(exc)[:40]}")
                time.sleep(2)
                continue

            winners = parse_winners(html)
            if not winners:
                print(f"{state_name:<22} {'no winners parsed':>11}")
                continue

            # Fetch DB politicians for this state
            db_pols = (
                db.table("politicians")
                .select("*")
                .eq("state_id", state_id)
                .in_("position", ["MLA", "Cabinet Minister", "Chief Minister"])
                .execute()
                .data
            ) or []

            verified_rows = []
            flagged       = []

            for w in winners:
                wiki_name  = w["winner"]
                wiki_const = w["constituency"]

                # Match: name + constituency first, then name-only fallback
                matched = None
                for pol in db_pols:
                    if names_match(wiki_name, pol["name"]) and const_match(wiki_const, pol.get("constituency") or ""):
                        matched = pol
                        break
                if matched is None:
                    for pol in db_pols:
                        if names_match(wiki_name, pol["name"]):
                            matched = pol
                            break

                if matched:
                    verified_rows.append(matched)
                else:
                    flagged.append(f"{wiki_name} ({wiki_const})")

            # Deduplicate by politician id (same person may match multiple constituencies)
            seen = {}
            for r in verified_rows:
                seen[r["id"]] = r
            verified_rows = list(seen.values())

            bulk_copy_to_verified(db, verified_rows)
            grand_verified += len(verified_rows)
            grand_flagged  += len(flagged)

            print(f"{state_name:<22} {len(winners):>11} {len(verified_rows):>9} {len(flagged):>8}")
            if flagged:
                for f in flagged[:3]:
                    print(f"  flagged: {f}")
                if len(flagged) > 3:
                    print(f"  ... and {len(flagged) - 3} more")

            time.sleep(1)

    print()
    print("=" * 56)
    print(f"Total verified : {grand_verified}")
    print(f"Total flagged  : {grand_flagged}")
    print(f"Copied to verified_politicians: {grand_verified}")


if __name__ == "__main__":
    main()
