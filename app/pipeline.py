"""
Generic state pipeline: scrape candidates → tag winners as MLA → tag ministers.
Each function is stateless and re-entrant: existing records are skipped/updated.
"""

import re
import time
from app.scraper import _get, _parse_rupees
from app.database import get_db

HONORIFICS = re.compile(r"^(Sri|Smt\.|Dr\.|Shri|Shrimati|Mr\.|Mrs\.|Sh\.)\s+", re.IGNORECASE)
RESERVATION = re.compile(r"\s*[\(\[]?\s*(SC|ST|OBC)\s*[\)\]]?\s*$", re.IGNORECASE)


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
                    "constituency": RESERVATION.sub("", const_name).strip(),
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

def reset_minister_positions(state_id: int) -> int:
    """
    Reset Chief Minister / Cabinet Minister positions back to MLA.
    Call before re-running tag_ministers to avoid stale tags.
    Returns count of records updated.
    """
    db = get_db()
    result = (
        db.table("politicians")
        .update({"position": "MLA"})
        .in_("position", ["Chief Minister", "Cabinet Minister"])
        .eq("state_id", state_id)
        .execute()
    )
    return len(result.data or [])


def tag_ministers(ministers_url: str, state_id: int) -> tuple[int, int]:
    """
    Scrape a state govt or Wikipedia council-of-ministers page and tag politicians.
    Returns (matched, total_ministers_found).

    Accepts tables with either a "Name" column (state govt pages) or
    a "Minister" column (Wikipedia pages).  Detects the Chief Minister by
    looking for "chief minister" text in the portfolio column, falling back
    to the first data row if no explicit text is found.
    """
    try:
        is_wiki = "wikipedia.org" in ministers_url
        soup = _get(ministers_url, wiki=is_wiki)
    except Exception as exc:
        raise RuntimeError(f"Ministers page unreachable: {exc}") from exc

    db = get_db()
    ministers: list[dict] = []

    # ── Table selection ───────────────────────────────────────────────────────
    # Scan all qualifying tables. Prefer the one that has an explicit
    # "Chief Minister" row in the portfolio column (Wikipedia style).
    # Fall back to the first qualifying table if none has an explicit CM row.
    _best: tuple | None = None   # (rows, name_idx, port_idx, const_idx, has_explicit_cm)

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue
        hdrs = [c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"])]
        if len(hdrs) < 3:
            continue
        joined = " ".join(hdrs)
        if "name" not in joined and "minister" not in joined:
            continue

        name_idx = next(
            (j for j, h in enumerate(hdrs)
             if h.strip() in ("minister", "ministers", "name") or
             ("name" in h and "portfolio" not in h)),
            None,
        )
        port_idx  = next((j for j, h in enumerate(hdrs) if "portfolio" in h or "department" in h), None)
        const_idx = next((j for j, h in enumerate(hdrs) if "constituency" in h or "seat" in h), None)
        if const_idx is not None and const_idx == name_idx:
            const_idx = None

        if name_idx is None:
            continue

        # Also detect designation column ("designation" or "status")
        desig_idx = next((j for j, h in enumerate(hdrs) if h.strip() in ("designation", "status", "position")), None)

        # Check if any row explicitly marks "Chief Minister" via:
        # (a) portfolio/department column, (b) designation column,
        # (c) "Chief Minister" section-header row, (d) name cell embedding
        has_cm = False
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            # Section-header row: single cell saying "Chief Minister"
            if len(cells) == 1:
                txt = cells[0].get_text(strip=True).lower()
                if "chief minister" in txt and "deputy" not in txt:
                    has_cm = True
                    break
                continue
            # Name cell embedding: "Mamata Banerjee(Chief Minister)"
            if name_idx < len(cells):
                nm = cells[name_idx].get_text(strip=True).lower()
                if "chief minister" in nm and "deputy" not in nm:
                    has_cm = True
                    break
            # Portfolio / department column
            if port_idx is not None and port_idx < len(cells):
                pt = cells[port_idx].get_text(strip=True).lower()
                if "chief minister" in pt and "deputy" not in pt:
                    has_cm = True
                    break
            # Designation column
            if desig_idx is not None and desig_idx < len(cells):
                dg = cells[desig_idx].get_text(strip=True).lower()
                if "chief minister" in dg and "deputy" not in dg:
                    has_cm = True
                    break

        if _best is None:
            _best = (rows, name_idx, port_idx, const_idx, desig_idx, has_cm)
        elif has_cm and not _best[5]:
            _best = (rows, name_idx, port_idx, const_idx, desig_idx, has_cm)

        if has_cm:
            break   # can't do better than an explicit CM table

    if _best is None:
        return 0, 0

    rows, name_idx, port_idx, const_idx, desig_idx, has_explicit_cm = _best

    # ── Extract ministers ─────────────────────────────────────────────────────
    next_is_cm = False  # set when a section-header row says "Chief Minister"
    first_data_row = True

    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue

        # Section-header row: single cell like "Chief Minister" or "Cabinet Ministers"
        if len(cells) <= 2 and name_idx >= len(cells):
            txt = cells[0].get_text(strip=True).lower()
            if "chief minister" in txt and "deputy" not in txt:
                next_is_cm = True
            else:
                next_is_cm = False
            continue

        if name_idx >= len(cells):
            continue

        raw_name   = cells[name_idx].get_text(strip=True)
        # Check for embedded "Chief Minister" in the name cell before cleaning
        name_has_cm = "chief minister" in raw_name.lower() and "deputy" not in raw_name.lower()
        clean_name  = HONORIFICS.sub("", raw_name).strip()
        # Strip embedded role suffix: "(Chief Minister)", "Chief Minister", "MLA from X"
        clean_name  = re.sub(r"\s*\(?(Chief\s+Minister|Cabinet\s+Minister|MLA|MP|Member)\)?.*$",
                             "", clean_name, flags=re.IGNORECASE).strip()
        clean_name  = re.sub(r",?\s*(from)\b.*$", "", clean_name, flags=re.IGNORECASE).strip()
        if not clean_name or len(clean_name) < 3:
            continue

        # Portfolio / department text
        portfolio_text = ""
        if port_idx is not None and port_idx < len(cells):
            portfolio_text = cells[port_idx].get_text(strip=True).lower()

        # Designation text (e.g., Karnataka has a "designation" column)
        desig_text = ""
        if desig_idx is not None and desig_idx < len(cells):
            desig_text = cells[desig_idx].get_text(strip=True).lower()

        if has_explicit_cm:
            cm_signal = (
                (name_has_cm) or
                next_is_cm or
                ("chief minister" in portfolio_text and "deputy" not in portfolio_text) or
                ("chief minister" in desig_text and "deputy" not in desig_text)
            )
            position = "Chief Minister" if cm_signal else "Cabinet Minister"
        else:
            # State-govt-style (e.g. Telangana): first data row = CM
            position = "Chief Minister" if first_data_row else "Cabinet Minister"

        next_is_cm = False  # consumed
        first_data_row = False

        constituency = ""
        if const_idx is not None and const_idx < len(cells):
            constituency = cells[const_idx].get_text(strip=True)
        const_clean = RESERVATION.sub("", constituency).strip()

        ministers.append({"name": clean_name, "position": position, "const_clean": const_clean})

    # Deduplicate: if a name appears as both CM and Cabinet Minister (reshuffle tables),
    # keep CM role — process CMs first so Cabinet Minister entries don't overwrite them.
    ministers.sort(key=lambda m: 0 if m["position"] == "Chief Minister" else 1)
    seen_names: set[str] = set()
    deduped: list[dict] = []
    for m in ministers:
        key = re.sub(r"\s+", " ", m["name"].lower().strip())
        if key not in seen_names:
            seen_names.add(key)
            deduped.append(m)
    ministers = deduped

    def _count(q) -> int:
        return len((q.execute()).data or [])

    def _update(pos: str, **filters) -> list:
        q = db.table("politicians").update({"position": pos})
        for k, v in filters.items():
            if k.endswith("__ilike"):
                q = q.ilike(k[:-7], v)
            elif k.endswith("__in"):
                q = q.in_(k[:-4], v)
            else:
                q = q.eq(k, v)
        return q.execute().data or []

    def _select(**filters):
        q = db.table("politicians").select("name,constituency,position")
        for k, v in filters.items():
            if k.endswith("__ilike"):
                q = q.ilike(k[:-7], v)
            elif k.endswith("__in"):
                q = q.in_(k[:-4], v)
            else:
                q = q.eq(k, v)
        return q.execute().data or []

    matched = 0
    for m in ministers:
        pos, clean, const_clean = m["position"], m["name"], m["const_clean"]
        tagged = False

        # ── Tier 1: Exact name (no wildcards, safe) ───────────────────────────
        res1 = db.table("politicians").update({"position": pos}).ilike("name", clean).eq("state_id", state_id).execute()
        if res1.data:
            matched += 1
            continue

        # ── Tier 1.5: Full name as substring (handles "Dr. Mohan Yadav" for "Mohan Yadav") ──
        check15 = _select(**{"name__ilike": f"%{clean}%", "state_id": state_id})
        if len(check15) == 1:
            db.table("politicians").update({"position": pos}).ilike("name", f"%{clean}%").eq("state_id", state_id).execute()
            matched += 1
            continue

        # ── Tier 1.6: Bigram substring (handles "Nayab Singh Saini" → "Nayab Singh") ─
        parts = clean.split()
        if len(parts) >= 2:
            for bi in range(len(parts) - 1):
                bigram = f"{parts[bi]} {parts[bi+1]}"
                if any(len(w) > 3 for w in bigram.split()):
                    check16 = _select(**{"name__ilike": f"%{bigram}%", "state_id": state_id})
                    if len(check16) == 1:
                        db.table("politicians").update({"position": pos}).ilike("name", f"%{bigram}%").eq("state_id", state_id).execute()
                        matched += 1
                        tagged = True
                        break
        if tagged:
            continue

        # ── Tier 2: Constituency + MLA (winner in that seat) ─────────────────
        if const_clean:
            check2 = _select(**{"constituency__ilike": f"%{const_clean}%", "state_id": state_id, "position": "MLA"})
            if len(check2) == 1:
                db.table("politicians").update({"position": pos}).ilike("constituency", f"%{const_clean}%").eq("state_id", state_id).eq("position", "MLA").execute()
                matched += 1
                continue

        # ── Tier 3: Constituency + any significant name word (SELECT before UPDATE) ──
        if const_clean:
            for word in parts:
                w = re.sub(r"^[A-Z]\.+", "", word).strip()   # strip initials like "N."
                if len(w) > 3:
                    check3 = _select(**{"constituency__ilike": f"%{const_clean}%", "state_id": state_id, "name__ilike": f"%{w}%"})
                    if len(check3) == 1:
                        db.table("politicians").update({"position": pos}).ilike("constituency", f"%{const_clean}%").eq("state_id", state_id).ilike("name", f"%{w}%").execute()
                        matched += 1
                        tagged = True
                        break
        if tagged:
            continue

        # ── Tier 4: Unique word (longest first, SELECT before UPDATE) ─────────
        for probe in sorted(parts, key=len, reverse=True):
            if len(probe) > 4:
                check4 = _select(**{"name__ilike": f"%{probe}%", "state_id": state_id})
                if len(check4) == 1:
                    db.table("politicians").update({"position": pos}).ilike("name", f"%{probe}%").eq("state_id", state_id).execute()
                    matched += 1
                    tagged = True
                    break
        if tagged:
            continue

        # ── Tier 5 (CM only): Best word-overlap candidate ─────────────────────
        if pos == "Chief Minister":
            cm_words = {w.lower() for w in parts if len(w) > 4}
            candidates: dict[str, int] = {}  # db_name → overlap count
            for word in sorted(cm_words, key=len, reverse=True):
                found = _select(**{"name__ilike": f"%{word}%", "state_id": state_id})
                for r in found:
                    db_words = {w.lower() for w in r["name"].split()}
                    overlap = len(cm_words & db_words)
                    if r["name"] not in candidates or candidates[r["name"]] < overlap:
                        candidates[r["name"]] = overlap
            if candidates:
                best_name = max(candidates, key=lambda n: candidates[n])
                if candidates[best_name] >= 2:
                    db.table("politicians").update({"position": pos}).ilike("name", best_name).eq("state_id", state_id).execute()
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
