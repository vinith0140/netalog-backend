"""
verify_politicians.py
Verifies all 30 state Chief Ministers using 3 independent API sources.

  Sources:
    1. Tavily search
    2. Perplexity sonar-pro
    3. Gemini 2.5 Flash with Google Search grounding

Consensus (2/3 majority):
  2+ match DB              -> VERIFIED [OK]  — copy to verified_politicians
  2+ agree, differ from DB -> FIXED    [~]   — auto-fix DB + copy
  No 2/3 consensus         -> NEEDS MANUAL REVIEW
"""

import os
import re
import sys
import time
import httpx
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

TAVILY_KEY  = os.environ.get("TAVILY_API_KEY", "")
PPLX_KEY    = os.environ.get("PERPLEXITY_API_KEY", "")
GEMINI_KEY  = os.environ.get("GEMINI_API_KEY", "")

KNOWN_PARTIES = [
    "Tamilaga Vettri Kazhagam", "TVK", "BJP", "INC", "Congress", "AAP",
    "TMC", "Trinamool", "JD(U)", "JDU", "RJD", "SP", "BSP", "DMK",
    "AIADMK", "TDP", "YSR Congress", "BRS", "TRS", "Shiv Sena", "NCP",
    "JMM", "CPM", "CPI", "AGP", "NPP", "NDPP", "NPF", "MNF", "ZPM",
    "SDF", "SKM", "BPF", "PDF",
]


# ── API callers ───────────────────────────────────────────────────────────────

def tavily_search(state: str) -> str:
    if not TAVILY_KEY:
        return "NO KEY"
    try:
        r = httpx.post("https://api.tavily.com/search", json={
            "api_key": TAVILY_KEY,
            "query": f"Who is the current Chief Minister of {state} India 2026",
            "max_results": 3,
            "include_answer": True,
        }, timeout=30)
        r.raise_for_status()
        d = r.json()
        return (d.get("answer") or "").strip()[:300] or "no answer"
    except Exception as e:
        msg = str(e)
        return "QUOTA" if ("429" in msg or "rate" in msg.lower()) else f"ERR:{msg[:40]}"


