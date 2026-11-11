"""Tests del logging: configuración, peticiones HTTP y errores de validación."""

import logging

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.config import settings
from app.logging_config import LOG_FORMAT, QUIET_LOGGERS, configure_logging
from app.main import app

LOGGER_NAME = "app.logging_config"


class FakeBody(BaseModel):
    username: str
    password: int


fake_router = APIRouter(prefix="/_test")


@fake_router.post("/validated", include_in_schema=False)
async def fake_validated(body: FakeBody) -> dict[str, bool]:
    return {"ok": True}


@fake_router.get("/broken", include_in_schema=False)
async def fake_broken() -> None:
    raise RuntimeError("fallo inesperado")


@pytest.fixture
def logged_client(client):
    """Añade rutas de prueba a la app real y las quita al terminar."""
    routes_before = list(app.router.routes)
    app.include_router(fake_router)
    yield client
    app.router.routes[:] = routes_before


def test_request_is_logged_with_method_path_status_and_duration(client, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        client.get("/health")

    assert "GET /health -> 200 (" in caplog.text
    assert " ms)" in caplog.text


def test_not_found_request_is_logged_with_its_status(client, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        client.get("/no-existe")

    assert "GET /no-existe -> 404" in caplog.text


def test_request_log_omits_query_string(client, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        client.get("/health?token=dato-sensible")

    assert "dato-sensible" not in caplog.text


def test_cors_preflight_is_logged(client, caplog):
    """El middleware de logging va por fuera de CORS: también registra las preflight."""
    origin = settings.CORS_ORIGINS[0]
    headers = {"Origin": origin, "Access-Control-Request-Method": "GET"}

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        client.options("/health", headers=headers)
        client.options("/health", headers={**headers, "Origin": "https://otro.example"})

    assert "OPTIONS /health -> 200" in caplog.text
    assert "OPTIONS /health -> 400" in caplog.text


def test_validation_error_returns_422_and_is_logged(logged_client, caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        response = logged_client.post(
            "/_test/validated", json={"username": "ash", "password": "no-es-un-numero"}
        )

    assert response.status_code == 422
    assert "Error de validación en POST /_test/validated" in caplog.text
    assert "body.password: int_parsing" in caplog.text


def test_validation_log_omits_received_values(logged_client, caplog):
    """El valor recibido no va al log, aunque sí vuelve en la respuesta al propio cliente."""
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        logged_client.post(
            "/_test/validated", json={"username": "ash", "password": "contrasena-secreta"}
        )

    assert "contrasena-secreta" not in caplog.text


def test_unhandled_exception_is_logged_as_500(logged_client, caplog):
    no_raise_client = TestClient(app, raise_server_exceptions=False)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        response = no_raise_client.get("/_test/broken")

    assert response.status_code == 500
    assert "GET /_test/broken -> 500" in caplog.text
    assert "RuntimeError: fallo inesperado" in caplog.text


@pytest.fixture
def clean_root_logger():
    """Guarda los handlers del logger raíz (y el nivel de los silenciados) y los restaura al terminar."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    quiet_levels = {name: logging.getLogger(name).level for name in QUIET_LOGGERS}
    yield root
    for name, quiet_level in quiet_levels.items():
        logging.getLogger(name).setLevel(quiet_level)
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


def test_configure_logging_keeps_existing_setup(clean_root_logger, tmp_path):
    """Con handlers ya configurados (como en pytest) no añade otros ni crea el archivo."""
    log_file = tmp_path / "test.log"
    handlers_before = clean_root_logger.handlers[:]

    configure_logging(str(log_file))

    assert clean_root_logger.handlers == handlers_before
    assert not log_file.exists()


def test_configure_logging_writes_to_file_and_console(clean_root_logger, tmp_path):
    log_file = tmp_path / "test.log"
    # Se vacía aquí porque pytest añade su handler justo antes de ejecutar el test.
    clean_root_logger.handlers.clear()

    configure_logging(str(log_file))
    logging.getLogger("prueba").warning("mensaje de prueba")

    assert {type(handler) for handler in clean_root_logger.handlers} == {
        logging.FileHandler,
        logging.StreamHandler,
    }
    assert clean_root_logger.handlers[0].formatter._fmt == LOG_FORMAT
    assert "prueba - WARNING - mensaje de prueba" in log_file.read_text(encoding="utf-8")


def test_configure_logging_silences_httpx_info(clean_root_logger, tmp_path):
    """Las líneas INFO de httpx no llegan al archivo (duplicarían las de pokeapi_service); sus avisos sí."""
    log_file = tmp_path / "test.log"
    clean_root_logger.handlers.clear()

    configure_logging(str(log_file))
    logging.getLogger("httpx").info('HTTP Request: GET https://pokeapi.co/api/v2/pokemon/25 "200 OK"')
    logging.getLogger("httpx").warning("aviso de httpx")
    logging.getLogger("app.services.pokeapi_service").info("PokeAPI GET ... -> 200 (40 ms)")

    content = log_file.read_text(encoding="utf-8")
    assert "HTTP Request" not in content
    assert "httpx - WARNING - aviso de httpx" in content
    assert "PokeAPI GET ... -> 200 (40 ms)" in content
