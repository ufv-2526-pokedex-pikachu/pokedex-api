"""Tests de los equipos de batalla: creación, listado, actualización y exportación en PDF."""

import io

import pytest
from fastapi import HTTPException
from PIL import Image
from reportlab.pdfgen.canvas import Canvas
from sqlmodel import select

from app.auth import create_access_token, hash_password
from app.limiter import limiter
from app.main import app
from app.models import PokedexEntry, Team, TeamMember, User
from app.routers.teams import combine_stats
from app.services.pdf_service import draw_pokemon_card
from app.services.pokeapi_service import get_pokeapi_service

TEAMS_URL = "/api/v1/teams"

# Datos mínimos de PokeAPI para las entradas de la Pokédex: id -> nombre.
POKEMON = {1: "bulbasaur", 4: "charmander", 7: "squirtle", 25: "pikachu", 133: "eevee", 143: "snorlax", 150: "mewtwo"}


def add_entry(session, user: User, pokemon_id: int, nickname: str | None = None) -> PokedexEntry:
    """Añade directamente a la Pokédex de `user` una entrada del Pokémon `pokemon_id`."""
    entry = PokedexEntry(
        owner_id=user.id,
        pokemon_id=pokemon_id,
        pokemon_name=POKEMON[pokemon_id],
        pokemon_sprite=f"https://img.test/{pokemon_id}.png",
        nickname=nickname,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def fake_pokemon(pokemon_id: int) -> dict:
    """Respuesta de `get_pokemon` con la forma real del servicio y stats predecibles."""
    return {
        "id": pokemon_id,
        "name": POKEMON[pokemon_id],
        "sprite": f"https://img.test/{pokemon_id}.png",
        "sprites": {},
        "types": ["normal"],
        "stats": {
            "hp": 10 * pokemon_id,
            "attack": 50,
            "defense": 40,
            "special-attack": 30,
            "special-defense": 20,
            "speed": 60,
        },
        "abilities": ["run-away"],
        "height": 4,
        "weight": 60,
    }


class FakePokeAPI:
    """Sustituye a PokeAPI en los tests: nunca usa la red y apunta lo que le piden."""

    def __init__(self, error: HTTPException | None = None):
        self.error = error
        self.calls: list[int] = []

    async def get_pokemon(self, identifier):
        self.calls.append(int(identifier))
        if self.error is not None:
            raise self.error
        return fake_pokemon(int(identifier))


@pytest.fixture
def pokedex(session, test_user):
    """Pokédex de `test_user` con 7 Pokémon (uno más del máximo de un equipo)."""
    return {pokemon_id: add_entry(session, test_user, pokemon_id) for pokemon_id in POKEMON}


@pytest.fixture
def other_user(session):
    """Otro usuario, con su propia Pokédex y un equipo."""
    user = User(username="gary", email="gary@poke.com", hashed_password=hash_password("Eevee1234"))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def other_headers(other_user):
    """Cabecera `Authorization` de `other_user`."""
    return {"Authorization": f"Bearer {create_access_token(other_user)}"}


@pytest.fixture
def fake_pokeapi():
    """Sustituye el servicio de PokeAPI por el fake mientras dura el test."""
    fake = FakePokeAPI()
    app.dependency_overrides[get_pokeapi_service] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_pokeapi_service, None)


def create_team(client, headers, pokemon_ids, name="Equipo Elite", description="Mi equipo principal"):
    """Hace el POST de creación y devuelve la respuesta."""
    body = {"name": name, "description": description, "pokemon_ids": pokemon_ids}
    return client.post(TEAMS_URL, json=body, headers=headers)


# --- POST /teams ---


def test_create_team_success(client, auth_headers, pokedex):
    """Crea el equipo del enunciado con 6 Pokémon, en el orden pedido."""
    response = create_team(client, auth_headers, [1, 4, 7, 25, 133, 143])

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Equipo Elite"
    assert data["description"] == "Mi equipo principal"
    assert data["pokemon_ids"] == [1, 4, 7, 25, 133, 143]
    assert [member["position"] for member in data["members"]] == [1, 2, 3, 4, 5, 6]
    assert data["members"][3]["pokemon_name"] == "pikachu"
    assert data["members"][3]["pokedex_entry_id"] == pokedex[25].id


