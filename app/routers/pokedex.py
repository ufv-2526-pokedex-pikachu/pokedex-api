"""Pokédex personal: añadir, listar, actualizar, borrar y exportar entradas."""

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.database import get_session
from app.dependencies import get_current_user
from app.limiter import LIMIT_AUTHENTICATED, LIMIT_POKEAPI, limiter
from app.models import (
    PokedexEntry,
    PokedexEntryCreate,
    PokedexEntryRead,
    PokedexEntryUpdate,
    PokedexSort,
    PokedexStats,
    User,
)
from app.services.pokeapi_service import PokeAPIService, get_pokeapi_service
from app.services.stats_service import compute_pokedex_stats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pokedex", tags=["pokedex"])

_NOT_FOUND = "Entrada no encontrada"
_EXPORT_COLUMNS = [
    "pokemon_id",
    "pokemon_name",
    "pokemon_types",
    "nickname",
    "is_captured",
    "capture_date",
    "favorite",
    "notes",
]


def _sync_capture_date(entry: PokedexEntry) -> None:
    """Capturado sin fecha → ahora; no capturado → sin fecha."""
    if not entry.is_captured:
        entry.capture_date = None
    elif entry.capture_date is None:
        entry.capture_date = datetime.now(timezone.utc)


def _own_entry(entry_id: int, user: User, session: Session) -> PokedexEntry:
    """Devuelve la entrada si es del usuario; si no existe o es de otro, 404."""
    entry = session.get(PokedexEntry, entry_id)
    # 404 también para las ajenas: no se revela que existen.
    if entry is None or entry.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    return entry


def _query_entries(
    session: Session,
    user: User,
    captured: Optional[bool],
    favorite: Optional[bool],
    sort: str = "pokemon_id",
    order: str = "asc",
):
    """Consulta de las entradas del usuario con filtros y orden (sin paginar)."""
    query = select(PokedexEntry).where(PokedexEntry.owner_id == user.id)
    if captured is not None:
        query = query.where(PokedexEntry.is_captured == captured)
    if favorite is not None:
        query = query.where(PokedexEntry.favorite == favorite)
    column = getattr(PokedexEntry, sort)
    # Desempate por id para que la paginación sea estable.
    return query.order_by(column.desc() if order == "desc" else column.asc(), PokedexEntry.id)


@router.post(
    "",
    response_model=PokedexEntryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Añadir un Pokémon a mi Pokédex",
    responses={404: {"description": "El Pokémon no existe en PokeAPI"},
               409: {"description": "Ya está en tu Pokédex"}},
)
@limiter.limit(LIMIT_POKEAPI)
async def add_entry(
    request: Request,
    data: PokedexEntryCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    pokeapi: PokeAPIService = Depends(get_pokeapi_service),
) -> PokedexEntry:
    """Valida el Pokémon en PokeAPI y guarda su nombre, sprite y tipos. 409 si está repetido."""
    conflict = HTTPException(status.HTTP_409_CONFLICT, "Ese Pokémon ya está en tu Pokédex")
    duplicate = session.exec(
        select(PokedexEntry).where(
            PokedexEntry.owner_id == current_user.id,
            PokedexEntry.pokemon_id == data.pokemon_id,
        )
    ).first()
    if duplicate is not None:
        raise conflict

    pokemon = await pokeapi.get_pokemon(data.pokemon_id)  # 404 si no existe
    entry = PokedexEntry(
        **data.model_dump(),
        owner_id=current_user.id,
        pokemon_name=pokemon["name"],
        pokemon_sprite=pokemon["sprite"] or "",
        pokemon_types=",".join(pokemon["types"]),
    )
    _sync_capture_date(entry)
    session.add(entry)
    try:
        session.commit()
    except IntegrityError as exc:  # dos altas iguales a la vez
        session.rollback()
        raise conflict from exc
    session.refresh(entry)
    logger.info("Usuario %s añade %s a su Pokédex", current_user.username, entry.pokemon_name)
    return entry


