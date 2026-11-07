"""Tests de los modelos y de las reglas de la base de datos."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.models import PokedexEntry, Team, TeamMember, User


def _crear_usuario_con_pikachu(session):
    """Crea un usuario con Pikachu en su Pokédex y devuelve ambos."""
    user = User(username="ash", email="ash@poke.com", hashed_password="hash")
    session.add(user)
    session.commit()
    entry = PokedexEntry(
        owner_id=user.id, pokemon_id=25, pokemon_name="pikachu", pokemon_sprite="http://img/25.png"
    )
    session.add(entry)
    session.commit()
    return user, entry


def test_usuario_y_entrada_relacionados(session):
    """Una entrada de la Pokédex queda enlazada con su dueño."""
    user, entry = _crear_usuario_con_pikachu(session)
    assert user.pokedex_entries == [entry]
    assert entry.owner.username == "ash"
    assert entry.created_at is not None


def test_borrar_equipo_borra_miembros_pero_no_la_pokedex(session):
    """Al borrar un equipo desaparecen sus miembros, pero la entrada de la Pokédex se conserva."""
    user, entry = _crear_usuario_con_pikachu(session)
    team = Team(trainer_id=user.id, name="Elite")
    team.members.append(TeamMember(pokedex_entry_id=entry.id, position=1))
    session.add(team)
    session.commit()
    assert team.members[0].pokedex_entry.pokemon_name == "pikachu"

    session.delete(team)
    session.commit()

    assert session.exec(select(TeamMember)).all() == []
    assert session.exec(select(PokedexEntry)).all() == [entry]


def test_clave_foranea_inexistente_falla(session):
    """No se puede crear una entrada para un usuario que no existe (claves foráneas activas)."""
    session.add(
        PokedexEntry(owner_id=999, pokemon_id=1, pokemon_name="bulbasaur", pokemon_sprite="x")
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_username_unico(session):
    """Dos usuarios no pueden tener el mismo username."""
    session.add(User(username="ash", email="a@poke.com", hashed_password="h"))
    session.commit()
    session.add(User(username="ash", email="b@poke.com", hashed_password="h"))
    with pytest.raises(IntegrityError):
        session.commit()
