"""
Shared weather-rule risk-assessment computation.

The pure rule evaluation behind the `/api/v1/risk-assessment/` endpoint (scalar `RiskInput` only, no reporting
envelope, no forwarding to the Reporting service), kept as its own module because it is DB-free and HTTP-free
logic worth testing in isolation.

This module does no I/O (no weather fetch, no DB query, no HTTP call): the caller fetches `weather_hourly` and
`pest_models` itself and passes the results in, so the endpoint keeps owning (and, in tests, mocking) its own
boundaries.
"""

from __future__ import annotations

import datetime
import statistics
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from core.version import PDM_APP_VERSION
from init.pest_model_seed import ruleset_version_by_name
from schemas.contract import GrowthStage, RiskInput
from schemas.inference_session import RiskAssessment
from .risk_index import prob_values, _safe_compare
from .wdutils import openmeteo_friendly_variables
from .risk_index import calculate_risk_index_probability_wd


NN_HIGH_FLOOR = 0.70
NN_MODERATE_FLOOR = 0.40


def nn_verdict(score: Optional[float]) -> str:
    """Map a 0-1 infestation score to a human-readable band."""
    if score is None:
        return "Unknown"
    if score >= NN_HIGH_FLOOR:
        return "High"
    if score >= NN_MODERATE_FLOOR:
        return "Moderate"
    return "Low"


VERDICT_TO_RISK_LEVEL = {"High": "High", "Moderate": "Medium", "Low": "Low"}

BRVCBR_HIGH_CONSEQUENCE_GROWTH_STAGES = frozenset({"BBCH-40", "BBCH-49"})


def _human_window(time_start: str, time_end: str) -> str:
    """Human-readable form of the assessment window for the report-facing
    `details` sentence ONLY — provenance keeps the raw ISO pair verbatim
    (machine-facing, test-pinned). Fail-open: an unparseable timestamp
    returns the raw pair unchanged; a presentation formatter never raises
    and never fabricates a date."""
    try:
        start = datetime.datetime.fromisoformat(time_start.replace("Z", "+00:00"))
        end = datetime.datetime.fromisoformat(time_end.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return f"{time_start}  →  {time_end}"
    tz = "UTC" if start.utcoffset() in (None, datetime.timedelta(0)) else start.strftime("%z")
    if start.date() == end.date():
        return (
            f"{start.strftime('%d %b %Y')}, "
            f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')} {tz}"
        )
    return (
        f"{start.strftime('%d %b %Y %H:%M')} {tz} - "
        f"{end.strftime('%d %b %Y %H:%M')} {tz}"
    )


def parse_iso_date(value: str) -> datetime.date:
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(normalized).date()
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid ISO-8601 date/time: {value}",
        ) from exc


def _unit_name(cond) -> str:
    return cond.unit.name if hasattr(cond.unit, "name") else str(cond.unit)


def format_rule_conditions(rule) -> str:
    """A rule's conditions as text, in the Open-Meteo variable names the
    provenance carries (e.g. ``temperature_2m >= 20 AND ...``)."""
    parts: List[str] = []
    for cond in rule.conditions:
        unit_name = _unit_name(cond)
        friendly = openmeteo_friendly_variables.get(unit_name, unit_name)
        parts.append(f"{friendly} {cond.operator.symbol} {cond.value}")
    return "  AND  ".join(parts)


def rule_holds(rule, value_for) -> bool:
    """
    Whether every condition of `rule` holds. `value_for(value_key, symbol)`
    returns the number a condition on the Open-Meteo variable `value_key`
    with operator `symbol` is compared against, or None when there is none
    (the condition then does not hold). The caller decides what a value
    means: one hour's reading (`build_risk_reasoning`) or a daily
    aggregate (`evaluate_forecast_days`).
    """
    for cond in rule.conditions:
        value_key = openmeteo_friendly_variables.get(_unit_name(cond))
        if not value_key:
            return False
        left = value_for(value_key, cond.operator.symbol)
        if cond.value is None or not _safe_compare(left, cond.operator.symbol, float(cond.value)):
            return False
    return True


