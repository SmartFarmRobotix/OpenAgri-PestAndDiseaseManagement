"""
Unit tests for pest-model seeding.

crud calls are mocked (no DB); load_seed_definitions reads the real JSON
file from disk.
"""
from __future__ import annotations

import os
import sys
import uuid
from unittest.mock import MagicMock

import pytest

_PDM_APP = os.path.join(os.path.dirname(__file__), "..")
if _PDM_APP not in sys.path:
    sys.path.insert(0, _PDM_APP)

from init import pest_model_seed  # noqa: E402


class TestLoadSeedDefinitions:
    def test_loads_cabbage_aphid_definition(self):
        definitions = pest_model_seed.load_seed_definitions()
        names = [d["name"] for d in definitions]
        assert "Cabbage aphid" in names

    def test_missing_file_raises_seed_error(self, monkeypatch):
        monkeypatch.setattr(pest_model_seed, "_SEED_FILES", ["does_not_exist.json"])
        with pytest.raises(pest_model_seed.PestModelSeedError):
            pest_model_seed.load_seed_definitions()


class TestRulesetVersionByName:
    """The ruleset version PDM already
    carries internally (each seed JSON's "version" field), exposed by name
    so `utils.risk_assessment_core` can attach it to provenance without a
    DB migration."""

    def test_cabbage_aphid_version_resolves_from_seed_json(self):
        pest_model_seed.ruleset_version_by_name.cache_clear()
        registry = pest_model_seed.ruleset_version_by_name()
        assert registry["Cabbage aphid"] == "1.0.0"

    def test_unknown_name_absent_not_guessed(self):
        pest_model_seed.ruleset_version_by_name.cache_clear()
        registry = pest_model_seed.ruleset_version_by_name()
        assert "some Excel-uploaded model" not in registry


class TestSeedPestModels:
    def _patch_crud(self, monkeypatch, existing_names=()):
        db = MagicMock()

        created_pest_models = []
        created_rules = []
        created_conditions = []

        monkeypatch.setattr(
            pest_model_seed.crud.pest_model,
            "get_all",
            lambda db: [MagicMock(name=n) for n in existing_names],
        )

        def _create_pest_model(db, obj_in):
            pm = MagicMock()
            pm.id = uuid.uuid4()
            pm.name = obj_in.name
            created_pest_models.append(pm)
            return pm

        monkeypatch.setattr(pest_model_seed.crud.pest_model, "create", _create_pest_model)

        def _create_rule(db, obj_in):
            rule = MagicMock()
            rule.id = len(created_rules) + 1
            rule.name = obj_in.name
            created_rules.append(rule)
            return rule

        monkeypatch.setattr(pest_model_seed.crud.rule, "create", _create_rule)

        def _create_condition(db, obj_in):
            created_conditions.append(obj_in)
            return MagicMock()

        monkeypatch.setattr(pest_model_seed.crud.condition, "create", _create_condition)

        unit_mock = MagicMock()
        unit_mock.id = 1
        operator_mock = MagicMock()
        operator_mock.id = 1
        monkeypatch.setattr(pest_model_seed.crud.unit, "get_by_name", lambda db, name: unit_mock)
        monkeypatch.setattr(pest_model_seed.crud.operator, "get_by_symbol", lambda db, symbol: operator_mock)

        return db, created_pest_models, created_rules, created_conditions

    def test_seeds_pest_model_rules_and_conditions(self, monkeypatch):
        db, created_pest_models, created_rules, created_conditions = self._patch_crud(
            monkeypatch, existing_names=()
        )

        pest_model_seed.seed_pest_models(db)

        definitions = pest_model_seed.load_seed_definitions()
        assert {model.name for model in created_pest_models} == {
            "Cabbage aphid",
            "Green peach aphid",
            "Melon aphid",
        }
        assert len(created_pest_models) == 3
        assert len(created_rules) == sum(len(definition["rules"]) for definition in definitions)
        assert len(created_conditions) > 0

    def test_idempotent_when_pest_model_already_exists(self, monkeypatch):
        db = MagicMock()

        class _Existing:
            def __init__(self, definition):
                self.name = definition["name"]
                self.crops = definition["crops"]
                self.eppo_code = definition["eppo_code"]

        definitions = pest_model_seed.load_seed_definitions()
        monkeypatch.setattr(
            pest_model_seed.crud.pest_model,
            "get_all",
            lambda db: [_Existing(definition) for definition in definitions],
        )

        created = []
        monkeypatch.setattr(
            pest_model_seed.crud.pest_model,
            "create",
            lambda db, obj_in: created.append(obj_in) or MagicMock(),
        )

        pest_model_seed.seed_pest_models(db)

        assert created == []

    def test_seed_failure_raises_pest_model_seed_error(self, monkeypatch):
        db, *_ = self._patch_crud(monkeypatch, existing_names=())
        monkeypatch.setattr(pest_model_seed.crud.pest_model, "create", lambda db, obj_in: None)

        with pytest.raises(pest_model_seed.PestModelSeedError):
            pest_model_seed.seed_pest_models(db)

    def test_unknown_unit_raises_pest_model_seed_error(self, monkeypatch):
        db, *_ = self._patch_crud(monkeypatch, existing_names=())
        monkeypatch.setattr(pest_model_seed.crud.unit, "get_by_name", lambda db, name: None)

        with pytest.raises(pest_model_seed.PestModelSeedError):
            pest_model_seed.seed_pest_models(db)
