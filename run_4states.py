"""
run_4states.py
Run MLA + CM verification for Assam, Kerala, Tamil Nadu, West Bengal only.
Copies all verified records to verified_politicians.
"""

import sys
import time
import httpx
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, ".")

TARGET = ["Assam", "Kerala", "Tamil Nadu", "West Bengal"]

# ── Imports from verify_mlas.py ───────────────────────────────────────────────
from verify_mlas import (
    STATE_ELECTIONS, WIKI_UA,
    wiki_sections, wiki_section_html, find_constituency_section,
    parse_winners, names_match as mla_names_match, const_match,
    bulk_copy_to_verified,
)

# ── Imports from verify_politicians.py ────────────────────────────────────────
from verify_politicians import (
    tavily_search, perplexity_search, gemini_search,
    names_match as cm_names_match, extract_name, is_name_variant,
    majority_agreed_name, copy_to_verified, auto_fix,
)


# ── Step 1: MLA verification ──────────────────────────────────────────────────

def run_mla_verification(db, states_by_name):
    print("\n" + "="*60)
    print("STEP 1 — MLA Verification (Wikipedia election results)")
    print("="*60)
    print(f"{'State':<22} {'Winners':>8} {'Verified':>9} {'Flagged':>8}")
    print("-"*52)

    grand_verified = grand_flagged = 0

    with httpx.Client(headers={"User-Agent": WIKI_UA}, timeout=30, follow_redirects=True) as client:
        for state_name in TARGET:
            state_id = states_by_name.get(state_name)
            article  = STATE_ELECTIONS.get(state_name)
            if not state_id or not article:
                print(f"{state_name:<22} skipped (no config)")
                continue

            try:
                sections    = wiki_sections(client, article)
                section_idx = find_constituency_section(sections)
                if section_idx is None:
                    print(f"{state_name:<22} no constituency section")
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
                print(f"{state_name:<22} no winners parsed")
                continue

            db_pols = (
                db.table("politicians")
                .select("*")
                .eq("state_id", state_id)
                .in_("position", ["MLA", "Cabinet Minister", "Chief Minister"])
                .execute()
                .data
            ) or []

            verified_rows, flagged = [], []

            for w in winners:
                wiki_name  = w["winner"]
                wiki_const = w["constituency"]
                matched = None
                for pol in db_pols:
                    if mla_names_match(wiki_name, pol["name"]) and const_match(wiki_const, pol.get("constituency") or ""):
                        matched = pol; break
                if matched is None:
                    for pol in db_pols:
                        if mla_names_match(wiki_name, pol["name"]):
                            matched = pol; break
                if matched:
                    verified_rows.append(matched)
                else:
                    flagged.append(f"{wiki_name} ({wiki_const})")

            # Deduplicate by id
            seen = {}
            for r in verified_rows:
                seen[r["id"]] = r
            verified_rows = list(seen.values())

            bulk_copy_to_verified(db, verified_rows)
            grand_verified += len(verified_rows)
            grand_flagged  += len(flagged)

            print(f"{state_name:<22} {len(winners):>8} {len(verified_rows):>9} {len(flagged):>8}")
            for f in flagged[:3]:
                print(f"  flagged: {f}")
            if len(flagged) > 3:
                print(f"  ... and {len(flagged)-3} more")

            time.sleep(1)

    print(f"\nMLA total verified : {grand_verified}")
    print(f"MLA total flagged  : {grand_flagged}")
    return grand_verified, grand_flagged


# ── Step 2: CM verification ───────────────────────────────────────────────────

