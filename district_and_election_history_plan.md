# District Hierarchy & Election History — Dry-Run Plan

**Scope:** 7 states — Telangana, Maharashtra, Karnataka, Delhi, Tamil Nadu, Uttar Pradesh, West Bengal  
**Mode:** Dry-run only. No Supabase writes. Output CSV/JSON for review.  
**Date:** 2026-06-14

---

## 1. What Already Exists in the DB

### Tables present
| Table | Has district? | Has election history? |
|-------|--------------|----------------------|
| `states` | No | No |
| `politicians` | No | No |
| `verified_politicians` | No | No |
| `achievements` | No | No |

### Fields already collected that help
| Field | Table | Useful for |
|-------|-------|-----------|
| `constituency` | `politicians` / `verified_politicians` | Join key for both features |
| `myneta_url` | `politicians` | Entry point for election history scraping |
| `id` | `verified_politicians` | `politician_id` for history table |
| `name` | `verified_politicians` | Name matching across elections |

### What is NOT collected yet
- District name (no table, no column anywhere)
- Election year of individual contest
- Result (Won / Lost) per election
- Votes received
- Vote margin
- Number of elections contested / won (summary)

---

## 2. Proposed Schema

### Table A — `constituency_district_mapping`

```sql
CREATE TABLE constituency_district_mapping (
  id                    SERIAL PRIMARY KEY,
  state_id              INTEGER REFERENCES states(id),
  state_name            TEXT NOT NULL,
  district_name         TEXT NOT NULL,
  constituency_name     TEXT NOT NULL,
  constituency_number   INTEGER,
  reservation           TEXT,          -- GEN / SC / ST (kept for reference, not shown in UI)
  mla_name              TEXT,          -- matched from verified_politicians
  mla_party             TEXT,
  politician_id         INTEGER,       -- NULL if no match found
  source_url            TEXT NOT NULL,
  source_name           TEXT NOT NULL,
  confidence            TEXT DEFAULT 'confirmed',  -- confirmed / uncertain
  scraped_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX ON constituency_district_mapping(state_id);
CREATE INDEX ON constituency_district_mapping(district_name);
CREATE UNIQUE INDEX ON constituency_district_mapping(state_id, constituency_name);
```

### Table B — `politician_election_history`

```sql
CREATE TABLE politician_election_history (
  id                SERIAL PRIMARY KEY,
  politician_id     INTEGER,           -- NULL if not yet matched to verified_politicians
  politician_name   TEXT NOT NULL,
  election_year     INTEGER NOT NULL,
  election_type     TEXT DEFAULT 'State Assembly',
  state             TEXT NOT NULL,
  constituency      TEXT NOT NULL,
  party             TEXT,
  result            TEXT,              -- 'Won' / 'Lost'
  votes             INTEGER,
  margin            INTEGER,           -- winner margin (positive = won by this many)
  source_url        TEXT NOT NULL,
  source_name       TEXT NOT NULL,
  confidence        TEXT DEFAULT 'confirmed',  -- confirmed / uncertain
  scraped_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX ON politician_election_history(politician_id);
CREATE INDEX ON politician_election_history(state, election_year);
CREATE INDEX ON politician_election_history(politician_name);
```

### Table C — `politician_track_record_summary`

Computed from Table B. One row per verified politician.

```sql
CREATE TABLE politician_track_record_summary (
  politician_id         INTEGER PRIMARY KEY REFERENCES verified_politicians(id),
  politician_name       TEXT NOT NULL,
  elections_contested   INTEGER DEFAULT 0,
  elections_won         INTEGER DEFAULT 0,
  win_rate_pct          NUMERIC(5,1),
  first_election_year   INTEGER,
  years_in_politics     INTEGER,   -- current_year - first_election_year
  constituencies        TEXT[],    -- all constituencies ever contested from
  parties               TEXT[],    -- all parties ever contested under
  last_updated          TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 3. Feature 1 — District Hierarchy

### Goal
```
State → District → Constituency → MLA (from verified_politicians)
```

### Primary Source: Wikipedia "List of constituencies" pages
Wikipedia has a dedicated article per state listing every constituency with its district.
These pages are stable, structured, and free.

| State | Wikipedia Article | URL |
|-------|------------------|-----|
| Karnataka | List_of_constituencies_of_the_Karnataka_Legislative_Assembly | en.wikipedia.org/wiki/List_of_constituencies_of_the_Karnataka_Legislative_Assembly |
| Maharashtra | List_of_constituencies_of_the_Maharashtra_Legislative_Assembly | en.wikipedia.org/wiki/List_of_constituencies_of_the_Maharashtra_Legislative_Assembly |
| Delhi | List_of_constituencies_of_the_Delhi_Legislative_Assembly | en.wikipedia.org/wiki/List_of_constituencies_of_the_Delhi_Legislative_Assembly |
| Tamil Nadu | List_of_constituencies_of_the_Tamil_Nadu_Legislative_Assembly | en.wikipedia.org/wiki/List_of_constituencies_of_the_Tamil_Nadu_Legislative_Assembly |
| Telangana | List_of_constituencies_of_the_Telangana_Legislative_Assembly | en.wikipedia.org/wiki/List_of_constituencies_of_the_Telangana_Legislative_Assembly |
| West Bengal | List_of_constituencies_of_the_West_Bengal_Legislative_Assembly | en.wikipedia.org/wiki/List_of_constituencies_of_the_West_Bengal_Legislative_Assembly |
| Uttar Pradesh | List_of_constituencies_of_the_Uttar_Pradesh_Legislative_Assembly | en.wikipedia.org/wiki/List_of_constituencies_of_the_Uttar_Pradesh_Legislative_Assembly |

### What these Wikipedia tables contain
Each article has a wikitable with columns (varies slightly by state):
- No. (constituency number)
- Constituency name
- District
- Reservation (GEN / SC / ST)

### Secondary / Verification Source: ECI
ECI publishes constituency-district maps in PDFs. These are authoritative but hard to scrape.
URLs at `https://eci.gov.in/` under Statistical Reports.
**Use only for spot-checking Wikipedia data, not primary scraping.**

