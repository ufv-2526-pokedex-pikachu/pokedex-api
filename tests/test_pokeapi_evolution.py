"""Tests de `PokeAPIService.get_evolution_chain`. Nunca usan la red: respx simula PokeAPI."""

import pytest
import respx
from fastapi import HTTPException

from app.services.pokeapi_service import PokeAPIService


def species_payload(name: str, chain_id: int | None) -> dict:
    """Especie reducida; la URL de la cadena es absoluta, como la da PokeAPI."""
    chain = None if chain_id is None else {"url": f"https://pokeapi.co/api/v2/evolution-chain/{chain_id}/"}
    return {"id": 1, "name": name, "evolution_chain": chain, "flavor_text_entries": []}


def node(name: str, *evolves_to: dict) -> dict:
    """Un nodo del árbol `chain` de PokeAPI."""
    return {"species": {"name": name, "url": "x"}, "evolves_to": list(evolves_to), "is_baby": False}


CHARMANDER_CHAIN = {"id": 2, "chain": node("charmander", node("charmeleon", node("charizard")))}
EEVEE_CHAIN = {"id": 67, "chain": node("eevee", node("vaporeon"), node("jolteon"), node("flareon"))}


@pytest.fixture
def service() -> PokeAPIService:
    return PokeAPIService()


@pytest.fixture
def api():
    """Simula PokeAPI. Cualquier petición no simulada falla, así que nunca se sale a la red."""
    with respx.mock(base_url=PokeAPIService.BASE_URL, assert_all_called=False) as router:
        yield router


async def test_get_evolution_chain_linear(service, api):
    """Charizard → especie → cadena 2 → los tres nombres en orden."""
    api.get("/pokemon-species/6").respond(json=species_payload("charizard", 2))
    api.get("/evolution-chain/2").respond(json=CHARMANDER_CHAIN)

    assert await service.get_evolution_chain(6) == ["charmander", "charmeleon", "charizard"]


async def test_get_evolution_chain_branching_is_breadth_first(service, api):
    """Eevee tiene varias evoluciones en la misma etapa: van después de Eevee, en orden."""
    api.get("/pokemon-species/133").respond(json=species_payload("eevee", 67))
    api.get("/evolution-chain/67").respond(json=EEVEE_CHAIN)

    assert await service.get_evolution_chain(133) == ["eevee", "vaporeon", "jolteon", "flareon"]


async def test_get_evolution_chain_uses_our_base_url_not_the_one_in_the_response(service, api):
    """Aunque la especie traiga una URL de otro dominio, solo se usa su número (evita SSRF)."""
    species = species_payload("charizard", 2)
    species["evolution_chain"]["url"] = "https://malicioso.example/api/v2/evolution-chain/2/"
    api.get("/pokemon-species/6").respond(json=species)
    route = api.get("/evolution-chain/2").respond(json=CHARMANDER_CHAIN)

    await service.get_evolution_chain(6)

    assert route.called
    assert all(str(call.request.url).startswith(PokeAPIService.BASE_URL) for call in api.calls)


async def test_get_evolution_chain_species_without_chain(service, api):
    """Si la especie no tiene cadena, devuelve solo su nombre y no pide nada más."""
    api.get("/pokemon-species/132").respond(json=species_payload("ditto", None))

    assert await service.get_evolution_chain(132) == ["ditto"]
    assert len(api.calls) == 1


async def test_get_evolution_chain_not_found_returns_404(service, api):
    """Un ID sin especie en PokeAPI (p. ej. una forma mega) → 404, como get_species."""
    api.get("/pokemon-species/10034").respond(404)

    with pytest.raises(HTTPException) as exc:
        await service.get_evolution_chain(10034)

    assert exc.value.status_code == 404


async def test_get_evolution_chain_unexpected_format_returns_502(service, api):
    """Si PokeAPI cambia el formato (falta `evolves_to`), se responde 502."""
    api.get("/pokemon-species/6").respond(json=species_payload("charizard", 2))
    api.get("/evolution-chain/2").respond(json={"chain": {"species": {"name": "charmander"}}})

    with pytest.raises(HTTPException) as exc:
        await service.get_evolution_chain(6)

    assert exc.value.status_code == 502


async def test_get_evolution_chain_invalid_identifier_returns_400(service, api):
    """Un identificador no válido no llega a PokeAPI."""
    with pytest.raises(HTTPException) as exc:
        await service.get_evolution_chain("../evolution-chain/1")

    assert exc.value.status_code == 400
    assert not api.calls.called


async def test_get_evolution_chain_is_cached_per_chain(api):
    """Con caché, Charmander y Charizard piden la cadena una sola vez (comparten la 2)."""
    cached = PokeAPIService(cache_ttl=60)
    api.get("/pokemon-species/4").respond(json=species_payload("charmander", 2))
    api.get("/pokemon-species/6").respond(json=species_payload("charizard", 2))
    chain_route = api.get("/evolution-chain/2").respond(json=CHARMANDER_CHAIN)

    first = await cached.get_evolution_chain(4)
    second = await cached.get_evolution_chain(6)

    assert first == second == ["charmander", "charmeleon", "charizard"]
    assert chain_route.call_count == 1
