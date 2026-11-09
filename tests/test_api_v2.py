"""Tests del versionado: v1 sigue igual y v2 añade total, tipos como lista y evolución."""

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.auth import hash_password
from app.limiter import limiter
from app.main import app
from app.models import PokedexEntry, User
from app.services.pokeapi_service import get_pokeapi_service

V1_URL = "/api/v1/pokedex"
V2_URL = "/api/v2/pokedex"

CHAINS = {
    4: ["charmander", "charmeleon", "charizard"],
    6: ["charmander", "charmeleon", "charizard"],
    25: ["pichu", "pikachu", "raichu"],
    133: ["eevee", "vaporeon", "jolteon", "flareon"],
}


class FakePokeAPI:
    """Sustituye a PokeAPI: devuelve cadenas fijas, falla con los IDs de `failing` y apunta las llamadas."""

    def __init__(self, failing: frozenset[int] = frozenset()):
        self.failing = failing
        self.calls: list[int] = []

    async def get_evolution_chain(self, pokemon_id: int) -> list[str]:
        self.calls.append(pokemon_id)
        if pokemon_id in self.failing:
            raise HTTPException(status_code=504, detail="PokeAPI no responde")
        return CHAINS[pokemon_id]


@pytest.fixture
def fake_pokeapi():
    fake = FakePokeAPI()
    app.dependency_overrides[get_pokeapi_service] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_pokeapi_service, None)


def add_entry(session, user: User, pokemon_id: int, name: str, types: str, **fields) -> PokedexEntry:
    entry = PokedexEntry(
        owner_id=user.id,
        pokemon_id=pokemon_id,
        pokemon_name=name,
        pokemon_sprite=f"https://img.test/{pokemon_id}.png",
        pokemon_types=types,
        **fields,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


@pytest.fixture
def pokedex(session, test_user):
    """Pokédex de `test_user`: Charizard (capturado y favorito), Pikachu (capturado) y Eevee."""
    now = datetime.now(timezone.utc)
    return [
        add_entry(session, test_user, 6, "charizard", "fire,flying",
                  is_captured=True, favorite=True, capture_date=now),
        add_entry(session, test_user, 25, "pikachu", "electric", is_captured=True, capture_date=now),
        add_entry(session, test_user, 133, "eevee", "normal"),
    ]


# --- v1 no cambia ---


def test_v1_still_returns_a_plain_list_with_types_as_text(client, auth_headers, pokedex, fake_pokeapi):
    """v1 responde igual que antes: una lista, `pokemon_types` como texto y sin evolución."""
    response = client.get(V1_URL, headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert [item["pokemon_name"] for item in data] == ["charizard", "pikachu", "eevee"]
    assert data[0]["pokemon_types"] == "fire,flying"
    assert "evolution_chain" not in data[0]
    assert fake_pokeapi.calls == []


# --- v2 ---


def test_v2_returns_page_with_total_types_list_and_evolution(client, auth_headers, pokedex, fake_pokeapi):
    """v2 envuelve la respuesta, da los tipos como lista y añade la cadena de evolución."""
    response = client.get(V2_URL, headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["limit"] == 20
    assert data["offset"] == 0
    charizard = data["items"][0]
    assert charizard["pokemon_name"] == "charizard"
    assert charizard["pokemon_types"] == ["fire", "flying"]
    assert charizard["evolution_chain"] == ["charmander", "charmeleon", "charizard"]
    assert charizard["favorite"] is True
    assert data["items"][2]["evolution_chain"] == ["eevee", "vaporeon", "jolteon", "flareon"]
    assert sorted(fake_pokeapi.calls) == [6, 25, 133]


def test_v2_and_v1_share_filters_sort_and_pagination(client, auth_headers, pokedex, fake_pokeapi):
    """Mismos filtros y orden que v1; `total` cuenta todo lo filtrado, no solo la página."""
    query = "?captured=true&sort=pokemon_name&order=desc&limit=1&offset=0"

    v1 = client.get(V1_URL + query, headers=auth_headers).json()
    v2 = client.get(V2_URL + query, headers=auth_headers).json()

    assert [item["pokemon_name"] for item in v1] == ["pikachu"]
    assert [item["pokemon_name"] for item in v2["items"]] == ["pikachu"]
    assert v2["total"] == 2
    assert (v2["limit"], v2["offset"]) == (1, 0)
    assert fake_pokeapi.calls == [25]


def test_v2_entry_without_types_gives_empty_list(client, auth_headers, session, test_user, fake_pokeapi):
    """Una entrada sin tipos guardados da `[]` en v2 (no `[""]`)."""
    add_entry(session, test_user, 4, "charmander", "")

    item = client.get(V2_URL, headers=auth_headers).json()["items"][0]

    assert item["pokemon_types"] == []


def test_v2_pokeapi_failure_gives_null_evolution_only_for_that_entry(client, auth_headers, pokedex):
    """Si PokeAPI falla con un Pokémon, esa entrada sale con `evolution_chain: null` y el resto bien."""
    fake = FakePokeAPI(failing=frozenset({25}))
    app.dependency_overrides[get_pokeapi_service] = lambda: fake
    try:
        response = client.get(V2_URL, headers=auth_headers)
    finally:
        app.dependency_overrides.pop(get_pokeapi_service, None)

    assert response.status_code == 200
    chains = {item["pokemon_name"]: item["evolution_chain"] for item in response.json()["items"]}
    assert chains["pikachu"] is None
    assert chains["charizard"] == ["charmander", "charmeleon", "charizard"]


def test_v2_empty_pokedex(client, auth_headers, fake_pokeapi):
    """Pokédex vacía: total 0, sin elementos y sin llamar a PokeAPI."""
    response = client.get(V2_URL, headers=auth_headers)

    assert response.json() == {"total": 0, "limit": 20, "offset": 0, "items": []}
    assert fake_pokeapi.calls == []


def test_v2_only_my_entries(client, auth_headers, session, pokedex, fake_pokeapi):
    """Las entradas de otro usuario no aparecen ni cuentan en el total."""
    gary = User(username="gary", email="gary@poke.com", hashed_password=hash_password("Eevee1234"))
    session.add(gary)
    session.commit()
    session.refresh(gary)
    add_entry(session, gary, 4, "charmander", "fire")

    data = client.get(V2_URL, headers=auth_headers).json()

    assert data["total"] == 3
    assert 4 not in [item["pokemon_id"] for item in data["items"]]


@pytest.mark.parametrize("query", ["?limit=0", "?limit=101", "?offset=-1", "?sort=nickname", "?order=up"])
def test_v2_invalid_params_return_422(client, auth_headers, fake_pokeapi, query):
    """Mismas validaciones de parámetros que v1."""
    assert client.get(V2_URL + query, headers=auth_headers).status_code == 422


def test_v2_requires_token(client):
    """Sin token → 401."""
    assert client.get(V2_URL).status_code == 401


def test_v2_rate_limit_is_pokeapi_limit(client, auth_headers, fake_pokeapi, monkeypatch):
    """v2 llama a PokeAPI: 30 peticiones/minuto; la 31.ª → 429."""
    monkeypatch.setattr(limiter, "enabled", True)

    statuses = [client.get(V2_URL, headers=auth_headers).status_code for _ in range(31)]

    assert statuses[:30] == [200] * 30
    assert statuses[30] == 429


def test_v2_is_documented_in_openapi(client):
    """v1 y v2 aparecen en /docs (OpenAPI) a la vez."""
    paths = client.get("/openapi.json").json()["paths"]

    assert "/api/v1/pokedex" in paths
    assert "/api/v2/pokedex" in paths
