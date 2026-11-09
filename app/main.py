"""Punto de entrada de la Pokédex API."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.database import create_db_and_tables
from app.limiter import limiter, rate_limit_exceeded_handler
from app.routers import auth, pokedex, teams


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Crea las tablas al arrancar la aplicación (sustituye a `@app.on_event`, obsoleto)."""
    create_db_and_tables()
    yield


app = FastAPI(
    title="Pokédex API",
    version="1.0.0",
    description="API REST de Pokédex personal: búsqueda en PokeAPI, Pokédex por usuario, "
    "estadísticas y equipos de batalla.",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# Solo orígenes, métodos y cabeceras concretos: nunca "*", que además no admite credenciales.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=3600,
)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(pokedex.router, prefix="/api/v1")
app.include_router(teams.router, prefix="/api/v1")


@app.get("/health", tags=["health"], summary="Comprobar que la API está viva")
def health() -> dict[str, str]:
    """Devuelve `{"status": "ok"}` si la API responde."""
    return {"status": "ok"}
