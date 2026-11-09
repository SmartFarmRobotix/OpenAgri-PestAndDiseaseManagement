"""
Unit tests for main.py's startup weather-configuration guard.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

_PDM_APP = os.path.join(os.path.dirname(__file__), "..")
if _PDM_APP not in sys.path:
    sys.path.insert(0, _PDM_APP)

import main as main_module  # noqa: E402
from init.pest_model_seed import PestModelSeedError  # noqa: E402


class TestValidateWeatherConfiguration:
    def test_weather_disabled_skips_seeding_entirely(self, monkeypatch):
        monkeypatch.setattr(main_module.settings, "PDM_WEATHER_ENABLED", False)
        session_opened = []
        monkeypatch.setattr(
            main_module, "SessionLocal", lambda: session_opened.append(1) or MagicMock()
        )

        main_module._validate_weather_configuration()

        assert session_opened == []

    def test_weather_enabled_seed_failure_raises_configuration_error(self, monkeypatch):
        monkeypatch.setattr(main_module.settings, "PDM_WEATHER_ENABLED", True)
        db = MagicMock()
        monkeypatch.setattr(main_module, "SessionLocal", lambda: db)

        def _raise(db_arg):
            raise PestModelSeedError("boom")

        monkeypatch.setattr(main_module, "seed_pest_models", _raise)

        with pytest.raises(main_module.PestModelConfigurationError):
            main_module._validate_weather_configuration()

        db.close.assert_called_once()

    def test_weather_enabled_empty_pest_models_after_seed_raises(self, monkeypatch):
        monkeypatch.setattr(main_module.settings, "PDM_WEATHER_ENABLED", True)
        db = MagicMock()
        monkeypatch.setattr(main_module, "SessionLocal", lambda: db)
        monkeypatch.setattr(main_module, "seed_pest_models", lambda db_arg: None)
        monkeypatch.setattr(main_module.crud.pest_model, "get_all", lambda db: [])

        with pytest.raises(main_module.PestModelConfigurationError):
            main_module._validate_weather_configuration()

        db.close.assert_called_once()

    def test_weather_enabled_valid_configuration_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(main_module.settings, "PDM_WEATHER_ENABLED", True)
        db = MagicMock()
        monkeypatch.setattr(main_module, "SessionLocal", lambda: db)
        monkeypatch.setattr(main_module, "seed_pest_models", lambda db_arg: None)
        monkeypatch.setattr(main_module.crud.pest_model, "get_all", lambda db: [MagicMock()])

        main_module._validate_weather_configuration()

        db.close.assert_called_once()


class TestHistoricalDefectReproduction:
    """
    With PDM_WEATHER_ENABLED=true and no pest models after seeding, startup is
    refused with PestModelConfigurationError, instead of every request answering
    with risk_assessment=None and a string in `errors`.
    """

    def test_weather_enabled_empty_pest_models_fails_loudly_at_startup(self, monkeypatch):
        monkeypatch.setattr(main_module.settings, "PDM_WEATHER_ENABLED", True)
        db = MagicMock()
        monkeypatch.setattr(main_module, "SessionLocal", lambda: db)
        monkeypatch.setattr(main_module, "seed_pest_models", lambda db_arg: None)
        monkeypatch.setattr(main_module.crud.pest_model, "get_all", lambda db: [])

        with pytest.raises(main_module.PestModelConfigurationError):
            main_module._validate_weather_configuration()

        db.close.assert_called_once()
