"""
Unit tests for OpenAgri-PestAndDiseaseManagement risk_index safe-operator dispatch.

Coverage targets
----------------
Regression: verify eval is gone and the safe-operator approach
    works correctly for every supported operator symbol.

Modules under test
------------------
app/utils/risk_index.py → _get_operator, _safe_compare,
                             _row_satisfies_conditions,
                             calculate_risk_index_probability_wd
"""
from __future__ import annotations

import ast
import importlib
import inspect
import os
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_PDM_APP = os.path.join(os.path.dirname(__file__), "..")
if _PDM_APP not in sys.path:
    sys.path.insert(0, _PDM_APP)


def _import_risk_index():
    """Return the risk_index module, reloading if necessary."""
    import utils.risk_index as ri  # noqa: PLC0415
    return ri


def _import_operator_module():
    import operator as _op  # noqa: PLC0415
    return _op


class TestNoEvalInSource:
    """
    Security regression: eval and exec must be
    completely absent from the risk_index module.
    """

    def _source_path(self) -> str:
        import utils.risk_index as ri
        return inspect.getfile(ri)

    def test_no_eval_call_in_ast(self):
        """Parse the source file and assert there is no bare eval call."""
        src = open(self._source_path(), encoding="utf-8").read()
        tree = ast.parse(src)
        eval_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "eval"
        ]
        assert eval_calls == [], (
            f"Found {len(eval_calls)} eval() call(s) in risk_index.py – "
            "the eval-removal security fix has regressed!"
        )

    def test_no_exec_call_in_ast(self):
        """Parse the source file and assert there is no bare exec call."""
        src = open(self._source_path(), encoding="utf-8").read()
        tree = ast.parse(src)
        exec_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "exec"
        ]
        assert exec_calls == [], (
            f"Found {len(exec_calls)} exec() call(s) in risk_index.py"
        )

    def test_eval_not_in_source_text(self):
        """Crude string search as a belt-and-suspenders check."""
        src = open(self._source_path(), encoding="utf-8").read()
        import re
        bare_eval = re.findall(r'\beval\s*\(', src)
        assert bare_eval == [], (
            f"Found bare eval() in source text: {bare_eval}"
        )

    def test_safe_operators_dict_present(self):
        """The module must expose a SAFE_OPERATORS dict."""
        ri = _import_risk_index()
        assert hasattr(ri, "SAFE_OPERATORS"), "SAFE_OPERATORS dict missing from risk_index"
        assert isinstance(ri.SAFE_OPERATORS, dict)
        assert len(ri.SAFE_OPERATORS) > 0

    def test_safe_operators_uses_operator_module(self):
        """SAFE_OPERATORS values must be callables from the operator stdlib."""
        ri = _import_risk_index()
        op = _import_operator_module()
        expected_callables = {
            op.gt, op.lt, op.ge, op.le, op.eq, op.ne,
        }
        for symbol, fn in ri.SAFE_OPERATORS.items():
            assert callable(fn), f"SAFE_OPERATORS[{symbol!r}] is not callable"
            assert fn in expected_callables, (
                f"SAFE_OPERATORS[{symbol!r}] = {fn!r} is not a stdlib operator"
            )


