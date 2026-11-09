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


def species_payload(entries: list[tuple[str, str]]) -> dict:
    """Respuesta de especie con pares (idioma, texto)."""
    return {
        "id": 25,
        "name": "pikachu",
        "flavor_text_entries": [
            {"flavor_text": text, "language": {"name": lang, "url": "x"}} for lang, text in entries
        ],
    }


async def test_get_species_prefers_spanish_and_cleans_text(service, api):
    """Elige el español aunque no sea el primero, y limpia saltos y guiones invisibles."""
    api.get("/pokemon-species/25").respond(
        json=species_payload(
            [("en", "In English."), ("es", "Ratón\neléc­trico\famarillo."), ("es", "Otra.")]
        )
    )

    assert await service.get_species(25) == {
        "id": 25,
        "name": "pikachu",
        "description": "Ratón eléctrico amarillo.",
    }


async def test_get_species_falls_back_to_english(service, api):
    api.get("/pokemon-species/25").respond(
        json=species_payload([("ja", "日本語"), ("en", "In English.")])
    )

    assert (await service.get_species(25))["description"] == "In English."


async def test_get_species_without_descriptions_returns_empty(service, api):
    api.get("/pokemon-species/25").respond(json=species_payload([("ja", "日本語")]))

    assert (await service.get_species(25))["description"] == ""


async def test_get_species_not_found_returns_404(service, api):
    api.get("/pokemon-species/99999").respond(404)

    with pytest.raises(HTTPException) as exc:
        await service.get_species(99999)

    assert exc.value.status_code == 404


async def test_get_species_invalid_identifier_returns_400(service, api):
    with pytest.raises(HTTPException) as exc:
        await service.get_species("../pokemon/1")

    assert exc.value.status_code == 400
    assert not api.calls.called


def listing_payload(names: list[str], count: int | None = None) -> dict:
    """Respuesta del listado de PokeAPI, que solo incluye nombre y URL."""
    return {
        "count": len(names) if count is None else count,
        "next": None,
        "previous": None,
        "results": [{"name": name, "url": f"http://api/pokemon/{name}/"} for name in names],
    }


async def test_search_pokemon_paginates_and_returns_summary(service, api):
    """Pasa limit y offset a PokeAPI, conserva el orden y devuelve la lista simplificada."""
    listing = api.get("/pokemon", params={"limit": 2, "offset": 4}).respond(
        json=listing_payload(["charmander", "charmeleon"], count=1302)
    )
    api.get("/pokemon/charmander").respond(json=pokemon_payload(4, "charmander"))
    api.get("/pokemon/charmeleon").respond(json=pokemon_payload(5, "charmeleon"))

    result = await service.search_pokemon(limit=2, offset=4)

    assert listing.called
    assert result == {
        "count": 1302,
        "results": [
            {
                "id": 4,
                "name": "charmander",
                "sprite": "http://img/4.png",
                "types": ["electric"],
            },
            {
                "id": 5,
                "name": "charmeleon",
                "sprite": "http://img/5.png",
                "types": ["electric"],
            },
        ],
    }


async def test_search_pokemon_logs_url_with_query_params(service, api, caplog):
    api.get("/pokemon").respond(json=listing_payload([]))

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await service.search_pokemon(limit=2, offset=4)

    assert f"{PokeAPIService.BASE_URL}/pokemon?limit=2&offset=4 -> 200" in caplog.text


async def test_search_pokemon_filters_by_partial_name(service, api):
    """Filtra sin distinguir mayúsculas; `count` es el total de coincidencias, no de la página."""
    api.get("/pokemon", params={"limit": 100_000, "offset": 0}).respond(
        json=listing_payload(["bulbasaur", "charmander", "charmeleon", "charizard"])
    )
    api.get("/pokemon/charmeleon").respond(json=pokemon_payload(5, "charmeleon"))

    result = await service.search_pokemon(limit=1, offset=1, name=" CHAR ")

    assert result["count"] == 3
    assert [pokemon["name"] for pokemon in result["results"]] == ["charmeleon"]


async def test_search_pokemon_without_matches_returns_empty(service, api):
    api.get("/pokemon").respond(json=listing_payload(["bulbasaur"]))

    assert await service.search_pokemon(name="zzz") == {"count": 0, "results": []}


async def test_search_pokemon_detail_failure_returns_502(service, api):
    """Si falla el detalle de un resultado, falla la búsqueda en vez de devolverla incompleta."""
    api.get("/pokemon").respond(json=listing_payload(["pikachu"]))
    api.get("/pokemon/pikachu").respond(500)

    with pytest.raises(HTTPException) as exc:
        await service.search_pokemon()

    assert exc.value.status_code == 502


async def test_search_pokemon_unexpected_format_returns_502(service, api):
    api.get("/pokemon").respond(json={"results": "no es un listado"})

    with pytest.raises(HTTPException) as exc:
        await service.search_pokemon()

    assert exc.value.status_code == 502


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"name": ""}, {"name": "../type"}],
)
async def test_search_pokemon_invalid_params_returns_400(service, api, params):
    with pytest.raises(HTTPException) as exc:
        await service.search_pokemon(**params)

    assert exc.value.status_code == 400
    assert not api.calls.called


