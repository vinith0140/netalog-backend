"""
scrape_state_data.py
Scrapes Wikipedia for state-level data and saves to Supabase states table.

Run this SQL in Supabase Dashboard -> SQL Editor FIRST:
  ALTER TABLE states ADD COLUMN IF NOT EXISTS capital        text;
  ALTER TABLE states ADD COLUMN IF NOT EXISTS population     bigint;
  ALTER TABLE states ADD COLUMN IF NOT EXISTS last_election  integer;
  ALTER TABLE states ADD COLUMN IF NOT EXISTS next_election  integer;
  ALTER TABLE states ADD COLUMN IF NOT EXISTS ruling_party   text;
  ALTER TABLE states ADD COLUMN IF NOT EXISTS party_seats    integer;
  ALTER TABLE states ADD COLUMN IF NOT EXISTS total_seats    integer;
  ALTER TABLE states ADD COLUMN IF NOT EXISTS in_power_since integer;

Usage:
  python scrape_state_data.py           # test: Telangana only
  python scrape_state_data.py --all     # all 30 states
"""

import re
import sys
import time
import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_UA  = "NetaLog/1.0 (vinith0140@gmail.com) python-httpx"

# Abbreviation → search terms used to find a party in election infobox text
PARTY_TERMS = {
    "BJP":   ["BJP", "Bharatiya Janata"],
    "INC":   ["INC", "Indian National Congress", "Congress"],
    "AAP":   ["AAP", "Aam Aadmi"],
    "TMC":   ["TMC", "AITC", "Trinamool"],
    "TVK":   ["TVK", "Tamilaga Vettri"],
    "TDP":   ["TDP", "Telugu Desam"],
    "DMK":   ["DMK", "Dravida Munnetra"],
    "JMM":   ["JMM", "Jharkhand Mukti"],
    "NPP":   ["NPP", "National People"],
    "ZPM":   ["ZPM", "Zoram People"],
    "SKM":   ["SKM", "Sikkim Krantikari"],
    "NDPP":  ["NDPP"],
    "NPF":   ["NPF", "Naga People"],
    "MNF":   ["MNF", "Mizo National"],
    "JD(U)": ["JD(U)", "JDU", "Janata Dal"],
    "BJD":   ["BJD", "Biju Janata"],
    "YSR":   ["YSRCP", "YCP", "YSR Congress"],
}

# Wikipedia article title for each state's main article
STATE_ARTICLES = {
    "Andhra Pradesh":    "Andhra Pradesh",
    "Arunachal Pradesh": "Arunachal Pradesh",
    "Assam":             "Assam",
    "Bihar":             "Bihar",
    "Chhattisgarh":      "Chhattisgarh",
    "Delhi":             "Delhi",
    "Goa":               "Goa",
    "Gujarat":           "Gujarat",
    "Haryana":           "Haryana",
    "Himachal Pradesh":  "Himachal Pradesh",
    "Jammu & Kashmir":   "Jammu and Kashmir",
    "Jharkhand":         "Jharkhand",
    "Karnataka":         "Karnataka",
    "Kerala":            "Kerala",
    "Madhya Pradesh":    "Madhya Pradesh",
    "Maharashtra":       "Maharashtra",
    "Manipur":           "Manipur",
    "Meghalaya":         "Meghalaya",
    "Mizoram":           "Mizoram",
    "Nagaland":          "Nagaland",
    "Odisha":            "Odisha",
    "Punjab":            "Punjab, India",
    "Rajasthan":         "Rajasthan",
    "Sikkim":            "Sikkim",
    "Tamil Nadu":        "Tamil Nadu",
    "Telangana":         "Telangana",
    "Tripura":           "Tripura",
    "Uttar Pradesh":     "Uttar Pradesh",
    "Uttarakhand":       "Uttarakhand",
    "West Bengal":       "West Bengal",
}

# Most recent state assembly election article titles
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

