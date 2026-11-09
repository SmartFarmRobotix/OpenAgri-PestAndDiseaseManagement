"""
Root conftest.py for OpenAgri-PestAndDiseaseManagement tests.

This file is loaded by pytest before any test module is imported, which gives
us the opportunity to:

  1. Inject all environment variables required by ``core.config.Settings``,
     which is instantiated at *module level* in ``app/core/config.py``. Without
     these vars the pydantic-settings validator raises a ValidationError the
     moment any module in the ``core`` / ``crud`` / ``utils`` package is
     imported, causing every test to fail at collection time.

  2. Pre-populate ``sys.modules`` with lightweight ``MagicMock`` stubs for
     third-party packages that are **not** installed in the test virtual-env
     (``openmeteo_requests``, ``requests_cache``, ``retry_requests``,
     ``shapely``). Several ``utils`` submodules import these at module level;
     mocking them here prevents ``ModuleNotFoundError`` during collection while
     keeping the actual business logic under test unaffected.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock


_TEST_ENV: dict[str, str] = {
    "CORS_ORIGINS": '["http://localhost:3000"]',
    "POSTGRES_USER": "test_user",
    "POSTGRES_PASSWORD": "test_password",
    "POSTGRES_DB": "test_db",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    "ACCESS_TOKEN_EXPIRATION_TIME": "3600",
    "REFRESH_TOKEN_EXPIRATION_TIME": "86400",
    "JWT_KEY": "test_jwt_key_for_unit_tests_only_never_use_in_prod",
    "SERVICE_PORT": "8001",
    "JWT_ALGORITHM": "HS256",
    "USING_GATEKEEPER": "false",
    "GATEKEEPER_BASE_URL": "http://localhost:8000",
    "GATEKEEPER_USERNAME": "admin",
    "GATEKEEPER_PASSWORD": "admin",
    "SERVICE_NAME": "pdm",
    "LOGGING": "INFO",
}

for _key, _val in _TEST_ENV.items():
    os.environ.setdefault(_key, _val)


_STUB_MODULES: list[str] = [
    "openmeteo_requests",
    "requests_cache",
    "retry_requests",
    "shapely",
    "shapely.wkt",
    "shapely.errors",
]

for _mod_name in _STUB_MODULES:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()
