import time
from collections import Counter, defaultdict

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from postgrest.exceptions import APIError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.database import get_db
from app.models import (
    Achievement,
    PaginatedAchievements,
    PaginatedPoliticians,
    Politician,
    PoliticianDetail,
    PoliticianPageData,
    State,
    StatePageData,
    StateSummary,
)
from app.scraper import scrape_pib_releases, scrape_myneta_politician
from app.state_config import FEATURED_STATE_IDS

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

limiter = Limiter(key_func=get_remote_address)

# ---------------------------------------------------------------------------
# In-memory cache (TTL: 5 minutes)
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[float, object]] = {}
_CACHE_TTL = 300


def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and time.monotonic() - entry[0] < _CACHE_TTL:
        return entry[1]
    return None


def _cache_set(key: str, val):
    _cache[key] = (time.monotonic(), val)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db_error(exc: APIError) -> HTTPException:
    if exc.code == "PGRST205":
        return HTTPException(
            status_code=503,
            detail="Database table not found. Run supabase_schema.sql first.",
        )
    return HTTPException(status_code=500, detail=exc.message)


def _compute_summary(state: dict, pols: list[dict]) -> StateSummary:
    """Calculate dashboard stats for one state from its verified_politicians rows."""
    cm            = next((p for p in pols if p["position"] == "Chief Minister"), None)
    mla_count     = sum(1 for p in pols if p["position"] == "MLA")
    cabinet_count = sum(1 for p in pols if p["position"] == "Cabinet Minister")
    total         = len(pols)
    with_crimes   = sum(1 for p in pols if p.get("criminal_cases") and int(p["criminal_cases"]) > 0)
    assets_vals   = [p["assets"] for p in pols if p.get("assets") is not None]
    avg_assets_cr = round(sum(assets_vals) / len(assets_vals) / 1e7, 2) if assets_vals else None
    party_breakdown = dict(Counter(p["party"] for p in pols if p.get("party")).most_common(6))

    return StateSummary(
        state_id=state["id"],
        state_name=state["name"],
        state_code=state["code"],
        capital=state.get("capital"),
        region=state.get("region"),
        population=state.get("population"),
        total_seats=state.get("total_seats"),
        ruling_party=state.get("ruling_party"),
        in_power_since=state.get("in_power_since"),
        last_election=state.get("last_election"),
        next_election=state.get("next_election"),
        cm_name=cm["name"] if cm else None,
        cm_party=cm["party"] if cm else None,
        cm_constituency=cm["constituency"] if cm else None,
        total_verified=total,
        mla_count=mla_count,
        cabinet_count=cabinet_count,
        with_criminal_cases=with_crimes,
        criminal_case_pct=round(with_crimes / total * 100, 1) if total else 0.0,
        avg_assets_cr=avg_assets_cr,
        party_breakdown=party_breakdown,
    )


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="NetaLog API",
    description="Track Indian politicians — achievements, assets, criminal cases and more.",
    version="1.0.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# States — list / featured
# ---------------------------------------------------------------------------

@app.get("/states", response_model=list[State], tags=["States"])
@limiter.limit("60/minute")
def get_states(request: Request):
    """All 30 states with metadata."""
    cached = _cache_get("states_all")
    if cached is not None:
        return cached
    db = get_db()
    try:
        data = db.table("states").select("*").order("name").execute().data
    except APIError as exc:
        raise _db_error(exc)
    _cache_set("states_all", data)
    return data


@app.get("/states/featured", response_model=list[State], tags=["States"])
@limiter.limit("60/minute")
def get_featured_states(request: Request):
    """The 8 featured states with full metadata."""
    db = get_db()
    try:
        return db.table("states").select("*").in_("id", FEATURED_STATE_IDS).order("name").execute().data
    except APIError as exc:
        raise _db_error(exc)


@app.get("/states/featured/summary", response_model=list[StateSummary], tags=["States"])
@limiter.limit("60/minute")
def get_featured_summary(request: Request):
    """
    Homepage dashboard — one call returns summary cards for all 8 featured states.
    Calculates live from verified_politicians + states. No AI, no new table.
    """
    cached = _cache_get("featured_summary")
    if cached is not None:
        return cached

    db = get_db()
    try:
        states_data = db.table("states").select("*").in_("id", FEATURED_STATE_IDS).execute().data or []
        pols_data   = db.table("verified_politicians").select(
            "id,name,party,position,constituency,assets,criminal_cases,state_id"
        ).in_("state_id", FEATURED_STATE_IDS).execute().data or []
    except APIError as exc:
        raise _db_error(exc)

    states_map = {s["id"]: s for s in states_data}
    pols_by_state: dict[int, list] = defaultdict(list)
    for p in pols_data:
        pols_by_state[p["state_id"]].append(p)

    result = [
        _compute_summary(states_map[sid], pols_by_state[sid])
        for sid in FEATURED_STATE_IDS
        if sid in states_map
    ]
    _cache_set("featured_summary", result)
    return result