class TestGetOperator:
    """_get_operator must return the right callable or raise on unknown symbols."""

    @pytest.mark.parametrize("symbol,a,b,expected", [
        (">",  5.0, 3.0, True),
        (">",  3.0, 5.0, False),
        ("<",  1.0, 2.0, True),
        ("<",  2.0, 1.0, False),
        (">=", 5.0, 5.0, True),
        (">=", 4.9, 5.0, False),
        ("<=", 5.0, 5.0, True),
        ("<=", 5.1, 5.0, False),
        ("==", 3.0, 3.0, True),
        ("==", 3.0, 4.0, False),
        ("=",  7.0, 7.0, True),
        ("!=", 1.0, 2.0, True),
        ("!=", 2.0, 2.0, False),
    ])
    def test_operator_correctness(self, symbol, a, b, expected):
        ri = _import_risk_index()
        fn = ri._get_operator(symbol)
        assert fn(a, b) is expected, (
            f"_get_operator({symbol!r})({a}, {b}) expected {expected}"
        )

    def test_unknown_symbol_raises_http_exception(self):
        from fastapi import HTTPException
        ri = _import_risk_index()
        with pytest.raises(HTTPException) as exc_info:
            ri._get_operator("??")
        assert exc_info.value.status_code == 400
        assert "??" in str(exc_info.value.detail)

    def test_empty_symbol_raises_http_exception(self):
        from fastapi import HTTPException
        ri = _import_risk_index()
        with pytest.raises(HTTPException):
            ri._get_operator("")

    def test_injection_attempt_raises_http_exception(self):
        """Strings that look like code must be rejected, not evaluated."""
        from fastapi import HTTPException
        ri = _import_risk_index()
        with pytest.raises(HTTPException):
            ri._get_operator("__import__('os').system('id')")


class TestSafeCompare:
    """_safe_compare wraps _get_operator with a None-left guard."""

    def test_none_left_returns_false(self):
        ri = _import_risk_index()
        for symbol in (">", "<", ">=", "<=", "==", "!="):
            assert ri._safe_compare(None, symbol, 0.0) is False

    def test_comparison_true(self):
        ri = _import_risk_index()
        assert ri._safe_compare(10.0, ">", 5.0) is True

    def test_comparison_false(self):
        ri = _import_risk_index()
        assert ri._safe_compare(3.0, ">", 5.0) is False

    def test_equality(self):
        ri = _import_risk_index()
        assert ri._safe_compare(42.0, "==", 42.0) is True
        assert ri._safe_compare(42.0, "==", 43.0) is False

    def test_inequality(self):
        ri = _import_risk_index()
        assert ri._safe_compare(1.0, "!=", 2.0) is True
        assert ri._safe_compare(1.0, "!=", 1.0) is False

    def test_invalid_operator_propagates_http_exception(self):
        from fastapi import HTTPException
        ri = _import_risk_index()
        with pytest.raises(HTTPException):
            ri._safe_compare(1.0, "???", 0.0)