async def test_get_pokemon_by_type_returns_id_and_name(service, api):
    api.get("/type/fire").respond(
        json={
            "id": 10,
            "name": "fire",
            "pokemon": [
                {"slot": 1, "pokemon": {"name": "charmander", "url": "http://api/pokemon/4/"}},
                {"slot": 1, "pokemon": {"name": "vulpix", "url": "http://api/pokemon/37"}},
            ],
        }
    )

    assert await service.get_pokemon_by_type("FIRE") == [
        {"id": 4, "name": "charmander"},
        {"id": 37, "name": "vulpix"},
    ]


async def test_get_pokemon_by_type_not_found_returns_404(service, api):
    api.get("/type/nope").respond(404)

    with pytest.raises(HTTPException) as exc:
        await service.get_pokemon_by_type("nope")

    assert exc.value.status_code == 404


async def test_get_pokemon_by_type_invalid_name_returns_400(service, api):
    with pytest.raises(HTTPException) as exc:
        await service.get_pokemon_by_type("../pokemon/1")

    assert exc.value.status_code == 400
    assert not api.calls.called


async def test_get_pokemon_by_type_unexpected_url_returns_502(service, api):
    api.get("/type/fire").respond(
        json={"pokemon": [{"pokemon": {"name": "x", "url": "http://api/pokemon/sin-id/"}}]}
    )

    with pytest.raises(HTTPException) as exc:
        await service.get_pokemon_by_type("fire")

    assert exc.value.status_code == 502


@pytest.fixture
def cached_service() -> PokeAPIService:
    """Servicio con caché activa, aunque la configuración la tenga desactivada."""
    return PokeAPIService(cache_ttl=60)


async def test_cache_repeated_request_calls_pokeapi_once(cached_service, api, caplog):
    route = api.get("/pokemon/25").respond(json=pokemon_payload())

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        first = await cached_service.get_pokemon(25)
        second = await cached_service.get_pokemon(25)

    assert first == second
    assert route.call_count == 1
    assert f"{PokeAPIService.BASE_URL}/pokemon/25 -> caché" in caplog.text


async def test_cache_uses_query_params_in_key(cached_service, api):
    """Dos páginas distintas del listado no comparten respuesta."""
    page_one = api.get("/pokemon", params={"limit": 1, "offset": 0}).respond(
        json=listing_payload([])
    )
    page_two = api.get("/pokemon", params={"limit": 1, "offset": 1}).respond(
        json=listing_payload([])
    )

    await cached_service.search_pokemon(limit=1, offset=0)
    await cached_service.search_pokemon(limit=1, offset=1)

    assert page_one.call_count == 1
    assert page_two.call_count == 1


async def test_cache_reuses_full_listing_between_name_searches(cached_service, api):
    route = api.get("/pokemon").respond(json=listing_payload(["bulbasaur"]))

    await cached_service.search_pokemon(name="zzz")
    await cached_service.search_pokemon(name="yyy")

    assert route.call_count == 1


async def test_cache_expired_entry_calls_pokeapi_again(cached_service, api, monkeypatch):
    route = api.get("/pokemon/25").respond(json=pokemon_payload())
    monkeypatch.setattr("app.services.pokeapi_service._now", lambda: 1000.0)
    await cached_service.get_pokemon(25)

    monkeypatch.setattr("app.services.pokeapi_service._now", lambda: 1059.0)
    await cached_service.get_pokemon(25)
    assert route.call_count == 1

    monkeypatch.setattr("app.services.pokeapi_service._now", lambda: 1060.0)
    await cached_service.get_pokemon(25)
    assert route.call_count == 2


async def test_cache_does_not_store_errors(cached_service, api):
    """Tras un fallo, la siguiente petición vuelve a intentarlo y puede recuperarse."""
    route = api.get("/pokemon/25").mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json=pokemon_payload())]
    )

    with pytest.raises(HTTPException):
        await cached_service.get_pokemon(25)
    assert (await cached_service.get_pokemon(25))["id"] == 25

    assert route.call_count == 2


async def test_cache_disabled_with_zero_ttl_always_calls_pokeapi(api):
    disabled = PokeAPIService(cache_ttl=0)
    route = api.get("/pokemon/25").respond(json=pokemon_payload())

    await disabled.get_pokemon(25)
    await disabled.get_pokemon(25)

    assert route.call_count == 2


async def test_cache_full_discards_oldest_entry(cached_service, api, monkeypatch):
    monkeypatch.setattr("app.services.pokeapi_service._CACHE_MAX_ENTRIES", 1)
    first = api.get("/pokemon/1").respond(json=pokemon_payload(1, "bulbasaur"))
    second = api.get("/pokemon/2").respond(json=pokemon_payload(2, "ivysaur"))

    await cached_service.get_pokemon(1)
    await cached_service.get_pokemon(2)
    await cached_service.get_pokemon(2)
    await cached_service.get_pokemon(1)

    assert second.call_count == 1
    assert first.call_count == 2


async def test_clear_cache_forces_new_call(cached_service, api):
    route = api.get("/pokemon/25").respond(json=pokemon_payload())

    await cached_service.get_pokemon(25)
    cached_service.clear_cache()
    await cached_service.get_pokemon(25)

    assert route.call_count == 2


def test_get_pokeapi_service_returns_single_instance():
    assert get_pokeapi_service() is pokeapi_service
