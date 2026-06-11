"""
scripts/official_sources/telangana_coverage_probe.py

Telangana official-source coverage probe.

Run ONCE in Supabase SQL Editor first:
  scripts/official_sources/staging_schema.sql

Then:
  cd c:/projects/netalog
  python scripts/official_sources/telangana_coverage_probe.py

Official sources (priority order):
  1. ECI Results portal       — 2023 Telangana election results
  2. ECI Affidavit portal     — accessibility probe
  3. MyNeta (ECI affidavit mirror) — winner list + affidavit fields
  4. telangana.gov.in         — CM and cabinet ministers
  5. Sansad / Lok Sabha NIC   — Telangana LS MPs

Wikipedia is fallback/cross-check only — never primary truth.

Writes to:
  official_source_runs           (one row per run)
  official_politician_staging    (one row per MLA/minister/MP)
  official_source_coverage       (one row per field, with coverage %)

Does NOT modify politicians or verified_politicians.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ── Path setup ────────────────────────────────────────────────────────────────
REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, REPO_ROOT)
load_dotenv(os.path.join(REPO_ROOT, ".env"))

from app.database import get_db

# ── Constants ─────────────────────────────────────────────────────────────────
STATE_CODE     = "TS"
STATE_NAME     = "Telangana"
TOTAL_SEATS    = 119
SCRIPT_VERSION = "1.0.0"

USER_AGENT = "NetaLog-coverage-probe/1.0 (vinith0140@gmail.com) python-httpx"
HEADERS    = {"User-Agent": USER_AGENT}

# ECI 2023 November election results — try known URL patterns
ECI_RESULT_URLS = [
    "https://results.eci.gov.in/ResultAcGenNov2023/",
    "https://results.eci.gov.in/AcResultsNov2023/",
    "https://results.eci.gov.in/ResultAcGenNov2023/index.htm",
]
ECI_AFFIDAVIT_URL      = "https://affidavit.eci.gov.in/"
MYNETA_WINNERS_BASE    = (
    "https://myneta.info/telangana2023/index.php"
    "?action=summary&subAction=winner_analyzed&sort=candidate&page={page}"
)
TELANGANA_GOV_MIN_URL  = "https://www.telangana.gov.in/government/council-of-ministers/"
SANSAD_LS_URL          = "https://sansad.in/ls/members"
LS_NIC_URL             = "https://loksabha.nic.in/Members/AlphaList.aspx"
WIKI_API               = "https://en.wikipedia.org/w/api.php"
HONORIFICS             = re.compile(
    r"^(Sri|Smt\.?|Dr\.?|Shri|Shrimati|Prof\.?)\s+", re.IGNORECASE
)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────

def safe_get(
    client: httpx.Client, url: str, *, label: str = ""
) -> tuple[Optional[BeautifulSoup], str]:
    """
    Fetch URL, return (BeautifulSoup | None, status_note).
    Never raises — all errors surfaced as a status string.
    """
    try:
        r = client.get(url, timeout=20)
        if r.status_code == 200:
            return BeautifulSoup(r.text, "html.parser"), "ok"
        return None, f"HTTP {r.status_code}"
    except httpx.TimeoutException:
        return None, "timeout"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:60]}"


# ─────────────────────────────────────────────────────────────────────────────
# Utility parsers
# ─────────────────────────────────────────────────────────────────────────────

def _int(s: str) -> Optional[int]:
    if not s:
        return None
    d = "".join(c for c in s if c.isdigit())
    return int(d) if d else None


def _parse_rupees(value: str) -> Optional[float]:
    """Convert Indian rupee strings ('Rs. 1,23,456', '5 Cr+', '88 Lacs+') to float."""
    if not value:
        return None
    cleaned = value.split("~")[0]
    cleaned = (
        cleaned.replace("Rs.", "").replace("Rs", "")
        .replace(",", "").replace("\xa0", " ").strip()
    )
    lower = cleaned.lower()
    multiplier = 1
    if "cr" in lower:
        multiplier = 10_000_000
        cleaned = cleaned[: lower.index("cr")].strip()
    elif "lac" in lower or "lak" in lower:
        idx = min(
            lower.index("lac") if "lac" in lower else 9999,
            lower.index("lak") if "lak" in lower else 9999,
        )
        multiplier = 100_000
        cleaned = cleaned[:idx].strip()
    elif "thou" in lower:
        multiplier = 1_000
        cleaned = cleaned[: lower.index("thou")].strip()
    digits = "".join(c for c in cleaned if c.isdigit() or c == ".")
    try:
        return float(digits) * multiplier if digits else None
    except ValueError:
        return None


def _col_map(header_cells: list[str]) -> dict[str, int]:
    """Map column keywords -> index from a header row."""
    col: dict[str, int] = {}
    for j, h in enumerate(header_cells):
        h = h.lower()
        if "candidate" in h:    col["name"]          = j
        elif "party" in h:      col["party"]         = j
        elif "criminal" in h:   col["criminal_cases"]= j
        elif "education" in h:  col["education"]     = j
        elif "age" in h:        col["age"]           = j
        elif "asset" in h:      col["assets"]        = j
        elif "liabilit" in h:   col["liabilities"]   = j
        elif "const" in h:      col["constituency"]  = j
        elif "vote" in h and ("total" in h or "win" in h):
            col["votes"] = j
    return col


def _cell_text(cells: list, col: dict, key: str) -> str:
    idx = col.get(key)
    if idx is None or idx >= len(cells):
        return ""
    return cells[idx].get_text(strip=True)


# ─────────────────────────────────────────────────────────────────────────────
# Source 1: ECI Results portal (2023 Telangana)
# ─────────────────────────────────────────────────────────────────────────────

def probe_eci_results(client: httpx.Client) -> dict:
    """
    Try ECI 2023 results portal.  Reports accessibility and any parseable data.
    The portal is static HTML post-election but URL may have changed over time.
    """
    result: dict = {"accessible": False, "url_used": None, "winners": [], "note": ""}

    for url in ECI_RESULT_URLS:
        soup, status = safe_get(client, url, label="ECI-results")
        time.sleep(1)
        if soup:
            result["accessible"] = True
            result["url_used"]   = url
            winners = _parse_eci_table(soup, url)
            result["winners"] = winners
            result["note"] = (
                f"reachable at {url}; parsed {len(winners)} rows"
                if winners else f"reachable at {url}; no parseable result table"
            )
            return result
        result["note"] = f"{url} -> {status}"

    # Sanity: check if eci.gov.in itself is up
    _, eci_status = safe_get(client, "https://eci.gov.in/", label="ECI-main")
    time.sleep(0.5)
    result["note"] += f"; eci.gov.in -> {eci_status}"
    return result


def _parse_eci_table(soup: BeautifulSoup, url: str) -> list[dict]:
    """Extract constituency/winner rows from an ECI results HTML page."""
    records = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue
        hdrs = [c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"])]
        if not any("const" in h or "cand" in h or "winner" in h for h in hdrs):
            continue
        for row in rows[1:]:
            cells = [c.get_text(strip=True) for c in row.find_all("td")]
            if len(cells) < 3:
                continue
            records.append({
                "source_type": "ECI_RESULTS",
                "source_url":  url,
                "raw_cells":   cells[:8],
            })
    return records


# ─────────────────────────────────────────────────────────────────────────────
# Source 2: ECI Affidavit portal accessibility probe
# ─────────────────────────────────────────────────────────────────────────────

def probe_eci_affidavit(client: httpx.Client) -> dict:
    """Check if ECI affidavit portal is reachable and shows a search interface."""
    soup, status = safe_get(client, ECI_AFFIDAVIT_URL, label="ECI-affidavit")
    time.sleep(1)
    if not soup:
        return {"accessible": False, "note": f"unreachable: {status}"}
    forms   = soup.find_all("form")
    selects = soup.find_all("select")
    return {
        "accessible": True,
        "note": f"reachable; forms={len(forms)} selects={len(selects)} "
                "(requires form interaction — not bulk-scrapeable without JS)",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Source 3: MyNeta (mirrors ECI affidavit data) — all winner pages
# ─────────────────────────────────────────────────────────────────────────────

def probe_myneta_all_pages(client: httpx.Client) -> tuple[list[dict], str]:
    """
    Scrape all paginated winner pages from MyNeta Telangana 2023.
    MyNeta sources its data directly from ECI affidavit filings.
    Returns (winner_records, note).
    """
    all_winners: list[dict] = []

    for page in range(1, 12):   # 119 seats ÷ ~20 per page ≈ 6 pages; cap at 11
        url = MYNETA_WINNERS_BASE.format(page=page)
        soup, status = safe_get(client, url, label=f"MyNeta-p{page}")
        time.sleep(1.5)

        if not soup:
            print(f"    page {page}: {status} — stopping")
            break

        page_records = _parse_myneta_winner_table(soup, url)
        if not page_records:
            break   # empty page = past last page

        all_winners.extend(page_records)
        print(f"    page {page}: {len(page_records)} winners  (running total: {len(all_winners)})")

    note = f"{len(all_winners)} winners from {min(page, 11)} pages"
    return all_winners, note


def _parse_myneta_winner_table(soup: BeautifulSoup, page_url: str) -> list[dict]:
    """Parse a single MyNeta winners page into structured records."""
    records = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        hdrs = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
        if "Candidate" not in " ".join(hdrs):
            continue

        col = _col_map(hdrs)

        for row in rows[1:]:
            cells = row.find_all("td")
            if not cells:
                continue

            # Name + individual affidavit URL
            name_idx = col.get("name", 0)
            name, cand_url = "", ""
            if name_idx < len(cells):
                a = cells[name_idx].find("a")
                if a:
                    name = a.get_text(strip=True).replace("Winner", "").strip()
                    href = a.get("href", "")
                    cand_url = (
                        "https://myneta.info/telangana2023/" + href
                        if href and not href.startswith("http")
                        else href
                    )
                else:
                    name = cells[name_idx].get_text(strip=True).replace("Winner", "").strip()

            if not name:
                continue

            record: dict = {
                "name":           name,
                "party":          _cell_text(cells, col, "party") or None,
                "constituency":   _cell_text(cells, col, "constituency") or None,
                "criminal_cases": _int(_cell_text(cells, col, "criminal_cases")),
                "education":      _cell_text(cells, col, "education") or None,
                "age":            _int(_cell_text(cells, col, "age")),
                "assets":         _parse_rupees(_cell_text(cells, col, "assets")),
                "liabilities":    _parse_rupees(_cell_text(cells, col, "liabilities")),
                "votes":          _int(_cell_text(cells, col, "votes")),
                "source_type":    "MYNETA_ECI_AFFIDAVIT",
                "source_url":     cand_url or page_url,
                "confidence":     "MEDIUM",  # data is official ECI; aggregator is unofficial
            }
            records.append(record)
        break   # found the right table

    return records


# ─────────────────────────────────────────────────────────────────────────────
# Source 4: Telangana Government — CM + Cabinet Ministers
# ─────────────────────────────────────────────────────────────────────────────

def probe_telangana_gov_ministers(client: httpx.Client) -> dict:
    """Scrape telangana.gov.in for CM and cabinet minister list with portfolios."""
    result: dict = {"accessible": False, "cm": None, "ministers": [], "note": ""}

    soup, status = safe_get(client, TELANGANA_GOV_MIN_URL, label="TG-gov-ministers")
    time.sleep(1)

    if not soup:
        result["note"] = f"unreachable: {status}"
        return result

    result["accessible"] = True
    ministers: list[dict] = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 3:
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
            raw  = cells[name_idx].get_text(strip=True)
            name = HONORIFICS.sub("", raw).strip()
            if not name:
                continue
            portfolio    = cells[port_idx].get_text(strip=True)  if port_idx  and port_idx  < len(cells) else None
            constituency = cells[const_idx].get_text(strip=True) if const_idx and const_idx < len(cells) else None
            position     = "Chief Minister" if i == 0 else "Cabinet Minister"

            ministers.append({
                "name":         name,
                "position":     position,
                "portfolio":    portfolio,
                "constituency": constituency,
                "source_type":  "STATE_GOV",
                "source_url":   TELANGANA_GOV_MIN_URL,
                "confidence":   "HIGH",
            })
            if i == 0:
                result["cm"] = name
        break

    result["ministers"] = ministers
    result["note"] = f"{len(ministers)} ministers (CM: {result['cm'] or 'not found'})"
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Source 5: Sansad / Lok Sabha NIC — Telangana LS MPs
# ─────────────────────────────────────────────────────────────────────────────

def probe_sansad_ls_mps(client: httpx.Client) -> dict:
    """
    Try sansad.in and the older loksabha.nic.in for Telangana Lok Sabha members.
    sansad.in is JS-rendered; loksabha.nic.in has a static member list.
    """
    result: dict = {"accessible": False, "mps": [], "note": "", "url_used": None}

    for url in (SANSAD_LS_URL, LS_NIC_URL):
        soup, status = safe_get(client, url, label="Sansad/LS")
        time.sleep(1.5)
        if not soup:
            result["note"] = f"{url} -> {status}"
            continue

        result["accessible"] = True
        result["url_used"]   = url
        mps = _parse_ls_members_for_telangana(soup, url)
        result["mps"]  = mps
        result["note"] = f"reachable ({url}); found {len(mps)} Telangana LS MPs"
        return result

    return result


def _parse_ls_members_for_telangana(soup: BeautifulSoup, url: str) -> list[dict]:
    """Extract Telangana MPs from a Lok Sabha member list page."""
    mps: list[dict] = []
    page_text = soup.get_text(" ")
    if "telangana" not in page_text.lower():
        return mps

    for table in soup.find_all("table"):
        if "telangana" not in table.get_text(" ").lower():
            continue
        for row in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if not cells or len(cells) < 2:
                continue
            if "telangana" not in " ".join(cells).lower():
                continue
            if cells[0].lower() in ("state", "s.no", "sl.no", "#"):
                continue
            mps.append({
                "name":         cells[1] if len(cells) > 1 else cells[0],
                "state":        "Telangana",
                "constituency": cells[2] if len(cells) > 2 else None,
                "party":        cells[3] if len(cells) > 3 else None,
                "position":     "MP-LS",
                "source_type":  "SANSAD",
                "source_url":   url,
                "confidence":   "HIGH",
            })
    return mps


# ─────────────────────────────────────────────────────────────────────────────
# Wikipedia fallback — cross-check ONLY, never primary truth
# ─────────────────────────────────────────────────────────────────────────────

def probe_wikipedia_crosscheck(client: httpx.Client) -> dict:
    """Read CM name and ruling party from Wikipedia infobox as a cross-check."""
    result: dict = {"accessible": False, "cm_name": None, "ruling_party": None, "note": ""}
    try:
        r = client.get(
            WIKI_API,
            params={"action": "parse", "page": "Telangana",
                    "prop": "text", "section": 0, "format": "json"},
            timeout=15,
        )
        r.raise_for_status()
        html = r.json().get("parse", {}).get("text", {}).get("*", "")
        time.sleep(1)

        soup = BeautifulSoup(html, "html.parser")
        infobox = soup.find("table", class_="infobox")
        if infobox:
            result["accessible"] = True
            for row in infobox.find_all("tr"):
                th = row.find("th")
                td = row.find("td")
                if not (th and td):
                    continue
                label = th.get_text(strip=True).lower()
                text  = td.get_text(strip=True)
                if "chief minister" in label and not result["cm_name"]:
                    result["cm_name"] = text.split("(")[0].strip()
                if "ruling party" in label or ("government" in label and "party" in label):
                    result["ruling_party"] = text.split("(")[0].strip()

        result["note"] = f"CM: {result['cm_name']}  party: {result['ruling_party']}"
    except Exception as exc:
        result["note"] = f"error: {exc}"
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Coverage analysis
# ─────────────────────────────────────────────────────────────────────────────

def compute_coverage(
    mla_records: list[dict],
    ministers:   list[dict],
    eci_result:  dict,
) -> dict:
    n = len(mla_records)

    def have(field: str) -> int:
        return sum(
            1 for r in mla_records
            if r.get(field) is not None and r.get(field) != ""
        )

    cov = {
        "mlas_found":         n,
        "mlas_expected":      TOTAL_SEATS,
        "age_found":          have("age"),
        "education_found":    have("education"),
        "assets_found":       have("assets"),
        "liabilities_found":  have("liabilities"),
        "criminal_found":     have("criminal_cases"),
        "constituency_found": have("constituency"),
        "party_found":        have("party"),
        "votes_found":        have("votes"),
        "cm_found":           next(
            (m["name"] for m in ministers if m.get("position") == "Chief Minister"),
            None,
        ),
        "ministers_found":    len(ministers),
        "eci_direct_access":  eci_result.get("accessible", False),
        "eci_direct_count":   len(eci_result.get("winners", [])),
    }

    missing: list[str] = []
    if n < TOTAL_SEATS:
        missing.append(f"MLAs: {n}/{TOTAL_SEATS} found ({TOTAL_SEATS - n} missing)")
    for field, label in [
        ("age",          "age"),
        ("education",    "education"),
        ("assets",       "assets"),
        ("liabilities",  "liabilities"),
        ("criminal_cases","criminal_cases"),
    ]:
        gap = n - cov[f"{field}_found" if field != "criminal_cases" else "criminal_found"]
        if field == "criminal_cases":
            gap = n - cov["criminal_found"]
        if gap > 0:
            missing.append(f"{label}: missing for {gap}/{n} MLAs")
    if not cov["cm_found"]:
        missing.append("CM not found from telangana.gov.in")

    cov["missing_fields"] = missing
    return cov


# ─────────────────────────────────────────────────────────────────────────────
# Supabase persistence
# ─────────────────────────────────────────────────────────────────────────────

def _tables_exist(db) -> bool:
    """Return True if all three staging tables are present."""
    try:
        db.table("official_source_runs").select("id").limit(1).execute()
        db.table("official_politician_staging").select("id").limit(1).execute()
        db.table("official_source_coverage").select("id").limit(1).execute()
        return True
    except Exception:
        return False


def _json_safe(obj):
    """Recursively convert an object to JSON-serializable form."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (int, float, str, bool)) or obj is None:
        return obj
    return str(obj)


