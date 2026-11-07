"""Cliente asíncrono de PokeAPI: valida la entrada, gestiona errores y registra cada llamada."""

import logging
import re
import time
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import HTTPException

from app.config import settings

logger = logging.getLogger(__name__)

# Solo letras, números y guiones (no al inicio): así "../algo" o "-1" nunca llegan a la URL.
_SLUG_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]*")
_MAX_SLUG_LENGTH = 100
# Tope de conexiones simultáneas, para no saturar una API pública y gratuita.
_MAX_CONNECTIONS = 20


def _elapsed_ms(start: float) -> float:
    """Milisegundos transcurridos desde `start`."""
    return (time.perf_counter() - start) * 1000


def _normalize_slug(value: str | int, field: str) -> str:
    """Devuelve el valor en minúsculas y sin espacios, o lanza 400 si no es válido."""
    text = "" if isinstance(value, bool) else str(value).strip().lower()
    is_valid = len(text) <= _MAX_SLUG_LENGTH and _SLUG_PATTERN.fullmatch(text) is not None
    if not is_valid or (text.isdigit() and int(text) == 0):
        raise HTTPException(status_code=400, detail=f"Valor no válido para {field}")
    return text


def _transform(transformer: Callable[[Any], Any], payload: Any) -> Any:
    """Aplica la transformación; si PokeAPI cambia el formato de la respuesta, lanza 502."""
    try:
        return transformer(payload)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        logger.error("PokeAPI devolvió un formato inesperado: %r", exc)
        raise HTTPException(status_code=502, detail="Respuesta no válida de PokeAPI") from exc


def _to_pokemon(data: dict[str, Any]) -> dict[str, Any]:
    """Extrae de la respuesta de PokeAPI solo los campos que usa nuestra API."""
    sprites = data["sprites"]
    return {
        "id": data["id"],
        "name": data["name"],
        "sprite": sprites["front_default"],
        "sprites": {
            key: sprites.get(key)
            for key in ("front_default", "back_default", "front_shiny", "back_shiny")
        },
        "types": [item["type"]["name"] for item in data["types"]],
        "stats": {item["stat"]["name"]: item["base_stat"] for item in data["stats"]},
        "abilities": [item["ability"]["name"] for item in data["abilities"]],
        "height": data["height"],
        "weight": data["weight"],
    }


class PokeAPIService:
    """Cliente REST de PokeAPI. Todos los fallos se convierten en `HTTPException`."""

    BASE_URL = settings.POKEAPI_BASE_URL.rstrip("/")
    TIMEOUT = settings.POKEAPI_TIMEOUT

    def _new_client(self) -> httpx.AsyncClient:
        """Crea un cliente HTTP con la URL base, el timeout y el tope de conexiones."""
        return httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=self.TIMEOUT,
            limits=httpx.Limits(max_connections=_MAX_CONNECTIONS),
        )

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, int] | None = None,
    ) -> Any:
        """Hace un GET a PokeAPI, registra URL, status y duración, y traduce los errores."""
        url = f"{self.BASE_URL}{path}"
        start = time.perf_counter()
        try:
            response = await client.get(path, params=params)
        except httpx.TimeoutException as exc:
            logger.warning("PokeAPI GET %s -> timeout (%.0f ms)", url, _elapsed_ms(start))
            raise HTTPException(status_code=504, detail="PokeAPI no responde") from exc
        except httpx.RequestError as exc:
            logger.error("PokeAPI GET %s -> error de red: %r", url, exc)
            raise HTTPException(status_code=502, detail="No se pudo conectar con PokeAPI") from exc

        logger.info(
            "PokeAPI GET %s -> %s (%.0f ms)", url, response.status_code, _elapsed_ms(start)
        )
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="No encontrado en PokeAPI")
        if not response.is_success:
            raise HTTPException(status_code=502, detail="Error al consultar PokeAPI")
        try:
            return response.json()
        except ValueError as exc:
            logger.error("PokeAPI GET %s -> respuesta que no es JSON", url)
            raise HTTPException(status_code=502, detail="Respuesta no válida de PokeAPI") from exc

    async def _fetch_pokemon(self, client: httpx.AsyncClient, slug: str) -> dict[str, Any]:
        """Pide un Pokémon con un cliente ya abierto y lo transforma."""
        return _transform(_to_pokemon, await self._get_json(client, f"/pokemon/{slug}"))

    async def get_pokemon(self, identifier: str | int) -> dict[str, Any]:
        """Obtiene los datos de un Pokémon por ID o nombre.

        Lanza 400 si el identificador no es válido, 404 si no existe, 504 si hay
        timeout y 502 si hay un error de red o PokeAPI responde con un fallo.
        """
        slug = _normalize_slug(identifier, "identifier")
        async with self._new_client() as client:
            return await self._fetch_pokemon(client, slug)


# Instancia única de la aplicación.
pokeapi_service = PokeAPIService()


def get_pokeapi_service() -> PokeAPIService:
    """Dependencia de FastAPI: permite sustituir el servicio en los tests de endpoints."""
    return pokeapi_service
