"""Punto de entrada de la Pokédex API."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.database import create_db_and_tables
from app.logging_config import configure_logging, log_requests, validation_exception_handler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Crea las tablas al arrancar la aplicación (sustituye a `@app.on_event`, obsoleto)."""
    configure_logging()
    create_db_and_tables()
    yield


app = FastAPI(
    title="Pokédex API",
    version="1.0.0",
    description="API REST de Pokédex personal: búsqueda en PokeAPI, Pokédex por usuario, "
    "estadísticas y equipos de batalla.",
    lifespan=lifespan,
)

app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.middleware("http")(log_requests)


@app.get("/health", tags=["health"], summary="Comprobar que la API está viva")
def health() -> dict[str, str]:
    """Devuelve `{"status": "ok"}` si la API responde."""
    return {"status": "ok"}
