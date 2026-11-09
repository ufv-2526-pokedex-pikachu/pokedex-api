"""Búsqueda y detalle de Pokémon (proxy a PokeAPI)."""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request

from app.dependencies import get_current_user
from app.limiter import LIMIT_POKEAPI, limiter
from app.models import PokemonDetail, PokemonSearchResult, User
from app.services.pokeapi_service import PokeAPIService, get_pokeapi_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pokemon", tags=["pokemon"])

_POKEAPI_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"description": "Parámetro no válido"},
    502: {"description": "Error al consultar PokeAPI"},
    504: {"description": "PokeAPI no responde"},
}


# Va antes que /{id_or_name}: si no, "search" se tomaría como el nombre de un Pokémon.
@router.get(
    "/search",
    response_model=PokemonSearchResult,
    summary="Buscar Pokémon en PokeAPI",
    responses=_POKEAPI_ERRORS,
)
@limiter.limit(LIMIT_POKEAPI)
async def search(
    request: Request,
    name: Optional[str] = Query(None, description="Parte del nombre, sin distinguir mayúsculas"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    pokeapi: PokeAPIService = Depends(get_pokeapi_service),
) -> dict[str, Any]:
    """Lista simplificada `{id, name, sprite, types}` con paginación y filtro opcional por nombre."""
    return await pokeapi.search_pokemon(limit=limit, offset=offset, name=name)


@router.get(
    "/{id_or_name}",
    response_model=PokemonDetail,
    summary="Ver el detalle de un Pokémon",
    responses={**_POKEAPI_ERRORS, 404: {"description": "El Pokémon no existe"}},
)
@limiter.limit(LIMIT_POKEAPI)
async def get_detail(
    request: Request,
    id_or_name: str,
    current_user: User = Depends(get_current_user),
    pokeapi: PokeAPIService = Depends(get_pokeapi_service),
) -> dict[str, Any]:
    """Detalle completo por ID o nombre: stats, habilidades, tipos y sprites."""
    return await pokeapi.get_pokemon(id_or_name)