class TestRowSatisfiesConditions:
    """_row_satisfies_conditions checks a pandas-like row against a list of conditions."""

    def _make_condition(self, unit_name: str, operator_symbol: str, value: float):
        """Return a minimal mock condition object."""
        cond = MagicMock()
        cond.unit.name = unit_name
        cond.operator.symbol = operator_symbol
        cond.value = value
        return cond

    def _make_row(self, **kwargs) -> dict:
        """Return a dict acting as a pandas Series (supports .get)."""
        row = MagicMock()
        row.get = lambda key, default=None: kwargs.get(key, default)
        return row

    def test_empty_conditions_returns_true(self):
        ri = _import_risk_index()
        row = self._make_row(temperature=20.0)
        assert ri._row_satisfies_conditions(row, []) is True

    def test_single_condition_satisfied(self):
        ri = _import_risk_index()
        cond = self._make_condition("temperature", ">", 15.0)
        row = self._make_row(temperature=20.0)
        assert ri._row_satisfies_conditions(row, [cond]) is True

    def test_single_condition_not_satisfied(self):
        ri = _import_risk_index()
        cond = self._make_condition("temperature", ">", 25.0)
        row = self._make_row(temperature=20.0)
        assert ri._row_satisfies_conditions(row, [cond]) is False

    def test_multiple_conditions_all_satisfied(self):
        ri = _import_risk_index()
        conds = [
            self._make_condition("temperature", ">", 15.0),
            self._make_condition("humidity", ">=", 60.0),
        ]
        row = self._make_row(temperature=20.0, humidity=70.0)
        assert ri._row_satisfies_conditions(row, conds) is True

    def test_multiple_conditions_one_fails(self):
        ri = _import_risk_index()
        conds = [
            self._make_condition("temperature", ">", 15.0),
            self._make_condition("humidity", ">=", 90.0),
        ]
        row = self._make_row(temperature=20.0, humidity=70.0)
        assert ri._row_satisfies_conditions(row, conds) is False

    def test_condition_with_none_value_returns_false(self):
        """A condition whose .value is None must short-circuit to False."""
        ri = _import_risk_index()
        cond = MagicMock()
        cond.unit.name = "temperature"
        cond.operator.symbol = ">"
        cond.value = None
        row = self._make_row(temperature=20.0)
        assert ri._row_satisfies_conditions(row, [cond]) is False

    def test_condition_key_missing_in_row_returns_false(self):
        """If the measurement key is absent from the row, safe_compare gets None → False."""
        ri = _import_risk_index()
        cond = self._make_condition("soil_temperature", ">", 5.0)
        row = self._make_row(temperature=20.0)
        assert ri._row_satisfies_conditions(row, [cond]) is False

    def test_boundary_condition_equal(self):
        ri = _import_risk_index()
        cond = self._make_condition("temperature", "==", 20.0)
        row = self._make_row(temperature=20.0)
        assert ri._row_satisfies_conditions(row, [cond]) is True

    def test_boundary_condition_le(self):
        ri = _import_risk_index()
        cond = self._make_condition("temperature", "<=", 20.0)
        row_at = self._make_row(temperature=20.0)
        row_below = self._make_row(temperature=19.9)
        row_above = self._make_row(temperature=20.1)
        assert ri._row_satisfies_conditions(row_at, [cond]) is True
        assert ri._row_satisfies_conditions(row_below, [cond]) is True
        assert ri._row_satisfies_conditions(row_above, [cond]) is False


