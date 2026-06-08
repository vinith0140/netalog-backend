"""
verify_politicians.py
Verifies all 30 state Chief Ministers using two independent Tavily searches per state.

  Query 1: "Chief Minister of {state} India 2026"
  Query 2: "{state} Chief Minister name June 2026"

Consensus:
  Both queries match DB              → VERIFIED [OK]   — copy to verified_politicians
  Both queries agree, differ from DB → FIXED    [~]    — auto-fix DB + copy to verified_politicians
  Queries disagree                   → NEEDS MANUAL REVIEW — flag only, no change
"""

import os
import re
import sys
import time
import httpx
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "")

QUERIES = [
    "Chief Minister of {state} India 2026",
    "{state} Chief Minister name June 2026",
]

KNOWN_PARTIES = [
    "Tamilaga Vettri Kazhagam", "TVK", "BJP", "INC", "Congress", "AAP",
    "TMC", "Trinamool", "JD(U)", "JDU", "RJD", "SP", "BSP", "DMK",
    "AIADMK", "TDP", "YSR Congress", "BRS", "TRS", "Shiv Sena", "NCP",
    "JMM", "CPM", "CPI", "AGP", "NPP", "NDPP", "NPF", "MNF", "ZPM",
    "SDF", "SKM", "BPF", "PDF",
]


# ── Tavily ────────────────────────────────────────────────────────────────────

def tavily_search(query: str) -> str:
    if not TAVILY_KEY:
        return "NO KEY"
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_KEY,
                "query": query,
                "max_results": 5,
                "include_answer": True,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        answer = data.get("answer", "")
        if answer:
            return answer.strip()
        results = data.get("results", [])
        return results[0].get("content", "")[:200] if results else ""


def get_party_tavily(name: str, state: str) -> str:
    try:
        answer = tavily_search(f"{name} political party {state} India 2026")
        for party in KNOWN_PARTIES:
            if party.lower() in answer.lower():
                return party
        return answer.split(".")[0].strip()[:60] or "Unknown"
    except Exception:
        return "Unknown"


# ── Name helpers ──────────────────────────────────────────────────────────────

def extract_name(tavily_answer: str) -> str:
    """Pull a person's name out of a Tavily answer sentence."""
    # "X is/was/became/has been the Chief Minister..."
    m = re.match(
        r'^([A-Z][A-Za-z.\s\-]{2,40}?)\s+(?:is|was|has been|became|assumed)\s+(?:the\s+)?(?:current\s+)?(?:Chief\s+Minister|CM)\b',
        tavily_answer,
    )
    if m:
        return m.group(1).strip()
    # "...Chief Minister of X is Y..."
    m2 = re.search(
        r'(?:Chief\s+Minister|CM)\s+(?:of\s+[\w\s&]{2,25}\s+)?is\s+([A-Z][A-Za-z.\s\-]{2,40}?)(?:\s*[,.])',
        tavily_answer,
    )
    if m2:
        return m2.group(1).strip()
    # Fallback: leading capitalised words (likely a name)
    words = tavily_answer.split()
    name_words = []
    for w in words[:6]:
        clean = w.strip(".,;:()")
        if clean and clean[0].isupper():
            name_words.append(clean)
        else:
            break
    return " ".join(name_words) if name_words else tavily_answer[:40]


