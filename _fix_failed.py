import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()

import httpx
from bs4 import BeautifulSoup
from app.pipeline import run_state_pipeline

HEADERS = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0"}

def probe(slug):
    url = f"https://myneta.info/{slug}/"
    try:
        with httpx.Client(headers=HEADERS, timeout=12, follow_redirects=True) as c:
            r = c.get(url)
        soup = BeautifulSoup(r.text, "html.parser")
        n = len([a for a in soup.find_all("a", href=True) if "show_candidates" in a.get("href","")])
        print(f"  {slug:<35} -> {n} constituencies  (status {r.status_code})")
        return n > 0
    except Exception as e:
        print(f"  {slug:<35} -> ERROR: {e}")
        return False

print("=== Probing Andhra Pradesh slugs ===")
ap_slug = None
for slug in ["andhrapradesh2024", "ap2024andhra", "ap2019", "andhra2024", "andhrapradesh2019"]:
    if probe(slug):
        ap_slug = slug
        break
    time.sleep(0.5)

print("\n=== Probing Uttar Pradesh slugs ===")
up_slug = None
for slug in ["uttarpradesh2022", "up_assembly2022", "uttar-pradesh2022", "up2017", "uttarpradesh2017"]:
    if probe(slug):
        up_slug = slug
        break
    time.sleep(0.5)

print(f"\nAP slug: {ap_slug}")
print(f"UP slug: {up_slug}")

if ap_slug:
    print("\n=== Running Andhra Pradesh ===")
    result = run_state_pipeline(
        {"state_id": 1, "name": "Andhra Pradesh", "myneta_slug": ap_slug, "ministers_url": None}
    )
    print(result)

if up_slug:
    print("\n=== Running Uttar Pradesh ===")
    result = run_state_pipeline(
        {"state_id": 26, "name": "Uttar Pradesh", "myneta_slug": up_slug, "ministers_url": None}
    )
    print(result)