def save_run(db, duration_s: float, status: str, summary: dict) -> int:
    row = db.table("official_source_runs").insert({
        "state_code":       STATE_CODE,
        "script_version":   SCRIPT_VERSION,
        "total_duration_s": duration_s,
        "status":           status,
        "summary_json":     _json_safe(summary),
    }).execute()
    return row.data[0]["id"]


def save_staging_records(
    db,
    run_id:      int,
    mla_records: list[dict],
    ministers:   list[dict],
    sansad_mps:  list[dict],
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []

    for r in mla_records:
        missing = [
            f for f in ("age", "education", "assets", "liabilities", "criminal_cases")
            if r.get(f) is None
        ]
        rows.append({
            "run_id":         run_id,
            "state_code":     STATE_CODE,
            "constituency":   r.get("constituency"),
            "name":           r.get("name"),
            "party":          r.get("party"),
            "position":       "MLA",
            "age":            r.get("age"),
            "education":      r.get("education"),
            "assets":         r.get("assets"),
            "liabilities":    r.get("liabilities"),
            "criminal_cases": r.get("criminal_cases"),
            "votes":          r.get("votes"),
            "source_type":    r.get("source_type"),
            "source_url":     r.get("source_url"),
            "confidence":     r.get("confidence", "MEDIUM"),
            "fetched_at":     now,
            "missing_fields": missing or None,
        })

    for m in ministers:
        rows.append({
            "run_id":       run_id,
            "state_code":   STATE_CODE,
            "constituency": m.get("constituency"),
            "name":         m.get("name"),
            "position":     m.get("position"),
            "source_type":  m.get("source_type"),
            "source_url":   m.get("source_url"),
            "confidence":   m.get("confidence", "HIGH"),
            "fetched_at":   now,
            "raw_json":     {"portfolio": m.get("portfolio")},
        })

    for mp in sansad_mps:
        rows.append({
            "run_id":       run_id,
            "state_code":   STATE_CODE,
            "constituency": mp.get("constituency"),
            "name":         mp.get("name"),
            "party":        mp.get("party"),
            "position":     mp.get("position", "MP-LS"),
            "source_type":  mp.get("source_type"),
            "source_url":   mp.get("source_url"),
            "confidence":   mp.get("confidence", "HIGH"),
            "fetched_at":   now,
        })

    for i in range(0, len(rows), 100):
        db.table("official_politician_staging").insert(rows[i : i + 100]).execute()

    return len(rows)


def save_coverage_report(db, run_id: int, cov: dict, source_notes: dict):
    now = datetime.now(timezone.utc).isoformat()
    n   = cov["mlas_expected"]

    # (field_name, total_found, total_expected, source_type)
    fields = [
        ("mlas",          cov["mlas_found"],        n,  "MYNETA_ECI_AFFIDAVIT"),
        ("age",           cov["age_found"],          n,  "MYNETA_ECI_AFFIDAVIT"),
        ("education",     cov["education_found"],    n,  "MYNETA_ECI_AFFIDAVIT"),
        ("assets",        cov["assets_found"],       n,  "MYNETA_ECI_AFFIDAVIT"),
        ("liabilities",   cov["liabilities_found"],  n,  "MYNETA_ECI_AFFIDAVIT"),
        ("criminal_cases",cov["criminal_found"],     n,  "MYNETA_ECI_AFFIDAVIT"),
        ("constituency",  cov["constituency_found"], n,  "MYNETA_ECI_AFFIDAVIT"),
        ("party",         cov["party_found"],        n,  "MYNETA_ECI_AFFIDAVIT"),
        ("votes",         cov["votes_found"],        n,  "MYNETA_ECI_AFFIDAVIT"),
        ("cm",            1 if cov["cm_found"] else 0, 1, "STATE_GOV"),
        ("ministers",     cov["ministers_found"],    13, "STATE_GOV"),
        ("eci_direct",    cov["eci_direct_count"],   n,  "ECI_RESULTS"),
    ]

    rows = []
    for field_name, found, expected, src in fields:
        pct = round((found / expected) * 100, 1) if expected > 0 else 0.0
        rows.append({
            "run_id":        run_id,
            "state_code":    STATE_CODE,
            "field_name":    field_name,
            "total_expected":expected,
            "total_found":   found,
            "coverage_pct":  pct,
            "source_type":   src,
            "notes":         source_notes.get(field_name, ""),
            "created_at":    now,
        })

    db.table("official_source_coverage").insert(rows).execute()


# ─────────────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────────────

def print_summary(cov: dict, source_notes: dict, wiki: dict):
    n = cov["mlas_expected"]
    W = 60

    def pct(found, total):
        return f"{found}/{total}  ({found/total*100:.0f}%)" if total else f"{found}/?"

    print()
    print("=" * W)
    print("  TELANGANA OFFICIAL-SOURCE COVERAGE REPORT")
    print("=" * W)
    print()
    print("  Assembly (MLA) data")
    print(f"    MLAs found        : {pct(cov['mlas_found'], n)}")
    print(f"    Affidavit records : {pct(cov['mlas_found'], n)}  (via MyNeta/ECI)")
    print(f"    Age               : {pct(cov['age_found'], cov['mlas_found'])}")
    print(f"    Education         : {pct(cov['education_found'], cov['mlas_found'])}")
    print(f"    Assets            : {pct(cov['assets_found'], cov['mlas_found'])}")
    print(f"    Liabilities       : {pct(cov['liabilities_found'], cov['mlas_found'])}")
    print(f"    Criminal cases    : {pct(cov['criminal_found'], cov['mlas_found'])}")
    print(f"    Constituency      : {pct(cov['constituency_found'], cov['mlas_found'])}")
    print(f"    Party             : {pct(cov['party_found'], cov['mlas_found'])}")
    print()
    print("  Government")
    cm_str = f"YES — {cov['cm_found']}" if cov["cm_found"] else "NO"
    print(f"    CM found          : {cm_str}")
    print(f"    Ministers found   : {cov['ministers_found']}")
    print()
    print("  ECI Direct portal")
    print(f"    Accessible        : {'YES' if cov['eci_direct_access'] else 'NO — results portal URL may have changed'}")
    if cov["eci_direct_access"]:
        print(f"    Direct rows found : {cov['eci_direct_count']}")
    print()
    print("  Wikipedia cross-check (fallback only)")
    print(f"    CM (Wikipedia)    : {wiki.get('cm_name', '?')}")
    print(f"    Party (Wikipedia) : {wiki.get('ruling_party', '?')}")
    print()

    if cov["missing_fields"]:
        print("  Gaps / Missing fields:")
        for m in cov["missing_fields"]:
            print(f"    - {m}")
        print()

    print("  Source notes:")
    for k, v in source_notes.items():
        print(f"    {k:<22}: {v}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    print(
        f"\nTelangana Coverage Probe  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        f"\nState: {STATE_NAME} ({STATE_CODE})  |  Assembly seats: {TOTAL_SEATS}"
    )
    print("=" * 60)

    db = get_db()

    source_notes:    dict      = {}
    all_mla_records: list[dict] = []
    all_ministers:   list[dict] = []
    all_sansad_mps:  list[dict] = []
    errors:          list       = []

    with httpx.Client(headers=HEADERS, timeout=25, follow_redirects=True) as client:

        # 1. ECI Results portal
        print("\n[1/6] ECI Results portal (2023 Telangana)...")
        eci_result = probe_eci_results(client)
        source_notes["eci_results"] = eci_result["note"]
        print(f"  -> {eci_result['note']}")

        # 2. ECI Affidavit portal
        print("\n[2/6] ECI Affidavit portal (accessibility probe)...")
        aff_result = probe_eci_affidavit(client)
        source_notes["eci_affidavit"] = aff_result["note"]
        print(f"  -> {aff_result['note']}")

        # 3. MyNeta (ECI affidavit mirror) — all pages
        print("\n[3/6] MyNeta / ECI affidavit mirror — all winner pages...")
        try:
            myneta_records, myneta_note = probe_myneta_all_pages(client)
            all_mla_records.extend(myneta_records)
            source_notes["myneta_affidavit"] = myneta_note
            print(f"  -> {myneta_note}")
        except Exception as exc:
            msg = f"error: {exc}"
            errors.append(("MyNeta", msg))
            source_notes["myneta_affidavit"] = msg
            print(f"  -> {msg}")

        # 4. Telangana government — ministers
        print("\n[4/6] Telangana Government (telangana.gov.in)...")
        try:
            gov_result  = probe_telangana_gov_ministers(client)
            all_ministers = gov_result["ministers"]
            source_notes["telangana_gov"] = gov_result["note"]
            print(f"  -> {gov_result['note']}")
        except Exception as exc:
            msg = f"error: {exc}"
            errors.append(("TG Gov", msg))
            source_notes["telangana_gov"] = msg
            print(f"  -> {msg}")

        # 5. Sansad / Lok Sabha NIC — MPs
        print("\n[5/6] Sansad / Lok Sabha (Telangana MPs)...")
        try:
            sansad_result = probe_sansad_ls_mps(client)
            all_sansad_mps = sansad_result["mps"]
            source_notes["sansad"] = sansad_result["note"]
            print(f"  -> {sansad_result['note']}")
        except Exception as exc:
            msg = f"error: {exc}"
            errors.append(("Sansad", msg))
            source_notes["sansad"] = msg
            print(f"  -> {msg}")

        # 6. Wikipedia cross-check
        print("\n[6/6] Wikipedia cross-check (fallback only)...")
        try:
            wiki_result = probe_wikipedia_crosscheck(client)
            source_notes["wikipedia"] = wiki_result["note"]
            print(f"  -> {wiki_result['note']}")
        except Exception as exc:
            wiki_result = {}
            source_notes["wikipedia"] = f"error: {exc}"
            print(f"  -> {source_notes['wikipedia']}")

    # ── Coverage analysis ────────────────────────────────────────────────────
    cov = compute_coverage(all_mla_records, all_ministers, eci_result)
    print_summary(cov, source_notes, wiki_result)

    # ── Save to Supabase ─────────────────────────────────────────────────────
    duration = round(time.time() - t0, 1)
    status   = "partial" if errors else "complete"

    if not _tables_exist(db):
        print(
            "  [WARN] Staging tables not found.\n"
            "  Run scripts/official_sources/staging_schema.sql in Supabase SQL Editor first.\n"
            "  Coverage output above is still valid.\n"
        )
    else:
        try:
            print("Saving to Supabase staging tables...")
            run_id    = save_run(db, duration, status, {**cov, "source_notes": source_notes})
            n_staging = save_staging_records(db, run_id, all_mla_records, all_ministers, all_sansad_mps)
            save_coverage_report(db, run_id, cov, source_notes)
            print(f"  run_id={run_id}  staging_records={n_staging}  duration={duration}s")
            print(
                "  Tables written: official_source_runs, "
                "official_politician_staging, official_source_coverage"
            )
        except Exception as exc:
            print(f"  [WARN] DB save error: {type(exc).__name__}: {str(exc)[:120]}")
            print("  (Coverage report above is still valid)")

    # ── Errors ───────────────────────────────────────────────────────────────
    if errors:
        print(f"\nErrors during run ({len(errors)}):")
        for src, msg in errors:
            print(f"  [{src}] {msg}")

    # ── Next step recommendation ─────────────────────────────────────────────
    mla_pct = cov["mlas_found"] / TOTAL_SEATS * 100
    print("\nNext recommended step:")
    if mla_pct >= 90:
        print(
            f"  Official-source V1 is FEASIBLE.\n"
            f"  {cov['mlas_found']}/{TOTAL_SEATS} MLAs found with affidavit data.\n"
            "  -> Review staging data, then promote to verified_politicians with your approval.\n"
            "  -> For 100% coverage: scrape individual affidavit pages for missing fields."
        )
    elif mla_pct >= 50:
        print(
            f"  Partial coverage ({cov['mlas_found']}/{TOTAL_SEATS} MLAs).\n"
            "  -> Check MyNeta pagination — may need to increase page limit.\n"
            "  -> Verify MYNETA_WINNERS_BASE URL is still valid."
        )
    else:
        print(
            f"  Low coverage ({cov['mlas_found']}/{TOTAL_SEATS} MLAs).\n"
            "  -> Check network access / source URLs.\n"
            "  -> MyNeta may have changed its URL structure."
        )
    print()


if __name__ == "__main__":
    main()
