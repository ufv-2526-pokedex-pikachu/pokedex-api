"""Rate limiting: instancia única del limiter y límites por tipo de endpoint.

Está en su propio módulo para que los routers lo importen sin depender de `app.main`,
que a su vez importa los routers (sería un import circular).

Uso en un endpoint (slowapi necesita que reciba `request: Request`):

    @router.post("/register")
    @limiter.limit(LIMIT_REGISTER)
    async def register(request: Request, ...): ...
"""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import settings

logger = logging.getLogger(__name__)

LIMIT_REGISTER = "5/hour"
LIMIT_LOGIN = "10/minute"
LIMIT_POKEAPI = "30/minute"
LIMIT_AUTHENTICATED = "100/minute"

limiter = Limiter(key_func=get_remote_address, enabled=settings.RATE_LIMIT_ENABLED)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Registra el exceso y responde 429 con el mismo formato que `HTTPException`."""
    logger.warning(
        "Rate limit superado: %s %s desde %s (límite: %s)",
        request.method,
        request.url.path,
        get_remote_address(request),
        exc.detail,
    )
    return JSONResponse(
        status_code=429,
        content={"detail": "Demasiadas peticiones. Inténtalo de nuevo más tarde."},
        # Duración de la ventana del límite: es la espera máxima hasta poder repetir.
        headers={"Retry-After": str(exc.limit.limit.get_expiry())},
    )
