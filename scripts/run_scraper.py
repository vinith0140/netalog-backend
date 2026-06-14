import re
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.scraper import scrape_telangana_politicians, _get
from app.database import get_db


def tag_winners_and_ministers():
    TELANGANA_STATE_ID = 24
    BASE = "https://myneta.info/telangana2023"
    db = get_db()

    # ── Step 0: reset any previously wrong minister tags back to MLA ──────────
    # We only reset Cabinet/Chief Minister — MLAs tagged earlier stay as-is
    print("Resetting stale minister tags...")
    db.table("politicians").update({"position": "MLA"}).eq("position", "Cabinet Minister").eq("state_id", TELANGANA_STATE_ID).execute()
    db.table("politicians").update({"position": "MLA"}).eq("position", "Chief Minister").eq("state_id", TELANGANA_STATE_ID).execute()

    # ── Step 1: collect all winner names across paginated pages ──────────────
    print("Fetching winners from MyNeta (6 pages)...")
    winner_names: list[str] = []

    for page in range(1, 8):
        url = (
            f"{BASE}/index.php?action=summary"
            f"&subAction=winner_analyzed&sort=candidate&page={page}"
        )
        try:
            soup = _get(url)
        except Exception as exc:
            print(f"  Page {page} error: {exc}")
            break

        names_this_page: list[str] = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            hdrs = [c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"])]
            if "candidate" not in " ".join(hdrs):
                continue
            name_idx = next((j for j, h in enumerate(hdrs) if "candidate" in h), None)
            if name_idx is None:
                continue
            for row in rows[1:]:
                cells = row.find_all("td")
                if not cells or name_idx >= len(cells):
                    continue
                a_tag = cells[name_idx].find("a")
                raw = a_tag.get_text(strip=True) if a_tag else cells[name_idx].get_text(strip=True)
                name = raw.replace("Winner", "").strip()
                if name:
                    names_this_page.append(name)
            break

        if not names_this_page:
            break
        winner_names.extend(names_this_page)
        print(f"  Page {page}: {len(names_this_page)} winners")
        time.sleep(0.2)

    print(f"Total winners collected: {len(winner_names)}")

    # ── Step 2: bulk-update position = "MLA" ─────────────────────────────────
    mla_tagged = 0
    if winner_names:
        result = (
            db.table("politicians")
            .update({"position": "MLA"})
            .in_("name", winner_names)
            .eq("state_id", TELANGANA_STATE_ID)
            .execute()
        )
        mla_tagged = len(result.data or [])

    print(f"Tagged {mla_tagged} politicians as MLA\n")

    # ── Step 3: scrape ministers ──────────────────────────────────────────────
    print("Fetching Council of Ministers from telangana.gov.in...")
    try:
        soup = _get("https://telangana.gov.in/government/council-of-ministers/")
    except Exception as exc:
        print(f"  Ministers page error: {exc}")
        return mla_tagged, 0, 0

    HONORIFICS = re.compile(r"^(Sri|Smt\.|Dr\.|Shri|Shrimati)\s+", re.IGNORECASE)
    # Strip reservation suffixes like "(SC)" or "(ST)" from constituency names
    RESERVATION = re.compile(r"\s*[\(\[]?\s*(SC|ST|OBC)\s*[\)\]]?\s*$", re.IGNORECASE)

    ministers: list[dict] = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 5:
            continue
        hdrs = [c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"])]
        if "name" not in " ".join(hdrs):
            continue
        name_idx  = next((j for j, h in enumerate(hdrs) if "name"         in h), None)
        port_idx  = next((j for j, h in enumerate(hdrs) if "portfolio"    in h), None)
        const_idx = next((j for j, h in enumerate(hdrs) if "constituency" in h), None)
        if name_idx is None:
            continue
        for i, row in enumerate(rows[1:]):
            cells = row.find_all(["td", "th"])
            if not cells or name_idx >= len(cells):
                continue
            raw_name     = cells[name_idx].get_text(strip=True)
            clean_name   = HONORIFICS.sub("", raw_name).strip()
            portfolio    = cells[port_idx].get_text(strip=True)    if port_idx  and port_idx  < len(cells) else ""
            constituency = cells[const_idx].get_text(strip=True)   if const_idx and const_idx < len(cells) else ""
            const_clean  = RESERVATION.sub("", constituency).strip()
            position     = "Chief Minister" if i == 0 else "Cabinet Minister"
            ministers.append({
                "name":         clean_name,
                "position":     position,
                "portfolio":    portfolio,
                "constituency": constituency,
                "const_clean":  const_clean,
            })
        break

    print(f"Found {len(ministers)} ministers")

    # ── Step 4: match each minister and update position ───────────────────────
    # Strategy:
    #   1. Exact ilike on full cleaned name
    #   2. Constituency + MLA (one winner per constituency — avoids surname collisions)
    #   3. Last-word-only match as final fallback for short/unique names
    matched = 0
    unmatched: list[str] = []

    for m in ministers:
        position     = m["position"]
        clean_name   = m["name"]
        const_clean  = m["const_clean"]

        # ── try 1: exact name match ──────────────────────────────────────────
        res = (
            db.table("politicians")
            .update({"position": position})
            .ilike("name", clean_name)
            .eq("state_id", TELANGANA_STATE_ID)
            .execute()
        )
        if res.data:
            matched += 1
            print(f"  [OK] {position}: {res.data[0]['name']}")
            continue

        # ── try 2: constituency + MLA (precise — one winner per seat) ────────
        if const_clean:
            res2 = (
                db.table("politicians")
                .update({"position": position})
                .ilike("constituency", f"%{const_clean}%")
                .eq("state_id", TELANGANA_STATE_ID)
                .eq("position", "MLA")
                .execute()
            )
            if res2.data:
                matched += 1
                print(f"  [~] {position}: {m['name']} -> matched '{res2.data[0]['name']}' (via constituency '{const_clean}')")
                continue

        # ── try 3: constituency + first word of name (catches spelling diffs) ─
        first_word = clean_name.split()[0].lstrip("D.").strip() if clean_name else ""
        if const_clean and first_word and len(first_word) > 3:
            res3 = (
                db.table("politicians")
                .update({"position": position})
                .ilike("constituency", f"%{const_clean}%")
                .eq("state_id", TELANGANA_STATE_ID)
                .ilike("name", f"%{first_word}%")
                .execute()
            )
            if res3.data and len(res3.data) == 1:
                matched += 1
                db.table("politicians").update({"position": "MLA"}).eq("id", res3.data[0]["id"]).execute()
                print(f"  [~] {position}: {m['name']} -> matched '{res3.data[0]['name']}' (constituency+firstname)")
                continue

        # ── try 4: distinctive middle part of name (for no-constituency cases) ─
        parts = clean_name.split()
        if len(parts) >= 2:
            middle = parts[1] if len(parts) > 2 else parts[-1]
            if len(middle) > 5:
                res4 = (
                    db.table("politicians")
                    .update({"position": position})
                    .ilike("name", f"%{middle}%")
                    .eq("state_id", TELANGANA_STATE_ID)
                    .execute()
                )
                if res4.data and len(res4.data) == 1:
                    matched += 1
                    print(f"  [~] {position}: {m['name']} -> matched '{res4.data[0]['name']}' (middle-name fallback)")
                    continue

        unmatched.append(m["name"])
        print(f"  [X] No match: {m['name']}  (constituency: {m['constituency']})")

    print(f"\n{'='*50}")
    print(f"MLAs tagged:           {mla_tagged}")
    print(f"Ministers found:       {len(ministers)}")
    print(f"Ministers matched:     {matched}")
    if unmatched:
        print(f"Ministers unmatched:   {len(unmatched)}")
        for u in unmatched:
            print(f"  - {u}")

    return mla_tagged, len(ministers), matched


if __name__ == "__main__":
    tag_winners_and_ministers()