def perplexity_search(state: str) -> str:
    if not PPLX_KEY:
        return "NO KEY"
    try:
        r = httpx.post("https://api.perplexity.ai/chat/completions", json={
            "model": "sonar-pro",
            "messages": [
                {"role": "system", "content": "Answer in one sentence with the person's full name only."},
                {"role": "user",   "content": f"Who is the current Chief Minister of {state}, India as of 2026?"},
            ],
            "max_tokens": 120,
        }, headers={"Authorization": f"Bearer {PPLX_KEY}"}, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()[:300]
    except Exception as e:
        msg = str(e)
        return "QUOTA" if ("429" in msg or "rate" in msg.lower()) else f"ERR:{msg[:40]}"


def gemini_search(state: str) -> str:
    if not GEMINI_KEY:
        return "NO KEY"
    try:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.5-flash:generateContent?key={GEMINI_KEY}"
        )
        r = httpx.post(url, json={
            "contents": [{"parts": [{"text": (
                f"Who is the current Chief Minister of {state}, India as of 2026? "
                "Answer in one sentence with the person's full name."
            )}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"maxOutputTokens": 150},
        }, timeout=30)
        r.raise_for_status()
        parts = r.json()["candidates"][0]["content"]["parts"]
        return " ".join(p.get("text", "") for p in parts).strip()[:300]
    except Exception as e:
        msg = str(e)
        return "QUOTA" if ("429" in msg or "rate" in msg.lower()) else f"ERR:{msg[:40]}"


def get_party_tavily(name: str, state: str) -> str:
    try:
        r = httpx.post("https://api.tavily.com/search", json={
            "api_key": TAVILY_KEY,
            "query": f"{name} political party {state} India 2026",
            "max_results": 3,
            "include_answer": True,
        }, timeout=30)
        r.raise_for_status()
        answer = (r.json().get("answer") or "").strip()
        for party in KNOWN_PARTIES:
            if party.lower() in answer.lower():
                return party
        return answer.split(".")[0].strip()[:60] or "Unknown"
    except Exception:
        return "Unknown"


# ── Name helpers ──────────────────────────────────────────────────────────────

def extract_name(answer: str) -> str:
    """Pull a person's name from an API answer sentence."""
    # "X is/was/became/has been the Chief Minister..."
    m = re.match(
        r'^([A-Z][A-Za-z.\s\-]{2,40}?)\s+(?:is|was|has been|became|assumed)\s+'
        r'(?:the\s+)?(?:current\s+)?(?:Chief\s+Minister|CM)\b',
        answer,
    )
    if m:
        return m.group(1).strip()
    # "...Chief Minister of X is Y..."
    m2 = re.search(
        r'(?:Chief\s+Minister|CM)\s+(?:of\s+[\w\s&]{2,25}\s+)?is\s+'
        r'([A-Z][A-Za-z.\s\-]{2,40}?)(?:\s*[,.])',
        answer,
    )
    if m2:
        return m2.group(1).strip()
    # Fallback: leading capitalised words
    words = answer.split()
    name_words = []
    for w in words[:6]:
        clean = w.strip(".,;:()**")
        if clean and clean[0].isupper():
            name_words.append(clean)
        else:
            break
    return " ".join(name_words) if name_words else answer[:40]


def names_match(reference: str, candidate: str) -> bool:
    if not reference or not candidate:
        return False
    if any(candidate.startswith(p) for p in ("NO KEY", "ERR", "QUOTA", "no answer")):
        return False
    r, c = reference.lower(), candidate.lower()
    if r in c or c in r:
        return True
    words = [w for w in r.split() if len(w) > 3]
    if not words:
        return False
    return sum(1 for w in words if w in c) >= max(1, (len(words) + 1) // 2)


def is_name_variant(api_name: str, db_name: str) -> bool:
    tw = [w.lower().strip(".,()") for w in api_name.split() if len(w.strip(".,()")) > 3]
    dw = [w.lower().strip(".,()") for w in db_name.split()  if len(w.strip(".,()")) > 3]
    for a in tw:
        for b in dw:
            if a == b or a.startswith(b) or b.startswith(a):
                return True
    return False


def majority_agreed_name(answers: list) -> str | None:
    """Return a name agreed upon by 2+ of the given answers, or None."""
    names = [extract_name(a) for a in answers]
    for i, j in [(0, 1), (0, 2), (1, 2)]:
        if names[i] and names[j] and names_match(names[i], names[j]):
            return names[i] if len(names[i]) >= len(names[j]) else names[j]
    return None


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


def auto_fix(db, state_id: int, state_name: str, old_cm, agreed_name: str):
    if old_cm:
        db.table("politicians").update({"position": "MLA"}).eq("id", old_cm["id"]).execute()

    found = search_politician(db, state_id, agreed_name)
    if found:
        db.table("politicians").update({"position": "Chief Minister"}).eq("id", found["id"]).execute()
        return found["id"]

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

    print("CM Verification  |  30 states  |  Tavily + Perplexity sonar-pro + Gemini 2.5 Flash  |  2/3 majority\n")
    print(f"{'State':<22} {'DB name':<28} Votes   Result")
    print("-" * 90)

    verified = fixed = needs_review = 0

    def tr(s: str, n: int = 28) -> str:
        s = s.replace("\n", " ").replace("**", "").strip()
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

        tv = tavily_search(state_name);     time.sleep(1)
        pp = perplexity_search(state_name); time.sleep(1)
        gm = gemini_search(state_name);     time.sleep(1)

        tv_match = names_match(scraped, tv)
        pp_match = names_match(scraped, pp)
        gm_match = names_match(scraped, gm)
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
            # Check if 2+ non-matching answers agree on a different name
            non_matching = [a for a, m in zip([tv, pp, gm], [tv_match, pp_match, gm_match]) if not m]
            agreed_name = majority_agreed_name(non_matching)

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
                        result = f"FIXED [~] -> {tr(agreed_name, 30)}"
                        fixed += 1
                    else:
                        result = "NEEDS MANUAL REVIEW"
                        needs_review += 1
            else:
                result = "NEEDS MANUAL REVIEW"
                needs_review += 1

        print(f"{state_name:<22} {tr(scraped):<28} {votes}  {result}")

    print()
    print("=" * 70)
    print(f"VERIFIED           : {verified}/30")
    print(f"FIXED              : {fixed}/30")
    print(f"NEEDS MANUAL REVIEW: {needs_review}/30")


if __name__ == "__main__":
    main()