def build_risk_reasoning(
    infestation_score: Optional[float],
    pest_models: list,
    risk_output: Optional[dict],
    weather_hourly: dict,
    time_start: str,
    time_end: str,
) -> Dict[str, Any]:
    """
    Produce a human-readable risk reasoning dict that explains how the NN
    detection score and the PDMS weather-rule evaluation combine to yield
    the final risk assessment. Five of its fields leave this module, read by
    `compute_risk_assessment_from_weather` below into the returned
    `RiskAssessment`: `combined_level` and `weather_rule_level_raw`, and `pdms_rules_evaluated`,
    `observed_weather` and `nn_verdict`, which land in the weather-rule
    path's provenance. The rest (`nn_note`, `narrative`) are kept for compatibility; the session orchestrator composes its own
    report content from `RiskAssessment.provenance`.
    """
    score = infestation_score or 0.0
    verdict = nn_verdict(infestation_score)

    nn_note = (
        f"The neural network (BugFinderAI inference service) detected pest "
        f"activity with an infestation score of {score:.2f}, placing the detection "
        f"in the {verdict.upper()} confidence band "
        f"({f'score >= {NN_HIGH_FLOOR:.2f}' if score >= NN_HIGH_FLOOR else f'score >= {NN_MODERATE_FLOOR:.2f}' if score >= NN_MODERATE_FLOOR else f'score < {NN_MODERATE_FLOOR:.2f}'})."
    )

    hourly_data = weather_hourly.get("data") or []
    temp_vals: List[float] = []
    hum_vals: List[float] = []
    precip_vals: List[float] = []
    for hour in hourly_data:
        vals = hour.get("values") or (hour if isinstance(hour, dict) else {})
        t = vals.get("temperature_2m")
        h = vals.get("relative_humidity_2m")
        p = vals.get("precipitation")
        if t is not None:
            temp_vals.append(float(t))
        if h is not None:
            hum_vals.append(float(h))
        if p is not None:
            precip_vals.append(float(p))

    observed: Dict[str, Any] = {
        "period": f"{time_start} to {time_end}",
    }
    if temp_vals:
        observed["temperature_2m_avg_C"] = round(statistics.mean(temp_vals), 1)
        observed["temperature_2m_max_C"] = round(max(temp_vals), 1)
        observed["temperature_2m_min_C"] = round(min(temp_vals), 1)
    if hum_vals:
        observed["relative_humidity_2m_avg_pct"] = round(statistics.mean(hum_vals), 1)
    if precip_vals:
        observed["precipitation_mm"] = round(sum(precip_vals), 2)

    pdms_rules_evaluated: List[Dict[str, Any]] = []
    for pm in (pest_models or []):
        rule_summaries: List[Dict[str, Any]] = []
        total_hours = len(hourly_data)

        for rule in pm.rules:
            conditions_str = format_rule_conditions(rule)

            hours_triggered = 0
            for hour in hourly_data:
                vals = hour.get("values") or {}
                if rule_holds(rule, lambda key, _symbol, vals=vals: vals.get(key)):
                    hours_triggered += 1

            rule_summaries.append({
                "level": rule.probability_value,
                "conditions": conditions_str,
                "hours_triggered": hours_triggered,
                "hours_total": total_hours,
            })

        dominant = "low"
        if risk_output:
            for entry in risk_output.get("@graph", []):
                for member in entry.get("hasMember", []):
                    lvl = str(member.get("hasSimpleResult", "")).lower()
                    if lvl in prob_values and prob_values[lvl] > prob_values[dominant]:
                        dominant = lvl

        pdms_rules_evaluated.append({
            "model": pm.name,
            "rules": rule_summaries,
            "dominant_level": dominant,
        })

    weather_dominant = "low"
    for entry_data in pdms_rules_evaluated:
        lvl = entry_data.get("dominant_level", "low")
        if lvl in prob_values and prob_values[lvl] > prob_values[weather_dominant]:
            weather_dominant = lvl

    nn_level_map = {"High": "high", "Moderate": "moderate", "Low": "low"}
    nn_as_prob = nn_level_map.get(verdict, "low")
    combined_prob = verdict if prob_values[nn_as_prob] >= prob_values[weather_dominant] else weather_dominant.capitalize()

    weather_part = ""
    if temp_vals and hum_vals:
        weather_part = (
            f"Observed weather during the scan window showed an average temperature "
            f"of {observed.get('temperature_2m_avg_C', '?')} °C (max "
            f"{observed.get('temperature_2m_max_C', '?')} °C) and average relative "
            f"humidity of {observed.get('relative_humidity_2m_avg_pct', '?')} %"
        )
        if precip_vals:
            weather_part += f", with {observed.get('precipitation_mm', 0.0)} mm precipitation"
        weather_part += ". "

    rule_part = ""
    if pdms_rules_evaluated:
        fired_high = sum(
            r["hours_triggered"]
            for e in pdms_rules_evaluated
            for r in e["rules"]
            if str(r.get("level", "")).lower() == "high"
        )
        total_h = hourly_data.__len__()
        if fired_high and total_h:
            rule_part = (
                f"The PDMS rule engine flagged {fired_high} of {total_h} hours as HIGH-risk "
                f"based on temperature and humidity thresholds. "
            )

    narrative = (
        f"The neural network returned a {verdict.lower()} detection confidence "
        f"(score {score:.2f}). "
        f"{weather_part}"
        f"{rule_part}"
        f"Combining the NN detection signal with the weather-rule evaluation "
        f"yields a final risk assessment of {combined_prob.upper()}."
    )

    return {
        "nn_score": score,
        "nn_verdict": verdict,
        "nn_note": nn_note,
        "pdms_rules_evaluated": pdms_rules_evaluated,
        "observed_weather": observed,
        "weather_window": f"{time_start}  →  {time_end}",
        "combined_level": combined_prob,
        "weather_rule_level_raw": weather_dominant,
        "narrative": narrative,
    }


