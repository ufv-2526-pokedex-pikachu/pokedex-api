# Pokédex API

![CI](https://github.com/ufv-2526-pokedex-pikachu/pokedex-api/actions/workflows/ci.yml/badge.svg)

## Descripción
API REST de Pokédex personal hecha con FastAPI. Consume la [PokeAPI](https://pokeapi.co/) y permite a cada usuario gestionar su Pokédex, sus estadísticas y sus equipos de batalla.

## Equipo
| Miembro | Usuario Git | Partes desarrolladas |
|---|---|---|
| Adrián Zúñiga García | [@adrianzuga](https://github.com/adrianzuga) | Esqueleto inicial y estructura base (config, base de datos, modelos, `/health` y sus tests); integración continua; autenticación JWT con refresh token y sus tests |
| Alejandro Fernández Pérez | [@aferperez3110-ux](https://github.com/aferperez3110-ux) | — |
| _(pendiente)_ | — | — |

## Instalación
Requisitos: Python 3.12, Git y [uv](https://docs.astral.sh/uv/) (recomendado).

```bash
git clone https://github.com/ufv-2526-pokedex-pikachu/pokedex-api.git
cd pokedex-api
uv venv --python 3.12 .venv
.venv\Scripts\activate          # Windows (en Linux/macOS: source .venv/bin/activate)
uv pip install -r requirements.txt
```

En Windows, si PowerShell no deja ejecutar `activate`, ejecuta una vez `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`.

## Configuración
Copia `.env.example` como `.env` y rellena los valores. El `.env` real nunca se sube al repositorio (está en `.gitignore`).

| Variable | Para qué sirve | Ejemplo |
|---|---|---|
| `SECRET_KEY` | Clave para firmar los JWT. **Obligatoria**: sin ella la app no arranca | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ALGORITHM` | Algoritmo de firma de los JWT | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Duración del token de acceso | `1440` (24 h) |
| `REFRESH_TOKEN_EXPIRE_DAYS` | Duración del refresh token | `7` |
| `DATABASE_URL` | Conexión a la base de datos | `sqlite:///./pokedex.db` |
| `CORS_ORIGINS` | Orígenes permitidos (lista JSON) | `["http://localhost:3000"]` |
| `POKEAPI_BASE_URL` | URL base de PokeAPI | `https://pokeapi.co/api/v2` |
| `POKEAPI_TIMEOUT` | Tiempo máximo de espera a PokeAPI (s) | `10` |
| `LOG_LEVEL` | Nivel de log | `INFO` |
| `RATE_LIMIT_ENABLED` | Activa o desactiva el rate limiting | `true` |

## Ejecución
```bash
uvicorn app.main:app --reload --reload-dir app
```
- API: http://127.0.0.1:8000
- Documentación Swagger: http://127.0.0.1:8000/docs
- Comprobación rápida: `GET /health` → `{"status": "ok"}`

`--reload-dir app` hace que solo se reinicie al cambiar el código, no al cambiar `.venv`.

## Testing
```bash
pytest --cov=app --cov-report=term-missing --cov-fail-under=80
```
Los tests usan una base de datos SQLite en memoria (nueva en cada test) y nunca usan la red: PokeAPI siempre va simulada. Si no existen las variables `SECRET_KEY` y `DATABASE_URL`, `tests/conftest.py` pone valores de prueba; el resto de variables se lee del `.env` si existe, así que un valor mal escrito en él (por ejemplo, `CORS_ORIGINS` que no sea una lista JSON) impide que arranquen.

La integración continua (`.github/workflows/ci.yml`) ejecuta estos mismos tests con cobertura en cada *push* y en cada *pull request* a `main` y `develop`. Si un test falla o la cobertura baja del 80 %, el flujo sale en rojo y el PR no se integra.

## Flujo de trabajo y despliegue
- `main`: versión estable y entregable. Solo recibe PRs desde `develop`.
- `develop`: rama de integración. Solo recibe PRs desde ramas `features/`.
- `features/<nombre>`: una rama por funcionalidad, integrada con PR revisado por otro miembro.
- `fix/<nombre>`: corrección de un error ya integrado en `develop`.

Tablero del proyecto: https://github.com/orgs/ufv-2526-pokedex-pikachu/projects/1 (una issue por rama; cada PR la cierra con `Closes #n`).

**Reglas de las ramas.** Nadie hace `push` directo a `main` ni a `develop`: todo entra por *pull request* con al menos una aprobación de otro miembro y la integración continua en verde. La organización usa el plan gratuito de GitHub, que no permite proteger ramas en repositorios privados (la API responde *"Upgrade to GitHub Pro or make this repository public to enable this feature"*), así que el equipo cumple esta regla por disciplina. El historial de *pull requests* lo demuestra.

## Endpoints

### Autenticación (`/api/v1/auth`)
| Método | Ruta | Auth | Descripción | Respuestas |
|---|---|---|---|---|
| POST | `/register` | No | Registra un usuario (JSON: `username`, `email`, `password`) | 201, 409 duplicado, 422 datos no válidos |
| POST | `/login` | No | Formulario `username` + `password`; devuelve `access_token` (24 h) y `refresh_token` (7 días) | 200, 401 |
| POST | `/refresh` | No | JSON `{"refresh_token": …}`; devuelve un par de tokens nuevo (bonus) | 200, 401 |
| GET | `/me` | Bearer | Datos del usuario del token | 200, 401 |

En `/docs`, pulsa **Authorize**, escribe tu usuario y contraseña y Swagger enviará el token en cada petición.

## Decisiones de Diseño

### Rate limiting
_Pendiente._

### Versionado
_Pendiente._

### Otras decisiones
- **Base de datos:** SQLite, sin servidor que instalar. Se cambia por PostgreSQL solo con cambiar `DATABASE_URL`.
- **Claves foráneas en SQLite:** se activan con `PRAGMA foreign_keys=ON` en cada conexión (`app/database.py`), porque SQLite las trae desactivadas.
- **Fechas:** `datetime.utcnow()` está obsoleto en Python 3.12; los modelos usan `datetime.now(timezone.utc)`.
- **Añadidos a los modelos del enunciado** (sin quitar nada): `Team.members` borra en cascada sus miembros al borrar el equipo (nunca las entradas de la Pokédex) y `TeamMember.pokedex_entry` enlaza cada miembro con su entrada.
- **Arranque:** las tablas se crean en el `lifespan` de FastAPI, porque `@app.on_event` está obsoleto.
- **`SECRET_KEY` sin valor por defecto:** la app no arranca sin ella, para no funcionar nunca con una clave insegura.
- **Versiones fijadas:** `bcrypt==4.0.1`, porque `passlib` 1.7.4 da avisos con `bcrypt` 4.1+ y errores con la 5.x.
- **Tests:** el enunciado pide `tests/test_api.py` con al menos 15 tests, pero su estructura de carpetas reparte los tests en `test_auth.py`, `test_pokedex.py` y `test_pokeapi_service.py`. Seguimos la estructura: cada bloque tiene su fichero y los 15 se cuentan entre todos. `tests/test_api.py` reúne los tests transversales y `tests/test_models.py` prueba las reglas de la base de datos.
- **Schemas:** `UserCreate`, `UserRead`, `Token` y `RefreshRequest` están en `app/models.py`, junto a las tablas, porque la estructura del enunciado no tiene un fichero de schemas.
- **Login por formulario** (`OAuth2PasswordRequestForm`) y no por JSON, para que funcione el botón *Authorize* de Swagger.
- **Usuario o email duplicado → 409 Conflict:** el recurso ya existe; no es un error de formato (422).

## Decisiones de Seguridad
- **Contraseñas:** se guardan solo como hash bcrypt (`passlib`), nunca en claro. La API nunca devuelve la contraseña ni su hash.
- **Política de contraseñas:** 8 caracteres o más, al menos 1 mayúscula y 1 número (422 si no se cumple). El email se valida con regex.
- **JWT:** firmados con HS256 y la `SECRET_KEY` del entorno (nunca en el código). Llevan `sub`, `user_id`, `iat`, `exp` y `type`. El token de acceso dura 24 h y el refresh token 7 días.
- **Tipos de token:** un refresh token no abre endpoints protegidos y un token de acceso no sirve para `/refresh`.
- **Errores genéricos:** el login responde siempre "Usuario o contraseña incorrectos" y tarda lo mismo exista o no el usuario, para no revelar qué usernames están registrados.
- **Usuarios inactivos** (`is_active=False`): no pueden hacer login y sus tokens dejan de valer.
- **Registro de fallos:** cada fallo de autenticación se apunta en el log, sin escribir nunca el token ni la contraseña.
- **HTTPS:** en producción la API debe servirse siempre por HTTPS, porque los tokens viajan en la cabecera `Authorization`.

## Mejoras Futuras
_Pendiente._
