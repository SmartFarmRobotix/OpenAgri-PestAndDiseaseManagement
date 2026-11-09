"""
Response type for the scalar-only rule-assessment endpoint. The request body is
`schemas.contract.RiskInput` directly — no wrapper, no reporting envelope —
so the session orchestrator forwards exactly the scalar risk-input fields and nothing else.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from .inference_session import RiskAssessment


class RiskAssessmentEndpointResponse(BaseModel):
    session_id: str
    risk_assessment: Optional[RiskAssessment] = None
    errors: List[str] = Field(default_factory=list)


class ForecastHour(BaseModel):
    """One hour of a day's weather, in the Open-Meteo variable names the
    rules are written against. A value the hour lacks is None: a rule
    condition on it does not hold."""

    time: str = Field(..., min_length=1)
    temperature_2m: Optional[float] = None
    relative_humidity_2m: Optional[float] = None
    precipitation: Optional[float] = None


class ForecastDay(BaseModel):
    """One day of a forecast: either the daily values the Weather service's
    daily forecast carries, or the day's `hours`. A value
    the forecast lacks is None: a rule condition on it does not hold."""

    date: str = Field(..., min_length=1)
    temp_max: Optional[float] = None
    temp_min: Optional[float] = None
    humidity: Optional[float] = None
    precipitation: Optional[float] = None
    hours: Optional[List[ForecastHour]] = Field(default=None, min_length=1)


class ForecastRiskRequest(BaseModel):
    crop: str = Field(..., min_length=1)
    days: List[ForecastDay]


class ForecastDayRisk(BaseModel):
    date: str
    level: str
    rules_triggered: List[str] = Field(default_factory=list)


class ForecastRiskResponse(BaseModel):
    """`days` in date order, or `reason` with no days (never a row of
    `low` for a crop nothing was evaluated for)."""

    days: List[ForecastDayRisk] = Field(default_factory=list)
    reason: Optional[str] = None
