"""
Full pipeline runner — all 30 states.
Scrapes candidates, tags MLA winners, tags ministers where a URL is configured.
Logs per-state results to pipeline_results.log and prints a live summary.

Usage:
    python run_all_states.py
    python run_all_states.py 5        # start from state index 5 (0-based)
"""

import sys
import os
import json
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from app.state_config import STATE_PIPELINES, FALLBACK_SLUGS
from app.pipeline import run_state_pipeline

LOG_FILE = os.path.join(os.path.dirname(__file__), "pipeline_results.log")

start_idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0

all_results: list[dict] = []

with open(LOG_FILE, "a", encoding="utf-8") as log:
    log.write(f"\n{'='*70}\n")
    log.write(f"Run started: {datetime.now().isoformat()}\n")
    log.write(f"States: {start_idx} → {len(STATE_PIPELINES)-1}\n")
    log.write(f"{'='*70}\n")

    for i, state in enumerate(STATE_PIPELINES[start_idx:], start=start_idx):
        header = f"[{i+1:2d}/{len(STATE_PIPELINES)}] {state['name']} (state_id={state['state_id']})"
        print(f"\n{'='*60}")
        print(header)
        print("="*60)

        t0 = time.time()
        result = run_state_pipeline(state, fallback_slugs=FALLBACK_SLUGS.get(state["state_id"]))
        elapsed = time.time() - t0

        result["elapsed_s"] = round(elapsed, 1)
        all_results.append(result)

        # ── per-state console summary ──────────────────────────────────────
        if "candidates_error" in result:
            print(f"  ERROR (candidates): {result['candidates_error']}")
        else:
            print(f"  Candidates : {result.get('candidates_saved', 0):4d} saved, "
                  f"{result.get('candidates_skipped', 0):4d} skipped")
            print(f"  MLAs tagged: {result.get('mla_tagged', 0):4d}  "
                  f"{'(error: '+result['mla_error']+')' if 'mla_error' in result else ''}")
            if "ministers_total" in result:
                print(f"  Ministers  : {result['ministers_matched']}/{result['ministers_total']} matched")
            elif "ministers_error" in result:
                print(f"  Ministers  : ERROR — {result['ministers_error']}")
        print(f"  Time: {elapsed:.1f}s")

        # ── log to file ────────────────────────────────────────────────────
        log.write(json.dumps(result, ensure_ascii=False) + "\n")
        log.flush()

# ── final summary ──────────────────────────────────────────────────────────────
print(f"\n\n{'='*60}")
print("FINAL SUMMARY")
print("="*60)

ok       = [r for r in all_results if "candidates_error" not in r]
failed   = [r for r in all_results if "candidates_error" in r]

total_saved     = sum(r.get("candidates_saved",   0) for r in ok)
total_skipped   = sum(r.get("candidates_skipped", 0) for r in ok)
total_mla       = sum(r.get("mla_tagged",         0) for r in ok)
total_ministers = sum(r.get("ministers_matched",  0) for r in ok)

print(f"States succeeded : {len(ok)}/{len(all_results)}")
print(f"Candidates saved : {total_saved:,}")
print(f"Candidates skip  : {total_skipped:,} (already existed)")
print(f"MLAs tagged      : {total_mla:,}")
print(f"Ministers tagged : {total_ministers}")

if failed:
    print(f"\nFailed states ({len(failed)}):")
    for r in failed:
        print(f"  [{r['state_id']:2d}] {r['name']} — {r.get('candidates_error','?')}")

print(f"\nFull log: {LOG_FILE}")
