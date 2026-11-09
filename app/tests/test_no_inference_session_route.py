"""
The service exposes no `/inference-session/` intake route: the session orchestrator owns intake and
orchestration, and this service serves only the scalar rule-assessment call (`/api/v1/risk-assessment/`).
"""
from __future__ import annotations

import os
import sys

_PDM_APP = os.path.join(os.path.dirname(__file__), "..")
if _PDM_APP not in sys.path:
    sys.path.insert(0, _PDM_APP)

from api.api_v1.api import api_router  # noqa: E402


class TestNoInferenceSessionEndpoint:
    def test_no_inference_session_route_registered(self):
        paths = {route.path for route in api_router.routes}
        assert "/inference-session/" not in paths
        assert not any("inference-session" in path for path in paths)

    def test_no_inference_session_module(self):
        try:
            import api.api_v1.endpoints.inference_session  # noqa: F401
        except ModuleNotFoundError:
            pass
        else:
            raise AssertionError("the service has no inference-session intake module")

    def test_risk_assessment_route_still_present(self):
        """The scalar rule-assessment call the orchestrator actually invokes is present."""
        paths = {route.path for route in api_router.routes}
        assert "/risk-assessment/" in paths