# ---------------------------------------------------------------------------
# States — single / sub-resources
# ---------------------------------------------------------------------------

@app.get("/states/{state_id}", response_model=State, tags=["States"])
@limiter.limit("60/minute")
def get_state(request: Request, state_id: int):
    """Single state with full metadata."""
    db = get_db()
    try:
        result = db.table("states").select("*").eq("id", state_id).limit(1).execute()
    except APIError as exc:
        raise _db_error(exc)
    if not result.data:
        raise HTTPException(status_code=404, detail="State not found")
    return result.data[0]


@app.get("/states/{state_id}/summary", response_model=StateSummary, tags=["States"])
@limiter.limit("60/minute")
def get_state_summary(request: Request, state_id: int):
    """Dashboard stats for one state: CM, counts, criminal case %, avg assets, party breakdown."""
    db = get_db()
    try:
        state_res = db.table("states").select("*").eq("id", state_id).limit(1).execute()
    except APIError as exc:
        raise _db_error(exc)
    if not state_res.data:
        raise HTTPException(status_code=404, detail="State not found")
    state_res.data = state_res.data[0]

    try:
        pols = db.table("verified_politicians").select(
            "id,name,party,position,constituency,assets,criminal_cases,state_id"
        ).eq("state_id", state_id).execute().data or []
    except APIError as exc:
        raise _db_error(exc)

    return _compute_summary(state_res.data, pols)


@app.get("/states/{state_id}/politicians", response_model=PaginatedPoliticians, tags=["States"])
@limiter.limit("60/minute")
def get_state_politicians(
    request: Request,
    state_id: int,
    position: str | None = Query(None, description="MLA | Cabinet Minister | Chief Minister"),
    party:    str | None = Query(None, description="Party name (partial match)"),
    search:   str | None = Query(None, description="Search by name"),
    limit:  int = Query(50, le=200),
    offset: int = Query(0),
):
    """All verified politicians for a state. Supports position/party/name filters + pagination."""
    db = get_db()
    query = db.table("verified_politicians").select("*", count="exact").eq("state_id", state_id)
    if position:
        query = query.eq("position", position)
    if party:
        query = query.ilike("party", f"%{party}%")
    if search:
        query = query.ilike("name", f"%{search}%")
    try:
        result = query.order("name").range(offset, offset + limit - 1).execute()
    except APIError as exc:
        raise _db_error(exc)
    return {"total": result.count or 0, "items": result.data or []}


@app.get("/states/{state_id}/party-breakdown", tags=["States"])
@limiter.limit("60/minute")
def get_party_breakdown(request: Request, state_id: int):
    """
    Party seat counts for a state from verified_politicians.
    Returns { party: count } sorted by count descending.
    """
    db = get_db()
    try:
        rows = db.table("verified_politicians").select("party").eq("state_id", state_id).execute().data or []
    except APIError as exc:
        raise _db_error(exc)
    counts = Counter(r["party"] for r in rows if r.get("party"))
    return {
        "state_id": state_id,
        "total": len(rows),
        "breakdown": dict(counts.most_common()),
    }


# ---------------------------------------------------------------------------
# Politicians — global search + detail
# ---------------------------------------------------------------------------

@app.get("/politicians", response_model=PaginatedPoliticians, tags=["Politicians"])
@limiter.limit("60/minute")
def get_politicians(
    request: Request,
    state_id: int | None = Query(None, description="Filter by state"),
    party:    str | None = Query(None, description="Filter by party name"),
    position: str | None = Query(None, description="Filter by position (exact match)"),
    search:   str | None = Query(None, description="Search by name"),
    limit:  int = Query(50, le=200),
    offset: int = Query(0),
):
    """Search verified politicians across all states."""
    db = get_db()
    query = db.table("verified_politicians").select("*", count="exact")
    if state_id is not None:
        query = query.eq("state_id", state_id)
    if party:
        query = query.ilike("party", f"%{party}%")
    if position:
        query = query.eq("position", position)
    if search:
        query = query.ilike("name", f"%{search}%")
    try:
        result = query.order("name").range(offset, offset + limit - 1).execute()
    except APIError as exc:
        raise _db_error(exc)
    return {"total": result.count or 0, "items": result.data or []}


@app.get("/politicians/{politician_id}", response_model=PoliticianDetail, tags=["Politicians"])
@limiter.limit("60/minute")
def get_politician(request: Request, politician_id: int):
    """Full politician profile — includes state info and achievements."""
    db = get_db()
    try:
        pol_res = db.table("verified_politicians").select("*").eq("id", politician_id).limit(1).execute()
    except APIError as exc:
        raise _db_error(exc)
    if not pol_res.data:
        raise HTTPException(status_code=404, detail="Politician not found")

    p = pol_res.data[0]
    try:
        state_res = db.table("states").select("*").eq("id", p["state_id"]).limit(1).execute()
        ach_res   = (
            db.table("achievements").select("*")
            .eq("politician_id", politician_id)
            .order("published_date", desc=True)
            .execute()
        )
    except APIError as exc:
        raise _db_error(exc)

    return {**p, "state": state_res.data[0] if state_res.data else None, "achievements": ach_res.data or []}


