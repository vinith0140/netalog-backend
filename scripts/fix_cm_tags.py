"""
fix_cm_tags.py
Step 1: Reset all 'Chief Minister' positions -> 'Cabinet Minister'
Step 2: Run minister tagging with the fixed parser for each state
Step 3: Query DB and print which person is tagged as CM per state

Run Telangana first to validate, then all 30 states.
"""

import sys
import os
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.database import get_db
from app.state_config import STATE_PIPELINES
from app.pipeline import tag_ministers, sync_state_metadata

# ── Step 1: Reset all Chief Ministers -> Cabinet Minister ─────────────────────
print("=" * 60)
print("STEP 1: Reset all Chief Minister positions -> Cabinet Minister")
print("=" * 60)

db = get_db()
reset_result = (
    db.table("politicians")
    .update({"position": "Cabinet Minister"})
    .eq("position", "Chief Minister")
    .execute()
)
reset_count = len(reset_result.data or [])
print(f"Reset {reset_count} politicians: Chief Minister -> Cabinet Minister\n")

# ── Step 2: Test Telangana first ──────────────────────────────────────────────
print("=" * 60)
print("STEP 2: Test Telangana first (id=24)")
print("=" * 60)

tg = next(s for s in STATE_PIPELINES if s["state_id"] == 24)
try:
    matched, total = tag_ministers(tg["ministers_url"], 24)
    print(f"Telangana: {matched}/{total} matched")
except Exception as exc:
    print(f"Telangana ERROR: {exc}")

# Verify who is CM in Telangana
cm_check = (
    db.table("politicians")
    .select("name,constituency,party")
    .eq("state_id", 24)
    .eq("position", "Chief Minister")
    .execute()
)
if cm_check.data:
    cm = cm_check.data[0]
    print(f"Telangana CM in DB: {cm['name']} ({cm['constituency']}, {cm['party']})")
    if "revanth" in cm["name"].lower():
        print("[OK] Revanth Reddy correctly tagged as Chief Minister")
    else:
        print(f"[~] Expected Revanth Reddy but got: {cm['name']}")
else:
    print("[X] No Chief Minister found in DB for Telangana after tagging!")

print()

# ── Step 3: Run all 30 states ─────────────────────────────────────────────────
print("=" * 60)
print("STEP 3: Run minister tagging for all 30 states")
print("=" * 60)

states_with_url = [s for s in STATE_PIPELINES if s.get("ministers_url")]
cm_results: list[dict] = []

for i, state in enumerate(states_with_url):
    state_id   = state["state_id"]
    state_name = state["name"]
    url        = state["ministers_url"]

    # Skip Telangana — already done above
    if state_id == 24:
        # Query existing result
        res = (
            db.table("politicians")
            .select("name")
            .eq("state_id", 24)
            .eq("position", "Chief Minister")
            .execute()
        )
        cm_name = res.data[0]["name"] if res.data else None
        cm_results.append({"state_id": 24, "name": "Telangana", "cm": cm_name, "matched": matched, "total": total})
        continue

    print(f"[{i+1:2d}/{len(states_with_url)}] {state_name}...", end="", flush=True)
    t0 = time.time()

    try:
        m, t = tag_ministers(url, state_id)
        elapsed = time.time() - t0

        # Query who got CM
        cm_res = (
            db.table("politicians")
            .select("name")
            .eq("state_id", state_id)
            .eq("position", "Chief Minister")
            .execute()
        )
        cm_name = cm_res.data[0]["name"] if cm_res.data else None

        print(f" {m}/{t} matched | CM: {cm_name or 'NONE'} ({elapsed:.1f}s)")
        cm_results.append({"state_id": state_id, "name": state_name, "cm": cm_name, "matched": m, "total": t})
    except Exception as exc:
        elapsed = time.time() - t0
        print(f" ERROR ({elapsed:.1f}s): {exc}")
        cm_results.append({"state_id": state_id, "name": state_name, "cm": None, "error": str(exc)})

    time.sleep(0.5)

# ── Final CM list ─────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("CHIEF MINISTERS DETECTED IN DB")
print("=" * 60)

found     = [r for r in cm_results if r.get("cm")]
not_found = [r for r in cm_results if not r.get("cm")]

for r in sorted(cm_results, key=lambda x: x["state_id"]):
    cm = r.get("cm") or "NOT FOUND"
    err = f" [ERROR: {r['error'][:40]}]" if "error" in r else ""
    print(f"  [{r['state_id']:2d}] {r['name']:<25} {cm}{err}")

print()
print(f"CMs tagged   : {len(found)}/{len(cm_results)}")
print(f"States missed: {len(not_found)}")
if not_found:
    print(f"  Missing: {', '.join(r['name'] for r in not_found)}")

# ── Sync states table metadata for all processed states ───────────────────────
print()
print("=" * 60)
print("STEP 4: Sync states table (ruling_party / last_election / next_election)")
print("=" * 60)

for state in STATE_PIPELINES:
    try:
        updated = sync_state_metadata(state["state_id"], state["myneta_slug"])
        if updated:
            print(f"  [{state['state_id']:2d}] {state['name']:<25} → {updated}")
    except Exception as exc:
        print(f"  [{state['state_id']:2d}] {state['name']:<25} ERROR: {exc}")