# Reliable fallback: constitutional total assembly seats (rarely change)
TOTAL_SEATS_FALLBACK = {
    "Andhra Pradesh": 175, "Arunachal Pradesh": 60, "Assam": 126, "Bihar": 243,
    "Chhattisgarh": 90, "Delhi": 70, "Goa": 40, "Gujarat": 182, "Haryana": 90,
    "Himachal Pradesh": 68, "Jammu & Kashmir": 90, "Jharkhand": 81, "Karnataka": 224,
    "Kerala": 140, "Madhya Pradesh": 230, "Maharashtra": 288, "Manipur": 60,
    "Meghalaya": 60, "Mizoram": 40, "Nagaland": 60, "Odisha": 147, "Punjab": 117,
    "Rajasthan": 200, "Sikkim": 32, "Tamil Nadu": 234, "Telangana": 119,
    "Tripura": 60, "Uttar Pradesh": 403, "Uttarakhand": 70, "West Bengal": 294,
}


# ── Wikipedia helpers ─────────────────────────────────────────────────────────

def wiki_html(client, article, section=0):
    r = client.get(WIKI_API, params={
        "action": "parse", "page": article,
        "prop": "text", "section": section, "format": "json",
    })
    r.raise_for_status()
    return r.json().get("parse", {}).get("text", {}).get("*", "")


def _clean(s):
    return re.sub(r'\[[\w\d ]*?\]', '', s).strip()


# ── State main article parser ─────────────────────────────────────────────────

def parse_state_article(html):
    """
    Extract from the state's Wikipedia main article infobox:
      capital, population, total_seats, ruling_party
    """
    soup = BeautifulSoup(html, "html.parser")
    result = {}

    infobox = soup.find("table", class_="infobox")
    if not infobox:
        return result

    in_pop = False

    for row in infobox.find_all("tr"):
        th = row.find("th")
        td = row.find("td")

        th_text = _clean(th.get_text(" ", strip=True)) if th else ""
        td_text = _clean(td.get_text(" ", strip=True)) if td else ""

        label = th_text.lower().strip()

        # ── Capital ──────────────────────────────────────────────────────────
        # Matches: "Capital", "Capital city", "Capital and largest city",
        #          "Administrative capital", "Seat of government"
        is_capital_row = ("capital" in label or "seat of government" in label)
        if is_capital_row and not result.get("capital") and td:
            # Use first line only — avoids "Shimla Dharamshala" (dual-capital states)
            first_line = td.get_text("\n").splitlines()
            first_line = next((l.strip() for l in first_line if l.strip()), "")
            cap = _clean(re.split(r'[/(]', first_line)[0].strip())
            cap = re.sub(r'\s+', ' ', cap).strip()
            # Reject if it looks like a number (e.g., area figures leaking in)
            if cap and not re.match(r'^[\d,.\s]+$', cap):
                result["capital"] = cap

        # ── Population section header ────────────────────────────────────────
        if re.match(r'^population', label) and not re.search(r'\d{6,}', td_text):
            in_pop = True
            continue

        # ── Population total sub-row ─────────────────────────────────────────
        if in_pop:
            combined = th_text + " " + td_text
            nums = re.findall(r'[\d,]+', combined)
            found = False
            for n in nums:
                v = int(n.replace(",", ""))
                if v > 100_000:
                    result["population"] = v
                    in_pop = False
                    found = True
                    break
            if not found and label and not any(x in label for x in
                    ["total", "rank", "density", "urban", "rural", "•"]):
                in_pop = False

        # ── Assembly total seats: "• Assembly" row ───────────────────────────
        if "assembly" in label and "legislative" not in label.replace("assembly", ""):
            m = re.search(r'(\d+)\s+seats', td_text)
            if m:
                result["total_seats"] = int(m.group(1))

        # ── Ruling party fallback: Chief Minister row "Name ( PARTY )" ─────────
        # Only used when verified_politicians has "IND" (bad MyNeta data)
        if "chief minister" in label and not result.get("wiki_ruling_party"):
            m = re.search(r'\(\s*([A-Z][A-Za-z()&()\s]{1,20}?)\s*\)', td_text)
            if m:
                result["wiki_ruling_party"] = m.group(1).strip()

    return result


# ── Election article parser ───────────────────────────────────────────────────

def _party_search_terms(ruling_party):
    """Return list of strings to look for in election infobox text."""
    terms = [ruling_party]
    for abbrev, aliases in PARTY_TERMS.items():
        if ruling_party == abbrev or ruling_party in aliases:
            terms.extend(aliases)
            terms.append(abbrev)
    return list(dict.fromkeys(terms))  # dedupe, preserve order


