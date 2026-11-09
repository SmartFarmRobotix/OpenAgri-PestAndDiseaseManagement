"""Cucumber aphid weather-rule tests."""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

_PDM_APP = os.path.join(os.path.dirname(__file__), "..")
if _PDM_APP not in sys.path:
    sys.path.insert(0, _PDM_APP)

from init import pest_model_seed  # noqa: E402
from utils.risk_index import calculate_risk_index_probability_wd  # noqa: E402


def _model(definition):
    rules = []
    for rule in definition["rules"]:
        conditions = [SimpleNamespace(unit=SimpleNamespace(name=c["unit"]), operator=SimpleNamespace(symbol=c["operator"]), value=c["value"]) for c in rule["conditions"]]
        rules.append(SimpleNamespace(probability_value=rule["probability_value"], conditions=conditions))
    return SimpleNamespace(name=definition["name"], eppo_code=definition["eppo_code"], rules=rules)


def _levels(definition):
    hours = [(25, 0, "high"), (25, 2, "moderate"), (18, 0, "moderate"), (31, 0, "moderate"), (34, 0, "low")]
    weather = {"data": [{"timestamp": str(i), "values": {"temperature_2m": temp, "precipitation": rain}} for i, (temp, rain, _) in enumerate(hours)]}
    graph = calculate_risk_index_probability_wd({"@id": "urn:parcel:test"}, [_model(definition)], weather, 45.0, 10.0)
    return [item["hasSimpleResult"] for item in graph["@graph"][0]["hasMember"]]


def test_both_cucumber_rulesets_band_the_five_specified_hours_and_cite_sources():
    definitions = [d for d in pest_model_seed.load_seed_definitions() if d["eppo_code"] in {"MYZUPE", "APHIGO"}]
    assert {d["eppo_code"] for d in definitions} == {"MYZUPE", "APHIGO"}
    for definition in definitions:
        assert definition["crops"] == ["cucumis-sativus"]
        assert _levels(definition) == ["high", "moderate", "moderate", "moderate", "low"]
        notes = " ".join(definition["_encoding_notes"])
        assert "Kocourek" in notes and "Satar" in notes and "Davis" in notes
    assert "The figures are the Aphis gossypii ones, used for Myzus persicae on cucumber as well, in the absence of a cucumber-specific source" in " ".join(definitions[0]["_encoding_notes"] if definitions[0]["eppo_code"] == "MYZUPE" else definitions[1]["_encoding_notes"])