def test_create_team_keeps_given_order_and_nickname(client, auth_headers, session, test_user):
    """La posición sale del orden de la lista, no del ID; el mote viene de la Pokédex."""
    add_entry(session, test_user, 25, nickname="Sparky")
    add_entry(session, test_user, 1)

    response = create_team(client, auth_headers, [25, 1], description=None)

    assert response.status_code == 201
    members = response.json()["members"]
    assert [(m["position"], m["pokemon_id"]) for m in members] == [(1, 25), (2, 1)]
    assert members[0]["nickname"] == "Sparky"
    assert response.json()["description"] is None


def test_create_team_pokemon_not_in_pokedex(client, auth_headers, session, test_user):
    """Si algún Pokémon no está en la Pokédex del usuario → 400 diciendo cuál."""
    add_entry(session, test_user, 25)

    response = create_team(client, auth_headers, [25, 150, 999])

    assert response.status_code == 400
    assert "150" in response.json()["detail"]
    assert "999" in response.json()["detail"]
    assert session.exec(select(Team)).all() == []


def test_create_team_cannot_use_other_user_pokedex(client, auth_headers, session, other_user):
    """Un Pokémon que solo está en la Pokédex de otro usuario no vale."""
    add_entry(session, other_user, 25)

    response = create_team(client, auth_headers, [25])

    assert response.status_code == 400


def test_create_team_more_than_six(client, auth_headers, pokedex):
    """Más de 6 Pokémon → 422, aunque todos estén en la Pokédex."""
    response = create_team(client, auth_headers, [1, 4, 7, 25, 133, 143, 150])

    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"name": "Vacío", "pokemon_ids": []},
        {"name": "Repetidos", "pokemon_ids": [25, 25]},
        {"name": "ID no válido", "pokemon_ids": [0]},
        {"name": "", "pokemon_ids": [25]},
        {"name": "x" * 101, "pokemon_ids": [25]},
        {"pokemon_ids": [25]},
    ],
)
def test_create_team_invalid_body(client, auth_headers, pokedex, body):
    """Lista vacía, repetidos, IDs < 1 o nombre vacío, demasiado largo o ausente → 422."""
    response = client.post(TEAMS_URL, json=body, headers=auth_headers)

    assert response.status_code == 422


# --- GET /teams ---


def test_list_teams_only_mine(client, auth_headers, other_headers, session, pokedex, other_user):
    """Cada usuario ve solo sus equipos."""
    create_team(client, auth_headers, [1, 4], name="Primero")
    create_team(client, auth_headers, [25], name="Segundo")
    add_entry(session, other_user, 150)
    create_team(client, other_headers, [150], name="De Gary")

    response = client.get(TEAMS_URL, headers=auth_headers)

    assert response.status_code == 200
    assert [team["name"] for team in response.json()] == ["Primero", "Segundo"]
    assert [team["name"] for team in client.get(TEAMS_URL, headers=other_headers).json()] == ["De Gary"]


