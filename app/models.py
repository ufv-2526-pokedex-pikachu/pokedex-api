"""Modelos de base de datos (SQLModel), tal como los define el enunciado.

Cambios respecto al enunciado (documentados en README → «Otras decisiones»):
- `datetime.utcnow()` está obsoleto en Python 3.12: se usa `datetime.now(timezone.utc)`.
- `Team.members` borra en cascada sus miembros al borrar el equipo.
- `TeamMember.pokedex_entry` permite llegar desde un miembro a su entrada de la Pokédex.
- `PokedexEntry.pokemon_types` guarda los tipos ("fire,flying") y un usuario no puede
  tener dos veces el mismo `pokemon_id`.
"""

import re
from datetime import datetime, timezone
from typing import Annotated, List, Literal, Optional

from pydantic import Field as PydanticField
from pydantic import field_validator
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel


def _ahora_utc() -> datetime:
    """Fecha y hora actual en UTC (sustituye a `datetime.utcnow`, obsoleto)."""
    return datetime.now(timezone.utc)


class User(SQLModel, table=True):
    """Usuario del sistema"""

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True, min_length=3, max_length=50)
    email: str = Field(unique=True, index=True)
    hashed_password: str
    created_at: datetime = Field(default_factory=_ahora_utc)
    is_active: bool = Field(default=True)

    # Relaciones
    pokedex_entries: List["PokedexEntry"] = Relationship(back_populates="owner")
    teams: List["Team"] = Relationship(back_populates="trainer")


