"""
Unit tests: wdutils.py's weather-data
proxy call must distinguish a connection-level failure (GK unreachable, DNS
failure, timeout -- requests never got an HTTP response at all) from an
HTTP-level upstream error (GK reachable, responded with a non-2xx status),
instead of collapsing everything into a blanket 400.
"""
from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest
import requests
from fastapi import HTTPException

from utils.wdutils import TimeUnit, fetch_weather_data


def _call(**overrides):
    kwargs = dict(
        latitude=48.5,
        longitude=21.3,
        start_date=datetime.date(2024, 1, 1),
        end_date=datetime.date(2024, 1, 2),
        variables=["atmospheric_temperature"],
        access_token="test-token",
        how_often=TimeUnit.DAILY,
    )
    kwargs.update(overrides)
    return fetch_weather_data(**kwargs)


class TestConnectionLevelFailure:
    """A forced connection failure (no HTTP response at all)."""

    def test_connection_error_is_503_not_400(self):
        with patch(
            "utils.wdutils.requests.post",
            side_effect=requests.exceptions.ConnectionError("simulated: GK unreachable"),
        ):
            with pytest.raises(HTTPException) as excinfo:
                _call()
        assert excinfo.value.status_code == 503

    def test_timeout_is_503_not_400(self):
        with patch(
            "utils.wdutils.requests.post",
            side_effect=requests.exceptions.Timeout("simulated: GK not responding"),
        ):
            with pytest.raises(HTTPException) as excinfo:
                _call()
        assert excinfo.value.status_code == 503


class TestHTTPLevelUpstreamError:
    """GK is reachable and answers -- these must stay distinguishable from
    the connection-level 503 above, and from each other."""

    @staticmethod
    def _response(status_code, reason="error", json_body=None):
        resp = MagicMock()
        resp.status_code = status_code
        resp.reason = reason
        resp.ok = status_code < 400
        resp.json.return_value = json_body or {}
        return resp

    def test_forced_gk_404_is_404_not_400(self):
        with patch("utils.wdutils.requests.post", return_value=self._response(404)):
            with pytest.raises(HTTPException) as excinfo:
                _call()
        assert excinfo.value.status_code == 404

    def test_upstream_400_is_400(self):
        with patch("utils.wdutils.requests.post", return_value=self._response(400)):
            with pytest.raises(HTTPException) as excinfo:
                _call()
        assert excinfo.value.status_code == 400

    def test_other_non_2xx_upstream_status_is_502_not_400(self):
        with patch("utils.wdutils.requests.post", return_value=self._response(500)):
            with pytest.raises(HTTPException) as excinfo:
                _call()
        assert excinfo.value.status_code == 502

    def test_2xx_upstream_response_is_returned_as_json(self):
        payload = {"result": "ok"}
        with patch(
            "utils.wdutils.requests.post", return_value=self._response(200, json_body=payload)
        ):
            assert _call() == payload

    def test_connection_failure_and_forced_404_are_distinguishable(self):
        """The crux: these two failure classes must not collapse
        into the same status code."""
        with patch(
            "utils.wdutils.requests.post",
            side_effect=requests.exceptions.ConnectionError("simulated"),
        ):
            with pytest.raises(HTTPException) as connection_excinfo:
                _call()

        with patch("utils.wdutils.requests.post", return_value=self._response(404)):
            with pytest.raises(HTTPException) as http_excinfo:
                _call()

        assert connection_excinfo.value.status_code != http_excinfo.value.status_code
