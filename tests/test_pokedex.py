"""Tests de la Pokédex personal: CRUD, filtros, orden, paginación y exportación."""

import csv
import io

import pytest
from fastapi import HTTPException

from app.auth import create_access_token, hash_password
from app.limiter import limiter
from app.main import app
from app.models import PokedexEntry, Team, TeamMember, User
from app.routers import pokedex as pokedex_router
from app.services.pokeapi_service import get_pokeapi_service

POKEDEX = "/api/v1/pokedex"

_FAKE_POKEMON = {
    1: ("bulbasaur", ["grass", "poison"]),
    4: ("charmander", ["fire"]),
    25: ("pikachu", ["electric"]),
}


class FakePokeAPI:
    """Sustituye a PokeAPI: nunca hay red en los tests."""

    async def get_pokemon(self, identifier):
        if int(identifier) not in _FAKE_POKEMON:
            raise HTTPException(status_code=404, detail="No encontrado en PokeAPI")
        name, types = _FAKE_POKEMON[int(identifier)]
        return {"id": int(identifier), "name": name, "types": types,
                "sprite": f"https://img/{identifier}.png"}


@pytest.fixture(autouse=True)
def fake_pokeapi():
    app.dependency_overrides[get_pokeapi_service] = FakePokeAPI
    yield
    app.dependency_overrides.pop(get_pokeapi_service, None)


@pytest.fixture
def other_user_entry(session):
    """Entrada de la Pokédex de otro usuario ("gary")."""
    gary = User(username="gary", email="gary@poke.com", hashed_password=hash_password("Eevee1234"))
    session.add(gary)
    session.commit()
    entry = PokedexEntry(owner_id=gary.id, pokemon_id=4, pokemon_name="charmander",
                         pokemon_sprite="x", pokemon_types="fire")
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _add(client, headers, pokemon_id, **extra):
    return client.post(POKEDEX, json={"pokemon_id": pokemon_id, **extra}, headers=headers)


# --- Tests del enunciado ---


def test_add_pokemon_to_pokedex(client, auth_headers):
    """Añadir Pokémon a la Pokédex"""
    response = _add(client, auth_headers, 25, nickname="Sparky", is_captured=True)
    assert response.status_code == 201
    body = response.json()
    assert body["pokemon_name"] == "pikachu"
    assert body["pokemon_types"] == "electric"
    assert body["pokemon_sprite"] == "https://img/25.png"
    assert body["nickname"] == "Sparky"
    assert body["capture_date"] is not None  # capturado sin fecha → ahora


def test_add_duplicate_pokemon(client, auth_headers):
    """No permitir duplicados"""
    assert _add(client, auth_headers, 25).status_code == 201
    assert _add(client, auth_headers, 25).status_code == 409


def test_get_pokedex_with_filters(client, auth_headers):
    """Filtros funcionan correctamente"""
    _add(client, auth_headers, 1, is_captured=True, favorite=True)
    _add(client, auth_headers, 4, is_captured=True)
    _add(client, auth_headers, 25)
    response = client.get(f"{POKEDEX}?captured=true&favorite=false", headers=auth_headers)
    assert response.status_code == 200
    assert [e["pokemon_name"] for e in response.json()] == ["charmander"]


