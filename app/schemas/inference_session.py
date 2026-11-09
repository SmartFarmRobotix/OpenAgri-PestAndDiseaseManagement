"""
`RiskAssessment` is imported by both `schemas/risk_assessment.py` (the response model of the
`/api/v1/risk-assessment/` endpoint) and `utils/risk_assessment_core.py` (the shared rule evaluation). The
module keeps its historical name so that both import paths stay stable.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class RiskAssessment(BaseModel):
    level: str
    details: Optional[str] = None
    provenance: Dict[str, Any] = Field(default_factory=dict)
