"""
run_tag_ministers.py
Standalone minister-tagging pipeline — does NOT re-scrape candidates or re-tag MLAs.

For each state with ministers_url configured:
  1. Resets existing Chief Minister / Cabinet Minister positions back to MLA
  2. Scrapes the ministers page and matches names to DB records
  3. Logs results to minister_results.log

Usage:
    python run_tag_ministers.py
    python run_tag_ministers.py 5   # start from state index 5 (0-based)
"""

import sys
import os
import json
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from app.state_config import STATE_PIPELINES
from app.pipeline import reset_minister_positions, tag_ministers

LOG_FILE = os.path.join(os.path.dirname(__file__), "minister_results.log")

start_idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0

states_with_url = [s for s in STATE_PIPELINES if s.get("ministers_url")]
print(f"States with ministers_url: {len(states_with_url)}/30")

results = []

with open(LOG_FILE, "a", encoding="utf-8") as log:
    log.write(f"\n{'='*70}\n")
    log.write(f"Run started: {datetime.now().isoformat()}\n")
    log.write(f"{'='*70}\n")

    for i, state in enumerate(states_with_url[start_idx:], start=start_idx):
        state_id   = state["state_id"]
        state_name = state["name"]
        url        = state["ministers_url"]

        header = f"[{i+1:2d}/{len(states_with_url)}] {state_name} (id={state_id})"
        print(f"\n{'='*60}")
        print(header)
        print(f"  URL: {url}")
        print("="*60)

        t0 = time.time()
        rec = {"state_id": state_id, "name": state_name, "url": url}

        # Reset stale minister positions first
        try:
            reset_count = reset_minister_positions(state_id)
            if reset_count:
                print(f"  Reset {reset_count} stale minister positions -> MLA")
        except Exception as exc:
            print(f"  WARN: reset failed: {exc}")

        # Tag ministers
        try:
            matched, total = tag_ministers(url, state_id)
            rec["matched"] = matched
            rec["total"]   = total
            pct = f"{matched}/{total}" if total else "0/0"
            print(f"  Ministers: {pct} matched")
        except Exception as exc:
            rec["error"] = str(exc)
            print(f"  ERROR: {exc}")

        elapsed = time.time() - t0
        rec["elapsed_s"] = round(elapsed, 1)
        results.append(rec)
        print(f"  Time: {elapsed:.1f}s")

        log.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log.flush()

        time.sleep(0.5)

# ── Final summary ─────────────────────────────────────────────────────────────
print(f"\n\n{'='*60}")
print("FINAL SUMMARY")
print("="*60)

ok     = [r for r in results if "error" not in r]
failed = [r for r in results if "error" in r]
total_matched = sum(r.get("matched", 0) for r in ok)
total_found   = sum(r.get("total",   0) for r in ok)

print(f"States succeeded : {len(ok)}/{len(results)}")
print(f"Ministers found  : {total_found}")
print(f"Ministers matched: {total_matched}")
print()
print("Per-state results:")
for r in results:
    if "error" in r:
        print(f"  [{r['state_id']:2d}] {r['name']:<22} ERROR: {r['error'][:60]}")
    else:
        pct = f"{r.get('matched',0)}/{r.get('total',0)}"
        print(f"  [{r['state_id']:2d}] {r['name']:<22} {pct}")

if failed:
    print(f"\nFailed ({len(failed)}):")
    for r in failed:
        print(f"  [{r['state_id']:2d}] {r['name']} -- {r['error']}")