def test_update_pokedex_entry(client, auth_headers):
    """Actualizar entrada de Pokédex"""
    entry_id = _add(client, auth_headers, 25).json()["id"]
    response = client.patch(
        f"{POKEDEX}/{entry_id}",
        json={"is_captured": True, "capture_date": "2025-01-13T10:00:00Z",
              "nickname": "Rayo", "favorite": True},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["nickname"] == "Rayo" and body["favorite"] and body["is_captured"]
    assert body["capture_date"].startswith("2025-01-13T10:00:00")


def test_delete_pokedex_entry(client, auth_headers):
    """Eliminar entrada de Pokédex"""
    entry_id = _add(client, auth_headers, 25).json()["id"]
    assert client.delete(f"{POKEDEX}/{entry_id}", headers=auth_headers).status_code == 204
    assert client.get(POKEDEX, headers=auth_headers).json() == []


def test_cannot_modify_other_user_pokedex(client, auth_headers, other_user_entry):
    """No se puede modificar Pokédex de otro usuario"""
    url = f"{POKEDEX}/{other_user_entry.id}"
    # 404 y no 403: no se revela que la entrada existe.
    assert client.patch(url, json={"nickname": "Mío"}, headers=auth_headers).status_code == 404
    assert client.delete(url, headers=auth_headers).status_code == 404
    assert other_user_entry.nickname is None


# --- Alta ---


def test_add_pokemon_not_in_pokeapi_returns_404(client, auth_headers):
    """El pokemon_id tiene que existir en PokeAPI."""
    assert _add(client, auth_headers, 99999).status_code == 404


def test_add_invalid_pokemon_id_returns_422(client, auth_headers):
    assert _add(client, auth_headers, 0).status_code == 422


def test_same_pokemon_for_two_users_is_allowed(client, auth_headers, other_user_entry):
    """El duplicado es por usuario: gary ya tiene a charmander y yo también puedo."""
    assert _add(client, auth_headers, 4).status_code == 201


def test_pokedex_requires_token(client):
    assert client.get(POKEDEX).status_code == 401
    assert client.post(POKEDEX, json={"pokemon_id": 25}).status_code == 401
    assert client.get(f"{POKEDEX}/export").status_code == 401


# --- Listado ---


def test_list_only_my_entries(client, auth_headers, other_user_entry):
    _add(client, auth_headers, 25)
    assert [e["pokemon_name"] for e in client.get(POKEDEX, headers=auth_headers).json()] == [
        "pikachu"
    ]


@pytest.mark.parametrize(
    "query, expected",
    [
        ("", ["bulbasaur", "charmander", "pikachu"]),
        ("?sort=pokemon_id&order=desc", ["pikachu", "charmander", "bulbasaur"]),
        ("?sort=pokemon_name&order=asc", ["bulbasaur", "charmander", "pikachu"]),
        ("?limit=1&offset=1", ["charmander"]),
    ],
)
def test_list_sort_and_pagination(client, auth_headers, query, expected):
    for pokemon_id in (25, 1, 4):
        _add(client, auth_headers, pokemon_id)
    response = client.get(POKEDEX + query, headers=auth_headers)
    assert [e["pokemon_name"] for e in response.json()] == expected


def test_list_sort_by_capture_date(client, auth_headers):
    _add(client, auth_headers, 25, is_captured=True, capture_date="2025-03-01T00:00:00Z")
    _add(client, auth_headers, 1, is_captured=True, capture_date="2025-01-01T00:00:00Z")
    response = client.get(f"{POKEDEX}?sort=capture_date", headers=auth_headers)
    assert [e["pokemon_name"] for e in response.json()] == ["bulbasaur", "pikachu"]


@pytest.mark.parametrize(
    "query", ["?sort=nickname", "?order=up", "?limit=0", "?limit=101", "?offset=-1"]
)
def test_list_invalid_params_return_422(client, auth_headers, query):
    assert client.get(POKEDEX + query, headers=auth_headers).status_code == 422


# --- Actualizar y borrar ---


def test_uncapture_clears_capture_date(client, auth_headers):
    entry_id = _add(client, auth_headers, 25, is_captured=True).json()["id"]
    body = client.patch(
        f"{POKEDEX}/{entry_id}", json={"is_captured": False}, headers=auth_headers
    ).json()
    assert body["capture_date"] is None


def test_patch_null_boolean_is_ignored(client, auth_headers):
    """Un null en un booleano no rompe la base de datos: se ignora."""
    entry_id = _add(client, auth_headers, 25, favorite=True).json()["id"]
    response = client.patch(f"{POKEDEX}/{entry_id}", json={"favorite": None},
                            headers=auth_headers)
    assert response.status_code == 200 and response.json()["favorite"] is True


def test_patch_and_delete_missing_entry_return_404(client, auth_headers):
    assert client.patch(f"{POKEDEX}/999", json={}, headers=auth_headers).status_code == 404
    assert client.delete(f"{POKEDEX}/999", headers=auth_headers).status_code == 404


def test_cannot_delete_entry_in_a_team(client, session, auth_headers, test_user):
    """Si el Pokémon está en un equipo, 409 en vez de dejar el equipo roto."""
    entry_id = _add(client, auth_headers, 25).json()["id"]
    team = Team(trainer_id=test_user.id, name="Elite")
    team.members.append(TeamMember(pokedex_entry_id=entry_id, position=1))
    session.add(team)
    session.commit()
    assert client.delete(f"{POKEDEX}/{entry_id}", headers=auth_headers).status_code == 409


# --- Exportar ---


def test_export_csv(client, auth_headers):
    _add(client, auth_headers, 25, is_captured=True, nickname="=HYPERLINK()")
    _add(client, auth_headers, 1)
    response = client.get(f"{POKEDEX}/export?format=csv&captured=true", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="pokedex.csv"'
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert [row["pokemon_name"] for row in rows] == ["pikachu"]
    # Una fórmula en el apodo se exporta como texto, no se ejecuta en Excel.
    assert rows[0]["nickname"] == "'=HYPERLINK()"


def test_export_pdf(client, auth_headers):
    _add(client, auth_headers, 25)
    response = client.get(f"{POKEDEX}/export?format=pdf", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="pokedex.pdf"'
    assert response.content.startswith(b"%PDF")


def test_export_pdf_wraps_long_cells_and_escapes_markup(client, auth_headers):
    """Las notas largas saltan de línea dentro de la página y "<" o "&" no rompen el PDF."""
    long_notes = "Lo encontré en la Ruta 1 <b>sin</b> Poké Balls & con prisa. " * 8
    _add(client, auth_headers, 25, notes=long_notes)
    response = client.get(f"{POKEDEX}/export?format=pdf", headers=auth_headers)
    assert response.status_code == 200 and response.content.startswith(b"%PDF")

    page_width = 700
    short = pokedex_router._export_table([["25", "pikachu", "electric", "", "False", "", "False", "hola"]],
                                         page_width)
    long = pokedex_router._export_table([["25", "pikachu", "electric", "", "False", "", "False", long_notes]],
                                        page_width)
    short_width, short_height = short.wrap(page_width, 10_000)
    long_width, long_height = long.wrap(page_width, 10_000)
    assert long_width <= page_width + 0.01
    assert long_height > short_height  # la fila crece hacia abajo en vez de salirse por la derecha
    # Un username con "<" (lo permite el registro) rompía el título del PDF con un 500.
    assert pokedex_router._to_pdf([], "ash<b").startswith(b"%PDF")


def test_export_invalid_format_returns_422(client, auth_headers):
    assert client.get(f"{POKEDEX}/export?format=xls", headers=auth_headers).status_code == 422


# --- Rate limiting ---


def test_add_entry_is_rate_limited_like_pokeapi(client, auth_headers, monkeypatch):
    """El alta llama a PokeAPI: 30 por minuto. La 31.ª devuelve 429."""
    monkeypatch.setattr(limiter, "enabled", True)
    codes = [_add(client, auth_headers, 25).status_code for _ in range(31)]
    assert codes[0] == 201 and codes[-1] == 429


def test_each_user_sees_only_own_entries(client, other_user_entry):
    """Cada usuario ve solo lo suyo (gary ve a su charmander)."""
    gary = other_user_entry.owner
    headers = {"Authorization": f"Bearer {create_access_token(gary)}"}
    assert [e["pokemon_name"] for e in client.get(POKEDEX, headers=headers).json()] == [
        "charmander"
    ]
