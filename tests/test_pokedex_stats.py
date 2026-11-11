"""Tests de las estadísticas de la Pokédex (`GET /api/v1/pokedex/stats`)."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.auth import hash_password
from app.limiter import limiter
from app.models import PokedexEntry, User
from app.services.stats_service import compute_pokedex_stats

STATS_URL = "/api/v1/pokedex/stats"
TODAY = date(2026, 11, 9)


def entry(
    pokemon_id: int = 1,
    types: str = "normal",
    captured: bool = False,
    favorite: bool = False,
    capture_date: datetime | None = None,
    owner_id: int = 1,
) -> PokedexEntry:
    """Entrada de la Pokédex en memoria (sin guardar) para probar el cálculo."""
    return PokedexEntry(
        owner_id=owner_id,
        pokemon_id=pokemon_id,
        pokemon_name=f"pokemon-{pokemon_id}",
        pokemon_sprite="https://img.test/x.png",
        pokemon_types=types,
        is_captured=captured,
        favorite=favorite,
        capture_date=capture_date,
    )


def captured_on(day: date, pokemon_id: int = 1, hour: int = 12) -> PokedexEntry:
    """Entrada capturada ese día (en UTC)."""
    moment = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
    return entry(pokemon_id=pokemon_id, captured=True, capture_date=moment)


def days_ago(n: int) -> date:
    return TODAY - timedelta(days=n)


# --- Cálculo (función pura, con fecha fija) ---


def test_empty_pokedex_is_all_zero():
    """Pokédex vacía: todo a 0, sin dividir entre cero, y sin tipo más común."""
    assert compute_pokedex_stats([], TODAY) == {
        "total_pokemon": 0,
        "captured": 0,
        "favorites": 0,
        "completion_percentage": 0.0,
        "most_common_type": None,
        "capture_streak_days": 0,
    }


def test_counts_and_percentage_with_one_decimal():
    """3 capturados de 7 → 42,9 %; los favoritos se cuentan aparte."""
    entries = [
        entry(1, captured=True, favorite=True),
        entry(2, captured=True),
        entry(3, captured=True),
        entry(4, favorite=True),
        entry(5),
        entry(6),
        entry(7),
    ]

    stats = compute_pokedex_stats(entries, TODAY)

    assert stats["total_pokemon"] == 7
    assert stats["captured"] == 3
    assert stats["favorites"] == 2
    assert stats["completion_percentage"] == 42.9


def test_percentage_example_of_the_statement():
    """El ejemplo del enunciado: 89 capturados de 150 → 59,3 %."""
    entries = [entry(i, captured=i <= 89) for i in range(1, 151)]

    assert compute_pokedex_stats(entries, TODAY)["completion_percentage"] == 59.3


def test_most_common_type_counts_every_type_of_each_pokemon():
    """"fire,flying" cuenta fire y flying: aquí flying aparece 3 veces y water 2."""
    entries = [
        entry(1, types="fire,flying"),
        entry(2, types="water"),
        entry(3, types="water,flying"),
        entry(4, types="normal,flying"),
    ]

    assert compute_pokedex_stats(entries, TODAY)["most_common_type"] == "flying"


def test_most_common_type_tie_is_alphabetical():
    """Empate → el primero en orden alfabético, para que el resultado sea estable."""
    entries = [entry(1, types="water"), entry(2, types="fire")]

    assert compute_pokedex_stats(entries, TODAY)["most_common_type"] == "fire"


def test_most_common_type_ignores_entries_without_types():
    """Las entradas sin tipos guardados no cuentan; si ninguna tiene, es None."""
    assert compute_pokedex_stats([entry(1, types="")], TODAY)["most_common_type"] is None
    entries = [entry(1, types=""), entry(2, types="grass,poison")]
    assert compute_pokedex_stats(entries, TODAY)["most_common_type"] == "grass"


def test_streak_counts_consecutive_days_until_today():
    """Capturas hoy, ayer y anteayer → racha de 3; varias el mismo día cuentan una vez."""
    entries = [
        captured_on(days_ago(0), 1),
        captured_on(days_ago(0), 2),
        captured_on(days_ago(1), 3),
        captured_on(days_ago(2), 4),
        captured_on(days_ago(5), 5),  # tras un hueco: no cuenta
    ]

    assert compute_pokedex_stats(entries, TODAY)["capture_streak_days"] == 3


def test_streak_ending_yesterday_is_still_alive():
    """Si hoy aún no has capturado, la racha que termina ayer sigue contando."""
    entries = [captured_on(days_ago(1)), captured_on(days_ago(2))]

    assert compute_pokedex_stats(entries, TODAY)["capture_streak_days"] == 2


def test_streak_is_zero_if_last_capture_was_before_yesterday():
    """Última captura hace 2 días → la racha se ha roto."""
    entries = [captured_on(days_ago(2)), captured_on(days_ago(3))]

    assert compute_pokedex_stats(entries, TODAY)["capture_streak_days"] == 0


def test_streak_ignores_uncaptured_and_entries_without_date():
    """Solo cuentan las entradas capturadas y con fecha de captura."""
    entries = [
        entry(1, captured=True, capture_date=None),
        entry(2, captured=False, capture_date=datetime(2026, 11, 9, tzinfo=timezone.utc)),
    ]

    assert compute_pokedex_stats(entries, TODAY)["capture_streak_days"] == 0


def test_streak_uses_utc_days_and_accepts_naive_dates():
    """Las fechas se pasan a UTC: 00:30 en Madrid (UTC+1) es todavía ayer en UTC.

    Una fecha sin zona horaria se trata como UTC.
    """
    madrid = timezone(timedelta(hours=1))
    entries = [
        entry(1, captured=True, capture_date=datetime(2026, 11, 9, 0, 30, tzinfo=madrid)),
        entry(2, captured=True, capture_date=datetime(2026, 11, 7, 10, 0)),
    ]

    # 8 nov (UTC) y 7 nov: racha de 2 que termina ayer.
    assert compute_pokedex_stats(entries, TODAY)["capture_streak_days"] == 2


# --- Endpoint ---


def add_entries(session, user: User, entries: list[PokedexEntry]) -> None:
    """Guarda las entradas en la Pokédex de `user`."""
    for item in entries:
        item.owner_id = user.id
        session.add(item)
    session.commit()


@pytest.fixture
def other_user(session):
    user = User(username="gary", email="gary@poke.com", hashed_password=hash_password("Eevee1234"))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_stats_route_is_not_taken_by_entry_id(client, auth_headers):
    """/pokedex/stats responde 200 (no lo captura ninguna ruta /pokedex/{entry_id})."""
    response = client.get(STATS_URL, headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "total_pokemon": 0,
        "captured": 0,
        "favorites": 0,
        "completion_percentage": 0.0,
        "most_common_type": None,
        "capture_streak_days": 0,
    }


def test_stats_endpoint_counts_only_my_entries(client, auth_headers, session, test_user, other_user):
    """Calcula con mis entradas y la fecha de hoy; las de otro usuario no cuentan."""
    now = datetime.now(timezone.utc)
    add_entries(session, test_user, [
        entry(6, types="fire,flying", captured=True, favorite=True, capture_date=now),
        entry(7, types="water", captured=True, capture_date=now - timedelta(days=1)),
        entry(9, types="water"),
    ])
    add_entries(session, other_user, [
        entry(25, types="electric", captured=True, favorite=True, capture_date=now),
        entry(26, types="electric", captured=True),
        entry(172, types="electric", captured=True),
    ])

    response = client.get(STATS_URL, headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "total_pokemon": 3,
        "captured": 2,
        "favorites": 1,
        "completion_percentage": 66.7,
        "most_common_type": "water",
        "capture_streak_days": 2,
    }


def test_stats_requires_token(client):
    """Sin token → 401."""
    assert client.get(STATS_URL).status_code == 401


def test_stats_rate_limit(client, auth_headers, monkeypatch):
    """Endpoint de base de datos: 100 peticiones/minuto; la 101.ª → 429."""
    monkeypatch.setattr(limiter, "enabled", True)

    statuses = [client.get(STATS_URL, headers=auth_headers).status_code for _ in range(101)]

    assert statuses[:100] == [200] * 100
    assert statuses[100] == 429