def names_match(reference: str, candidate: str) -> bool:
    """True if reference name is recognisably present in candidate string."""
    if not reference or not candidate:
        return False
    if candidate.startswith(("NO KEY", "ERR", "QUOTA", "NO CREDITS")):
        return False
    r, c = reference.lower(), candidate.lower()
    if r in c or c in r:
        return True
    words = [w for w in r.split() if len(w) > 3]
    if not words:
        return False
    return sum(1 for w in words if w in c) >= max(1, (len(words) + 1) // 2)


# ── DB helpers ────────────────────────────────────────────────────────────────

def search_politician(db, state_id: int, name: str):
    kws = [w.strip(".,()") for w in name.split() if len(w.strip(".,()")) > 3]
    best, best_hits = None, 0
    for kw in kws:
        rows = (
            db.table("politicians")
            .select("id,name,party,position")
            .eq("state_id", state_id)
            .ilike("name", f"%{kw}%")
            .execute()
            .data
        )
        for row in rows:
            hits = sum(1 for k in kws if k.lower() in row["name"].lower())
            if hits > best_hits:
                best_hits, best = hits, row
    return best if best_hits >= max(1, len(kws) // 2) else None


def copy_to_verified(db, politician_id: int) -> None:
    row = db.table("politicians").select("*").eq("id", politician_id).single().execute().data
    if not row:
        return
    row["verified_at"] = datetime.now(timezone.utc).isoformat()
    db.table("verified_politicians").upsert(row, on_conflict="id").execute()


def auto_fix(db, state_id: int, state_name: str, old_cm: dict | None, agreed_name: str) -> int | None:
    if old_cm:
        db.table("politicians").update({"position": "MLA"}).eq("id", old_cm["id"]).execute()

    found = search_politician(db, state_id, agreed_name)
    if found:
        db.table("politicians").update({"position": "Chief Minister"}).eq("id", found["id"]).execute()
        return found["id"]

    # Not in DB — fetch party then insert
    time.sleep(2)
    party = get_party_tavily(agreed_name, state_name)
    time.sleep(2)

    result = db.table("politicians").insert({
        "name":     agreed_name,
        "party":    party,
        "state_id": state_id,
        "position": "Chief Minister",
    }).execute()
    return result.data[0]["id"] if result.data else None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    from app.database import get_db
    db = get_db()

    states_raw = db.table("states").select("id,name").execute().data or []
    if not states_raw:
        sys.exit("ERROR: No states found in Supabase")
    states_sorted = sorted(states_raw, key=lambda s: s["name"])

    print(f"CM Verification  |  {len(states_sorted)} states  |  Tavily x2\n")

    cw = [24, 30, 34, 34, 22]
    print(
        f"{'State':<{cw[0]}} {'Scraped CM':<{cw[1]}} "
        f"{'Tavily Q1':<{cw[2]}} {'Tavily Q2':<{cw[3]}} Result"
    )
    print("-" * (sum(cw) + 4))

    verified = fixed = needs_review = 0

    def tr(s: str, n: int) -> str:
        return s[:n-1] + "~" if len(s) >= n else s

    for state in states_sorted:
        state_id   = state["id"]
        state_name = state["name"]

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

        # Two independent Tavily searches
        q_answers = []
        for q_template in QUERIES:
            try:
                ans = tavily_search(q_template.format(state=state_name))
            except Exception as exc:
                msg = str(exc)
                ans = "QUOTA" if ("429" in msg or "rate" in msg.lower()) else "ERR"
            q_answers.append(ans)
            time.sleep(2)

        q1, q2 = q_answers
        q1_match = names_match(scraped, q1)
        q2_match = names_match(scraped, q2)

        if q1_match and q2_match:
            # Both confirm what's in DB
            result = "VERIFIED [OK]"
            verified += 1
            if cm_record:
                try:
                    copy_to_verified(db, cm_record["id"])
                except Exception:
                    pass

        elif not q1_match and not q2_match and names_match(extract_name(q1), q2):
            # Both disagree with DB and agree with each other → auto-fix
            agreed_name = extract_name(q1)
            new_id = auto_fix(db, state_id, state_name, cm_record, agreed_name)
            if new_id:
                try:
                    copy_to_verified(db, new_id)
                except Exception:
                    pass
                result = "FIXED [~]"
                fixed += 1
            else:
                result = "NEEDS MANUAL REVIEW"
                needs_review += 1

        else:
            result = "NEEDS MANUAL REVIEW"
            needs_review += 1

        print(
            f"{state_name:<{cw[0]}} "
            f"{tr(scraped, cw[1]):<{cw[1]}} "
            f"{tr(q1,      cw[2]):<{cw[2]}} "
            f"{tr(q2,      cw[3]):<{cw[3]}} "
            f"{result}"
        )

    print()
    print("=" * 60)
    print(f"VERIFIED           : {verified}/30")
    print(f"FIXED              : {fixed}/30")
    print(f"NEEDS MANUAL REVIEW: {needs_review}/30")


if __name__ == "__main__":
    main()
