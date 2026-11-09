from typing import Optional, Any, List

from password_validator import PasswordValidator
from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings
from os import path, environ


PLACEHOLDER_JWT_SECRETS = {
    "change-me",
    "some-key",
    "dev_jwt_signing_key",
    "smoketest-jwt-key-change-in-production",
}


class Settings(BaseSettings):
    CORS_ORIGINS: List[AnyHttpUrl] | List[str] = None

    PROJECT_ROOT: str = path.dirname(path.dirname(path.realpath(__file__)))

    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    POSTGRES_HOST: str
    POSTGRES_PORT: int

    SQLALCHEMY_DATABASE_URI: Optional[str] = None

    @field_validator("SQLALCHEMY_DATABASE_URI", mode="before")
    def assemble_db_connection(cls, v: Optional[str], values) -> Any:
        if isinstance(v, str):
            return v

        url = "postgresql://{}:{}@{}:{}/{}".format(
            environ.get("POSTGRES_USER"),
            environ.get("POSTGRES_PASSWORD"),
            environ.get("POSTGRES_HOST"),
            environ.get("POSTGRES_PORT"),
            environ.get("POSTGRES_DB")
        )

        return url

    PASSWORD_SCHEMA_OBJ: PasswordValidator = PasswordValidator()
    PASSWORD_SCHEMA_OBJ \
        .min(8) \
        .max(100) \
        .has().uppercase() \
        .has().lowercase() \
        .has().digits() \
        .has().no().spaces() \

    ACCESS_TOKEN_EXPIRATION_TIME: int
    REFRESH_TOKEN_EXPIRATION_TIME: int
    JWT_KEY: str
    SERVICE_PORT: int
    JWT_ALGORITHM: str

    @field_validator("JWT_KEY")
    def reject_placeholder_jwt_key(cls, v: str) -> str:
        """
        Fail startup with an explicit error
        rather than silently signing tokens with a known-placeholder secret.
        """
        if v in PLACEHOLDER_JWT_SECRETS:
            raise ValueError(
                "JWT_KEY is set to a known placeholder secret ({!r}); refusing "
                "to start. Set JWT_KEY to a real, non-placeholder signing "
                "secret.".format(v)
            )
        return v

    # Gatekeeper info
    USING_GATEKEEPER: bool
    GATEKEEPER_BASE_URL: Optional[AnyHttpUrl] = None
    GATEKEEPER_USERNAME: str
    GATEKEEPER_PASSWORD: str
    SERVICE_NAME: str
    LOGGING: str

    GK_REGISTRATION_TIMEOUT_SECONDS: float = 5

    WEATHER_PROXY_TIMEOUT_SECONDS: float = 15

    PDM_WEATHER_ENABLED: bool = True


settings = Settings()