def run_cm_verification(db, states_by_name):
    print("\n" + "="*60)
    print("STEP 2 — CM Verification (Tavily + Perplexity + Gemini)")
    print("="*60)
    print(f"{'State':<22} {'DB name':<26} Votes   Result")
    print("-"*80)

    verified = fixed = needs_review = 0

    def tr(s, n=26):
        s = s.replace("\n", " ").replace("**", "").strip()
        return s[:n-1] + "~" if len(s) >= n else s

    for state_name in TARGET:
        state_id = states_by_name.get(state_name)
        if not state_id:
            print(f"{state_name:<22} skipped (not in DB)")
            continue

        cm_record = None
        try:
            res = (
                db.table("politicians")
                .select("*")
                .eq("state_id", state_id)
                .eq("position", "Chief Minister")
                .limit(1)
                .execute()
            )
            cm_record = res.data[0] if res.data else None
            scraped   = cm_record["name"] if cm_record else "(no CM)"
        except Exception:
            scraped = "(db err)"

        tv = tavily_search(state_name);     time.sleep(1)
        pp = perplexity_search(state_name); time.sleep(1)
        gm = gemini_search(state_name);     time.sleep(1)

        tv_match = cm_names_match(scraped, tv)
        pp_match = cm_names_match(scraped, pp)
        gm_match = cm_names_match(scraped, gm)
        match_count = sum([tv_match, pp_match, gm_match])
        votes = f"T={'Y' if tv_match else 'N'} P={'Y' if pp_match else 'N'} G={'Y' if gm_match else 'N'}"

        if match_count >= 2:
            result = "VERIFIED [OK]"
            verified += 1
            if cm_record:
                try:
                    copy_to_verified(db, cm_record["id"])
                except Exception:
                    pass
        else:
            non_matching = [a for a, m in zip([tv, pp, gm], [tv_match, pp_match, gm_match]) if not m]
            agreed_name  = majority_agreed_name(non_matching)
            if agreed_name and len(agreed_name) >= 3:
                if cm_record and is_name_variant(agreed_name, scraped):
                    try:
                        db.table("politicians").update({"name": agreed_name}).eq("id", cm_record["id"]).execute()
                        copy_to_verified(db, cm_record["id"])
                    except Exception:
                        pass
                    result = "VERIFIED [OK]"
                    verified += 1
                else:
                    new_id = auto_fix(db, state_id, state_name, cm_record, agreed_name)
                    if new_id:
                        try:
                            copy_to_verified(db, new_id)
                        except Exception:
                            pass
                        result = f"FIXED [~] -> {tr(agreed_name, 24)}"
                        fixed += 1
                    else:
                        result = "NEEDS MANUAL REVIEW"
                        needs_review += 1
            else:
                result = "NEEDS MANUAL REVIEW"
                needs_review += 1

        print(f"{state_name:<22} {tr(scraped):<26} {votes}  {result}")

    print(f"\nCM verified : {verified}/4")
    print(f"CM fixed    : {fixed}/4")
    print(f"CM review   : {needs_review}/4")
    return verified, fixed, needs_review


# ── Step 3: Summary ───────────────────────────────────────────────────────────

def print_summary(db, states_by_name, mla_verified, mla_flagged, cm_verified, cm_fixed):
    print("\n" + "="*60)
    print("SUMMARY — verified_politicians counts per state")
    print("="*60)
    print(f"{'State':<22} {'Total in VP':>12} {'MLAs':>8} {'CM/Cabinet':>10}")
    print("-"*56)

    for state_name in TARGET:
        state_id = states_by_name.get(state_name)
        if not state_id:
            continue
        all_vp = (
            db.table("verified_politicians")
            .select("position")
            .eq("state_id", state_id)
            .execute()
            .data
        ) or []
        total  = len(all_vp)
        mlas   = sum(1 for r in all_vp if r["position"] == "MLA")
        gov    = sum(1 for r in all_vp if r["position"] in ("Chief Minister", "Cabinet Minister"))
        print(f"{state_name:<22} {total:>12} {mlas:>8} {gov:>10}")

    print(f"\nMLA  verified : {mla_verified}  flagged : {mla_flagged}")
    print(f"CM   verified : {cm_verified}   fixed   : {cm_fixed}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    from app.database import get_db
    db = get_db()

    states_raw     = db.table("states").select("id,name").execute().data or []
    states_by_name = {s["name"]: s["id"] for s in states_raw}

    mla_v, mla_f          = run_mla_verification(db, states_by_name)
    cm_v, cm_fx, cm_rev   = run_cm_verification(db, states_by_name)
    print_summary(db, states_by_name, mla_v, mla_f, cm_v, cm_fx)


if __name__ == "__main__":
    main()