@router.get("", response_model=list[PokedexEntryRead], summary="Ver mi Pokédex")
@limiter.limit(LIMIT_AUTHENTICATED)
def list_entries(
    request: Request,
    captured: Optional[bool] = None,
    favorite: Optional[bool] = None,
    sort: PokedexSort = "pokemon_id",
    order: Literal["asc", "desc"] = "asc",
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[PokedexEntry]:
    """Lista mis entradas con filtros (`captured`, `favorite`), orden y paginación."""
    query = _query_entries(session, current_user, captured, favorite, sort, order)
    return list(session.exec(query.offset(offset).limit(limit)).all())


# Va antes que /{entry_id}: si no, "export" se tomaría como un id.
@router.get(
    "/export",
    summary="Exportar mi Pokédex en CSV o PDF",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}, "application/pdf": {}},
                     "description": "Archivo descargable"}},
)
@limiter.limit(LIMIT_AUTHENTICATED)
def export_entries(
    request: Request,
    format: Literal["csv", "pdf"] = "csv",
    captured: Optional[bool] = None,
    favorite: Optional[bool] = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Descarga toda mi Pokédex (o solo capturados/favoritos) como CSV o PDF."""
    entries = session.exec(_query_entries(session, current_user, captured, favorite)).all()
    rows = [[_cell(getattr(entry, column)) for column in _EXPORT_COLUMNS] for entry in entries]
    if format == "csv":
        content, media_type = _to_csv(rows), "text/csv; charset=utf-8"
    else:
        content, media_type = _to_pdf(rows, current_user.username), "application/pdf"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="pokedex.{format}"'},
    )


def _cell(value) -> str:
    """Valor de una celda como texto. Evita la inyección de fórmulas al abrir el CSV en Excel."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def _to_csv(rows: list[list[str]]) -> bytes:
    """CSV con cabecera. BOM UTF-8 para que Excel muestre bien las tildes."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_EXPORT_COLUMNS)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def _to_pdf(rows: list[list[str]], username: str) -> bytes:
    """PDF apaisado con una tabla de las entradas."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), title=f"Pokédex de {username}")
    title = Paragraph(f"Pokédex de {username} ({len(rows)} entradas)",
                      getSampleStyleSheet()["Title"])
    table = Table([_EXPORT_COLUMNS, *rows], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ]))
    doc.build([title, table])
    return buffer.getvalue()


# Ruta fija: va antes que las de /{entry_id}, como /export.
@router.get("/stats", response_model=PokedexStats, summary="Ver las estadísticas de mi Pokédex")
@limiter.limit(LIMIT_AUTHENTICATED)
def get_stats(
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PokedexStats:
    """Totales, capturados, favoritos, % completado, tipo más común y racha de capturas.

    Solo cuenta mis entradas. La racha son los días seguidos (en UTC) con al menos una
    captura, terminando hoy o ayer.
    """
    entries = session.exec(_query_entries(session, current_user, None, None)).all()
    return PokedexStats(**compute_pokedex_stats(entries, datetime.now(timezone.utc).date()))


@router.patch(
    "/{entry_id}",
    response_model=PokedexEntryRead,
    summary="Actualizar una entrada de mi Pokédex",
    responses={404: {"description": _NOT_FOUND}},
)
@limiter.limit(LIMIT_AUTHENTICATED)
def update_entry(
    request: Request,
    entry_id: int,
    data: PokedexEntryUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PokedexEntry:
    """Cambia solo los campos enviados. Solo el propietario."""
    entry = _own_entry(entry_id, current_user, session)
    changes = data.model_dump(exclude_unset=True)
    # nickname, notes y capture_date se pueden borrar con null; los booleanos no.
    changes = {key: value for key, value in changes.items()
               if value is not None or key not in ("is_captured", "favorite")}
    entry.sqlmodel_update(changes)
    _sync_capture_date(entry)
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


@router.delete(
    "/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Borrar una entrada de mi Pokédex",
    responses={404: {"description": _NOT_FOUND},
               409: {"description": "La entrada está en un equipo"}},
)
@limiter.limit(LIMIT_AUTHENTICATED)
def delete_entry(
    request: Request,
    entry_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """Borra la entrada. Solo el propietario."""
    entry = _own_entry(entry_id, current_user, session)
    session.delete(entry)
    try:
        session.commit()
    except IntegrityError as exc:  # la clave foránea de TeamMember lo impide
        session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Ese Pokémon está en un equipo: sácalo antes de borrarlo"
        ) from exc
