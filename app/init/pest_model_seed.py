"""
Pest-model ruleset seeding.

The stock service never seeds pest models: unless an operator calls `POST /pest-model/`
or the Excel upload route, `crud.pest_model.get_all` is empty and the weather rules never
fire. This module loads the versioned rulesets under `app/pest_models/` at startup,
idempotently (by pest-model name), using the same crud calls the Excel-upload endpoint
relies on.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any, Dict, List

from sqlalchemy.orm import Session

import crud
from schemas import CreateCondition, CreatePestModel, CreateRule

logger = logging.getLogger(__name__)

_SEED_DIR = os.path.join(os.path.dirname(__file__), "..", "pest_models")
_SEED_FILES = [
    "cabbage_aphid_brvcbr_v1.json",
    "green_peach_aphid_myzupe_v1.json",
    "melon_aphid_aphigo_v1.json",
]

_LEGACY_EPPO_CODES = {"BREBRA": "BRVCBR"}


class PestModelSeedError(RuntimeError):
    """
    Raised when a pest-model seed definition cannot be loaded or applied. With weather mode enabled, a failure here must surface as an
    explicit startup configuration error, never a silent empty pest_models
    list that later degrades into the old "risk index skipped" behavior.
    """


def load_seed_definitions() -> List[Dict[str, Any]]:
    """Load and parse every registered seed file. Pure I/O, no DB access."""
    definitions = []
    for filename in _SEED_FILES:
        path = os.path.join(_SEED_DIR, filename)
        if not os.path.isfile(path):
            raise PestModelSeedError(f"Pest-model seed file not found: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            try:
                definitions.append(json.load(fh))
            except json.JSONDecodeError as exc:
                raise PestModelSeedError(f"Invalid JSON in seed file {path}: {exc}") from exc
    return definitions


@lru_cache(maxsize=1)
def ruleset_version_by_name() -> Dict[str, str]:
    """
    The ruleset version each seeded
    pest model already carries internally is this JSON `"version"` field
    (e.g. `cabbage_aphid_brvcbr_v1.json`'s `"1.0.0"`) — not tracked anywhere
    in the DB. Read straight from the same seed definitions
    `seed_pest_models` loads at startup, keyed by pest-model name, so
    `utils.risk_assessment_core` can attach it to a session's provenance
    without a schema migration. A pest model with no matching seed entry
    (e.g. Excel-uploaded) is simply absent here — callers must treat that
    as a missing version, never guess one.
    """
    return {
        definition["name"]: definition["version"]
        for definition in load_seed_definitions()
        if definition.get("version")
    }


def seed_pest_models(db: Session) -> None:
    """
    Idempotently seed the versioned pest-model rulesets into the database,
    keyed by name. Safe to call on every startup: a pest model already
    present (matched by name) is left untouched, so an operator-edited
    ruleset in the DB is never clobbered by a redeploy.
    """
    existing_models = {pm.name: pm for pm in crud.pest_model.get_all(db=db)}

    for definition in load_seed_definitions():
        name = definition["name"]
        existing = existing_models.get(name)
        if existing is not None:
            changed = False
            if getattr(existing, "crops", None) is None and definition.get("crops") is not None:
                existing.crops = definition["crops"]
                changed = True
            definition_eppo_code = definition.get("eppo_code")
            existing_eppo_code = getattr(existing, "eppo_code", None)
            if definition_eppo_code is not None and (
                existing_eppo_code is None or existing_eppo_code in _LEGACY_EPPO_CODES
            ):
                existing.eppo_code = definition_eppo_code
                changed = True
            if changed:
                db.add(existing)
                db.commit()
                db.refresh(existing)
            continue

        pest_model_db = crud.pest_model.create(
            db=db,
            obj_in=CreatePestModel(
                name=name,
                description=definition.get("description"),
                geo_areas_of_application=definition.get("geo_areas_of_application"),
                cultivations=[],
                crops=definition.get("crops"),
                eppo_code=definition.get("eppo_code"),
            ),
        )
        if pest_model_db is None:
            raise PestModelSeedError(f"Failed to create pest model {name!r} in the database")

        for rule_def in definition.get("rules", []):
            rule_db = crud.rule.create(
                db=db,
                obj_in=CreateRule(
                    name=rule_def["name"],
                    description=rule_def.get("description"),
                    probability_value=rule_def["probability_value"],
                    pest_model_id=pest_model_db.id,
                ),
            )
            if rule_db is None:
                raise PestModelSeedError(
                    f"Failed to create rule {rule_def['name']!r} for pest model {name!r}"
                )

            for cond_def in rule_def.get("conditions", []):
                unit = crud.unit.get_by_name(db=db, name=cond_def["unit"])
                operator = crud.operator.get_by_symbol(db=db, symbol=cond_def["operator"])
                if unit is None or operator is None:
                    raise PestModelSeedError(
                        f"Rule {rule_def['name']!r} of pest model {name!r} references an "
                        f"unknown unit/operator: unit={cond_def['unit']!r} "
                        f"operator={cond_def['operator']!r} (was init_units()/init_operators() "
                        "run before pest-model seeding?)"
                    )
                crud.condition.create(
                    db=db,
                    obj_in=CreateCondition(
                        rule_id=rule_db.id,
                        unit_id=unit.id,
                        operator_id=operator.id,
                        value=cond_def["value"],
                    ),
                )

        logger.info(
            "Seeded pest model %r from ruleset version %s (%d rules)",
            name,
            definition.get("version", "unknown"),
            len(definition.get("rules", [])),
        )
