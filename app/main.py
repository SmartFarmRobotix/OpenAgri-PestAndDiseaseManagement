from contextlib import asynccontextmanager

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request, Response
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

import crud
from api.api_v1.api import api_router
from core.config import settings
from core.version import PDM_APP_VERSION
from db.session import SessionLocal, engine
from init.db_init import init_db
from init.init_gatekeeper import register_apis_to_gatekeeper
from init.pest_model_seed import PestModelSeedError, seed_pest_models
from utils.custom_logger import request_id_var

from jobs.background_tasks import get_open_meteo_data


import logging

logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Reads the inbound X-Request-ID header
    (when present) and makes it available to structured log lines for the
    duration of the request via a context-local var (utils.custom_logger).
    Does not mint an id when the header is absent — this is consumption of
    an id GK already generates upstream, not end-to-end propagation.
    """

    async def dispatch(self, request: Request, call_next):
        incoming_request_id = request.headers.get("X-Request-ID")
        token = request_id_var.set(incoming_request_id or "-")
        try:
            response = await call_next(request)
            logger.info(
                "%s %s -> %s", request.method, request.url.path, response.status_code
            )
        finally:
            request_id_var.reset(token)
        if incoming_request_id:
            response.headers["X-Request-ID"] = incoming_request_id
        return response


class PestModelConfigurationError(RuntimeError):
    """
    Weather mode is enabled but no
    pest models are configured. Failing startup here replaces the old
    behavior of silently skipping the risk index at request time.
    """


def _validate_weather_configuration() -> None:
    """
    Log the effective, declared weather mode (observable at
    startup) and, when enabled, seed/verify the pest-model ruleset(s) that
    mode depends on.
    """
    logger.info(
        "PDM weather configuration: PDM_WEATHER_ENABLED=%s",
        settings.PDM_WEATHER_ENABLED,
    )
    if not settings.PDM_WEATHER_ENABLED:
        return

    db = SessionLocal()
    try:
        try:
            seed_pest_models(db)
        except PestModelSeedError as exc:
            raise PestModelConfigurationError(
                f"PDM_WEATHER_ENABLED=true but pest-model seeding failed: {exc}"
            ) from exc

        pest_models = crud.pest_model.get_all(db=db)
        if not pest_models:
            raise PestModelConfigurationError(
                "PDM_WEATHER_ENABLED=true but no pest models are configured "
                "after seeding — refusing to start with weather mode enabled "
                "against an empty pest-model set."
            )
        logger.info(
            "PDM weather configuration valid: %d pest model(s) configured",
            len(pest_models),
        )
    finally:
        db.close()


@asynccontextmanager
async def lifespan(fa: FastAPI):
    init_db()
    _validate_weather_configuration()
    if settings.USING_GATEKEEPER:
        register_apis_to_gatekeeper()
    if not settings.USING_GATEKEEPER:
        scheduler.add_job(get_open_meteo_data, 'cron', day_of_week='*', hour=0, minute=5, second=0)
    scheduler.start()
    yield
    scheduler.shutdown()
    logging.shutdown()


app = FastAPI(
    title="Pest and Disease Management",
    version=PDM_APP_VERSION,
    openapi_url="/api/v1/openapi.json",
    lifespan=lifespan,
)

jobstores = {
    'default': MemoryJobStore()
}

scheduler = AsyncIOScheduler(jobstores=jobstores)

app.add_middleware(RequestIDMiddleware)

# Set all CORS enabled origins
if settings.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
def health() -> dict:
    """
    Liveness probe. Returns 2xx as long as
    the process is up and able to handle requests — deliberately has no
    dependency on the database or any other downstream service.
    """
    return {"status": "ok"}


@app.get("/ready")
def ready(response: Response) -> dict:
    """
    Readiness probe. Verifies the database
    connection is usable; returns a degraded body (and non-2xx status) when
    it is not, so orchestration tooling can distinguish "process is up" from
    "process can actually serve requests".
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("Readiness check failed: DB unreachable: %s", exc)
        response.status_code = 503
        return {"status": "degraded", "detail": "database unreachable"}

    return {"status": "ok"}
