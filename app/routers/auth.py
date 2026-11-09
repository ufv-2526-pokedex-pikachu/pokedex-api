"""Endpoints de autenticación: registro, login, refresh y usuario actual."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, or_, select

from app.auth import (
    REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.database import get_session
from app.dependencies import get_current_user
from app.limiter import LIMIT_AUTHENTICATED, LIMIT_LOGIN, LIMIT_REGISTER, limiter
from app.models import RefreshRequest, Token, User, UserCreate, UserRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Hash de relleno: si el usuario no existe se compara igual, para que el tiempo de
# respuesta no delate qué usernames están registrados.
_DUMMY_HASH = hash_password("relleno-para-igualar-tiempos")


def _tokens(user: User) -> Token:
    """Par de tokens (acceso + refresh) para el usuario."""
    return Token(
        access_token=create_access_token(user), refresh_token=create_refresh_token(user)
    )


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar un usuario",
    responses={409: {"description": "El username o el email ya están registrados"}},
)
@limiter.limit(LIMIT_REGISTER)
def register(
    request: Request, data: UserCreate, session: Session = Depends(get_session)
) -> User:
    """Crea el usuario con la contraseña hasheada. 409 si el username o el email ya existen."""
    duplicate = session.exec(
        select(User).where(or_(User.username == data.username, User.email == data.email))
    ).first()
    if duplicate is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "El username o el email ya están registrados")

    user = User(
        username=data.username, email=data.email, hashed_password=hash_password(data.password)
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError as exc:  # dos registros iguales a la vez
        session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "El username o el email ya están registrados"
        ) from exc
    session.refresh(user)
    logger.info("Usuario registrado: %s", user.username)
    return user


@router.post(
    "/login",
    response_model=Token,
    summary="Iniciar sesión",
    responses={401: {"description": "Credenciales incorrectas"}},
)
@limiter.limit(LIMIT_LOGIN)
def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_session)
) -> Token:
    """Devuelve un token de acceso (24 h) y un refresh token (7 días).

    Recibe `username` y `password` como formulario, igual que el botón Authorize de /docs.
    """
    user = session.exec(select(User).where(User.username == form.username)).first()
    password_ok = verify_password(form.password, user.hashed_password if user else _DUMMY_HASH)
    if user is None or not password_ok or not user.is_active:
        logger.warning("Login fallido para el username %r", form.username)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Usuario o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _tokens(user)


@router.post(
    "/refresh",
    response_model=Token,
    summary="Renovar los tokens con un refresh token",
    responses={401: {"description": "Refresh token no válido o caducado"}},
)
# Público y emite tokens, como el login: mismo límite.
@limiter.limit(LIMIT_LOGIN)
def refresh(
    request: Request, data: RefreshRequest, session: Session = Depends(get_session)
) -> Token:
    """Cambia un refresh token válido por un par de tokens nuevo (bonus del enunciado)."""
    error = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Refresh token no válido o caducado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(data.refresh_token, REFRESH)
    except JWTError as exc:
        logger.warning("Refresh fallido: %s", exc)
        raise error from exc
    user = session.get(User, payload.get("user_id"))
    if user is None or not user.is_active:
        raise error
    return _tokens(user)


@router.get("/me", response_model=UserRead, summary="Ver mi usuario")
@limiter.limit(LIMIT_AUTHENTICATED)
def me(request: Request, current_user: User = Depends(get_current_user)) -> User:
    """Devuelve el usuario dueño del token."""
    return current_user
