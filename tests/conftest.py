"""Fixtures compartidas por todos los tests."""

import os

# Antes de importar la app: los tests nunca usan el .env ni la base de datos real.
os.environ.setdefault("SECRET_KEY", "clave-solo-para-tests")
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

import app.models  # noqa: E402,F401  (registra las tablas)
from app.database import get_session  # noqa: E402
from app.limiter import limiter  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def reset_rate_limits():
    """Reinicia los contadores en cada test para que los límites no se acumulen entre ellos."""
    limiter.reset()


@pytest.fixture
def session():
    """Base de datos SQLite en memoria, nueva y vacía para cada test."""
    # StaticPool: todas las conexiones comparten la misma base en memoria.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(session):
    """Cliente HTTP de pruebas que usa la base de datos del test en vez de la real."""
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
