"""Equipos de batalla: crear, listar, actualizar y exportar en PDF (máximo 6 Pokémon)."""

import asyncio
import logging
from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlmodel import Session, select

from app.database import get_session
from app.dependencies import get_current_user
from app.limiter import LIMIT_AUTHENTICATED, LIMIT_POKEAPI, limiter
from app.models import (
    PokedexEntry,
    Team,
    TeamCreate,
    TeamMember,
    TeamMemberRead,
    TeamRead,
    TeamUpdate,
    User,
)
from app.services.pdf_service import STAT_LABELS, build_team_pdf
from app.services.pokeapi_service import PokeAPIService, get_pokeapi_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/teams", tags=["teams"])

_NOT_FOUND = "Equipo no encontrado"


def _members_from_pokedex(
    pokemon_ids: list[int], user: User, session: Session
) -> list[TeamMember]:
    """Crea los miembros a partir de la Pokédex del usuario, con posición = orden de la lista.

    Responde 400 indicando qué IDs no están en la Pokédex del usuario.
    """
    entries = session.exec(
        select(PokedexEntry).where(
            PokedexEntry.owner_id == user.id, PokedexEntry.pokemon_id.in_(pokemon_ids)
        )
    ).all()
    by_pokemon_id = {entry.pokemon_id: entry for entry in entries}
    missing = [pokemon_id for pokemon_id in pokemon_ids if pokemon_id not in by_pokemon_id]
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Estos Pokémon no están en tu Pokédex: {', '.join(map(str, missing))}",
        )
    return [
        TeamMember(pokedex_entry=by_pokemon_id[pokemon_id], position=position)
        for position, pokemon_id in enumerate(pokemon_ids, start=1)
    ]


def _own_team(team_id: int, user: User, session: Session) -> Team:
    """Devuelve el equipo si es del usuario; si no existe o es de otro, 404."""
    team = session.get(Team, team_id)
    # 404 también para los ajenos: no se revela que existen.
    if team is None or team.trainer_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    return team


def _sorted_members(team: Team) -> list[TeamMember]:
    """Miembros del equipo ordenados por posición."""
    return sorted(team.members, key=lambda member: member.position)


def _to_read(team: Team) -> TeamRead:
    """Convierte un equipo de la base de datos en la respuesta de la API."""
    members = [
        TeamMemberRead(
            position=member.position,
            pokedex_entry_id=member.pokedex_entry_id,
            pokemon_id=member.pokedex_entry.pokemon_id,
            pokemon_name=member.pokedex_entry.pokemon_name,
            pokemon_sprite=member.pokedex_entry.pokemon_sprite,
            nickname=member.pokedex_entry.nickname,
        )
        for member in _sorted_members(team)
    ]
    return TeamRead(
        id=team.id,
        name=team.name,
        description=team.description,
        created_at=team.created_at,
        pokemon_ids=[member.pokemon_id for member in members],
        members=members,
    )


def combine_stats(pokemons: Iterable[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Estadísticas combinadas del equipo: suma y media de cada stat base.

    Devuelve `{stat: {"total": suma, "average": media}}` para las 6 stats de PokeAPI,
    más `"total"` con la suma de todas (la potencia global del equipo). Una stat que
    falte en un Pokémon cuenta como 0.
    """
    pokemons = list(pokemons)
    count = len(pokemons) or 1
    combined: dict[str, dict[str, float]] = {}
    for key in STAT_LABELS:
        total = sum(int(pokemon.get("stats", {}).get(key, 0)) for pokemon in pokemons)
        combined[key] = {"total": total, "average": round(total / count, 1)}
    grand_total = sum(values["total"] for values in combined.values())
    combined["total"] = {"total": grand_total, "average": round(grand_total / count, 1)}
    return combined


@router.post(
    "",
    response_model=TeamRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un equipo",
    responses={400: {"description": "Algún Pokémon no está en tu Pokédex"}},
)
@limiter.limit(LIMIT_AUTHENTICATED)
def create_team(
    request: Request,
    data: TeamCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> TeamRead:
    """Crea un equipo con hasta 6 Pokémon de tu Pokédex (IDs de PokeAPI, sin repetir).

    El orden de `pokemon_ids` es la posición de cada uno en el equipo (1-6).
    """
    team = Team(
        trainer_id=current_user.id,
        name=data.name,
        description=data.description,
        members=_members_from_pokedex(data.pokemon_ids, current_user, session),
    )
    session.add(team)
    session.commit()
    session.refresh(team)
    logger.info("Equipo %s creado por %s", team.id, current_user.username)
    return _to_read(team)


@router.get("", response_model=list[TeamRead], summary="Listar mis equipos")
@limiter.limit(LIMIT_AUTHENTICATED)
def list_teams(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[TeamRead]:
    """Devuelve todos los equipos del usuario, del más antiguo al más reciente."""
    teams = session.exec(
        select(Team).where(Team.trainer_id == current_user.id).order_by(Team.id)
    ).all()
    return [_to_read(team) for team in teams]


@router.put(
    "/{team_id}",
    response_model=TeamRead,
    summary="Actualizar un equipo",
    responses={
        400: {"description": "Algún Pokémon no está en tu Pokédex"},
        404: {"description": _NOT_FOUND},
    },
)
@limiter.limit(LIMIT_AUTHENTICATED)
def update_team(
    request: Request,
    team_id: int,
    data: TeamUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> TeamRead:
    """Sustituye el nombre, la descripción y los miembros del equipo.

    Es un PUT: se envía el equipo completo. Los miembros anteriores se borran (nunca
    las entradas de la Pokédex) y se crean los nuevos en el orden de `pokemon_ids`.
    """
    team = _own_team(team_id, current_user, session)
    new_members = _members_from_pokedex(data.pokemon_ids, current_user, session)
    team.name = data.name
    team.description = data.description
    # La cascada "delete-orphan" de Team.members borra los miembros que salen.
    team.members = new_members
    session.add(team)
    session.commit()
    session.refresh(team)
    logger.info("Equipo %s actualizado por %s", team.id, current_user.username)
    return _to_read(team)


@router.get(
    "/{team_id}/export",
    summary="Exportar un equipo en PDF",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "PDF del equipo"},
        404: {"description": _NOT_FOUND},
        502: {"description": "Error al consultar PokeAPI"},
        504: {"description": "PokeAPI no responde"},
    },
)
@limiter.limit(LIMIT_POKEAPI)
async def export_team(
    request: Request,
    team_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    pokeapi: PokeAPIService = Depends(get_pokeapi_service),
) -> Response:
    """Descarga un PDF con el nombre y la descripción del equipo, la ficha de cada
    Pokémon y las estadísticas combinadas (suma y media de las stats base).

    Las stats, tipos y habilidades se piden a PokeAPI en paralelo (límite: 30/minuto).
    """
    team = _own_team(team_id, current_user, session)
    members = _sorted_members(team)
    pokemons = await asyncio.gather(
        *(pokeapi.get_pokemon(member.pokedex_entry.pokemon_id) for member in members)
    )
    pdf = build_team_pdf(
        name=team.name,
        description=team.description,
        trainer=current_user.username,
        members=[
            (member.position, member.pokedex_entry.nickname, pokemon)
            for member, pokemon in zip(members, pokemons)
        ],
        combined_stats=combine_stats(pokemons),
    )
    logger.info("Equipo %s exportado en PDF por %s", team.id, current_user.username)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="equipo-{team.id}.pdf"'},
    )
