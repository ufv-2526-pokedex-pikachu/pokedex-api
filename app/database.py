"""Conexión a la base de datos y sesión por petición."""

import sqlite3
from collections.abc import Generator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

# SQLite solo deja usar una conexión desde el hilo que la creó; FastAPI usa varios hilos.
connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)


@event.listens_for(Engine, "connect")
def _activar_claves_foraneas(dbapi_connection, connection_record) -> None:
    """Activa las claves foráneas en cada conexión SQLite (vienen desactivadas por defecto)."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_db_and_tables() -> None:
    """Crea las tablas de todos los modelos si todavía no existen."""
    import app.models  # noqa: F401  (registra los modelos en SQLModel.metadata)

    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    """Dependencia de FastAPI: abre una sesión para la petición y la cierra al terminar."""
    with Session(engine) as session:
        yield session
