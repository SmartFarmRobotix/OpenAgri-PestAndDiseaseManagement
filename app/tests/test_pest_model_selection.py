"""Crop-scoped pest-model selection tests."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_PDM_APP = os.path.join(os.path.dirname(__file__), "..")
if _PDM_APP not in sys.path:
    sys.path.insert(0, _PDM_APP)

from api.api_v1.endpoints import risk_assessment as endpoint  # noqa: E402
from crud import pest_model as pest_model_crud  # noqa: E402
from db.base_class import Base  # noqa: E402
from init import pest_model_seed  # noqa: E402
from models import PestModel  # noqa: E402
from schemas.contract import GrowthStage, RiskInput  # noqa: E402
from schemas.risk_assessment import ForecastRiskRequest  # noqa: E402
from utils.risk_index import calculate_risk_index_probability_wd  # noqa: E402


def _seeded_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add_all([
        PestModel(name="Cabbage aphid", crops=["brassica-oleracea", "brassica-oleracea-sabellica"], eppo_code="BRVCBR"),
        PestModel(name="Green peach aphid", crops=["cucumis-sativus"], eppo_code="MYZUPE"),
        PestModel(name="Melon aphid", crops=["cucumis-sativus"], eppo_code="APHIGO"),
    ])
    db.commit()
    return db


def _risk_input(crop: str) -> RiskInput:
    return RiskInput(
        session_id="crop-selection", infestation_score=0.6, crop=crop,
        growth_stage=GrowthStage.unknown_for("enum"),
        time_start="2026-09-23T00:00:00Z", time_end="2026-09-23T01:00:00Z",
        latitude=45.0, longitude=10.0,
    )


def test_real_get_for_crop_selects_only_the_two_cucumber_seeded_rows():
    db = _seeded_db()
    assert [pm.eppo_code for pm in pest_model_crud.get_for_crop(db, "cucumis-sativus")] == ["MYZUPE", "APHIGO"]


def test_real_get_for_crop_selects_only_the_cabbage_model_for_kale():
    db = _seeded_db()
    assert [pm.name for pm in pest_model_crud.get_for_crop(db, "brassica-oleracea-sabellica")] == ["Cabbage aphid"]


def test_unknown_crop_is_score_only_with_a_crop_specific_reason(monkeypatch):
    monkeypatch.setattr(endpoint.crud.pest_model, "get_for_crop", lambda **_: [])
    monkeypatch.setattr(endpoint.settings, "PDM_WEATHER_ENABLED", True)
    monkeypatch.setattr(endpoint, "fetch_weather_data", lambda **_: {"data": []})
    result = endpoint.assess_risk(_risk_input("unknown-crop"), access_token="token", db=MagicMock())
    assert result.risk_assessment.provenance["degraded_reason"] == "no_pest_model_for_crop"
    assert "no weather rules for this crop" in result.risk_assessment.details


def test_seed_backfills_an_existing_model_without_creating_a_duplicate(monkeypatch):
    existing = SimpleNamespace(name="Cabbage aphid", crops=None, eppo_code=None)
    db = MagicMock()
    monkeypatch.setattr(pest_model_seed, "_SEED_FILES", ["cabbage_aphid_brvcbr_v1.json"])
    monkeypatch.setattr(pest_model_seed.crud.pest_model, "get_all", lambda db: [existing])
    monkeypatch.setattr(pest_model_seed.crud.pest_model, "create", lambda **_: (_ for _ in ()).throw(AssertionError("duplicate")))
    pest_model_seed.seed_pest_models(db)
    assert existing.crops == ["brassica-oleracea", "brassica-oleracea-sabellica"]
    assert existing.eppo_code == "BRVCBR"
    db.commit.assert_called_once()


def test_seed_corrects_a_legacy_code_on_an_existing_row_without_creating_a_duplicate(monkeypatch):
    """A row already seeded with the legacy
    code BREBRA is corrected to BRVCBR in place; its crops are kept, and no
    new pest model is created."""
    existing = SimpleNamespace(
        name="Cabbage aphid", crops=["brassica-oleracea", "brassica-oleracea-sabellica"], eppo_code="BREBRA"
    )
    db = MagicMock()
    monkeypatch.setattr(pest_model_seed, "_SEED_FILES", ["cabbage_aphid_brvcbr_v1.json"])
    monkeypatch.setattr(pest_model_seed.crud.pest_model, "get_all", lambda db: [existing])
    monkeypatch.setattr(pest_model_seed.crud.pest_model, "create", lambda **_: (_ for _ in ()).throw(AssertionError("duplicate")))
    pest_model_seed.seed_pest_models(db)
    assert existing.crops == ["brassica-oleracea", "brassica-oleracea-sabellica"]
    assert existing.eppo_code == "BRVCBR"
    db.commit.assert_called_once()


def test_forecast_endpoint_selects_models_by_the_requested_crop(monkeypatch):
    pm = SimpleNamespace(name="Melon aphid", crops=["cucumis-sativus"], rules=[])
    selected = []

    def _get_for_crop(*, db, crop):
        selected.append(crop)
        return [pm]

    monkeypatch.setattr(endpoint.crud.pest_model, "get_for_crop", _get_for_crop)
    result = endpoint.assess_forecast_risk(
        ForecastRiskRequest(crop="cucumis-sativus", days=[{"date": "2026-09-24", "temp_max": 25, "temp_min": 18}]),
        db=MagicMock(),
    )
    assert selected == ["cucumis-sativus"]
    assert [(day.date, day.level) for day in result.days] == [("2026-09-24", "low")]


def test_jsonld_graph_uses_the_evaluated_model_eppo_concept():
    pm = SimpleNamespace(name="Green peach aphid", eppo_code="MYZUPE", rules=[])
    graph = calculate_risk_index_probability_wd(
        parcel={"@id": "urn:parcel:test"}, pest_models=[pm], weather_data={"data": []}, lat=45.0, lon=10.0,
    )
    pest = graph["@graph"][0]["observedProperty"]["hasAgriPest"]
    assert pest == {"@id": "urn:openagri:pest:MYZUPE", "@type": "AgriPest", "name": "Green peach aphid", "eppoConcept": "https://gd.eppo.int/taxon/MYZUPE"}