class PokedexEntry(SQLModel, table=True):
    """Entrada en la Pokédex de un usuario"""

    # El enunciado prohíbe duplicados; la BD lo garantiza aunque lleguen dos peticiones a la vez.
    __table_args__ = (UniqueConstraint("owner_id", "pokemon_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: int = Field(foreign_key="user.id")

    # Datos del Pokémon (de PokeAPI)
    pokemon_id: int = Field(index=True)  # ID en PokeAPI
    pokemon_name: str
    pokemon_sprite: str  # URL de la imagen
    pokemon_types: str = ""  # Tipos separados por comas, p. ej. "fire,flying"

    # Datos del usuario
    is_captured: bool = Field(default=False)
    capture_date: Optional[datetime] = None
    nickname: Optional[str] = Field(default=None, max_length=50)
    notes: Optional[str] = Field(default=None, max_length=500)
    favorite: bool = Field(default=False)

    created_at: datetime = Field(default_factory=_ahora_utc)

    # Relaciones
    owner: User = Relationship(back_populates="pokedex_entries")


class Team(SQLModel, table=True):
    """Equipo de batalla (máximo 6 Pokémon)"""

    id: Optional[int] = Field(default=None, primary_key=True)
    trainer_id: int = Field(foreign_key="user.id")
    name: str = Field(max_length=100)
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=_ahora_utc)

    # Relaciones
    trainer: User = Relationship(back_populates="teams")
    # Al borrar el equipo se borran sus miembros, nunca las entradas de la Pokédex.
    members: List["TeamMember"] = Relationship(
        back_populates="team",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class TeamMember(SQLModel, table=True):
    """Relación muchos a muchos entre Team y PokedexEntry"""

    id: Optional[int] = Field(default=None, primary_key=True)
    team_id: int = Field(foreign_key="team.id")
    pokedex_entry_id: int = Field(foreign_key="pokedexentry.id")
    position: int = Field(ge=1, le=6)  # Posición en el equipo (1-6)

    # Relaciones
    team: Team = Relationship(back_populates="members")
    pokedex_entry: PokedexEntry = Relationship()


# --- Schemas de la API (no son tablas: validan lo que entra y lo que sale) ---

# Algo@algo.dominio, sin espacios; la verificación real sería mandar un correo.
_EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


class UserCreate(SQLModel):
    """Datos para registrarse."""

    username: str = Field(min_length=3, max_length=50)
    email: str = Field(max_length=254)
    password: str

    @field_validator("email")
    @classmethod
    def validar_email(cls, value: str) -> str:
        """El enunciado pide validar el email con regex."""
        if not _EMAIL_PATTERN.fullmatch(value):
            raise ValueError("El email no tiene un formato válido")
        return value.lower()

    @field_validator("password")
    @classmethod
    def validar_password(cls, value: str) -> str:
        """Mínimo 8 caracteres, al menos 1 mayúscula y 1 número; máximo 72 bytes."""
        if len(value) < 8 or not re.search(r"[A-Z]", value) or not re.search(r"\d", value):
            raise ValueError(
                "La contraseña necesita al menos 8 caracteres, 1 mayúscula y 1 número"
            )
        # bcrypt ignora lo que pasa de 72 bytes (no caracteres: la ñ ocupa 2).
        if len(value.encode("utf-8")) > 72:
            raise ValueError("La contraseña no puede ocupar más de 72 bytes")
        return value


class UserRead(SQLModel):
    """Datos públicos de un usuario (nunca incluye la contraseña ni su hash)."""

    id: int
    username: str
    email: str
    created_at: datetime
    is_active: bool


class Token(SQLModel):
    """Respuesta del login y del refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(SQLModel):
    """Cuerpo de `POST /auth/refresh`."""

    refresh_token: str


class PokedexEntryCreate(SQLModel):
    """Cuerpo de `POST /pokedex`. El nombre, el sprite y los tipos se sacan de PokeAPI."""

    pokemon_id: int = Field(ge=1)
    nickname: Optional[str] = Field(default=None, max_length=50)
    is_captured: bool = False
    capture_date: Optional[datetime] = None
    notes: Optional[str] = Field(default=None, max_length=500)
    favorite: bool = False


class PokedexEntryUpdate(SQLModel):
    """Cuerpo de `PATCH /pokedex/{entry_id}`: solo se cambian los campos enviados."""

    is_captured: Optional[bool] = None
    capture_date: Optional[datetime] = None
    nickname: Optional[str] = Field(default=None, max_length=50)
    notes: Optional[str] = Field(default=None, max_length=500)
    favorite: Optional[bool] = None


class PokedexEntryRead(SQLModel):
    """Una entrada de la Pokédex tal como la devuelve la API."""

    id: int
    pokemon_id: int
    pokemon_name: str
    pokemon_sprite: str
    pokemon_types: str
    is_captured: bool
    capture_date: Optional[datetime]
    nickname: Optional[str]
    notes: Optional[str]
    favorite: bool
    created_at: datetime


PokedexSort = Literal["pokemon_id", "capture_date", "pokemon_name"]


class PokedexStats(SQLModel):
    """Respuesta de `GET /pokedex/stats`, con los campos exactos del enunciado."""

    total_pokemon: int
    captured: int
    favorites: int
    completion_percentage: float
    most_common_type: Optional[str] = None
    capture_streak_days: int


# --- Schemas de equipos ---

MAX_TEAM_SIZE = 6


class TeamCreate(SQLModel):
    """Cuerpo de `POST /teams`.

    `pokemon_ids` son IDs de PokeAPI que tienen que estar en la Pokédex del usuario.
    Su orden es la posición en el equipo (1-6).
    """

    name: str = Field(min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)
    pokemon_ids: List[Annotated[int, PydanticField(ge=1)]] = Field(
        min_length=1, max_length=MAX_TEAM_SIZE
    )

    @field_validator("pokemon_ids")
    @classmethod
    def validar_sin_repetidos(cls, value: List[int]) -> List[int]:
        """Un mismo Pokémon de la Pokédex no puede ocupar dos puestos del equipo."""
        if len(set(value)) != len(value):
            raise ValueError("pokemon_ids no puede tener Pokémon repetidos")
        return value


class TeamUpdate(TeamCreate):
    """Cuerpo de `PUT /teams/{team_id}`: sustituye el equipo entero (nombre, descripción y miembros)."""


class TeamMemberRead(SQLModel):
    """Un miembro del equipo, con los datos de su entrada de la Pokédex."""

    position: int
    pokedex_entry_id: int
    pokemon_id: int
    pokemon_name: str
    pokemon_sprite: str
    nickname: Optional[str] = None


class TeamRead(SQLModel):
    """Equipo tal como lo devuelve la API, con los miembros ordenados por posición."""

    id: int
    name: str
    description: Optional[str] = None
    created_at: datetime
    pokemon_ids: List[int]
    members: List[TeamMemberRead]