### MLA joining strategy
After scraping constituency → district from Wikipedia, join against `verified_politicians`
on `constituency` name match (same fuzzy match logic already used in `verify_mlas.py`).
Where no match found: `politician_id = NULL`, `confidence = 'uncertain'`.

### Dry-run output
File: `output/district_mapping_dry_run.csv`

Columns:
```
state_id, state_name, district_name, constituency_name, constituency_number,
reservation, mla_name, mla_party, politician_id, match_type,
source_url, source_name, confidence
```

---

## 4. Feature 2 — Election History / Track Record

### Goal
Per verified politician: every election they contested — year, result, votes, margin.

### Source Strategy

#### Primary: Wikipedia state election result pages (multiple years)
We already scrape these in `verify_mlas.py` for the **most recent** election.
For history, we scrape **all available election year pages** per state.

| State | Elections to scrape | Wikipedia articles |
|-------|--------------------|--------------------|
| Karnataka | 2023, 2018, 2013, 2008 | `2023_Karnataka_...`, `2018_Karnataka_...`, etc. |
| Maharashtra | 2024, 2019, 2014 | `2024_Maharashtra_...`, etc. |
| Delhi | 2025, 2020, 2015 | `2025_Delhi_...`, etc. |
| Tamil Nadu | 2026, 2021, 2016 | `2026_Tamil_Nadu_...`, etc. |
| Telangana | 2023, 2018 | (Telangana formed 2014, first election 2018) |
| West Bengal | 2026, 2021, 2016 | `2026_West_Bengal_...`, etc. |
| Uttar Pradesh | 2022, 2017, 2012 | `2022_Uttar_Pradesh_...`, etc. |

Wikipedia constituency result tables include:
- Constituency name
- Winner name
- Winner party
- Winner votes
- Runner-up votes
- Margin

**Losers** are NOT in Wikipedia result tables (only winners per constituency).
For loser history, we need MyNeta.

#### Secondary: MyNeta candidate pages (for losers + vote counts)
Each politician has a `myneta_url` stored in `verified_politicians`.
MyNeta candidate page shows:
- Candidate name, party, constituency for THAT election
- Assets / criminal cases (already scraped)

MyNeta does NOT show a politician's past elections on the same page.
For past elections, the approach is:
1. Take the politician's name
2. Search across older MyNeta election slugs for the same state
3. URL pattern: `https://myneta.info/{state}{year}/index.php?action=show_candidates&constituency_id=X`

This is complex and will produce uncertain matches. Mark as `confidence = 'uncertain'` unless
constituency + name match exactly.

**Do NOT use AI to guess past elections.**

#### What we can collect confidently (Wikipedia only)
- All election winners for the last 3–4 elections per state
- Year, constituency, party, votes, margin for winners
- `politician_id` linkage where name matches `verified_politicians`

#### What remains uncertain
- Losers' full histories (MyNeta needed, complex matching)
- Exact vote counts for all candidates (Wikipedia only shows winner + margin)
- Elections before 2008 (Wikipedia coverage sparse)

### Dry-run output
File: `output/election_history_dry_run.csv`

Columns:
```
politician_id, politician_name, election_year, election_type, state,
constituency, party, result, votes, margin, source_url, source_name, confidence
```

File: `output/track_record_summary_dry_run.csv`

Columns:
```
politician_id, politician_name, elections_contested, elections_won,
win_rate_pct, first_election_year, years_in_politics, source_url
```

---

## 5. Dry-Run Scripts

| Script | What it does | Output |
|--------|-------------|--------|
| `dry_run_district_mapping.py` | Scrapes Wikipedia constituency-list pages for all 7 states. Joins MLA from `verified_politicians`. No DB write. | `output/district_mapping_dry_run.csv` |
| `dry_run_election_history.py` | Scrapes Wikipedia election result pages (3–4 years per state). Extracts winner, votes, margin. Matches to `verified_politicians`. No DB write. | `output/election_history_dry_run.csv` + `output/track_record_summary_dry_run.csv` |

---

## 6. Risks & Notes

| Risk | Mitigation |
|------|-----------|
| Wikipedia page structure varies by state | Parser uses flexible column detection (same approach as `verify_mlas.py`) |
| Constituency name spelling differs (MyNeta vs Wikipedia) | Fuzzy word-overlap match + flag mismatches as `uncertain` |
| Old election pages (pre-2015) may be sparse or missing | Only scrape years where the Wikipedia article exists; skip gracefully |
| Loser election history unavailable from Wikipedia | Clearly mark source coverage gap in CSV; don't guess |
| Telangana only has 2018 + 2023 (state formed 2014) | Only 2 elections available — note in output |

---

## 7. Recommended Insert Order (after review)

1. Run dry-run scripts → review CSVs
2. Fix any mismatches manually in CSV
3. Run `supabase_schema_v2.sql` (adds 3 new tables)
4. Insert `constituency_district_mapping` from CSV
5. Insert `politician_election_history` from CSV
6. Compute and insert `politician_track_record_summary` from history table

---

## 8. Frontend API endpoints to add (after data is in DB)

```
GET /states/{id}/districts
GET /districts/{district_name}/constituencies
GET /politicians/{id}/elections          ← track record
GET /politicians/{id}/track-record       ← summary stats
```
