import contextvars
import logging

from core import settings

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


class RequestIdLogFilter(logging.Filter):
    """Injects the current context-local request id into each log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


# Set to level specified in .env
logging.basicConfig(
    level=settings.LOGGING,
    format="%(asctime)s %(levelname)s [request_id=%(request_id)s] %(name)s: %(message)s",
)
for _handler in logging.getLogger().handlers:
    _handler.addFilter(RequestIdLogFilter())

def get_logger(api_path_name: str):
    if settings.LOGGING:
        return logging.getLogger(api_path_name)

    return None