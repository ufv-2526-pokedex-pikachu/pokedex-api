"""Cliente asíncrono de PokeAPI: valida la entrada, gestiona errores y registra cada llamada."""

import asyncio
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
_MAX_SEARCH_LIMIT = 100
# PokeAPI no filtra por texto: para buscar por nombre hay que pedir la lista completa.
_ALL_POKEMON_LIMIT = 100_000
# Tope de respuestas guardadas en la caché, para que la memoria no crezca sin límite.
_CACHE_MAX_ENTRIES = 1000


def _now() -> float:
    """Reloj de la caché: no retrocede aunque cambie la hora del sistema."""
    return time.monotonic()


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


def _to_species(data: dict[str, Any]) -> dict[str, Any]:
    """Extrae la especie con su descripción en español o, si no existe, en inglés."""
    descriptions: dict[str, str] = {}
    for entry in data["flavor_text_entries"]:
        descriptions.setdefault(entry["language"]["name"], entry["flavor_text"])
    description = descriptions.get("es") or descriptions.get("en") or ""
    # Los textos de PokeAPI traen saltos de línea y guiones invisibles de los juegos originales.
    return {
        "id": data["id"],
        "name": data["name"],
        "description": " ".join(description.replace("­", "").split()),
    }


def _to_listing(data: dict[str, Any]) -> tuple[int, list[str]]:
    """Extrae de un listado de PokeAPI el total y los nombres de la página."""
    return data["count"], [item["name"] for item in data["results"]]


def _to_type_members(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrae los Pokémon de un tipo; su ID solo aparece al final de la URL de cada uno."""
    return [
        {
            "id": int(item["pokemon"]["url"].rstrip("/").rsplit("/", 1)[-1]),
            "name": item["pokemon"]["name"],
        }
        for item in data["pokemon"]
    ]


class PokeAPIService:
    """Cliente REST de PokeAPI. Todos los fallos se convierten en `HTTPException`."""

    BASE_URL = settings.POKEAPI_BASE_URL.rstrip("/")
    TIMEOUT = settings.POKEAPI_TIMEOUT

    def __init__(self, cache_ttl: int = settings.POKEAPI_CACHE_TTL) -> None:
        """`cache_ttl` son los segundos que dura cada respuesta guardada; 0 desactiva la caché."""
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, Any]] = {}

    def clear_cache(self) -> None:
        """Vacía la caché (los tests la vacían entre uno y otro para que no se mezclen)."""
        self._cache.clear()

    def _read_cache(self, key: str) -> Any | None:
        """Devuelve la respuesta guardada, o `None` si no existe o ha caducado."""
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires_at, payload = entry
        if _now() >= expires_at:
            del self._cache[key]
            return None
        return payload

    def _write_cache(self, key: str, payload: Any) -> None:
        """Guarda una respuesta correcta; si la caché está llena, descarta la más antigua."""
        if self._cache_ttl <= 0:
            return
        if len(self._cache) >= _CACHE_MAX_ENTRIES:
            del self._cache[next(iter(self._cache))]
        self._cache[key] = (_now() + self._cache_ttl, payload)

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
        """Hace un GET a PokeAPI (o usa la caché), registra la llamada y traduce los errores."""
        url = client.build_request("GET", path, params=params).url
        cached = self._read_cache(str(url))
        if cached is not None:
            logger.info("PokeAPI GET %s -> caché", url)
            return cached

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
            payload = response.json()
        except ValueError as exc:
            logger.error("PokeAPI GET %s -> respuesta que no es JSON", url)
            raise HTTPException(status_code=502, detail="Respuesta no válida de PokeAPI") from exc
        self._write_cache(str(url), payload)
        return payload

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

    async def get_species(self, identifier: str | int) -> dict[str, Any]:
        """Obtiene la especie de un Pokémon, con los mismos errores que `get_pokemon`."""
        slug = _normalize_slug(identifier, "identifier")
        async with self._new_client() as client:
            payload = await self._get_json(client, f"/pokemon-species/{slug}")
        return _transform(_to_species, payload)

    async def search_pokemon(
        self, limit: int = 20, offset: int = 0, name: str | None = None
    ) -> dict[str, Any]:
        """Lista Pokémon con paginación, con filtro opcional por parte del nombre.

        El listado de PokeAPI solo trae nombres, así que el sprite y los tipos de
        cada resultado se piden en paralelo. Lanza 400 si los parámetros no son válidos.
        """
        if not 1 <= limit <= _MAX_SEARCH_LIMIT:
            raise HTTPException(
                status_code=400, detail=f"limit debe estar entre 1 y {_MAX_SEARCH_LIMIT}"
            )
        if offset < 0:
            raise HTTPException(status_code=400, detail="offset no puede ser negativo")
        term = None if name is None else _normalize_slug(name, "name")

        async with self._new_client() as client:
            if term is None:
                params = {"limit": limit, "offset": offset}
                payload = await self._get_json(client, "/pokemon", params)
                count, names = _transform(_to_listing, payload)
            else:
                params = {"limit": _ALL_POKEMON_LIMIT, "offset": 0}
                payload = await self._get_json(client, "/pokemon", params)
                _, all_names = _transform(_to_listing, payload)
                matches = [candidate for candidate in all_names if term in candidate]
                count, names = len(matches), matches[offset : offset + limit]

            pokemons = await asyncio.gather(
                *(self._fetch_pokemon(client, pokemon_name) for pokemon_name in names)
            )

        results = [
            {key: pokemon[key] for key in ("id", "name", "sprite", "types")}
            for pokemon in pokemons
        ]
        return {"count": count, "results": results}

    async def get_pokemon_by_type(self, type_name: str) -> list[dict[str, Any]]:
        """Obtiene todos los Pokémon de un tipo. Lanza 400 si no es válido y 404 si no existe."""
        slug = _normalize_slug(type_name, "type_name")
        async with self._new_client() as client:
            payload = await self._get_json(client, f"/type/{slug}")
        return _transform(_to_type_members, payload)


# Instancia única de la aplicación.
pokeapi_service = PokeAPIService()


def get_pokeapi_service() -> PokeAPIService:
    """Dependencia de FastAPI: permite sustituir el servicio en los tests de endpoints."""
    return pokeapi_service
