"""Tests de autenticación: registro, login, refresh, usuario actual y tokens."""

from datetime import timedelta

import pytest

from app.auth import ACCESS, _create_token, create_refresh_token, decode_token
from app.limiter import limiter
from app.models import User
from tests.conftest import TEST_PASSWORD

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
ME = "/api/v1/auth/me"

NEW_USER = {"username": "misty", "email": "misty@poke.com", "password": "Starmie123"}


def _login(client, username="ash", password=TEST_PASSWORD):
    return client.post(LOGIN, data={"username": username, "password": password})


# --- Tests del enunciado ---


def test_register_user_success(client, session):
    """Registro exitoso de usuario"""
    response = client.post(REGISTER, json=NEW_USER)
    assert response.status_code == 201
    body = response.json()
    assert body["username"] == "misty"
    assert body["email"] == "misty@poke.com"
    assert "password" not in body and "hashed_password" not in body
    # La contraseña se guarda hasheada, nunca en claro.
    stored = session.get(User, body["id"])
    assert stored.hashed_password != NEW_USER["password"]
    assert stored.hashed_password.startswith("$2b$")


def test_register_duplicate_username(client, test_user):
    """Error al registrar username duplicado"""
    response = client.post(REGISTER, json={**NEW_USER, "username": "ash"})
    assert response.status_code == 409


def test_login_success(client, test_user):
    """Login exitoso retorna JWT válido"""
    response = _login(client)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    claims = decode_token(body["access_token"], ACCESS)
    assert claims["sub"] == "ash"
    assert claims["user_id"] == test_user.id
    assert claims["exp"] - claims["iat"] == 24 * 60 * 60


def test_login_invalid_credentials(client, test_user):
    """Login con credenciales incorrectas retorna 401"""
    wrong_password = _login(client, password="Incorrecta123")
    unknown_user = _login(client, username="nadie")
    assert wrong_password.status_code == unknown_user.status_code == 401
    # Mismo mensaje en los dos casos: no se revela si el usuario existe.
    assert wrong_password.json() == unknown_user.json()


def test_access_protected_endpoint_without_token(client):
    """Acceso sin token retorna 401"""
    response = client.get(ME)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


# --- Validaciones del registro ---


def test_register_duplicate_email(client, test_user):
    """Un email ya registrado también da 409."""
    response = client.post(REGISTER, json={**NEW_USER, "email": "ash@poke.com"})
    assert response.status_code == 409


@pytest.mark.parametrize(
    "password", ["Corta1", "sinmayuscula1", "SinNumeroAqui"], ids=["corta", "mayus", "numero"]
)
def test_register_weak_password_returns_422(client, password):
    """La contraseña necesita 8 caracteres, 1 mayúscula y 1 número."""
    response = client.post(REGISTER, json={**NEW_USER, "password": password})
    assert response.status_code == 422


def test_register_password_over_72_bytes_returns_422(client):
    """bcrypt solo usa 72 bytes: se rechaza lo que pase, contando bytes y no caracteres."""
    password = "Pikachu1" + "ñ" * 33  # 41 caracteres, pero 74 bytes
    response = client.post(REGISTER, json={**NEW_USER, "password": password})
    assert response.status_code == 422


@pytest.mark.parametrize("email",["sin-arroba.com", "a@b", "con espacio@poke.com"])
def test_register_invalid_email_returns_422(client, email):
    """El email se valida con regex."""
    response = client.post(REGISTER, json={**NEW_USER, "email": email})
    assert response.status_code == 422


@pytest.mark.parametrize("username", ["ab", "x" * 51])
def test_register_invalid_username_length_returns_422(client, username):
    """El username tiene entre 3 y 50 caracteres."""
    response = client.post(REGISTER, json={**NEW_USER, "username": username})
    assert response.status_code == 422


# --- Usuario actual y tokens ---


def test_me_with_token_returns_user(client, auth_headers):
    """Con un token válido, /me devuelve el usuario."""
    response = client.get(ME, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["username"] == "ash"


def test_expired_token_returns_401(client, test_user):
    """Un token caducado no sirve."""
    expired = _create_token(test_user, ACCESS, timedelta(seconds=-1))
    response = client.get(ME, headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401


def test_invalid_token_returns_401(client):
    """Un token mal firmado o inventado no sirve."""
    response = client.get(ME, headers={"Authorization": "Bearer no-es-un-jwt"})
    assert response.status_code == 401


def test_refresh_token_cannot_be_used_as_access_token(client, test_user):
    """Un refresh token no abre endpoints protegidos."""
    refresh = create_refresh_token(test_user)
    response = client.get(ME, headers={"Authorization": f"Bearer {refresh}"})
    assert response.status_code == 401


def test_inactive_user_cannot_use_token_or_login(client, session, test_user, auth_headers):
    """Un usuario desactivado ya no puede entrar."""
    test_user.is_active = False
    session.add(test_user)
    session.commit()
    assert client.get(ME, headers=auth_headers).status_code == 401
    assert _login(client).status_code == 401


# --- Refresh token (bonus) ---


def test_refresh_returns_new_tokens(client, test_user):
    """Un refresh token válido devuelve un par de tokens nuevo."""
    refresh = _login(client).json()["refresh_token"]
    response = client.post(REFRESH, json={"refresh_token": refresh})
    assert response.status_code == 200
    new_access = response.json()["access_token"]
    assert client.get(ME, headers={"Authorization": f"Bearer {new_access}"}).status_code == 200


def test_refresh_rejects_access_token(client, test_user):
    """Un token de acceso no sirve como refresh token."""
    access = _login(client).json()["access_token"]
    response = client.post(REFRESH, json={"refresh_token": access})
    assert response.status_code == 401


def test_refresh_rejects_deleted_user(client, session, test_user):
    """Si el usuario ya no existe, el refresh falla."""
    refresh = create_refresh_token(test_user)
    session.delete(test_user)
    session.commit()
    response = client.post(REFRESH, json={"refresh_token": refresh})
    assert response.status_code == 401


def test_refresh_token_lasts_seven_days(test_user):
    """El refresh token caduca a los 7 días."""
    claims = decode_token(create_refresh_token(test_user), "refresh")
    assert claims["exp"] - claims["iat"] == 7 * 24 * 60 * 60


# --- Rate limiting de los endpoints de auth ---


@pytest.fixture
def limited(monkeypatch):
    """Activa el limiter aunque el `.env` lo apague."""
    monkeypatch.setattr(limiter, "enabled", True)


def test_login_rate_limit_returns_429_on_11th_request(client, test_user, limited):
    """Rate limit del enunciado: 10 logins por minuto; el 11.º devuelve 429."""
    codes = [_login(client).status_code for _ in range(11)]
    assert codes == [200] * 10 + [429]


def test_register_rate_limit_returns_429_on_6th_request(client, limited):
    """5 registros por hora e IP; el 6.º devuelve 429 aunque los datos sean válidos."""
    codes = [
        client.post(
            REGISTER, json={**NEW_USER, "username": f"user{i}", "email": f"u{i}@poke.com"}
        ).status_code
        for i in range(6)
    ]
    assert codes == [201] * 5 + [429]
