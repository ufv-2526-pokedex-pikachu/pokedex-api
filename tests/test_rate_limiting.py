"""Tests del rate limiting, con rutas de prueba porque los endpoints reales llegan en otras ramas."""

import logging

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.limiter import (
    LIMIT_AUTHENTICATED,
    LIMIT_LOGIN,
    LIMIT_POKEAPI,
    LIMIT_REGISTER,
    limiter,
    rate_limit_exceeded_handler,
)
from app.main import app

fake_router = APIRouter(prefix="/_test")


@fake_router.post("/register", include_in_schema=False)
@limiter.limit(LIMIT_REGISTER)
async def fake_register(request: Request) -> dict[str, bool]:
    return {"ok": True}


@fake_router.post("/login", include_in_schema=False)
@limiter.limit(LIMIT_LOGIN)
async def fake_login(request: Request) -> dict[str, bool]:
    return {"ok": True}


@fake_router.get("/pokeapi", include_in_schema=False)
@limiter.limit(LIMIT_POKEAPI)
async def fake_pokeapi(request: Request) -> dict[str, bool]:
    return {"ok": True}


@fake_router.get("/authenticated", include_in_schema=False)
@limiter.limit(LIMIT_AUTHENTICATED)
async def fake_authenticated(request: Request) -> dict[str, bool]:
    return {"ok": True}


@pytest.fixture
def limited_client(client):
    """Añade las rutas de prueba a la app real, para probar su configuración de verdad."""
    routes_before = list(app.router.routes)
    app.include_router(fake_router)
    yield client
    app.router.routes[:] = routes_before


def exhaust_login(client: TestClient):
    """Hace 11 peticiones de login y devuelve la última."""
    responses = [client.post("/_test/login") for _ in range(11)]
    assert all(response.status_code == 200 for response in responses[:10])
    return responses[-1]


def test_rate_limit_exceeded(limited_client):
    """Rate limit funciona correctamente: la 11ª petición de login devuelve 429."""
    assert exhaust_login(limited_client).status_code == 429


@pytest.mark.parametrize(
    ("method", "path", "allowed"),
    [
        ("POST", "/_test/register", 5),
        ("GET", "/_test/pokeapi", 30),
        ("GET", "/_test/authenticated", 100),
    ],
)
def test_limit_by_endpoint_type_returns_429_after_allowed(limited_client, method, path, allowed):
    """Cada tipo de endpoint admite exactamente su límite."""
    for _ in range(allowed):
        assert limited_client.request(method, path).status_code == 200

    assert limited_client.request(method, path).status_code == 429


def test_limits_are_counted_per_endpoint(limited_client):
    """Agotar el login no bloquea otros endpoints."""
    exhaust_login(limited_client)

    assert limited_client.get("/_test/pokeapi").status_code == 200


def test_limits_are_counted_per_ip(limited_client):
    """Agotar el límite desde una IP no bloquea a otra."""
    exhaust_login(limited_client)
    other_ip = TestClient(app, client=("203.0.113.7", 50000))

    assert other_ip.post("/_test/login").status_code == 200


def test_rate_limit_response_has_detail_and_retry_after(limited_client):
    response = exhaust_login(limited_client)

    assert response.json() == {"detail": "Demasiadas peticiones. Inténtalo de nuevo más tarde."}
    assert response.headers["retry-after"] == "60"


def test_rate_limit_exceeded_is_logged(limited_client, caplog):
    with caplog.at_level(logging.WARNING, logger="app.limiter"):
        exhaust_login(limited_client)

    assert "Rate limit superado: POST /_test/login desde testclient" in caplog.text


def test_disabled_limiter_never_returns_429():
    """Con `RATE_LIMIT_ENABLED=false` no se aplica ningún límite."""
    disabled = Limiter(key_func=get_remote_address, enabled=False)
    disabled_app = FastAPI()
    disabled_app.state.limiter = disabled
    disabled_app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    @disabled_app.get("/limited")
    @disabled.limit("1/minute")
    async def limited(request: Request) -> dict[str, bool]:
        return {"ok": True}

    test_client = TestClient(disabled_app)

    assert [test_client.get("/limited").status_code for _ in range(3)] == [200, 200, 200]
