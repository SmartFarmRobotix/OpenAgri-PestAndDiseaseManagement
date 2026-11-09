"""
Unit tests for the cabbage-aphid (BRVCBR) pest-model ruleset.

Loads the real versioned ruleset JSON (app/pest_models/cabbage_aphid_brvcbr_v1.json)
and evaluates it through the real calculate_risk_index_probability_wd — no
database required, following test_risk_index.py's mock-object pattern.
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock

_PDM_APP = os.path.join(os.path.dirname(__file__), "..")
if _PDM_APP not in sys.path:
    sys.path.insert(0, _PDM_APP)

_RULESET_PATH = os.path.join(_PDM_APP, "pest_models", "cabbage_aphid_brvcbr_v1.json")


def _load_ruleset() -> dict:
    with open(_RULESET_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _mock_pest_model_from_definition(definition: dict):
    pm = MagicMock()
    pm.name = definition["name"]
    rules = []
    for rule_def in definition["rules"]:
        rule = MagicMock()
        rule.probability_value = rule_def["probability_value"]
        conditions = []
        for cond_def in rule_def["conditions"]:
            cond = MagicMock()
            cond.unit.name = cond_def["unit"]
            cond.operator.symbol = cond_def["operator"]
            cond.value = cond_def["value"]
            conditions.append(cond)
        rule.conditions = conditions
        rules.append(rule)
    pm.rules = rules
    return pm


def _weather(temperature_2m: float, precipitation: float = 0.0) -> dict:
    return {
        "source": "openmeteo",
        "location": {"lat": 45.0, "lon": 10.0},
        "data": [
            {
                "timestamp": "2026-06-01T12:00:00",
                "values": {
                    "temperature_2m": temperature_2m,
                    "relative_humidity_2m": 60.0,
                    "precipitation": precipitation,
                },
            }
        ],
    }


class TestRulesetStructure:
    def test_ruleset_loads_and_cites_source_version(self):
        definition = _load_ruleset()
        assert definition["eppo_code"] == "BRVCBR"
        assert definition["source"] == "literature (see _encoding_notes)"
        assert definition["source_version"] == "2"
        assert definition["review_status"] == "literature-sourced"
        assert len(definition["rules"]) >= 1

    def test_every_rule_has_a_valid_probability_value(self):
        definition = _load_ruleset()
        for rule in definition["rules"]:
            assert rule["probability_value"] in ("low", "moderate", "high")
            assert len(rule["conditions"]) >= 1


class TestRulesetEvaluation:
    def test_optimal_dry_conditions_evaluate_to_high(self):
        from utils.risk_index import calculate_risk_index_probability_wd

        pm = _mock_pest_model_from_definition(_load_ruleset())
        result = calculate_risk_index_probability_wd(
            parcel={"@id": "urn:test:parcel"},
            pest_models=[pm],
            weather_data=_weather(temperature_2m=22.0, precipitation=0.0),
            lat=45.0,
            lon=10.0,
        )
        assert result["@graph"][0]["hasMember"][0]["hasSimpleResult"] == "high"

    def test_optimal_wet_conditions_downgrade_to_moderate(self):
        from utils.risk_index import calculate_risk_index_probability_wd

        pm = _mock_pest_model_from_definition(_load_ruleset())
        result = calculate_risk_index_probability_wd(
            parcel={"@id": "urn:test:parcel"},
            pest_models=[pm],
            weather_data=_weather(temperature_2m=22.0, precipitation=5.0),
            lat=45.0,
            lon=10.0,
        )
        assert result["@graph"][0]["hasMember"][0]["hasSimpleResult"] == "moderate"

    def test_precipitation_gate_is_zero_vs_nonzero_not_a_rate_threshold(self):
        """
        The gate cites the literature for only a qualitative
        dampening *direction*, not a sourced rate magnitude, so it
        must be zero-vs-nonzero — any recorded precipitation counts as wet,
        not just precipitation above some invented cutoff. A rate cutoff such as "<= 1 mm/h" would keep 0.5 mm classified as
        dry/high; no such magnitude is traceable to the literature.
        """
        from utils.risk_index import calculate_risk_index_probability_wd

        pm = _mock_pest_model_from_definition(_load_ruleset())
        result = calculate_risk_index_probability_wd(
            parcel={"@id": "urn:test:parcel"},
            pest_models=[pm],
            weather_data=_weather(temperature_2m=22.0, precipitation=0.5),
            lat=45.0,
            lon=10.0,
        )
        assert result["@graph"][0]["hasMember"][0]["hasSimpleResult"] == "moderate"

    def test_upper_transitional_band_is_moderate(self):
        from utils.risk_index import calculate_risk_index_probability_wd

        pm = _mock_pest_model_from_definition(_load_ruleset())
        result = calculate_risk_index_probability_wd(
            parcel={"@id": "urn:test:parcel"},
            pest_models=[pm],
            weather_data=_weather(temperature_2m=28.0),
            lat=45.0,
            lon=10.0,
        )
        assert result["@graph"][0]["hasMember"][0]["hasSimpleResult"] == "moderate"

    def test_lower_transitional_band_is_moderate(self):
        from utils.risk_index import calculate_risk_index_probability_wd

        pm = _mock_pest_model_from_definition(_load_ruleset())
        result = calculate_risk_index_probability_wd(
            parcel={"@id": "urn:test:parcel"},
            pest_models=[pm],
            weather_data=_weather(temperature_2m=17.0),
            lat=45.0,
            lon=10.0,
        )
        assert result["@graph"][0]["hasMember"][0]["hasSimpleResult"] == "moderate"

    def test_outside_all_bands_defaults_to_low(self):
        from utils.risk_index import calculate_risk_index_probability_wd

        pm = _mock_pest_model_from_definition(_load_ruleset())
        result = calculate_risk_index_probability_wd(
            parcel={"@id": "urn:test:parcel"},
            pest_models=[pm],
            weather_data=_weather(temperature_2m=2.0),
            lat=45.0,
            lon=10.0,
        )
        assert result["@graph"][0]["hasMember"][0]["hasSimpleResult"] == "low"
