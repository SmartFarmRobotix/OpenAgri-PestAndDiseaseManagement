"""
Tests for the scalar-only rule-assessment endpoint: the real endpoint
function is exercised with only the DB/HTTP boundary mocked.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

_PDM_APP = os.path.join(os.path.dirname(__file__), "..")
if _PDM_APP not in sys.path:
    sys.path.insert(0, _PDM_APP)

from schemas.contract import GrowthStage, RiskInput  # noqa: E402
from api.api_v1.endpoints import risk_assessment as endpoint_module  # noqa: E402
from core.version import PDM_APP_VERSION  # noqa: E402


def _risk_input(
    infestation_score: float = 0.6,
    growth_stage: GrowthStage | None = None,
    latitude: float | None = 45.0,
    longitude: float | None = 10.0,
) -> RiskInput:
    return RiskInput(
        session_id="sess-1",
        infestation_score=infestation_score,
        crop="brassica-oleracea",
        growth_stage=growth_stage or GrowthStage.unknown_for("enum"),
        time_start="2026-01-01T00:00:00Z",
        time_end="2026-01-01T01:00:00Z",
        latitude=latitude,
        longitude=longitude,
    )


def _pest_model_mock(name: str = "Cabbage aphid", rules: list | None = None):
    pm = MagicMock()
    pm.name = name
    pm.crops = ["brassica-oleracea", "brassica-oleracea-sabellica"]
    pm.rules = rules or []
    return pm


def _rule_mock(probability_value: str, conditions: list):
    rule = MagicMock()
    rule.probability_value = probability_value
    rule.conditions = conditions
    return rule


def _cond_mock(unit_name: str, operator_symbol: str, value: float):
    cond = MagicMock()
    cond.unit.name = unit_name
    cond.operator.symbol = operator_symbol
    cond.value = value
    return cond


def _hourly_weather(temperature_2m: float) -> dict:
    return {
        "source": "openmeteo",
        "location": {"lat": 45.0, "lon": 10.0},
        "data": [
            {
                "timestamp": "2026-01-01T00:00:00",
                "values": {"temperature_2m": temperature_2m, "relative_humidity_2m": 60.0},
            }
        ],
    }


@pytest.fixture(autouse=True)
def _mock_boundaries(monkeypatch):
    monkeypatch.setattr(endpoint_module.crud.pest_model, "get_all", lambda db: [])
    monkeypatch.setattr(endpoint_module, "fetch_weather_data", lambda **kwargs: {"data": []})


class TestAssessRiskRequestShape:
    """PDM must receive exactly the scalar
    risk-input fields — enforced by RiskInput's own `extra="forbid"`
    (schemas/contract.py), exercised here at the endpoint boundary."""

    def test_endpoint_accepts_a_bare_risk_input_no_envelope(self):
        db = MagicMock()
        result = endpoint_module.assess_risk(risk_input=_risk_input(), access_token="tok", db=db)
        assert result.session_id == "sess-1"

    def test_reporting_envelope_fields_are_not_part_of_the_request_model(self):
        from pydantic import ValidationError

        with pytest.raises((ValidationError, TypeError)):
            RiskInput(
                session_id="sess-1",
                infestation_score=0.5,
                crop="brassica-oleracea",
                growth_stage=GrowthStage.unknown_for("enum"),
                time_start="2026-01-01T00:00:00Z",
                time_end="2026-01-01T01:00:00Z",
                best_image_id="img-1",
            )


class TestAssessRiskWeatherRuleBased:
    def test_weather_rule_based_assessment(self, monkeypatch):
        db = MagicMock()
        high_rule = _rule_mock("high", [_cond_mock("atmospheric_temperature", ">=", 20)])
        pm = _pest_model_mock(rules=[high_rule])
        monkeypatch.setattr(endpoint_module.crud.pest_model, "get_all", lambda db: [pm])
        monkeypatch.setattr(endpoint_module, "fetch_weather_data", lambda **kw: _hourly_weather(25.0))
        monkeypatch.setattr(endpoint_module.settings, "PDM_WEATHER_ENABLED", True)

        result = endpoint_module.assess_risk(risk_input=_risk_input(), access_token="tok", db=db)

        assert result.risk_assessment is not None
        assert result.risk_assessment.level == "High"
        assert result.risk_assessment.provenance["weather_rule_based"] is True
        assert result.risk_assessment.provenance["weather_window"] == (
            "2026-01-01T00:00:00Z  →  2026-01-01T01:00:00Z"
        )
        assert result.risk_assessment.provenance["weather_rule_level_raw"] == "high"
        assert result.risk_assessment.provenance["ruleset_version"] == "1.0.0"
        assert result.risk_assessment.provenance["pdm_app_version"] == PDM_APP_VERSION

    def test_high_consequence_growth_stage_escalates(self, monkeypatch):
        db = MagicMock()
        moderate_rule = _rule_mock("moderate", [_cond_mock("atmospheric_temperature", ">=", 15)])
        pm = _pest_model_mock(rules=[moderate_rule])
        monkeypatch.setattr(endpoint_module.crud.pest_model, "get_all", lambda db: [pm])
        monkeypatch.setattr(endpoint_module, "fetch_weather_data", lambda **kw: _hourly_weather(20.0))
        monkeypatch.setattr(endpoint_module.settings, "PDM_WEATHER_ENABLED", True)

        head_formation = GrowthStage(representation="enum", unknown=False, stage_id="BBCH-40")
        result = endpoint_module.assess_risk(
            risk_input=_risk_input(growth_stage=head_formation, infestation_score=0.1),
            access_token="tok",
            db=db,
        )

        assert result.risk_assessment.level == "High"
        assert result.risk_assessment.provenance["growth_stage_escalated"] is True


class TestAssessRiskWeatherRuleBasedRawBandDivergesFromCombinedLevel:
    """Proves `weather_rule_level_raw`
    is genuinely the pre-score weather-only band, distinct from `level`
    (still score-combined) -- the case a caller re-fusing `level` a second
    time would get wrong."""

    def test_high_score_dominates_combined_while_raw_stays_low(self, monkeypatch):
        db = MagicMock()
        never_fires_rule = _rule_mock("high", [_cond_mock("atmospheric_temperature", ">=", 30)])
        pm = _pest_model_mock(rules=[never_fires_rule])
        monkeypatch.setattr(endpoint_module.crud.pest_model, "get_all", lambda db: [pm])
        monkeypatch.setattr(endpoint_module, "fetch_weather_data", lambda **kw: _hourly_weather(10.0))
        monkeypatch.setattr(endpoint_module.settings, "PDM_WEATHER_ENABLED", True)

        result = endpoint_module.assess_risk(
            risk_input=_risk_input(infestation_score=0.95), access_token="tok", db=db
        )

        assert result.risk_assessment.level == "High"
        assert result.risk_assessment.provenance["weather_rule_level_raw"] == "low"
        assert result.risk_assessment.provenance["weather_rule_level_raw"] != result.risk_assessment.level


class TestAssessRiskDegradedNeverMasqueradesAsWeatherRule:
    def test_missing_coordinates_labeled_position_unavailable(self, monkeypatch):
        db = MagicMock()
        monkeypatch.setattr(endpoint_module.settings, "PDM_WEATHER_ENABLED", True)

        def _unexpected_weather_lookup(**kwargs):
            raise AssertionError("coordinate-less sessions must not request weather")

        monkeypatch.setattr(endpoint_module, "fetch_weather_data", _unexpected_weather_lookup)
        result = endpoint_module.assess_risk(
            risk_input=_risk_input(latitude=None, longitude=None), access_token="tok", db=db
        )

        assert result.risk_assessment is not None
        assert result.risk_assessment.provenance["weather_rule_based"] is False
        assert result.risk_assessment.provenance["degraded_reason"] == "position_unavailable"
        assert result.risk_assessment.provenance["weather_rule_level_raw"] is None
        assert result.risk_assessment.provenance["ruleset_version"] is None
        assert result.risk_assessment.provenance["pdm_app_version"] == PDM_APP_VERSION

    def test_weather_disabled_labeled_not_applicable(self, monkeypatch):
        db = MagicMock()
        pm = _pest_model_mock(rules=[_rule_mock("high", [_cond_mock("atmospheric_temperature", ">=", 20)])])
        monkeypatch.setattr(endpoint_module.crud.pest_model, "get_all", lambda db: [pm])
        monkeypatch.setattr(endpoint_module.settings, "PDM_WEATHER_ENABLED", False)

        result = endpoint_module.assess_risk(risk_input=_risk_input(), access_token="tok", db=db)

        assert result.risk_assessment.provenance["weather_rule_based"] is False
        assert result.risk_assessment.provenance["degraded_reason"] == "weather_not_applicable"
        assert result.risk_assessment.provenance["weather_rule_level_raw"] is None
        assert result.risk_assessment.provenance["ruleset_version"] == "1.0.0"

    def test_weather_unreachable_labeled_unavailable(self, monkeypatch):
        db = MagicMock()
        pm = _pest_model_mock(rules=[_rule_mock("high", [_cond_mock("atmospheric_temperature", ">=", 20)])])
        monkeypatch.setattr(endpoint_module.crud.pest_model, "get_all", lambda db: [pm])
        monkeypatch.setattr(endpoint_module.settings, "PDM_WEATHER_ENABLED", True)

        def _raise(**kw):
            raise HTTPException(status_code=400, detail="WeatherService unreachable")

        monkeypatch.setattr(endpoint_module, "fetch_weather_data", _raise)

        result = endpoint_module.assess_risk(risk_input=_risk_input(), access_token="tok", db=db)

        assert result.risk_assessment.provenance["weather_rule_based"] is False
        assert result.risk_assessment.provenance["degraded_reason"] == "weather_unavailable"

    def test_no_pest_model_for_crop_is_labeled_degraded(self, monkeypatch):
        db = MagicMock()
        monkeypatch.setattr(endpoint_module.crud.pest_model, "get_all", lambda db: [])
        monkeypatch.setattr(endpoint_module.settings, "PDM_WEATHER_ENABLED", True)
        monkeypatch.setattr(endpoint_module, "fetch_weather_data", lambda **kw: _hourly_weather(25.0))

        result = endpoint_module.assess_risk(risk_input=_risk_input(), access_token="tok", db=db)

        assert result.risk_assessment is not None
        assert result.risk_assessment.provenance["degraded_reason"] == "no_pest_model_for_crop"
        assert any("degraded to score-only" in e for e in result.errors)


class TestAssessRiskDoesNotForwardToReporting:
    """This endpoint is scalar-in/assessment-out only — it must never touch
    ReportingService."""

    def test_no_outbound_http_call_is_made(self, monkeypatch):
        db = MagicMock()
        pm = _pest_model_mock(rules=[_rule_mock("high", [_cond_mock("atmospheric_temperature", ">=", 20)])])
        monkeypatch.setattr(endpoint_module.crud.pest_model, "get_all", lambda db: [pm])
        monkeypatch.setattr(endpoint_module, "fetch_weather_data", lambda **kw: _hourly_weather(25.0))
        monkeypatch.setattr(endpoint_module.settings, "PDM_WEATHER_ENABLED", True)

        assert not hasattr(endpoint_module, "requests")

        result = endpoint_module.assess_risk(risk_input=_risk_input(), access_token="tok", db=db)
        assert result.risk_assessment is not None


class TestDetailsSentenceHumanWindow:
    """The report-facing
    `details` sentence carries a human-readable window; provenance keeps
    the raw ISO pair verbatim (the pinned machine-facing record)."""

    def test_details_window_is_human_readable_and_provenance_stays_raw(self, monkeypatch):
        db = MagicMock()
        high_rule = _rule_mock("high", [_cond_mock("atmospheric_temperature", ">=", 20)])
        pm = _pest_model_mock(rules=[high_rule])
        monkeypatch.setattr(endpoint_module.crud.pest_model, "get_all", lambda db: [pm])
        monkeypatch.setattr(endpoint_module, "fetch_weather_data", lambda **kw: _hourly_weather(25.0))
        monkeypatch.setattr(endpoint_module.settings, "PDM_WEATHER_ENABLED", True)

        result = endpoint_module.assess_risk(risk_input=_risk_input(), access_token="tok", db=db)

        details = result.risk_assessment.details
        assert "01 Jan 2026, 00:00-01:00 UTC" in details
        assert "2026-01-01T00:00:00Z" not in details
        assert result.risk_assessment.provenance["weather_window"] == (
            "2026-01-01T00:00:00Z  →  2026-01-01T01:00:00Z"
        )

    def test_unparseable_window_falls_back_to_raw(self):
        from utils.risk_assessment_core import _human_window

        assert _human_window("not-a-timestamp", "also-not") == "not-a-timestamp  →  also-not"

    def test_cross_day_window_keeps_both_dates(self):
        from utils.risk_assessment_core import _human_window

        assert _human_window("2026-01-01T22:00:00Z", "2026-01-02T06:10:00Z") == (
            "01 Jan 2026 22:00 UTC - 02 Jan 2026 06:10 UTC"
        )


def _two_rule_model_over_24_hours():
    """One pest model of two rules over a 24-hour window: the moderate rule
    (temperature >= 20 AND humidity >= 70) holds for the first 6 hours, the
    high rule (temperature >= 30) never does."""
    moderate = _rule_mock(
        "moderate",
        [
            _cond_mock("atmospheric_temperature", ">=", 20),
            _cond_mock("atmospheric_relative_humidity", ">=", 70),
        ],
    )
    high = _rule_mock("high", [_cond_mock("atmospheric_temperature", ">=", 30)])
    pm = _pest_model_mock(rules=[moderate, high])
    hours = []
    for h in range(24):
        warm = h < 6
        hours.append(
            {
                "timestamp": f"2026-01-01T{h:02d}:00:00",
                "values": {
                    "temperature_2m": 25.0 if warm else 15.0,
                    "relative_humidity_2m": 80.0 if warm else 68.0,
                    "precipitation": 0.5 if h in (3, 4) else 0.0,
                },
            }
        )
    return pm, {"data": hours}


def _compute_two_rule_case(score: float = 0.45):
    from utils.risk_assessment_core import compute_risk_assessment_from_weather

    pm, weather = _two_rule_model_over_24_hours()
    risk_input = RiskInput(
        session_id="sess-1",
        infestation_score=score,
        crop="brassica-oleracea",
        growth_stage=GrowthStage.unknown_for("enum"),
        time_start="2026-01-01T00:00:00Z",
        time_end="2026-01-02T00:00:00Z",
        latitude=45.0,
        longitude=10.0,
    )
    return compute_risk_assessment_from_weather(
        risk_input, weather, [pm],
        weather_enabled=True, coordinates_available=True, weather_unavailable=False,
    )


class TestProvenanceCarriesTheRuleFacts:
    """The weather-rule path's
    provenance gains the facts behind the level, additively; the degraded
    path carries none of them."""

    GOLDEN_LEVEL = "Medium"
    GOLDEN_DETAILS = (
        "Weather-rule-based risk assessment (weather window: "
        "01 Jan 2026 00:00 UTC - 02 Jan 2026 00:00 UTC). "
        "Highest risk level observed in the hourly window, combined with the "
        "session's infestation score."
    )

    def test_level_and_details_are_byte_equal_to_the_pre_change_values(self):
        assessment = _compute_two_rule_case().risk_assessment
        assert assessment.level == self.GOLDEN_LEVEL
        assert assessment.details == self.GOLDEN_DETAILS

    def test_a_weather_rule_assessment_exposes_its_rule_facts(self):
        provenance = _compute_two_rule_case().risk_assessment.provenance

        (model,) = provenance["rules_evaluated"]
        assert model["model"] == "Cabbage aphid"
        assert model["dominant_level"] == "moderate"
        first, second = model["rules"]
        assert first == {
            "level": "moderate",
            "conditions": "temperature_2m >= 20  AND  relative_humidity_2m >= 70",
            "hours_triggered": 6,
            "hours_total": 24,
        }
        assert second["level"] == "high"
        assert second["conditions"] == "temperature_2m >= 30"
        assert second["hours_triggered"] == 0
        assert second["hours_total"] == 24

        observed = provenance["observed_weather"]
        assert observed["temperature_2m_avg_C"] == 17.5
        assert observed["temperature_2m_max_C"] == 25.0
        assert observed["relative_humidity_2m_avg_pct"] == 71.0
        assert observed["precipitation_mm"] == 1.0

        assert provenance["nn_band"] == "Moderate"
        assert provenance["nn_band_boundaries"] == {"high": 0.70, "moderate": 0.40}
        assert provenance["combination_rule"] == "max(weather_band, detection_band)"

    def test_the_existing_provenance_keys_keep_their_values(self):
        provenance = _compute_two_rule_case().risk_assessment.provenance

        assert provenance["weather_rule_based"] is True
        assert provenance["degraded_reason"] is None
        assert provenance["weather_window"] == "2026-01-01T00:00:00Z  →  2026-01-02T00:00:00Z"
        assert provenance["weather_rule_level_raw"] == "moderate"
        assert provenance["infestation_score_combined"] is True

    def test_a_degraded_assessment_carries_no_rule_facts(self):
        from utils.risk_assessment_core import compute_risk_assessment_from_weather

        pm, weather = _two_rule_model_over_24_hours()
        risk_input = _risk_input(infestation_score=0.45)
        result = compute_risk_assessment_from_weather(
            risk_input, weather, [pm],
            weather_enabled=True, coordinates_available=True, weather_unavailable=True,
        )

        provenance = result.risk_assessment.provenance
        assert provenance["degraded_reason"] == "weather_unavailable"
        for key in ("rules_evaluated", "observed_weather", "nn_band", "nn_band_boundaries", "combination_rule"):
            assert key not in provenance


def _forecast_day(date: str, temp_max, temp_min, humidity, precipitation=0.0) -> dict:
    return {
        "date": date,
        "temp_max": temp_max,
        "temp_min": temp_min,
        "humidity": humidity,
        "precipitation": precipitation,
    }


def _forecast(days: list[dict], crop: str = "brassica-oleracea"):
    from schemas.risk_assessment import ForecastRiskRequest

    return endpoint_module.assess_forecast_risk(
        request=ForecastRiskRequest(crop=crop, days=days), db=MagicMock()
    )


class TestForecastEndpoint:
    """The configured pest models' rules
    on daily forecast values, no weather fetch, no score, no change to the
    session assessment (whose golden values above stay byte-equal)."""

    _WARM_AND_HUMID = "temperature_2m >= 20  AND  relative_humidity_2m >= 70"

    def _one_moderate_rule(self, monkeypatch):
        rule = _rule_mock(
            "moderate",
            [
                _cond_mock("atmospheric_temperature", ">=", 20),
                _cond_mock("atmospheric_relative_humidity", ">=", 70),
            ],
        )
        monkeypatch.setattr(
            endpoint_module.crud.pest_model, "get_all", lambda db: [_pest_model_mock(rules=[rule])]
        )

    def test_each_forecast_day_gets_a_level_in_date_order(self, monkeypatch):
        self._one_moderate_rule(monkeypatch)
        days = [
            _forecast_day("2026-09-14", 24.0, 12.0, 78.0),
            _forecast_day("2026-09-11", 19.9, 10.0, 90.0),
            _forecast_day("2026-09-13", 26.0, 14.0, 55.0),
            _forecast_day("2026-09-12", 21.0, 6.0, 72.0),
            _forecast_day("2026-09-15", 15.0, 5.0, 60.0),
        ]

        result = _forecast(days)

        assert result.reason is None
        assert [d.date for d in result.days] == [
            "2026-09-11", "2026-09-12", "2026-09-13", "2026-09-14", "2026-09-15",
        ]
        assert [d.level for d in result.days] == ["low", "moderate", "low", "moderate", "low"]
        assert result.days[1].rules_triggered == [self._WARM_AND_HUMID]
        assert result.days[3].rules_triggered == [self._WARM_AND_HUMID]
        for quiet in (result.days[0], result.days[2], result.days[4]):
            assert quiet.rules_triggered == []

    def test_an_upper_bound_is_checked_against_the_days_minimum(self, monkeypatch):
        frost = _rule_mock("high", [_cond_mock("atmospheric_temperature", "<=", 5)])
        monkeypatch.setattr(
            endpoint_module.crud.pest_model, "get_all", lambda db: [_pest_model_mock(rules=[frost])]
        )

        result = _forecast(
            [
                _forecast_day("2026-09-11", 12.0, 3.0, 60.0),
                _forecast_day("2026-09-12", 4.0, 6.0, 60.0),
            ]
        )

        assert [d.level for d in result.days] == ["high", "low"]
        assert result.days[0].rules_triggered == ["temperature_2m <= 5"]

    def test_the_highest_level_wins_and_every_rule_that_holds_is_named(self, monkeypatch):
        moderate = _rule_mock("moderate", [_cond_mock("atmospheric_temperature", ">=", 15)])
        high = _rule_mock("high", [_cond_mock("atmospheric_temperature", ">=", 25)])
        wet = _rule_mock("low", [_cond_mock("precipitation", ">", 2)])
        monkeypatch.setattr(
            endpoint_module.crud.pest_model,
            "get_all",
            lambda db: [_pest_model_mock(rules=[moderate, high, wet])],
        )

        (day,) = _forecast([_forecast_day("2026-09-11", 27.0, 14.0, 60.0, precipitation=3.5)]).days

        assert day.level == "high"
        assert day.rules_triggered == [
            "temperature_2m >= 15",
            "temperature_2m >= 25",
            "precipitation > 2",
        ]

    def test_a_value_the_forecast_lacks_or_cannot_be_bounded_does_not_satisfy_a_condition(self, monkeypatch):
        needs_humidity = _rule_mock("high", [_cond_mock("atmospheric_relative_humidity", ">=", 70)])
        exact_temp = _rule_mock("high", [_cond_mock("atmospheric_temperature", "==", 20)])
        soil = _rule_mock("high", [_cond_mock("soil_temperature_10cm", ">=", 5)])
        monkeypatch.setattr(
            endpoint_module.crud.pest_model,
            "get_all",
            lambda db: [_pest_model_mock(rules=[needs_humidity, exact_temp, soil])],
        )

        (day,) = _forecast([_forecast_day("2026-09-11", 20.0, 20.0, None)]).days

        assert day.level == "low"
        assert day.rules_triggered == []

    def test_no_pest_model_is_reported_honestly_not_as_five_low_rows(self, monkeypatch):
        monkeypatch.setattr(endpoint_module.crud.pest_model, "get_all", lambda db: [])

        result = _forecast([_forecast_day(f"2026-09-{d}", 20.0, 10.0, 70.0) for d in range(11, 16)])

        assert result.reason == "no_pest_models_configured"
        assert result.days == []

    def test_nothing_is_fetched(self, monkeypatch):
        self._one_moderate_rule(monkeypatch)

        def _refuse(**kwargs):
            raise AssertionError("the forecast endpoint must not fetch weather")

        monkeypatch.setattr(endpoint_module, "fetch_weather_data", _refuse)
        assert _forecast([_forecast_day("2026-09-11", 25.0, 10.0, 80.0)]).days[0].level == "moderate"

    def test_the_request_refuses_an_empty_crop_and_a_day_without_a_date(self):
        from pydantic import ValidationError
        from schemas.risk_assessment import ForecastRiskRequest

        with pytest.raises(ValidationError):
            ForecastRiskRequest(crop="", days=[])
        with pytest.raises(ValidationError):
            ForecastRiskRequest(crop="brassica-oleracea", days=[{"temp_max": 20.0}])

    def test_the_route_sits_next_to_the_session_assessment_and_is_gatekeeper_gated(self):
        from api import deps
        from api.api_v1.api import api_router

        routes = {r.path: r for r in api_router.routes}
        assert "/risk-assessment/" in routes
        forecast = routes["/risk-assessment/forecast/"]
        assert "POST" in forecast.methods
        assert deps.is_using_gatekeeper in [d.dependency for d in forecast.dependencies]


def _hour(time: str, temperature, humidity, precipitation=0.0) -> dict:
    return {
        "time": time,
        "temperature_2m": temperature,
        "relative_humidity_2m": humidity,
        "precipitation": precipitation,
    }


def _hourly_day(date: str, hours: list[dict]) -> dict:
    return {"date": date, "hours": hours}


class TestForecastEndpointHourlyForm:
    """The forecast route's hourly form: a day carrying `hours` is evaluated
    hour by hour with the per-rule check the session assessment applies to its
    hourly window (a rule holds on the day when it holds in any hour); the
    daily form beside it is unchanged."""

    _WARM_AND_HUMID = "temperature_2m >= 20  AND  relative_humidity_2m >= 70"

    def _one_moderate_rule(self, monkeypatch):
        rule = _rule_mock(
            "moderate",
            [
                _cond_mock("atmospheric_temperature", ">=", 20),
                _cond_mock("atmospheric_relative_humidity", ">=", 70),
            ],
        )
        monkeypatch.setattr(
            endpoint_module.crud.pest_model, "get_all", lambda db: [_pest_model_mock(rules=[rule])]
        )

    def test_the_conditions_must_hold_in_the_same_hour(self, monkeypatch):
        self._one_moderate_rule(monkeypatch)
        apart = _hourly_day(
            "2026-03-07",
            [_hour("2026-03-07T03:00", 12.0, 90.0), _hour("2026-03-07T15:00", 24.0, 50.0)],
        )
        together = _hourly_day(
            "2026-03-06",
            [_hour("2026-03-06T03:00", 12.0, 90.0), _hour("2026-03-06T15:00", 22.0, 75.0)],
        )

        result = _forecast([apart, together])

        assert [d.date for d in result.days] == ["2026-03-06", "2026-03-07"]
        assert [d.level for d in result.days] == ["moderate", "low"]
        assert result.days[0].rules_triggered == [self._WARM_AND_HUMID]
        assert result.days[1].rules_triggered == []

    def test_a_rule_that_holds_in_several_hours_is_named_once_and_the_highest_level_wins(self, monkeypatch):
        moderate = _rule_mock("moderate", [_cond_mock("atmospheric_temperature", ">=", 15)])
        high = _rule_mock("high", [_cond_mock("atmospheric_temperature", ">=", 25)])
        monkeypatch.setattr(
            endpoint_module.crud.pest_model, "get_all", lambda db: [_pest_model_mock(rules=[moderate, high])]
        )

        (day,) = _forecast(
            [
                _hourly_day(
                    "2026-03-06",
                    [_hour(f"2026-03-06T{h:02d}:00", t, 60.0) for h, t in ((6, 16.0), (12, 27.0), (15, 26.0))],
                )
            ]
        ).days

        assert day.level == "high"
        assert day.rules_triggered == ["temperature_2m >= 15", "temperature_2m >= 25"]

    def test_the_hourly_check_is_the_one_the_session_assessment_uses(self, monkeypatch):
        from utils.risk_assessment_core import build_risk_reasoning

        rule = _rule_mock("moderate", [_cond_mock("atmospheric_temperature", ">=", 20)])
        monkeypatch.setattr(
            endpoint_module.crud.pest_model, "get_all", lambda db: [_pest_model_mock(rules=[rule])]
        )
        hours = [_hour("2026-03-06T10:00", 19.9, 60.0), _hour("2026-03-06T14:00", 20.0, 60.0)]

        (day,) = _forecast([_hourly_day("2026-03-06", hours)]).days
        reasoning = build_risk_reasoning(
            0.0,
            [_pest_model_mock(rules=[rule])],
            None,
            {"data": [{"timestamp": h["time"], "values": h} for h in hours]},
            "2026-03-06T00:00",
            "2026-03-06T23:00",
        )

        assert reasoning["pdms_rules_evaluated"][0]["rules"][0]["hours_triggered"] == 1
        assert day.level == "moderate"

    def test_a_value_the_hour_lacks_does_not_satisfy_a_condition(self, monkeypatch):
        self._one_moderate_rule(monkeypatch)

        (day,) = _forecast([_hourly_day("2026-03-06", [_hour("2026-03-06T12:00", 25.0, None)])]).days

        assert day.level == "low"
        assert day.rules_triggered == []

    def test_daily_and_hourly_days_can_share_one_request(self, monkeypatch):
        self._one_moderate_rule(monkeypatch)

        result = _forecast(
            [
                _hourly_day("2026-03-07", [_hour("2026-03-07T12:00", 22.0, 80.0)]),
                _forecast_day("2026-03-06", 24.0, 12.0, 72.0),
            ]
        )

        assert [(d.date, d.level) for d in result.days] == [("2026-03-06", "moderate"), ("2026-03-07", "moderate")]

    def test_no_pest_model_is_reported_honestly_for_the_hourly_form_too(self, monkeypatch):
        monkeypatch.setattr(endpoint_module.crud.pest_model, "get_all", lambda db: [])

        result = _forecast([_hourly_day("2026-03-06", [_hour("2026-03-06T12:00", 22.0, 80.0)])])

        assert result.reason == "no_pest_models_configured"
        assert result.days == []

    def test_a_day_with_an_empty_hours_list_or_an_hour_without_a_time_is_refused(self):
        from pydantic import ValidationError
        from schemas.risk_assessment import ForecastRiskRequest

        with pytest.raises(ValidationError):
            ForecastRiskRequest(crop="brassica-oleracea", days=[{"date": "2026-03-06", "hours": []}])
        with pytest.raises(ValidationError):
            ForecastRiskRequest(
                crop="brassica-oleracea",
                days=[{"date": "2026-03-06", "hours": [{"temperature_2m": 20.0}]}],
            )
