"""
Scalar-only rule-assessment endpoint.

The request body is `RiskInput` directly (session_id, infestation_score, crop, growth_stage,
time_start/time_end, latitude/longitude, position_accuracy_m): no reporting envelope, no contract wrapper,
nothing report-facing. This is the endpoint the session orchestrator calls via Gatekeeper for the PDM path of
the dual risk assessment; it does not forward anything to the Reporting service (that composition belongs to
the orchestrator). The pure rule evaluation lives in
`utils.risk_assessment_core.compute_risk_assessment_from_weather`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

import crud
from api import deps
from core import settings
from schemas.contract import RiskInput
from schemas.risk_assessment import (
    ForecastRiskRequest,
    ForecastRiskResponse,
    RiskAssessmentEndpointResponse,
)
from utils import TimeUnit, fetch_weather_data, openmeteo_friendly_variables
from utils.risk_assessment_core import (
    compute_risk_assessment_from_weather,
    evaluate_forecast_days,
    parse_iso_date,
)

router = APIRouter()


@router.post(
    "/risk-assessment/",
    response_model=RiskAssessmentEndpointResponse,
    dependencies=[Depends(deps.is_using_gatekeeper)],
)
def assess_risk(
    risk_input: RiskInput,
    access_token: str = Depends(deps.get_jwt),
    db=Depends(deps.get_db),
) -> RiskAssessmentEndpointResponse:
    errors: list[str] = []

    start_date = parse_iso_date(risk_input.time_start)
    end_date = parse_iso_date(risk_input.time_end)
    if start_date > end_date:
        raise HTTPException(
            status_code=400,
            detail="time_start must be earlier than time_end",
        )

    weather_enabled = settings.PDM_WEATHER_ENABLED

    pest_models = crud.pest_model.get_for_crop(db=db, crop=risk_input.crop)
    if not pest_models:
        errors.append(
            "No weather rules configured for this crop; risk assessment degraded to score-only."
        )

    variables_for_weather: list[str] = []
    for pest_model in pest_models or []:
        for rule in pest_model.rules:
            for cond in rule.conditions:
                variables_for_weather.append(cond.unit.name)
    variables_for_weather = list(set(variables_for_weather))
    variables_for_weather_openmeteo = [
        openmeteo_friendly_variables[var]
        for var in variables_for_weather
        if var in openmeteo_friendly_variables
    ]
    if not variables_for_weather_openmeteo:
        variables_for_weather_openmeteo = ["temperature_2m", "relative_humidity_2m"]

    coordinates_available = risk_input.latitude is not None and risk_input.longitude is not None
    weather_unavailable = False
    if not coordinates_available:
        weather_hourly = {"data": [], "source": "none"}
        errors.append(
            "Session coordinates are absent; risk assessment degraded to score-only "
            "(position unavailable, no weather lookup attempted)."
        )
    elif weather_enabled:
        try:
            weather_hourly = fetch_weather_data(
                latitude=risk_input.latitude,
                longitude=risk_input.longitude,
                access_token=access_token,
                start_date=start_date,
                end_date=end_date,
                variables=variables_for_weather_openmeteo,
                how_often=TimeUnit.HOURLY,
            )
            if "data" not in weather_hourly:
                weather_unavailable = True
        except HTTPException:
            weather_unavailable = True

        if weather_unavailable:
            weather_hourly = {"data": [], "source": "none"}
            errors.append(
                "WeatherService/OWDS unreachable or returned no data for this "
                "session window; risk assessment degraded to score-only "
                "(weather unavailable, distinct from weather-disabled)."
            )
    else:
        weather_hourly = {"data": [], "source": "none"}
        errors.append(
            "Weather mode not applicable for this deployment "
            "(PDM_WEATHER_ENABLED=false); risk assessment based on "
            "inference score only."
        )

    computation = compute_risk_assessment_from_weather(
        risk_input=risk_input,
        weather_hourly=weather_hourly,
        pest_models=pest_models,
        weather_enabled=weather_enabled,
        coordinates_available=coordinates_available,
        weather_unavailable=weather_unavailable,
        parcel_id=None,
        no_pest_model_for_crop=not pest_models,
    )
    if computation.risk_assessment is None:
        errors.append("No risk assessment produced (no infestation score).")

    return RiskAssessmentEndpointResponse(
        session_id=risk_input.session_id,
        risk_assessment=computation.risk_assessment,
        errors=errors,
    )


@router.post(
    "/risk-assessment/forecast/",
    response_model=ForecastRiskResponse,
    dependencies=[Depends(deps.is_using_gatekeeper), Depends(deps.get_jwt)],
)
def assess_forecast_risk(
    request: ForecastRiskRequest,
    db=Depends(deps.get_db),
) -> ForecastRiskResponse:
    """The configured pest models' rules
    evaluated on daily forecast values (the outlook of a session report), or,
    for a day that carries its `hours`, hour by hour with the same per-rule
    check the session assessment applies to its hourly window.
    Nothing is fetched, no infestation score is combined, and the session
    assessment above is untouched. Pest models are selected by `crop`,
    exactly as the session assessment selects them.
    """
    pest_models = crud.pest_model.get_for_crop(db=db, crop=request.crop)
    if not pest_models:
        return ForecastRiskResponse(reason="no_pest_models_configured")
    days = evaluate_forecast_days(
        pest_models, [day.model_dump() for day in request.days]
    )
    return ForecastRiskResponse(days=days)