def find_party_seats(text, ruling_party):
    """
    Find seat count for ruling_party in election infobox text.
    Handles "Party P1 P2 ... Seats won S1 S2" positional format,
    and falls back to proximity search.
    """
    if not ruling_party:
        return None

    terms = _party_search_terms(ruling_party)

    # Positional parse: "Party INC BRS ... Seats won 64 39"
    party_m = re.search(
        r'\bParty\b\s+([\w()\s&/]{2,60}?)\s+(?:Leader|Last election|Seats won|Seat change)',
        text,
    )
    seats_m = re.search(r'Seats\s+won\s+([\d ]+)', text)

    if party_m and seats_m:
        tokens = party_m.group(1).strip().split()
        nums   = [int(x) for x in seats_m.group(1).strip().split() if x.isdigit()]
        for term in terms:
            for i, tok in enumerate(tokens):
                if term.lower() == tok.lower():
                    if i < len(nums):
                        return nums[i]

    # Proximity fallback: party term near a plausible seat number
    for term in terms:
        pattern = rf'(?:{re.escape(term)}[\s\S]{{0,60}}?)(\d{{1,3}})(?!\d)'
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            val = int(m.group(1))
            if 1 <= val <= 500:
                return val

    return None


def parse_election_article(html, election_year, ruling_party=None):
    """
    Extract from election article infobox:
      last_election, total_seats, party_seats
    party_seats is for ruling_party specifically (not always the majority winner).
    """
    soup = BeautifulSoup(html, "html.parser")
    result = {"last_election": election_year}

    for table in soup.find_all("table"):
        if "infobox" not in " ".join(table.get("class", [])):
            continue

        text = re.sub(r'\s+', ' ', table.get_text(" ", strip=True))

        # Total seats: "All 119 seats in the" or "119 / 119"
        m = re.search(r'(?:All\s+)?(\d+)\s+(?:of\s+\d+\s+)?seats\s+in\s+the', text)
        if m:
            result["total_seats"] = int(m.group(1))

        if not result.get("total_seats"):
            m2 = re.search(r'(\d+)\s*/\s*(\d+)', text)
            if m2:
                result["total_seats"] = max(int(m2.group(1)), int(m2.group(2)))

        # Party seats: search for ruling party specifically, fall back to majority
        if ruling_party:
            seats = find_party_seats(text, ruling_party)
            if seats:
                result["party_seats"] = seats

        if not result.get("party_seats"):
            m3 = re.search(r'Seats\s+won\s+(\d+)', text)
            if m3:
                result["party_seats"] = int(m3.group(1))

        break

    return result


# ── DB helpers ───────────────────────────────────────────────────────────────

def get_ruling_party_from_db(db, state_id, wiki_fallback=None):
    """
    Primary: verified_politicians CM party.
    Falls back to wiki_fallback when DB has 'IND' (bad MyNeta data).
    """
    try:
        cm = (
            db.table("verified_politicians")
            .select("party")
            .eq("state_id", state_id)
            .eq("position", "Chief Minister")
            .limit(1)
            .execute()
            .data
        )
        if cm:
            party = cm[0]["party"] or ""
            if party and party not in ("IND", "Independent", ""):
                return party
    except Exception:
        pass
    # CM party is IND/missing — use Wikipedia state article value
    return wiki_fallback


def get_party_seats_from_db(db, state_id, ruling_party):
    """
    Count Chief Minister + Cabinet Ministers from verified_politicians
    belonging to ruling_party in this state.
    This reflects current government composition, not election result.
    """
    if not ruling_party:
        return None
    try:
        rows = (
            db.table("verified_politicians")
            .select("id")
            .eq("state_id", state_id)
            .eq("party", ruling_party)
            .in_("position", ["Chief Minister", "Cabinet Minister"])
            .execute()
            .data
        )
        return len(rows) if rows else None
    except Exception:
        return None


# ── Per-state scraper ─────────────────────────────────────────────────────────