_LOWER_BOUND_OPERATORS = frozenset({">", ">="})
_UPPER_BOUND_OPERATORS = frozenset({"<", "<="})


def _daily_value_for(day: Dict[str, Any]):
    """`rule_holds`' value resolver for one forecast day (temp_max, temp_min,
    humidity, precipitation). Temperature under an operator that is neither a
    lower nor an upper bound has no conservative aggregate, and a variable
    the forecast does not carry has no value: the condition does not hold."""

    def value_for(value_key: str, symbol: str) -> Optional[float]:
        if value_key == "temperature_2m":
            if symbol in _LOWER_BOUND_OPERATORS:
                return day.get("temp_max")
            if symbol in _UPPER_BOUND_OPERATORS:
                return day.get("temp_min")
            return None
        if value_key == "relative_humidity_2m":
            return day.get("humidity")
        if value_key == "precipitation":
            return day.get("precipitation")
        return None

    return value_for


def _hourly_value_for(hour: Dict[str, Any]):
    """`rule_holds`' value resolver for one hour of a day: the hour's own
    reading of the variable, exactly what `build_risk_reasoning` compares per
    hour of the session window. A variable the hour does not carry has no
    value: the condition does not hold."""

    def value_for(value_key: str, _symbol: str) -> Optional[float]:
        return hour.get(value_key)

    return value_for


