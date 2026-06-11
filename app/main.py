from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from postgrest.exceptions import APIError

from app.database import get_db
from app.models import Politician, PoliticianDetail, State, Achievement, StateSummary
from app.scraper import scrape_pib_releases, scrape_myneta_politician
from app.state_config import FEATURED_STATE_IDS


def _db_error(exc: APIError) -> HTTPException:
    if exc.code == "PGRST205":
        return HTTPException(
            status_code=503,
            detail="Database table not found. Run supabase_schema.sql in your Supabase SQL editor first.",
        )
    return HTTPException(status_code=500, detail=exc.message)


app = FastAPI(
    title="NetaLog API",
    description="Track Indian politicians — achievements, assets, criminal cases and more.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

@app.get("/states", response_model=list[State], tags=["States"])
def get_states():
    db = get_db()
    try:
        result = db.table("states").select("*").order("name").execute()
    except APIError as exc:
        raise _db_error(exc)
    return result.data


@app.get("/states/featured", response_model=list[State], tags=["States"])
def get_featured_states():
    """Return the 8 featured states with full metadata."""
    db = get_db()
    try:
        result = db.table("states").select("*").in_("id", FEATURED_STATE_IDS).order("name").execute()
    except APIError as exc:
        raise _db_error(exc)
    return result.data


@app.get("/states/{state_id}/summary", response_model=StateSummary, tags=["States"])
def get_state_summary(state_id: int):
    """Per-state dashboard stats: CM, verified counts, criminal case %, avg assets."""
    db = get_db()
    try:
        state_res = db.table("states").select("*").eq("id", state_id).single().execute()
    except APIError as exc:
        raise _db_error(exc)
    if not state_res.data:
        raise HTTPException(status_code=404, detail="State not found")
    s = state_res.data

    try:
        pols = db.table("verified_politicians").select(
            "id,name,party,position,constituency,assets,criminal_cases"
        ).eq("state_id", state_id).execute().data or []
    except APIError as exc:
        raise _db_error(exc)

    cm = next((p for p in pols if p["position"] == "Chief Minister"), None)
    mla_count     = sum(1 for p in pols if p["position"] == "MLA")
    cabinet_count = sum(1 for p in pols if p["position"] == "Cabinet Minister")
    with_crimes   = sum(1 for p in pols if p.get("criminal_cases") and int(p["criminal_cases"]) > 0)
    total         = len(pols)
    assets_vals   = [p["assets"] for p in pols if p.get("assets") is not None]
    avg_assets_cr = round(sum(assets_vals) / len(assets_vals) / 1e7, 2) if assets_vals else None

    return StateSummary(
        state_id=state_id,
        state_name=s["name"],
        state_code=s["code"],
        capital=s.get("capital"),
        region=s.get("region"),
        population=s.get("population"),
        total_seats=s.get("total_seats"),
        ruling_party=s.get("ruling_party"),
        in_power_since=s.get("in_power_since"),
        last_election=s.get("last_election"),
        next_election=s.get("next_election"),
        cm_name=cm["name"] if cm else None,
        cm_party=cm["party"] if cm else None,
        cm_constituency=cm["constituency"] if cm else None,
        total_verified=total,
        mla_count=mla_count,
        cabinet_count=cabinet_count,
        with_criminal_cases=with_crimes,
        criminal_case_pct=round(with_crimes / total * 100, 1) if total else 0.0,
        avg_assets_cr=avg_assets_cr,
    )


@app.get("/states/{state_id}", response_model=State, tags=["States"])
def get_state(state_id: int):
    db = get_db()
    try:
        result = db.table("states").select("*").eq("id", state_id).single().execute()
    except APIError as exc:
        raise _db_error(exc)
    if not result.data:
        raise HTTPException(status_code=404, detail="State not found")
    return result.data


# ---------------------------------------------------------------------------
# Politicians
# ---------------------------------------------------------------------------

@app.get("/politicians", response_model=list[Politician], tags=["Politicians"])
def get_politicians(
    state_id: int | None = Query(None, description="Filter by state"),
    party: str | None = Query(None, description="Filter by party name"),
    position: str | None = Query(None, description="Filter by position (exact match)"),
    search: str | None = Query(None, description="Search by name"),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    db = get_db()
    query = db.table("verified_politicians").select("*")

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
    return result.data


@app.get("/politicians/{politician_id}", response_model=PoliticianDetail, tags=["Politicians"])
def get_politician(politician_id: int):
    db = get_db()
    try:
        pol_result = db.table("verified_politicians").select("*").eq("id", politician_id).single().execute()
    except APIError as exc:
        raise _db_error(exc)

    if not pol_result.data:
        raise HTTPException(status_code=404, detail="Politician not found")

    politician = pol_result.data

    try:
        state_result = db.table("states").select("*").eq("id", politician["state_id"]).single().execute()
        ach_result = (
            db.table("achievements")
            .select("*")
            .eq("politician_id", politician_id)
            .order("published_date", desc=True)
            .execute()
        )
    except APIError as exc:
        raise _db_error(exc)

    return {
        **politician,
        "state": state_result.data,
        "achievements": ach_result.data or [],
    }


# ---------------------------------------------------------------------------
# Achievements
# ---------------------------------------------------------------------------

@app.get("/achievements", response_model=list[Achievement], tags=["Achievements"])
def get_achievements(
    politician_id: int | None = Query(None),
    category: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    db = get_db()
    query = db.table("achievements").select("*")

    if politician_id is not None:
        query = query.eq("politician_id", politician_id)
    if category:
        query = query.eq("category", category)

    try:
        result = query.order("published_date", desc=True).range(offset, offset + limit - 1).execute()
    except APIError as exc:
        raise _db_error(exc)
    return result.data


# ---------------------------------------------------------------------------
# Scraper endpoints
# ---------------------------------------------------------------------------

@app.get("/scrape/pib", tags=["Scraper"])
def scrape_pib(query: str = Query("", description="Search keyword"), max_results: int = Query(20, le=50)):
    """Fetch live press releases from PIB for a given keyword."""
    try:
        releases = scrape_pib_releases(query=query, max_results=max_results)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"count": len(releases), "results": releases}


@app.get("/scrape/myneta", tags=["Scraper"])
def scrape_myneta(url: str = Query(..., description="Full MyNeta candidate page URL")):
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
