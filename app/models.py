from pydantic import BaseModel
from typing import Optional
from datetime import date


class State(BaseModel):
    id: int
    name: str
    code: str
    region: Optional[str] = None

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

    class Config:
        from_attributes = True


class Achievement(BaseModel):
    id: int
    politician_id: int
    title: str
    description: str
    source_url: Optional[str] = None
    published_date: Optional[date] = None
    category: Optional[str] = None  # e.g. "infrastructure", "health", "education"

    class Config:
        from_attributes = True


class PoliticianDetail(Politician):
    state: Optional[State] = None
    achievements: list[Achievement] = []
