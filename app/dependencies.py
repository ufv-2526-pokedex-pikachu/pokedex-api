"""Dependencias compartidas por los routers: usuario autenticado a partir del JWT."""

import logging

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlmodel import Session

from app.auth import ACCESS, decode_token
from app.database import get_session
from app.models import User

logger = logging.getLogger(__name__)

# tokenUrl hace que el botón "Authorize" de /docs sepa dónde hacer login.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme), session: Session = Depends(get_session)
) -> User:
    """Devuelve el usuario del token de acceso o responde 401.

    Falla si el token no es válido, ha caducado, no es de acceso o el usuario
    ya no existe o está inactivo. El token nunca se escribe en el log.
    """
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No se pudo validar el token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token, ACCESS)
    except JWTError as exc:
        logger.warning("Autenticación fallida: %s", exc)
        raise credentials_error from exc

    user = session.get(User, payload.get("user_id"))
    if user is None or not user.is_active:
        logger.warning("Autenticación fallida: usuario %s inexistente o inactivo", payload.get("sub"))
        raise credentials_error
    return user
