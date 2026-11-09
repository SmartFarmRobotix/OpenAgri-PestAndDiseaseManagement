"""
`core.config.Settings` must fail construction -- not silently start -- when
`JWT_KEY` is a known placeholder value. Exercises the pydantic
`field_validator` directly, which is exactly what runs the moment the
process-wide `settings = Settings` singleton is constructed at import
time (root conftest.py already provides all *other* required env vars for
every test in this suite).
"""
from __future__ import annotations

from pydantic import ValidationError

from core.config import Settings, PLACEHOLDER_JWT_SECRETS


class TestPlaceholderJWTKeyRejection:
    def test_each_known_placeholder_value_is_rejected(self):
        for placeholder in PLACEHOLDER_JWT_SECRETS:
            try:
                Settings(JWT_KEY=placeholder)
            except ValidationError:
                continue
            raise AssertionError(
                f"Settings(JWT_KEY={placeholder!r}) must raise ValidationError"
            )

    def test_real_shaped_secret_is_accepted(self):
        settings = Settings(JWT_KEY="a-real-unique-signing-secret-not-on-the-blocklist")
        assert settings.JWT_KEY == "a-real-unique-signing-secret-not-on-the-blocklist"
