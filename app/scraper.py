import time
import httpx
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Optional

PIB_BASE_URL = "https://pib.gov.in"
MYNETA_BASE_URL = "https://www.myneta.info"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def _get(url: str) -> BeautifulSoup:
    with httpx.Client(headers=HEADERS, timeout=15, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def scrape_pib_releases(query: str = "", max_results: int = 20) -> list[dict]:
    """
    Scrape press releases from PIB's listing page.
    Releases are in ul.release_list > li > a; full title is in the `title` attribute.
    """
    url = f"{PIB_BASE_URL}/indexd.aspx?reg=3&lang=1"

    try:
        soup = _get(url)
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch PIB page: {exc}") from exc

    results = []

    # PIB's search/filter is JS-rendered; we fetch all and filter client-side
    all_tags = soup.select("ul.release_list li a")
    for a_tag in all_tags:
        title = a_tag.get("title", "").strip() or a_tag.get_text(strip=True)
        href = a_tag.get("href", "")
        if href and not href.startswith("http"):
            href = PIB_BASE_URL + ("" if href.startswith("/") else "/") + href

        if not title:
            continue
        if query and query.lower() not in title.lower():
            continue

        results.append({
            "title": title,
            "description": title,
            "source_url": href,
            "published_date": None,
            "category": "government",
        })

    return results


def scrape_myneta_politician(myneta_url: str) -> dict:
    """
    Scrape politician details from a MyNeta candidate page.
    Returns a dict with available fields.
    """
    try:
        soup = _get(myneta_url)
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch MyNeta page: {exc}") from exc

    data: dict = {"myneta_url": myneta_url}

    # Name
    name_tag = soup.select_one("h2.cand-name") or soup.select_one("div.cand_name h2")
    if name_tag:
        data["name"] = name_tag.get_text(strip=True)

    # Party
    party_tag = soup.select_one("div.party-name") or soup.select_one("td:-soup-contains('Party') + td")
    if party_tag:
        data["party"] = party_tag.get_text(strip=True)

    # Parse the affidavit table for assets, liabilities, criminal cases
    rows = soup.select("table.cand-detail-table tr")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        label = cells[0].get_text(strip=True).lower()
        value = cells[1].get_text(strip=True)

        if "total assets" in label:
            data["assets"] = _parse_rupees(value)
        elif "total liabilities" in label or "liabilities" in label:
            data["liabilities"] = _parse_rupees(value)
        elif "criminal" in label:
            try:
                data["criminal_cases"] = int("".join(filter(str.isdigit, value)) or "0")
            except ValueError:
                pass
        elif "education" in label:
            data["education"] = value
        elif "age" in label:
            try:
                data["age"] = int("".join(filter(str.isdigit, value)))
            except ValueError:
                pass

    return data


def scrape_myneta_state(state_slug: str) -> list[dict]:
    """
    Scrape list of candidates/politicians for a given state from MyNeta.
    state_slug examples: 'ls2024', 'up2022'
    Returns list of dicts with name, party, constituency, myneta_url.
    """
    url = f"{MYNETA_BASE_URL}/{state_slug}/"
    try:
        soup = _get(url)
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch MyNeta state page: {exc}") from exc

    politicians = []
    rows = soup.select("table.stat-table tr")
    for row in rows[1:]:  # skip header
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        link_tag = cells[0].find("a")
        if not link_tag:
            continue
        name = link_tag.get_text(strip=True)
        href = link_tag.get("href", "")
        if href and not href.startswith("http"):
            href = MYNETA_BASE_URL + ("" if href.startswith("/") else "/") + href

        politicians.append({
            "name": name,
            "party": cells[1].get_text(strip=True) if len(cells) > 1 else None,
            "constituency": cells[2].get_text(strip=True) if len(cells) > 2 else None,
            "myneta_url": href,
        })

    return politicians


def scrape_telangana_politicians() -> list[dict]:
    """
    Scrape all candidates from MyNeta Telangana 2023 and save to Supabase.
    Iterates all constituency pages (~121). Skips existing records by name.
    Returns list of newly inserted politicians.
    """
    from app.database import get_db

    TELANGANA_STATE_ID = 24
    BASE = "https://myneta.info/telangana2023"

    try:
        main_soup = _get(f"{BASE}/")
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch Telangana main page: {exc}") from exc

    # Collect constituency (href, display_name) pairs, preserving order, no dupes
    seen_hrefs: set[str] = set()
    constituencies: list[tuple[str, str]] = []
    for a in main_soup.find_all("a", href=True):
        href = a.get("href", "")
        if "show_candidates" in href and href not in seen_hrefs:
            seen_hrefs.add(href)
            constituencies.append((href, a.get_text(strip=True)))

    print(f"Found {len(constituencies)} constituencies")

    db = get_db()

    # Pre-load existing names to detect duplicates without per-row DB queries
    existing_resp = db.table("politicians").select("name").eq("state_id", TELANGANA_STATE_ID).execute()
    existing_names: set[str] = {row["name"].lower() for row in (existing_resp.data or [])}

    saved: list[dict] = []
    total_skipped = 0

    for i, (href, constituency_name) in enumerate(constituencies):
        url = f"{BASE}/{href}"
        try:
            soup = _get(url)
        except Exception as exc:
            print(f"  Skipping {constituency_name}: {exc}")
            continue

        # Find the candidates table by checking for a "Candidate" header
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            hdrs = [c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"])]
            if "candidate" not in " ".join(hdrs):
                continue

            # Map column names → indices
            col: dict[str, int] = {}
            for j, h in enumerate(hdrs):
                if "candidate" in h:   col["name"] = j
                elif "party" in h:     col["party"] = j
                elif "criminal" in h:  col["criminal"] = j
                elif "education" in h: col["education"] = j
                elif "age" in h:       col["age"] = j
                elif "asset" in h:     col["assets"] = j
                elif "liabilit" in h:  col["liabilities"] = j

            def _cell(cells: list, key: str) -> str:
                idx = col.get(key)
                if idx is None or idx >= len(cells):
                    return ""
                return cells[idx].get_text(strip=True)

            to_insert: list[dict] = []
            for row in rows[1:]:
                cells = row.find_all("td")
                if not cells:
                    continue

                # Name — strip "Winner" badge; grab MyNeta URL from the anchor
                name_idx = col.get("name", 0)
                name, myneta_url = "", ""
                if name_idx < len(cells):
                    a_tag = cells[name_idx].find("a")
                    if a_tag:
                        name = a_tag.get_text(strip=True).replace("Winner", "").strip()
                        cand_href = a_tag.get("href", "")
                        myneta_url = (
                            f"{BASE}/{cand_href}"
                            if cand_href and not cand_href.startswith("http")
                            else cand_href
                        )
                    else:
                        name = cells[name_idx].get_text(strip=True).replace("Winner", "").strip()

                if not name:
                    continue
                if name.lower() in existing_names:
                    total_skipped += 1
                    continue

                age, criminal_cases = None, 0
                try:
                    d = "".join(filter(str.isdigit, _cell(cells, "age")))
                    age = int(d) if d else None
                except ValueError:
                    pass
                try:
                    d = "".join(filter(str.isdigit, _cell(cells, "criminal")))
                    criminal_cases = int(d) if d else 0
                except ValueError:
                    pass

                record = {
                    "name": name,
                    "party": _cell(cells, "party") or "Unknown",
                    "state_id": TELANGANA_STATE_ID,
                    "constituency": constituency_name,
                    "education": _cell(cells, "education") or None,
                    "age": age,
                    "criminal_cases": criminal_cases,
                    "assets": _parse_rupees(_cell(cells, "assets")),
                    "liabilities": _parse_rupees(_cell(cells, "liabilities")),
                    "myneta_url": myneta_url or None,
                }
                to_insert.append(record)
                existing_names.add(name.lower())

            if to_insert:
                try:
                    result = db.table("politicians").insert(to_insert).execute()
                    saved.extend(result.data or [])
                except Exception as exc:
                    print(f"  DB error ({constituency_name}): {exc}")

            break  # found the right table

        if (i + 1) % 10 == 0 or i == len(constituencies) - 1:
            print(f"  [{i+1}/{len(constituencies)}] saved={len(saved)}, skipped={total_skipped}")

        time.sleep(0.3)

    print(f"\nDone. Saved {len(saved)} politicians, skipped {total_skipped} duplicates.")
    return saved


def _parse_rupees(value: str) -> Optional[float]:
    """Convert Indian rupee strings ('Rs. 1,23,456', 'Rs 5 Cr+', '88 Lacs+') to float."""
    if not value:
        return None
    # "Rs 10,000~ 10 Thou+" — take only the part before "~"
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
        multiplier = 100_000
        idx = min(
            lower.index("lac") if "lac" in lower else 9999,
            lower.index("lak") if "lak" in lower else 9999,
        )
        cleaned = cleaned[:idx].strip()
    elif "thou" in lower:
        multiplier = 1_000
        cleaned = cleaned[: lower.index("thou")].strip()
    digits = "".join(c for c in cleaned if c.isdigit() or c == ".")
    try:
        return float(digits) * multiplier if digits else None
    except ValueError:
        return None
