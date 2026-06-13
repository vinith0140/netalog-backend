"""
pipeline.py
Full NetaLog automated pipeline:

  Step 1 — run_scraper.py : scrape all 30 states from MyNeta
  Step 2 — fix_cm_tags.py : tag CMs / Cabinet Ministers / MLAs from Wikipedia ministry pages
  Step 3 — verify_mlas.py : verify MLAs + CMs from Wikipedia election results,
                            copy to verified_politicians

Prints a summary with timing and counts at the end.
"""

import re
import subprocess
import sys
import time
from datetime import datetime

STEPS = [
    ("Scrape MyNeta (all 30 states)",        "run_scraper.py"),
    ("Tag CMs / Cabinet Ministers / MLAs",   "fix_cm_tags.py"),
    ("Verify MLAs — Wikipedia election results", "verify_mlas.py"),
]

# Patterns to extract counts from each script's stdout
COUNT_PATTERNS = {
    "run_scraper.py": [
        (r"Total politicians scraped[^\d]*(\d+)", "scraped"),
        (r"(\d+)\s+politicians?\s+inserted",       "scraped"),
    ],
    "fix_cm_tags.py": [
        (r"Chief\s+Ministers?\s+tagged[^\d]*(\d+)", "cms_tagged"),
    ],
    "verify_mlas.py": [
        (r"Total verified\s*:\s*(\d+)", "mla_verified"),
        (r"Total flagged\s*:\s*(\d+)",  "mla_flagged"),
    ],
}


def run_step(label: str, script: str) -> tuple[int, str]:
    """Run a script, stream output, return (exit_code, stdout)."""
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"  python {script}")
    print(f"{'='*70}")

    t0 = time.time()
    output_lines = []

    proc = subprocess.Popen(
        [sys.executable, script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    for line in proc.stdout:
        print(line, end="", flush=True)
        output_lines.append(line)

    proc.wait()
    elapsed = time.time() - t0
    status = "OK" if proc.returncode == 0 else f"FAILED (exit {proc.returncode})"
    print(f"\n  [{status}]  {elapsed:.1f}s")

    return proc.returncode, "".join(output_lines)


def extract_counts(script: str, output: str) -> dict:
    counts = {}
    for pattern, key in COUNT_PATTERNS.get(script, []):
        m = re.search(pattern, output, re.IGNORECASE)
        if m:
            counts[key] = int(m.group(1))
    return counts


def main():
    pipeline_start = time.time()
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\nNetaLog Pipeline  |  started {started_at}")
    print("=" * 70)

    results = []
    all_counts = {}
    failed = False

    for label, script in STEPS:
        step_start = time.time()
        rc, output = run_step(label, script)
        elapsed = time.time() - step_start

        counts = extract_counts(script, output)
        all_counts.update(counts)

        results.append({
            "label":   label,
            "script":  script,
            "rc":      rc,
            "elapsed": elapsed,
            "counts":  counts,
        })

        if rc != 0:
            print(f"\n[PIPELINE ABORT]  {script} failed with exit code {rc}")
            failed = True
            break

    total_elapsed = time.time() - pipeline_start

    print(f"\n\n{'='*70}")
    print(f"PIPELINE SUMMARY  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")
    for r in results:
        status = "OK" if r["rc"] == 0 else "FAILED"
        print(f"  [{status:<6}]  {r['elapsed']:6.1f}s   {r['label']}")

    print(f"\n  Total time : {total_elapsed:.1f}s  ({total_elapsed/60:.1f} min)")
    print()

    if "scraped" in all_counts:
        print(f"  Politicians scraped  : {all_counts['scraped']}")
    if "mla_verified" in all_counts:
        print(f"  Verified to DB       : {all_counts['mla_verified']}")
    if "mla_flagged" in all_counts:
        print(f"  Flagged (no match)   : {all_counts['mla_flagged']}")

    print()
    if failed:
        print("  STATUS: PIPELINE FAILED")
        sys.exit(1)
    else:
        print("  STATUS: PIPELINE COMPLETE")


if __name__ == "__main__":
    main()
