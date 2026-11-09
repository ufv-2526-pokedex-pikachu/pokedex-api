"""API v2: Pokédex con paginación con total, tipos como lista y datos de evolución.

v1 (`/api/v1/pokedex`) sigue igual. Por qué existe v2 y qué cambia: README → «Versionado».
"""

import asyncio
import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session, func, select

from app.database import get_session
from app.dependencies import get_current_user
from app.limiter import LIMIT_POKEAPI, limiter
from app.models import PokedexEntry, PokedexEntryReadV2, PokedexPageV2, PokedexSort, User
from app.routers.pokedex import _query_entries
from app.services.pokeapi_service import PokeAPIService, get_pokeapi_service

logger = logging.getLogger(__name__)

v2_router = APIRouter(prefix="/api/v2", tags=["v2"])


async def _evolution_chain(pokeapi: PokeAPIService, pokemon_id: int) -> Optional[list[str]]:
    """Cadena de evolución del Pokémon, o `None` si PokeAPI falla para él.

    Un fallo en una entrada no tumba toda la lista: esa entrada sale sin evolución.
    """
    try:
        return await pokeapi.get_evolution_chain(pokemon_id)
    except HTTPException as exc:
        logger.warning(
            "Sin cadena de evolución para el Pokémon %s: %s %s",
            pokemon_id,
            exc.status_code,
            exc.detail,
        )
        return None


def _to_read_v2(entry: PokedexEntry, evolution_chain: Optional[list[str]]) -> PokedexEntryReadV2:
    """Convierte una entrada al formato v2 (tipos "fire,flying" → ["fire", "flying"])."""
    return PokedexEntryReadV2(
        id=entry.id,
        pokemon_id=entry.pokemon_id,
        pokemon_name=entry.pokemon_name,
        pokemon_sprite=entry.pokemon_sprite,
        pokemon_types=[name for name in (entry.pokemon_types or "").split(",") if name],
        evolution_chain=evolution_chain,
        is_captured=entry.is_captured,
        capture_date=entry.capture_date,
        nickname=entry.nickname,
        notes=entry.notes,
        favorite=entry.favorite,
        created_at=entry.created_at,
    )


@v2_router.get("/pokedex", response_model=PokedexPageV2, summary="Ver mi Pokédex (v2)")
@limiter.limit(LIMIT_POKEAPI)
async def get_pokedex_v2(
    request: Request,
    captured: Optional[bool] = None,
    favorite: Optional[bool] = None,
    sort: PokedexSort = "pokemon_id",
    order: Literal["asc", "desc"] = "asc",
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    pokeapi: PokeAPIService = Depends(get_pokeapi_service),
) -> PokedexPageV2:
    """Mismos filtros, orden y paginación que v1, pero devuelve `{total, limit, offset, items}`,
    los tipos como lista y la cadena de evolución de cada Pokémon (pedida a PokeAPI en paralelo).

    Límite de 30/minuto porque llama a PokeAPI, a diferencia de v1.
    """
    query = _query_entries(session, current_user, captured, favorite, sort, order)
    total = session.exec(select(func.count()).select_from(query.subquery())).one()
    entries = session.exec(query.offset(offset).limit(limit)).all()
    chains = await asyncio.gather(
        *(_evolution_chain(pokeapi, entry.pokemon_id) for entry in entries)
    )
    return PokedexPageV2(
        total=total,
        limit=limit,
        offset=offset,
        items=[_to_read_v2(entry, chain) for entry, chain in zip(entries, chains)],
    )
