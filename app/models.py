from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime


class State(BaseModel):
    id: int
    name: str
    code: str
    region: Optional[str] = None
    capital: Optional[str] = None
    population: Optional[int] = None
    last_election: Optional[int] = None
    next_election: Optional[int] = None
    ruling_party: Optional[str] = None
    party_seats: Optional[int] = None
    total_seats: Optional[int] = None
    in_power_since: Optional[int] = None

    class Config:
        from_attributes = True


class Politician(BaseModel):
    id: int
    name: str
    party: str
    state_id: int
    constituency: Optional[str] = None
    position: Optional[str] = None
    education: Optional[str] = None
    assets: Optional[float] = None
    liabilities: Optional[float] = None
    criminal_cases: Optional[int] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    image_url: Optional[str] = None
    myneta_url: Optional[str] = None
    verified_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TimelineEvent(BaseModel):
    id: int
    politician_id: int
    type: str
    year: Optional[int] = None
    title: str
    short_description: Optional[str] = None
    source_url: Optional[str] = None
    source_name: Optional[str] = None
    confidence: Optional[str] = None

    class Config:
        from_attributes = True


class Achievement(BaseModel):
    id: int
    politician_id: int
    title: str
    description: str
    source_url: Optional[str] = None
    published_date: Optional[date] = None
    category: Optional[str] = None

    class Config:
        from_attributes = True


class PoliticianDetail(Politician):
    state: Optional[State] = None
    achievements: list[Achievement] = []
    timeline: list[TimelineEvent] = []


class PaginatedPoliticians(BaseModel):
    total: int
    items: list["Politician"]


class PaginatedAchievements(BaseModel):
    total: int
    items: list["Achievement"]


class StatePageData(BaseModel):
    summary: "StateSummary"
    cm: Optional[Politician] = None
    politicians: PaginatedPoliticians


class PoliticianPageData(BaseModel):
    politician: PoliticianDetail
    related: list[Politician] = []


class StateSummary(BaseModel):
    state_id: int
    state_name: str
    state_code: str
    capital: Optional[str] = None
    region: Optional[str] = None
    population: Optional[int] = None
    total_seats: Optional[int] = None
    ruling_party: Optional[str] = None
    in_power_since: Optional[int] = None
    last_election: Optional[int] = None
    next_election: Optional[int] = None
    cm_name: Optional[str] = None
    cm_party: Optional[str] = None
    cm_constituency: Optional[str] = None
    total_verified: int = 0
    mla_count: int = 0
    cabinet_count: int = 0
    with_criminal_cases: int = 0
    criminal_case_pct: float = 0.0
    avg_assets_cr: Optional[float] = None
    party_breakdown: dict[str, int] = {}