# ---------------------------------------------------------------------------
# Achievements
# ---------------------------------------------------------------------------

@app.get("/achievements", response_model=PaginatedAchievements, tags=["Achievements"])
@limiter.limit("60/minute")
def get_achievements(
    request: Request,
    politician_id: int | None = Query(None),
    category:      str | None = Query(None),
    limit:  int = Query(50, le=200),
    offset: int = Query(0),
):
    db = get_db()
    query = db.table("achievements").select("*", count="exact")
    if politician_id is not None:
        query = query.eq("politician_id", politician_id)
    if category:
        query = query.eq("category", category)
    try:
        result = query.order("published_date", desc=True).range(offset, offset + limit - 1).execute()
    except APIError as exc:
        raise _db_error(exc)
    return {"total": result.count or 0, "items": result.data or []}


# ---------------------------------------------------------------------------
# Combined page-data endpoints (reduce frontend round trips to 1)
# ---------------------------------------------------------------------------

@app.get("/states/{state_id}/page-data", response_model=StatePageData, tags=["States"])
@limiter.limit("60/minute")
def get_state_page_data(request: Request, state_id: int):
    """
    Single call for the state page: summary stats + CM + first 24 politicians.
    Replaces 3 separate frontend requests. Cached per state for 5 minutes.
    """
    cache_key = f"state_page_{state_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    db = get_db()
    try:
        state_res = db.table("states").select("*").eq("id", state_id).limit(1).execute()
    except APIError as exc:
        raise _db_error(exc)
    if not state_res.data:
        raise HTTPException(status_code=404, detail="State not found")
    state = state_res.data[0]

    try:
        pols_res = (
            db.table("verified_politicians")
            .select("*", count="exact")
            .eq("state_id", state_id)
            .order("name")
            .execute()
        )
    except APIError as exc:
        raise _db_error(exc)

    all_pols = pols_res.data or []
    total    = pols_res.count or 0
    summary  = _compute_summary(state, all_pols)
    cm       = next((p for p in all_pols if p.get("position") == "Chief Minister"), None)

    result = StatePageData(
        summary=summary,
        cm=cm,
        politicians={"total": total, "items": all_pols[:24]},
    )
    _cache_set(cache_key, result)
    return result


@app.get("/politicians/{politician_id}/page-data", response_model=PoliticianPageData, tags=["Politicians"])
@limiter.limit("60/minute")
def get_politician_page_data(request: Request, politician_id: int):
    """
    Single call for the politician page: full profile + state + achievements + 4 related politicians.
    Replaces 2 separate frontend requests. Cached per politician for 5 minutes.
    """
    cache_key = f"pol_page_{politician_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    db = get_db()
    try:
        pol_res = db.table("verified_politicians").select("*").eq("id", politician_id).limit(1).execute()
    except APIError as exc:
        raise _db_error(exc)
    if not pol_res.data:
        raise HTTPException(status_code=404, detail="Politician not found")

    p = pol_res.data[0]
    try:
        state_res   = db.table("states").select("*").eq("id", p["state_id"]).limit(1).execute()
        ach_res     = (
            db.table("achievements").select("*")
            .eq("politician_id", politician_id)
            .order("published_date", desc=True)
            .execute()
        )
        related_res = (
            db.table("verified_politicians").select("*")
            .eq("state_id", p["state_id"])
            .neq("id", politician_id)
            .order("name")
            .limit(4)
            .execute()
        )
    except APIError as exc:
        raise _db_error(exc)

    politician = {
        **p,
        "state":        state_res.data[0] if state_res.data else None,
        "achievements": ach_res.data or [],
    }
    result = PoliticianPageData(
        politician=politician,
        related=related_res.data or [],
    )
    _cache_set(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# Scraper endpoints
# ---------------------------------------------------------------------------

@app.get("/scrape/pib", tags=["Scraper"])
@limiter.limit("60/minute")
def scrape_pib(
    request: Request,
    query: str = Query("", description="Search keyword"),
    max_results: int = Query(20, le=50),
):
    """Fetch live press releases from PIB for a given keyword."""
    try:
        releases = scrape_pib_releases(query=query, max_results=max_results)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"count": len(releases), "results": releases}


@app.get("/scrape/myneta", tags=["Scraper"])
@limiter.limit("60/minute")
def scrape_myneta(request: Request, url: str = Query(..., description="Full MyNeta candidate page URL")):
    """Scrape a politician's data from MyNeta by URL."""
    try:
        data = scrape_myneta_politician(url)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return data


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["System"])
def health():
    return {"status": "ok", "service": "NetaLog API"}
