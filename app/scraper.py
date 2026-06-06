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


def _parse_rupees(value: str) -> Optional[float]:
    """Convert Indian rupee string like 'Rs. 1,23,456' to float."""
    cleaned = value.replace("Rs.", "").replace(",", "").strip()
    digits = "".join(c for c in cleaned if c.isdigit() or c == ".")
    try:
        return float(digits) if digits else None
    except ValueError:
        return None