def evaluate_forecast_days(pest_models: list, days: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Per forecast day: the highest level among the rules of the configured
    pest models that hold, and the conditions of every rule that holds. A
    day carrying `hours` is evaluated hour by hour (a rule holds on the day
    when it holds in any of its hours, the session assessment's reading of
    an hourly window); a day without them on its conservative daily
    aggregates. Pure: no weather fetch, no score, no DB. Days come back in
    date order; a day no rule holds on is `low` with no rule named.
    """
    entries: List[Dict[str, Any]] = []
    for day in sorted(days, key=lambda d: d["date"]):
        hours = day.get("hours")
        value_fors = [_hourly_value_for(hour) for hour in hours] if hours else [_daily_value_for(day)]
        level = "low"
        triggered: List[str] = []
        for pm in pest_models or []:
            for rule in pm.rules:
                if not any(rule_holds(rule, value_for) for value_for in value_fors):
                    continue
                triggered.append(format_rule_conditions(rule))
                rule_level = str(rule.probability_value or "low").lower()
                if prob_values.get(rule_level, 0) > prob_values[level]:
                    level = rule_level
        entries.append({"date": day["date"], "level": level, "rules_triggered": triggered})
    return entries


def _ruleset_version_for(pest_models: list) -> Optional[str]:
    """
    The ruleset version(s) behind
    this evaluation, resolved from the seed registry
    (`init.pest_model_seed.ruleset_version_by_name`) by pest-model name.
    `None` when no evaluated model has a tracked seed version (e.g. an
    Excel-uploaded model, or `pest_models` empty) — recorded as an honest
    missing field downstream, never guessed. Multiple distinct versions
    (more than one seeded ruleset evaluated together) are joined rather than
    silently collapsed to one.
    """
    registry = ruleset_version_by_name()
    versions = sorted({
        registry[pm.name] for pm in (pest_models or []) if getattr(pm, "name", None) in registry
    })
    if not versions:
        return None
    return versions[0] if len(versions) == 1 else ",".join(versions)


@dataclass
class RiskAssessmentComputation:
    risk_assessment: Optional[RiskAssessment]
    risk_output: Optional[dict]
    risk_reasoning: Dict[str, Any]
    degraded_reason: Optional[str]
    weather_window: str
    growth_stage_known: bool
    growth_stage_code: Optional[str]


def compute_risk_assessment_from_weather(
    risk_input: RiskInput,
    weather_hourly: dict,
    pest_models: list,
    weather_enabled: bool,
    coordinates_available: bool,
    weather_unavailable: bool,
    parcel_id: Optional[str] = None,
    no_pest_model_for_crop: bool = False,
) -> RiskAssessmentComputation:
    """
    Pure (no I/O) evaluation of pest-model rules + growth-stage escalation +
    the session infestation score into a single `RiskAssessment`, given
    already-fetched weather data and pest models. The caller owns fetching
    `weather_hourly`/`pest_models` and computing `coordinates_available`/
    `weather_unavailable` itself (see the endpoint in `risk_assessment.py`, this module's sole caller).
    """
    degraded_reason: Optional[str] = None
    if not coordinates_available:
        degraded_reason = "position_unavailable"
    elif not weather_enabled:
        degraded_reason = "weather_not_applicable"
    elif weather_unavailable:
        degraded_reason = "weather_unavailable"
    elif not pest_models:
        degraded_reason = (
            "no_pest_model_for_crop"
            if no_pest_model_for_crop
            else "no_pest_models_configured"
        )

    weather_window = f"{risk_input.time_start}  →  {risk_input.time_end}"
    ruleset_version = _ruleset_version_for(pest_models)

    growth_stage: GrowthStage = risk_input.growth_stage
    growth_stage_known = growth_stage.representation == "enum" and not growth_stage.unknown
    growth_stage_code = growth_stage.stage_id if growth_stage_known else None
    growth_stage_escalate = growth_stage_code in BRVCBR_HIGH_CONSEQUENCE_GROWTH_STAGES

    risk_output = None
    if degraded_reason is None:
        risk_output = calculate_risk_index_probability_wd(
            parcel={"@id": parcel_id or "urn:openagri:parcel:unknown"},
            pest_models=pest_models,
            weather_data=weather_hourly,
            lat=risk_input.latitude,
            lon=risk_input.longitude,
            growth_stage_escalate=growth_stage_escalate,
        )

    risk_reasoning = build_risk_reasoning(
        infestation_score=risk_input.infestation_score,
        pest_models=pest_models or [],
        risk_output=risk_output,
        weather_hourly=weather_hourly,
        time_start=risk_input.time_start,
        time_end=risk_input.time_end,
    )
    formatted_level = (
        VERDICT_TO_RISK_LEVEL.get(risk_reasoning["combined_level"], risk_reasoning["combined_level"])
        if degraded_reason is None
        else None
    )

    if degraded_reason is None and formatted_level:
        risk_assessment = RiskAssessment(
            level=formatted_level,
            details=(
                "Weather-rule-based risk assessment (weather window: "
                f"{_human_window(risk_input.time_start, risk_input.time_end)}). "
                "Highest risk level observed in the hourly window, combined with the "
                "session's infestation score."
            ),
            provenance={
                "weather_rule_based": True,
                "degraded_reason": None,
                "weather_window": weather_window,
                "growth_stage_known": growth_stage_known,
                "growth_stage": growth_stage_code,
                "growth_stage_escalated": growth_stage_escalate,
                "infestation_score_combined": True,
                "weather_rule_level_raw": risk_reasoning["weather_rule_level_raw"],
                "ruleset_version": ruleset_version,
                "pdm_app_version": PDM_APP_VERSION,
                "rules_evaluated": risk_reasoning["pdms_rules_evaluated"],
                "observed_weather": risk_reasoning["observed_weather"],
                "nn_band": risk_reasoning["nn_verdict"],
                "nn_band_boundaries": {
                    "high": NN_HIGH_FLOOR,
                    "moderate": NN_MODERATE_FLOOR,
                },
                "combination_rule": "max(weather_band, detection_band)",
            },
        )
    elif risk_input.infestation_score is not None:
        nn_verdict_level = nn_verdict(risk_input.infestation_score)
        fallback_level = VERDICT_TO_RISK_LEVEL.get(nn_verdict_level, nn_verdict_level)
        degraded_label = {
            "weather_not_applicable": "weather not applicable",
            "weather_unavailable": "weather unavailable",
            "position_unavailable": "position unavailable",
            "no_pest_models_configured": "no pest models configured",
            "no_pest_model_for_crop": "no weather rules for this crop",
        }.get(degraded_reason, "degraded")
        risk_assessment = RiskAssessment(
            level=fallback_level,
            details=(
                f"Score-only/degraded risk assessment ({degraded_label}) - "
                "derived from the inference score alone; no weather-rule "
                "provenance."
            ),
            provenance={
                "weather_rule_based": False,
                "degraded_reason": degraded_reason,
                "weather_window": None,
                "growth_stage_known": growth_stage_known,
                "growth_stage": growth_stage_code,
                "weather_rule_level_raw": None,
                "ruleset_version": ruleset_version,
                "pdm_app_version": PDM_APP_VERSION,
            },
        )
    else:
        risk_assessment = None

    return RiskAssessmentComputation(
        risk_assessment=risk_assessment,
        risk_output=risk_output,
        risk_reasoning=risk_reasoning,
        degraded_reason=degraded_reason,
        weather_window=weather_window,
        growth_stage_known=growth_stage_known,
        growth_stage_code=growth_stage_code,
    )
