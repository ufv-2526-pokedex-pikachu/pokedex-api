"""Tests de /api/v1/pokemon. PokeAPI siempre va simulada con respx: nunca se usa la red."""

import httpx
import pytest
import respx

from app.limiter import limiter
from app.services.pokeapi_service import PokeAPIService
from tests.test_pokeapi_service import listing_payload, pokemon_payload

BASE = PokeAPIService.BASE_URL
SEARCH = "/api/v1/pokemon/search"
DETAIL = "/api/v1/pokemon"


@pytest.fixture
def api():
    """Simula PokeAPI. Cualquier petición no simulada falla, así que nunca se sale a la red."""
    with respx.mock(assert_all_called=False) as router:
        yield router


def test_search_returns_simplified_list_with_filter_and_pagination(client, auth_headers, api):
    api.get(f"{BASE}/pokemon", params={"limit": 100_000, "offset": 0}).respond(
        json=listing_payload(["bulbasaur", "charmander", "charmeleon", "charizard"])
    )
    api.get(f"{BASE}/pokemon/charmeleon").respond(json=pokemon_payload(5, "charmeleon"))

    response = client.get(
        SEARCH, params={"name": "char", "limit": 1, "offset": 1}, headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json() == {
        "count": 3,
        "results": [
            {"id": 5, "name": "charmeleon", "sprite": "http://img/5.png", "types": ["electric"]}
        ],
    }


def test_search_without_name_uses_pokeapi_pagination(client, auth_headers, api):
    listing = api.get(f"{BASE}/pokemon", params={"limit": 20, "offset": 0}).respond(
        json=listing_payload([], count=1351)
    )

    response = client.get(SEARCH, headers=auth_headers)

    assert response.json() == {"count": 1351, "results": []}
    assert listing.called


def test_search_word_is_not_taken_as_pokemon_name(client, auth_headers, api):
    """`/search` está declarada antes que `/{id_or_name}`: nunca se pide el Pokémon "search"."""
    api.get(f"{BASE}/pokemon").respond(json=listing_payload([]))
    as_name = api.get(f"{BASE}/pokemon/search").respond(json=pokemon_payload())

    response = client.get(SEARCH, headers=auth_headers)

    assert response.status_code == 200
    assert "count" in response.json()
    assert not as_name.called


def test_search_without_token_returns_401(client, api):
    assert client.get(SEARCH).status_code == 401
    assert not api.calls.called


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
def test_search_invalid_pagination_returns_422(client, auth_headers, api, params):
    assert client.get(SEARCH, params=params, headers=auth_headers).status_code == 422
    assert not api.calls.called


def test_search_invalid_name_returns_400_from_service(client, auth_headers, api):
    response = client.get(SEARCH, params={"name": "../type"}, headers=auth_headers)

    assert response.status_code == 400
    assert not api.calls.called


def test_detail_returns_full_pokemon(client, auth_headers, api):
    api.get(f"{BASE}/pokemon/pikachu").respond(json=pokemon_payload())

    response = client.get(f"{DETAIL}/Pikachu", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "pikachu"
    assert body["types"] == ["electric"]
    assert body["stats"] == {"hp": 35, "speed": 90}
    assert body["abilities"] == ["static"]
    assert set(body["sprites"]) == {"front_default", "back_default", "front_shiny", "back_shiny"}


def test_detail_unknown_pokemon_returns_404(client, auth_headers, api):
    api.get(f"{BASE}/pokemon/missingno").respond(404)

    assert client.get(f"{DETAIL}/missingno", headers=auth_headers).status_code == 404


def test_detail_pokeapi_timeout_returns_504(client, auth_headers, api):
    api.get(f"{BASE}/pokemon/25").mock(side_effect=httpx.ReadTimeout("lento"))

    assert client.get(f"{DETAIL}/25", headers=auth_headers).status_code == 504


def test_detail_without_token_returns_401(client, api):
    assert client.get(f"{DETAIL}/25").status_code == 401
    assert not api.calls.called


def test_search_rate_limit_31st_request_returns_429(client, auth_headers, api, monkeypatch):
    """Los endpoints que llaman a PokeAPI admiten 30 peticiones por minuto."""
    monkeypatch.setattr(limiter, "enabled", True)
    api.get(f"{BASE}/pokemon").respond(json=listing_payload([]))

    codes = [client.get(SEARCH, headers=auth_headers).status_code for _ in range(31)]

    assert codes[:30] == [200] * 30
    assert codes[30] == 429
