"""
Generic state pipeline: scrape candidates → tag winners as MLA → tag ministers.
Each function is stateless and re-entrant: existing records are skipped/updated.
"""

import re
import time
from app.scraper import _get, _parse_rupees
from app.database import get_db

HONORIFICS = re.compile(r"^(Sri|Smt\.|Dr\.|Shri|Shrimati|Mr\.|Mrs\.|Sh\.)\s+", re.IGNORECASE)
RESERVATION = re.compile(r"\s*\((SC|ST|OBC)\)\s*$", re.IGNORECASE)


# ── Candidates ────────────────────────────────────────────────────────────────

def scrape_candidates(base_url: str, state_id: int) -> tuple[int, int]:
    """
    Scrape all candidates from a MyNeta state election main page.
    Returns (saved, skipped).  base_url must end with '/'.
    """
    try:
        main_soup = _get(base_url)
    except Exception as exc:
        raise RuntimeError(f"Cannot reach {base_url}: {exc}") from exc

    seen: set[str] = set()
    constituencies: list[tuple[str, str]] = []
    for a in main_soup.find_all("a", href=True):
        href = a.get("href", "")
        if "show_candidates" in href and href not in seen:
            seen.add(href)
            constituencies.append((href, a.get_text(strip=True)))

    if not constituencies:
        raise RuntimeError(f"No constituency links found at {base_url}")

    db = get_db()
    existing_resp = db.table("politicians").select("name").eq("state_id", state_id).execute()
    existing_names: set[str] = {r["name"].lower() for r in (existing_resp.data or [])}

    saved = 0
    skipped = 0

    for i, (href, const_name) in enumerate(constituencies):
        url = f"{base_url.rstrip('/')}/{href}"
        try:
            soup = _get(url)
        except Exception:
            continue

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            hdrs = [c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"])]
            if "candidate" not in " ".join(hdrs):
                continue

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
                return cells[idx].get_text(strip=True) if idx is not None and idx < len(cells) else ""

            to_insert: list[dict] = []
            for row in rows[1:]:
                cells = row.find_all("td")
                if not cells:
                    continue

                name_idx = col.get("name", 0)
                name, myneta_url = "", ""
                if name_idx < len(cells):
                    a_tag = cells[name_idx].find("a")
                    if a_tag:
                        name = a_tag.get_text(strip=True).replace("Winner", "").strip()
                        cand_href = a_tag.get("href", "")
                        myneta_url = (
                            f"{base_url.rstrip('/')}/{cand_href}"
                            if cand_href and not cand_href.startswith("http")
                            else cand_href
                        )
                    else:
                        name = cells[name_idx].get_text(strip=True).replace("Winner", "").strip()

                if not name:
                    continue
                if name.lower() in existing_names:
                    skipped += 1
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
                    "state_id": state_id,
                    "constituency": const_name,
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
                    saved += len(result.data or [])
                except Exception as exc:
                    print(f"    DB error ({const_name}): {exc}")

            break  # found the right table

        if (i + 1) % 20 == 0:
            print(f"    [{i+1}/{len(constituencies)}] saved={saved} skipped={skipped}")

        time.sleep(0.25)

    return saved, skipped


# ── Winners / MLA ─────────────────────────────────────────────────────────────

def tag_winners(base_url: str, state_id: int) -> int:
    """
    Scrape winner pages from MyNeta and set position='MLA' for each.
    Returns count of politicians updated.
    """
    db = get_db()
    winner_names: list[str] = []

    for page in range(1, 20):  # stop early when a page returns 0 names
        url = (
            f"{base_url.rstrip('/')}/index.php"
            f"?action=summary&subAction=winner_analyzed&sort=candidate&page={page}"
        )
        try:
            soup = _get(url)
        except Exception:
            break

        names_here: list[str] = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            hdrs = [c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"])]
            if "candidate" not in " ".join(hdrs):
                continue
            name_idx = next((j for j, h in enumerate(hdrs) if "candidate" in h), None)
            if name_idx is None:
                continue
            for row in rows[1:]:
                cells = row.find_all("td")
                if not cells or name_idx >= len(cells):
                    continue
                a_tag = cells[name_idx].find("a")
                raw = a_tag.get_text(strip=True) if a_tag else cells[name_idx].get_text(strip=True)
                name = raw.replace("Winner", "").strip()
                if name:
                    names_here.append(name)
            break

        if not names_here:
            break
        winner_names.extend(names_here)
        time.sleep(0.2)

    if not winner_names:
        return 0

    result = (
        db.table("politicians")
        .update({"position": "MLA"})
        .in_("name", winner_names)
        .eq("state_id", state_id)
        .execute()
    )
    return len(result.data or [])


# ── Ministers ─────────────────────────────────────────────────────────────────

def tag_ministers(ministers_url: str, state_id: int) -> tuple[int, int]:
    """
    Scrape a state govt council-of-ministers page and tag politicians.
    Returns (matched, total_ministers_found).
    Looks for a <table> with Name / Portfolio / Constituency columns.
    """
    try:
        soup = _get(ministers_url)
    except Exception as exc:
        raise RuntimeError(f"Ministers page unreachable: {exc}") from exc

    db = get_db()
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
            raw_name     = cells[name_idx].get_text(strip=True)
            clean_name   = HONORIFICS.sub("", raw_name).strip()
            constituency = cells[const_idx].get_text(strip=True) if const_idx and const_idx < len(cells) else ""
            const_clean  = RESERVATION.sub("", constituency).strip()
            position     = "Chief Minister" if i == 0 else "Cabinet Minister"
            if clean_name:
                ministers.append({"name": clean_name, "position": position, "const_clean": const_clean})
        break

    matched = 0
    for m in ministers:
        pos, clean, const_clean = m["position"], m["name"], m["const_clean"]

        # 1. Exact name
        res = db.table("politicians").update({"position": pos}).ilike("name", clean).eq("state_id", state_id).execute()
        if res.data:
            matched += 1
            continue

        # 2. Constituency + MLA (winner in that seat)
        if const_clean:
            res2 = (
                db.table("politicians")
                .update({"position": pos})
                .ilike("constituency", f"%{const_clean}%")
                .eq("state_id", state_id)
                .eq("position", "MLA")
                .execute()
            )
            if res2.data:
                matched += 1
                continue

        # 3. Constituency + any candidate (catches missed winners)
        if const_clean:
            parts = clean.split()
            first = parts[0].lstrip("D.").strip() if parts else ""
            if first and len(first) > 3:
                res3 = (
                    db.table("politicians")
                    .update({"position": pos})
                    .ilike("constituency", f"%{const_clean}%")
                    .eq("state_id", state_id)
                    .ilike("name", f"%{first}%")
                    .execute()
                )
                if res3.data and len(res3.data) == 1:
                    matched += 1
                    continue

        # 4. Unique middle/last word
        parts = clean.split()
        probe = parts[-1] if len(parts) >= 2 else ""
        if probe and len(probe) > 5:
            res4 = (
                db.table("politicians")
                .update({"position": pos})
                .ilike("name", f"%{probe}%")
                .eq("state_id", state_id)
                .execute()
            )
            if res4.data and len(res4.data) == 1:
                matched += 1

    return matched, len(ministers)


# ── Full state pipeline ───────────────────────────────────────────────────────

def run_state_pipeline(state: dict, fallback_slugs: list[str] | None = None) -> dict:
    """
    Run candidates → winners → ministers for one state.
    Returns a summary dict logged by run_all_states.py.
    """
    result: dict = {"state_id": state["state_id"], "name": state["name"]}

    base = f"https://myneta.info/{state['myneta_slug']}/"

    # ── Candidates ────────────────────────────────────────────────────────────
    saved, skipped = 0, 0
    tried_bases = [base] + [f"https://myneta.info/{s}/" for s in (fallback_slugs or [])]
    candidates_error = None

    for attempt_base in tried_bases:
        try:
            saved, skipped = scrape_candidates(attempt_base, state["state_id"])
            result["myneta_url_used"] = attempt_base
            break
        except RuntimeError as exc:
            candidates_error = str(exc)

    if "myneta_url_used" not in result:
        result["candidates_error"] = candidates_error
        return result

    result["candidates_saved"]   = saved
    result["candidates_skipped"] = skipped

    # ── Winners / MLA ─────────────────────────────────────────────────────────
    try:
        mla = tag_winners(result["myneta_url_used"], state["state_id"])
        result["mla_tagged"] = mla
    except Exception as exc:
        result["mla_error"] = str(exc)

    # ── Ministers ─────────────────────────────────────────────────────────────
    if state.get("ministers_url"):
        try:
            matched, total = tag_ministers(state["ministers_url"], state["state_id"])
            result["ministers_matched"] = matched
            result["ministers_total"]   = total
        except Exception as exc:
            result["ministers_error"] = str(exc)

    return result
