"""Logging de la aplicación: configuración, peticiones HTTP y errores de validación."""

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import settings

logger = logging.getLogger(__name__)

LOG_FILE = "pokedex_api.log"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
# Librerías que escriben una línea INFO por cada petición que ya registramos nosotros.
QUIET_LOGGERS = ("httpx",)


def configure_logging(log_file: str = LOG_FILE) -> None:
    """Envía los logs de la aplicación a un archivo y a la consola.

    Si el logging ya está configurado (por ejemplo, por pytest) no cambia nada ni crea el archivo.
    `httpx` se sube a WARNING: `pokeapi_service` ya registra cada llamada con su duración, y
    si no cada una saldría dos veces. Sus avisos y errores se siguen viendo.
    """
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format=LOG_FORMAT,
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
    )
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


async def log_requests(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Registra método, path, status y duración de cada petición.

    Solo se registra el path, no la query, porque podría contener datos sensibles.
    """
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "%s %s -> 500 (%.0f ms)", request.method, request.url.path, _elapsed_ms(start)
        )
        raise
    logger.info(
        "%s %s -> %s (%.0f ms)",
        request.method,
        request.url.path,
        response.status_code,
        _elapsed_ms(start),
    )
    return response


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Registra el error de validación y responde el 422 estándar de FastAPI.

    Solo se registran el campo y el tipo de error: el valor recibido podría ser una contraseña.
    """
    errors = [
        f"{'.'.join(str(part) for part in error['loc'])}: {error['type']}"
        for error in exc.errors()
    ]
    logger.warning(
        "Error de validación en %s %s: %s", request.method, request.url.path, "; ".join(errors)
    )
    return await request_validation_exception_handler(request, exc)


def _elapsed_ms(start: float) -> float:
    """Milisegundos transcurridos desde `start`."""
    return (time.perf_counter() - start) * 1000
