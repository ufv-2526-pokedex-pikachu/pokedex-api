"""Tests del cliente de PokeAPI. Nunca usan la red: respx simula todas las respuestas."""

import logging

import httpx
import pytest
import respx
from fastapi import HTTPException

from app.services.pokeapi_service import (
    PokeAPIService,
    get_pokeapi_service,
    pokeapi_service,
)

LOGGER_NAME = "app.services.pokeapi_service"


def pokemon_payload(pokemon_id: int = 25, name: str = "pikachu") -> dict:
    """Respuesta de PokeAPI reducida, con algún campo que el servicio debe descartar."""
    return {
        "id": pokemon_id,
        "name": name,
        "height": 4,
        "weight": 60,
        "base_experience": 112,
        "sprites": {
            "front_default": f"http://img/{pokemon_id}.png",
            "back_default": f"http://img/back/{pokemon_id}.png",
            "front_shiny": None,
            "versions": {"descartado": True},
        },
        "types": [{"slot": 1, "type": {"name": "electric", "url": "x"}}],
        "stats": [
            {"base_stat": 35, "effort": 0, "stat": {"name": "hp", "url": "x"}},
            {"base_stat": 90, "effort": 2, "stat": {"name": "speed", "url": "x"}},
        ],
        "abilities": [{"ability": {"name": "static", "url": "x"}, "slot": 1}],
    }


@pytest.fixture
def service() -> PokeAPIService:
    return PokeAPIService()


@pytest.fixture
def api():
    """Simula PokeAPI. Cualquier petición no simulada falla, así que nunca se sale a la red."""
    with respx.mock(base_url=PokeAPIService.BASE_URL, assert_all_called=False) as router:
        yield router


async def test_pokeapi_service_get_pokemon(service, api):
    """Servicio obtiene Pokémon correctamente y extrae solo los campos relevantes."""
    api.get("/pokemon/25").respond(json=pokemon_payload())

    assert await service.get_pokemon(25) == {
        "id": 25,
        "name": "pikachu",
        "sprite": "http://img/25.png",
        "sprites": {
            "front_default": "http://img/25.png",
            "back_default": "http://img/back/25.png",
            "front_shiny": None,
            "back_shiny": None,
        },
        "types": ["electric"],
        "stats": {"hp": 35, "speed": 90},
        "abilities": ["static"],
        "height": 4,
        "weight": 60,
    }


async def test_get_pokemon_normalizes_name(service, api):
    """El nombre se pide en minúsculas y sin espacios alrededor."""
    route = api.get("/pokemon/pikachu").respond(json=pokemon_payload())

    await service.get_pokemon("  PikaChu ")

    assert route.called


async def test_pokeapi_service_handles_404(service, api):
    """Servicio maneja Pokémon no encontrado."""
    api.get("/pokemon/missingno").respond(404)

    with pytest.raises(HTTPException) as exc:
        await service.get_pokemon("missingno")

    assert exc.value.status_code == 404


@pytest.mark.parametrize("status", [500, 503, 429, 301])
async def test_get_pokemon_upstream_failure_returns_502(service, api, status):
    """Un 5xx o cualquier otro código inesperado de PokeAPI se traduce en 502."""
    api.get("/pokemon/25").respond(status)

    with pytest.raises(HTTPException) as exc:
        await service.get_pokemon(25)

    assert exc.value.status_code == 502


async def test_get_pokemon_timeout_returns_504(service, api):
    api.get("/pokemon/25").mock(side_effect=httpx.ReadTimeout("lento"))

    with pytest.raises(HTTPException) as exc:
        await service.get_pokemon(25)

    assert exc.value.status_code == 504


async def test_get_pokemon_network_error_returns_502(service, api):
    api.get("/pokemon/25").mock(side_effect=httpx.ConnectError("sin red"))

    with pytest.raises(HTTPException) as exc:
        await service.get_pokemon(25)

    assert exc.value.status_code == 502


async def test_get_pokemon_invalid_json_returns_502(service, api):
    api.get("/pokemon/25").respond(200, text="<html>no soy JSON</html>")

    with pytest.raises(HTTPException) as exc:
        await service.get_pokemon(25)

    assert exc.value.status_code == 502


async def test_get_pokemon_unexpected_format_returns_502(service, api):
    """Si a la respuesta le faltan campos, se responde 502 y no un error 500 interno."""
    api.get("/pokemon/25").respond(json={"id": 25})

    with pytest.raises(HTTPException) as exc:
        await service.get_pokemon(25)

    assert exc.value.status_code == 502


@pytest.mark.parametrize(
    "identifier",
    ["", "   ", "../type/fire", "a/b", "pika?x=1", "pi\nka", 0, "000", -1, True, "a" * 101],
)
async def test_get_pokemon_invalid_identifier_returns_400(service, api, identifier):
    """Un identificador no válido se rechaza sin llegar a llamar a PokeAPI."""
    with pytest.raises(HTTPException) as exc:
        await service.get_pokemon(identifier)

    assert exc.value.status_code == 400
    assert not api.calls.called


async def test_get_pokemon_logs_url_status_and_duration(service, api, caplog):
    api.get("/pokemon/25").respond(json=pokemon_payload())

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await service.get_pokemon(25)

    assert f"{PokeAPIService.BASE_URL}/pokemon/25 -> 200" in caplog.text
    assert " ms)" in caplog.text


async def test_get_pokemon_logs_timeout(service, api, caplog):
    api.get("/pokemon/25").mock(side_effect=httpx.ReadTimeout("lento"))

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME), pytest.raises(HTTPException):
        await service.get_pokemon(25)

    assert f"{PokeAPIService.BASE_URL}/pokemon/25 -> timeout" in caplog.text


def test_get_pokeapi_service_returns_single_instance():
    assert get_pokeapi_service() is pokeapi_service