def scrape_one_state(client, db, state_name, state_id):
    data = {}

    main_article  = STATE_ARTICLES.get(state_name, state_name)
    elect_article = STATE_ELECTIONS.get(state_name, "")

    # Election year from article title, e.g. "2023_Telangana_..."
    m = re.match(r'^(\d{4})_', elect_article)
    elect_year = int(m.group(1)) if m else None

    # 1. State main article → capital, population, total_seats
    #    Also captures wiki_ruling_party as fallback for IND cases
    wiki_article_data = {}
    try:
        html = wiki_html(client, main_article, section=0)
        wiki_article_data = {k: v for k, v in parse_state_article(html).items() if v is not None}
        data.update({k: v for k, v in wiki_article_data.items() if k != "wiki_ruling_party"})
        time.sleep(1)
    except Exception as e:
        print(f"  [WARN] main article: {e}")

    # 2. Ruling party: verified_politicians CM party (primary).
    #    Falls back to Wikipedia CM row if DB has "IND" (bad MyNeta scrape data).
    wiki_party = wiki_article_data.get("wiki_ruling_party")
    ruling_party = get_ruling_party_from_db(db, state_id, wiki_fallback=wiki_party)
    if ruling_party:
        data["ruling_party"] = ruling_party

    # 3. Party seats: count CM + Cabinet Ministers in verified_politicians for ruling party
    party_seats = get_party_seats_from_db(db, state_id, ruling_party)
    if party_seats:
        data["party_seats"] = party_seats

    # 4. Election article → total_seats, last_election (no longer used for party_seats)
    if elect_article:
        try:
            html = wiki_html(client, elect_article, section=0)
            elec = parse_election_article(html, elect_year, ruling_party)
            if elec.get("total_seats") and not data.get("total_seats"):
                data["total_seats"] = elec["total_seats"]
            data["last_election"] = elect_year or elec.get("last_election")
            time.sleep(1)
        except Exception as e:
            print(f"  [WARN] election article: {e}")
    else:
        data["last_election"] = elect_year

    # Fallback total_seats
    if not data.get("total_seats"):
        data["total_seats"] = TOTAL_SEATS_FALLBACK.get(state_name)

    # Computed fields
    if data.get("last_election"):
        data["next_election"]  = data["last_election"] + 5
        data["in_power_since"] = data["last_election"]

    return data


# ── DB update ─────────────────────────────────────────────────────────────────

def update_state_db(db, state_id, data):
    payload = {k: v for k, v in data.items() if v is not None}
    if payload:
        db.table("states").update(payload).eq("id", state_id).execute()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    from app.database import get_db
    db = get_db()

    states_raw = db.table("states").select("id,name").execute().data or []
    if not states_raw:
        sys.exit("ERROR: No states found in Supabase")

    states_by_name = {s["name"]: s["id"] for s in states_raw}

    if "--all" in sys.argv:
        targets = sorted(states_by_name.items())
        dry_run = False
        print(f"Full run — {len(targets)} states.\n")
    elif "--test3" in sys.argv:
        test_states = ["Bihar", "West Bengal", "Karnataka"]
        targets = [(n, states_by_name[n]) for n in test_states if n in states_by_name]
        dry_run = True
        print("TEST (no save) — Bihar, West Bengal, Karnataka.\n")
    else:
        targets = [("Telangana", states_by_name["Telangana"])]
        dry_run = True
        print("TEST (no save) — Telangana only. Use --test3 or --all to run more.\n")

    print(f"{'State':<22} {'Capital':<18} {'Pop (M)':<8} {'Seats':<7} {'Last':<6} {'Next':<6} {'Ruling Party':<22} Gov Seats")
    print("-" * 106)

    ok = err = 0

    with httpx.Client(headers={"User-Agent": WIKI_UA}, timeout=30, follow_redirects=True) as client:
        for state_name, state_id in targets:
            try:
                data = scrape_one_state(client, db, state_name, state_id)
                if not dry_run:
                    update_state_db(db, state_id, data)

                pop_m = f"{data['population'] / 1e6:.1f}" if data.get("population") else "?"
                print(
                    f"{state_name:<22} "
                    f"{str(data.get('capital') or '?'):<18} "
                    f"{pop_m:<8} "
                    f"{str(data.get('total_seats') or '?'):<7} "
                    f"{str(data.get('last_election') or '?'):<6} "
                    f"{str(data.get('next_election') or '?'):<6} "
                    f"{str(data.get('ruling_party') or '?'):<22} "
                    f"{data.get('party_seats') or '?'}"
                )
                ok += 1
            except Exception as e:
                print(f"{state_name:<22} ERROR: {e}")
                err += 1

            time.sleep(1)

    print(f"\n{'='*60}")
    if dry_run:
        print(f"DRY RUN — {ok} scraped, {err} errors. Nothing saved to DB.")
        print("Use --all to run and save all 30 states.")
    else:
        print(f"Saved: {ok}   Errors: {err}")


if __name__ == "__main__":
    main()