def test_list_teams_empty(client, auth_headers):
    """Sin equipos, la lista está vacía."""
    response = client.get(TEAMS_URL, headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == []


# --- PUT /teams/{team_id} ---


def test_update_team_replaces_everything(client, auth_headers, session, pokedex):
    """El PUT cambia nombre, descripción y miembros; los antiguos se borran, la Pokédex no."""
    team_id = create_team(client, auth_headers, [1, 4, 7]).json()["id"]

    response = client.put(
        f"{TEAMS_URL}/{team_id}",
        json={"name": "Renovado", "description": "Nuevo plan", "pokemon_ids": [150, 25]},
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == team_id
    assert data["name"] == "Renovado"
    assert data["description"] == "Nuevo plan"
    assert data["pokemon_ids"] == [150, 25]
    members = session.exec(select(TeamMember)).all()
    assert sorted((m.position, m.pokedex_entry_id) for m in members) == [
        (1, pokedex[150].id),
        (2, pokedex[25].id),
    ]
    assert len(session.exec(select(PokedexEntry)).all()) == len(POKEMON)


def test_update_team_pokemon_not_in_pokedex_keeps_team(client, auth_headers, session, test_user):
    """Si el PUT no es válido, el equipo se queda como estaba."""
    add_entry(session, test_user, 25)
    team_id = create_team(client, auth_headers, [25], name="Original").json()["id"]

    response = client.put(
        f"{TEAMS_URL}/{team_id}",
        json={"name": "Cambiado", "pokemon_ids": [25, 150]},
        headers=auth_headers,
    )

    assert response.status_code == 400
    team = client.get(TEAMS_URL, headers=auth_headers).json()[0]
    assert team["name"] == "Original"
    assert team["pokemon_ids"] == [25]


def test_update_team_more_than_six(client, auth_headers, pokedex):
    """El PUT también rechaza más de 6 Pokémon."""
    team_id = create_team(client, auth_headers, [1]).json()["id"]

    response = client.put(
        f"{TEAMS_URL}/{team_id}",
        json={"name": "Grande", "pokemon_ids": [1, 4, 7, 25, 133, 143, 150]},
        headers=auth_headers,
    )

    assert response.status_code == 422


def test_update_other_user_team_returns_404(client, auth_headers, other_headers, session, other_user, pokedex):
    """El equipo de otro usuario no se puede modificar y no se revela que existe."""
    add_entry(session, other_user, 150)
    team_id = create_team(client, other_headers, [150], name="De Gary").json()["id"]

    response = client.put(
        f"{TEAMS_URL}/{team_id}", json={"name": "Robado", "pokemon_ids": [25]}, headers=auth_headers
    )

    assert response.status_code == 404
    assert client.get(TEAMS_URL, headers=other_headers).json()[0]["name"] == "De Gary"


def test_update_missing_team_returns_404(client, auth_headers, pokedex):
    """Un equipo que no existe → 404."""
    response = client.put(
        f"{TEAMS_URL}/999", json={"name": "Nada", "pokemon_ids": [25]}, headers=auth_headers
    )

    assert response.status_code == 404


def test_cannot_delete_pokedex_entry_used_in_team(client, auth_headers, pokedex):
    """Integración con la Pokédex: un Pokémon que está en un equipo no se puede borrar (409)."""
    team_id = create_team(client, auth_headers, [25, 1]).json()["id"]

    response = client.delete(f"/api/v1/pokedex/{pokedex[25].id}", headers=auth_headers)

    assert response.status_code == 409
    team = client.get(TEAMS_URL, headers=auth_headers).json()[0]
    assert team["id"] == team_id
    assert team["pokemon_ids"] == [25, 1]


# --- Autenticación ---


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("post", TEAMS_URL),
        ("get", TEAMS_URL),
        ("put", f"{TEAMS_URL}/1"),
        ("get", f"{TEAMS_URL}/1/export"),
    ],
)
def test_teams_require_token(client, method, url):
    """Todos los endpoints de equipos exigen token → 401 sin él."""
    body = {"name": "x", "pokemon_ids": [1]}
    kwargs = {"json": body} if method in ("post", "put") else {}

    response = getattr(client, method)(url, **kwargs)

    assert response.status_code == 401


# --- GET /teams/{team_id}/export ---