class TestCalculateRiskIndexProbabilityWd:
    """
    Tests for calculate_risk_index_probability_wd using fully mocked
    pest models and synthetic weather data – no database required.
    """

    def _make_pest_model(
        self,
        name: str,
        rules: list,
    ):
        pm = MagicMock()
        pm.name = name
        pm.rules = rules
        return pm

    def _make_rule(self, probability_value: str, conditions: list):
        rule = MagicMock()
        rule.probability_value = probability_value
        rule.conditions = conditions
        return rule

    def _make_cond(self, unit_name: str, operator_symbol: str, value: float):
        cond = MagicMock()
        cond.unit.name = unit_name
        cond.operator.symbol = operator_symbol
        cond.value = value
        return cond

    def _make_weather_data(self, hourly_records: list[dict[str, Any]]) -> dict:
        """Wrap hourly records in the shape returned by fetch_weather_data."""
        return {
            "source": "open-meteo",
            "location": {"lat": 48.5, "lon": 21.3},
            "data": hourly_records,
        }

    def _make_hour(self, timestamp: str, **values) -> dict:
        return {"timestamp": timestamp, "values": values}


    def test_returns_json_ld_structure(self):
        ri = _import_risk_index()
        pest_model = self._make_pest_model("TestPest", rules=[])
        weather = self._make_weather_data([
            self._make_hour("2024-01-01T00:00:00", temperature_2m=10.0),
        ])
        result = ri.calculate_risk_index_probability_wd(
            parcel={"@id": "urn:openagri:parcel:1"},
            pest_models=[pest_model],
            weather_data=weather,
            lat=48.5,
            lon=21.3,
        )
        assert "@context" in result
        assert "@graph" in result
        assert isinstance(result["@graph"], list)

    def test_no_pest_models_returns_empty_graph(self):
        ri = _import_risk_index()
        weather = self._make_weather_data([])
        result = ri.calculate_risk_index_probability_wd(
            parcel={"@id": "urn:openagri:parcel:1"},
            pest_models=[],
            weather_data=weather,
            lat=48.5,
            lon=21.3,
        )
        assert result["@graph"] == []

    def test_high_risk_rule_fires_when_conditions_met(self):
        """A high-risk rule whose conditions are satisfied must produce 'high' results."""
        ri = _import_risk_index()

        cond = self._make_cond("atmospheric_temperature", ">", 15.0)
        high_rule = self._make_rule("high", [cond])
        pm = self._make_pest_model("Powdery Mildew", [high_rule])

        weather = self._make_weather_data([
            self._make_hour("2024-06-01T00:00:00", temperature_2m=25.0),
        ])

        result = ri.calculate_risk_index_probability_wd(
            parcel={"@id": "urn:test:parcel"},
            pest_models=[pm],
            weather_data=weather,
            lat=48.5,
            lon=21.3,
        )

        graph = result["@graph"]
        assert len(graph) == 1
        members = graph[0]["hasMember"]
        assert len(members) == 1
        assert members[0]["hasSimpleResult"] == "high"

    def test_low_risk_when_conditions_not_met(self):
        """When no rule fires the result must be the baseline 'low'."""
        ri = _import_risk_index()

        cond = self._make_cond("atmospheric_temperature", ">", 30.0)
        high_rule = self._make_rule("high", [cond])
        pm = self._make_pest_model("Test Pest", [high_rule])

        weather = self._make_weather_data([
            self._make_hour("2024-06-01T00:00:00", temperature_2m=10.0),
        ])

        result = ri.calculate_risk_index_probability_wd(
            parcel={"@id": "urn:test:parcel"},
            pest_models=[pm],
            weather_data=weather,
            lat=48.5,
            lon=21.3,
        )

        members = result["@graph"][0]["hasMember"]
        assert members[0]["hasSimpleResult"] == "low"

    def test_multiple_hours_each_get_risk_entry(self):
        """One hasMember entry must exist per weather data hour."""
        ri = _import_risk_index()

        pm = self._make_pest_model("Pest", rules=[])
        weather = self._make_weather_data([
            self._make_hour("2024-06-01T00:00:00", temperature_2m=10.0),
            self._make_hour("2024-06-01T01:00:00", temperature_2m=11.0),
            self._make_hour("2024-06-01T02:00:00", temperature_2m=12.0),
        ])

        result = ri.calculate_risk_index_probability_wd(
            parcel={"@id": "urn:test:parcel"},
            pest_models=[pm],
            weather_data=weather,
            lat=48.5,
            lon=21.3,
        )

        members = result["@graph"][0]["hasMember"]
        assert len(members) == 3

    def test_higher_probability_rule_overrides_lower(self):
        """If both a low and a high rule's conditions are satisfied the high one wins."""
        ri = _import_risk_index()

        low_cond  = self._make_cond("atmospheric_temperature", ">", 0.0)
        high_cond = self._make_cond("atmospheric_temperature", ">", 0.0)
        low_rule  = self._make_rule("low",  [low_cond])
        high_rule = self._make_rule("high", [high_cond])
        pm = self._make_pest_model("Dual Rule Pest", [low_rule, high_rule])

        weather = self._make_weather_data([
            self._make_hour("2024-06-01T00:00:00", temperature_2m=20.0),
        ])

        result = ri.calculate_risk_index_probability_wd(
            parcel={"@id": "urn:test:parcel"},
            pest_models=[pm],
            weather_data=weather,
            lat=48.5,
            lon=21.3,
        )

        members = result["@graph"][0]["hasMember"]
        assert members[0]["hasSimpleResult"] == "high"

    def test_multiple_pest_models_produce_multiple_graph_entries(self):
        ri = _import_risk_index()

        pm1 = self._make_pest_model("Pest A", rules=[])
        pm2 = self._make_pest_model("Pest B", rules=[])
        weather = self._make_weather_data([
            self._make_hour("2024-06-01T00:00:00", temperature_2m=15.0),
        ])

        result = ri.calculate_risk_index_probability_wd(
            parcel={"@id": "urn:test:parcel"},
            pest_models=[pm1, pm2],
            weather_data=weather,
            lat=48.5,
            lon=21.3,
        )

        assert len(result["@graph"]) == 2

    def test_growth_stage_escalate_default_false_unchanged_behavior(self):
        """Every existing caller (no growth-stage concept) is unaffected: the
        new parameter defaults to False and produces byte-identical output."""
        ri = _import_risk_index()

        cond = self._make_cond("atmospheric_temperature", ">", 15.0)
        moderate_rule = self._make_rule("moderate", [cond])
        pm = self._make_pest_model("Test Pest", [moderate_rule])
        weather = self._make_weather_data([
            self._make_hour("2024-06-01T00:00:00", temperature_2m=20.0),
        ])

        result = ri.calculate_risk_index_probability_wd(
            parcel={"@id": "urn:test:parcel"},
            pest_models=[pm],
            weather_data=weather,
            lat=48.5,
            lon=21.3,
        )
        assert result["@graph"][0]["hasMember"][0]["hasSimpleResult"] == "moderate"

    def test_growth_stage_escalate_true_bumps_moderate_to_high(self):
        """A concrete growth
        stage must enter the rule evaluation itself — proven here by the
        escalation flag changing the computed level, not just being copied
        into a provenance field afterward."""
        ri = _import_risk_index()

        cond = self._make_cond("atmospheric_temperature", ">", 15.0)
        moderate_rule = self._make_rule("moderate", [cond])
        pm = self._make_pest_model("Test Pest", [moderate_rule])
        weather = self._make_weather_data([
            self._make_hour("2024-06-01T00:00:00", temperature_2m=20.0),
        ])

        result = ri.calculate_risk_index_probability_wd(
            parcel={"@id": "urn:test:parcel"},
            pest_models=[pm],
            weather_data=weather,
            lat=48.5,
            lon=21.3,
            growth_stage_escalate=True,
        )
        assert result["@graph"][0]["hasMember"][0]["hasSimpleResult"] == "high"

    def test_growth_stage_escalate_never_manufactures_risk_from_low(self):
        """An hour with no weather signal at all (baseline 'low') must stay
        'low' regardless of growth stage — escalation only compounds an
        already-elevated weather signal, it never invents one (no sourced
        growth-stage-only quantitative threshold exists)."""
        ri = _import_risk_index()

        cond = self._make_cond("atmospheric_temperature", ">", 100.0)
        high_rule = self._make_rule("high", [cond])
        pm = self._make_pest_model("Test Pest", [high_rule])
        weather = self._make_weather_data([
            self._make_hour("2024-06-01T00:00:00", temperature_2m=20.0),
        ])

        result = ri.calculate_risk_index_probability_wd(
            parcel={"@id": "urn:test:parcel"},
            pest_models=[pm],
            weather_data=weather,
            lat=48.5,
            lon=21.3,
            growth_stage_escalate=True,
        )
        assert result["@graph"][0]["hasMember"][0]["hasSimpleResult"] == "low"

    def test_graph_entries_contain_location(self):
        """Each graph entry must include lat/lon in hasFeatureOfInterest."""
        ri = _import_risk_index()
        pm = self._make_pest_model("LocPest", rules=[])
        weather = self._make_weather_data([
            self._make_hour("2024-06-01T00:00:00", temperature_2m=10.0),
        ])

        result = ri.calculate_risk_index_probability_wd(
            parcel={"@id": "urn:test:parcel"},
            pest_models=[pm],
            weather_data=weather,
            lat=51.1,
            lon=17.0,
        )

        foi = result["@graph"][0]["hasFeatureOfInterest"]
        assert str(foi["lat"]) == "51.1"
        assert str(foi["long"]) == "17.0"
