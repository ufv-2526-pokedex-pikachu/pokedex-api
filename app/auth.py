"""Seguridad: hash de contraseñas con bcrypt y creación/lectura de tokens JWT."""

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings
from app.models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ACCESS = "access"
REFRESH = "refresh"


def hash_password(password: str) -> str:
    """Devuelve el hash bcrypt de la contraseña (la contraseña nunca se guarda en claro)."""
    return pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Comprueba si la contraseña coincide con su hash."""
    return pwd_context.verify(password, hashed_password)


def _create_token(user: User, token_type: str, expires_delta: timedelta) -> str:
    """Firma un JWT con los claims del enunciado (`sub`, `user_id`, `exp`, `iat`) y su tipo."""
    now = datetime.now(timezone.utc)
    claims = {
        "sub": user.username,
        "user_id": user.id,
        "iat": now,
        "exp": now + expires_delta,
        "type": token_type,
    }
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(user: User) -> str:
    """Token de acceso: válido `ACCESS_TOKEN_EXPIRE_MINUTES` (24 h por defecto)."""
    return _create_token(
        user, ACCESS, timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )


def create_refresh_token(user: User) -> str:
    """Refresh token (bonus): válido `REFRESH_TOKEN_EXPIRE_DAYS` (7 días por defecto)."""
    return _create_token(user, REFRESH, timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS))


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    """Verifica firma, caducidad y tipo del token y devuelve sus claims.

    Lanza `JWTError` si algo falla. Comprobar el tipo impide usar un refresh token
    como token de acceso y al revés.
    """
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    if payload.get("type") != expected_type:
        raise JWTError(f"Se esperaba un token de tipo {expected_type}")
    return payload