def test_export_team_pdf(client, auth_headers, pokedex, fake_pokeapi):
    """Descarga un PDF del equipo y pide a PokeAPI (simulada) cada miembro, en orden."""
    team_id = create_team(client, auth_headers, [143, 25, 1, 4, 7, 133]).json()["id"]

    response = client.get(f"{TEAMS_URL}/{team_id}/export", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == f'attachment; filename="equipo-{team_id}.pdf"'
    assert response.content.startswith(b"%PDF")
    assert sorted(fake_pokeapi.calls) == [1, 4, 7, 25, 133, 143]


def test_export_other_user_team_returns_404(client, auth_headers, other_headers, session, other_user, fake_pokeapi):
    """No se puede exportar el equipo de otro usuario; ni siquiera se llama a PokeAPI."""
    add_entry(session, other_user, 150)
    team_id = create_team(client, other_headers, [150]).json()["id"]

    response = client.get(f"{TEAMS_URL}/{team_id}/export", headers=auth_headers)

    assert response.status_code == 404
    assert fake_pokeapi.calls == []


def test_export_team_pokeapi_error(client, auth_headers, pokedex):
    """Si PokeAPI falla, el export devuelve el error del servicio (aquí 504)."""
    team_id = create_team(client, auth_headers, [25]).json()["id"]
    fake = FakePokeAPI(error=HTTPException(status_code=504, detail="PokeAPI no responde"))
    app.dependency_overrides[get_pokeapi_service] = lambda: fake
    try:
        response = client.get(f"{TEAMS_URL}/{team_id}/export", headers=auth_headers)
    finally:
        app.dependency_overrides.pop(get_pokeapi_service, None)

    assert response.status_code == 504


# --- Rate limiting ---


@pytest.fixture
def limiter_on(monkeypatch):
    """Activa el limiter aunque el `.env` lo apague (los contadores ya se reinician en cada test)."""
    monkeypatch.setattr(limiter, "enabled", True)


def test_teams_rate_limit_authenticated(client, auth_headers, limiter_on):
    """Los endpoints de base de datos admiten 100 peticiones/minuto; la 101.ª → 429."""
    statuses = [client.get(TEAMS_URL, headers=auth_headers).status_code for _ in range(101)]

    assert statuses[:100] == [200] * 100
    assert statuses[100] == 429


def test_export_rate_limit_pokeapi(client, auth_headers, pokedex, fake_pokeapi, limiter_on):
    """El export llama a PokeAPI: admite 30 peticiones/minuto; la 31.ª → 429."""
    team_id = create_team(client, auth_headers, [25]).json()["id"]
    url = f"{TEAMS_URL}/{team_id}/export"

    statuses = [client.get(url, headers=auth_headers).status_code for _ in range(31)]

    assert statuses[:30] == [200] * 30
    assert statuses[30] == 429


# --- Funciones auxiliares ---


def test_combine_stats_sum_and_average():
    """Suma y media de cada stat y el total global del equipo."""
    combined = combine_stats([fake_pokemon(1), fake_pokemon(25)])

    assert combined["hp"] == {"total": 260, "average": 130.0}
    assert combined["speed"] == {"total": 120, "average": 60.0}
    # Por Pokémon: hp + 50 + 40 + 30 + 20 + 60 → 210 + 450 = 660 en total.
    assert combined["total"] == {"total": 660, "average": 330.0}


def test_combine_stats_missing_stat_counts_as_zero():
    """Una stat ausente cuenta como 0 y un equipo vacío no divide entre cero."""
    assert combine_stats([{"stats": {"hp": 45}}])["attack"] == {"total": 0, "average": 0.0}
    assert combine_stats([])["total"] == {"total": 0, "average": 0.0}


def test_draw_pokemon_card_with_description_and_sprite():
    """La ficha admite descripción larga, sprite en bytes y nombres que no caben."""
    image = io.BytesIO()
    Image.new("RGB", (8, 8), "yellow").save(image, format="PNG")
    pokemon = fake_pokemon(25) | {"name": "pikachu-con-un-nombre-larguisimo-que-no-cabe"}
    buffer = io.BytesIO()
    canvas = Canvas(buffer)

    draw_pokemon_card(
        canvas,
        pokemon,
        40,
        40,
        250,
        200,
        description="Cuando se enfada, descarga la electricidad de sus mejillas. " * 20,
        sprite=image.getvalue(),
        subtitle="Posición 1",
    )
    canvas.save()

    assert buffer.getvalue().startswith(b"%PDF")
