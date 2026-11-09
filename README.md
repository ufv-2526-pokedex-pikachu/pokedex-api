# Pokédex API

![CI](https://github.com/ufv-2526-pokedex-pikachu/pokedex-api/actions/workflows/ci.yml/badge.svg)

## Descripción
API REST de Pokédex personal hecha con FastAPI. Consume la [PokeAPI](https://pokeapi.co/) y permite a cada usuario gestionar su Pokédex, sus estadísticas y sus equipos de batalla.

## Equipo
| Miembro | Usuario Git | Partes desarrolladas |
|---|---|---|
| Adrián Zúñiga García | [@adrianzuga](https://github.com/adrianzuga) | Esqueleto inicial y estructura base (config, base de datos, modelos, `/health` y sus tests); integración continua; autenticación JWT con refresh token y sus tests; Pokédex personal (CRUD, filtros, ordenación, paginación y exportación CSV/PDF) |
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

### Pokédex personal (`/api/v1/pokedex`)
Todos requieren token (Bearer). Cada usuario solo ve y modifica sus entradas.

| Método | Ruta | Descripción | Respuestas |
|---|---|---|---|
| POST | _(raíz)_ | Añade un Pokémon (JSON: `pokemon_id`, y opcionales `nickname`, `is_captured`, `capture_date`, `notes`, `favorite`). Comprueba en PokeAPI que existe y guarda su nombre, sprite y tipos | 201, 404 no existe en PokeAPI, 409 ya está, 422 |
| GET | _(raíz)_ | Lista mi Pokédex. Filtros `captured`, `favorite`; orden `sort=pokemon_id\|capture_date\|pokemon_name` y `order=asc\|desc`; paginación `limit` (1-100, 20 por defecto) y `offset` | 200, 422 |
| PATCH | `/{entry_id}` | Cambia solo los campos enviados (`is_captured`, `capture_date`, `nickname`, `notes`, `favorite`) | 200, 404, 422 |
| DELETE | `/{entry_id}` | Borra la entrada | 204, 404, 409 si está en un equipo |
| GET | `/export` | Descarga la Pokédex: `format=csv\|pdf` (csv por defecto), filtros `captured` y `favorite` | 200 archivo, 422 |

## Decisiones de Diseño

### Rate limiting
Se aplica con `slowapi`, con una instancia única en `app/limiter.py`. Se cuenta por IP y por endpoint.

| Tipo de endpoint | Límite | Constante |
|---|---|---|
| Registro | 5/hora | `LIMIT_REGISTER` |
| Login y refresh | 10/minuto | `LIMIT_LOGIN` |
| Llaman a PokeAPI | 30/minuto | `LIMIT_POKEAPI` |
| Resto de autenticados | 100/minuto | `LIMIT_AUTHENTICATED` |

- **¿Por qué límites distintos?** Porque cada endpoint tiene un riesgo y un coste distintos. Registrarse es algo poco frecuente y se puede usar para crear cuentas falsas. El login es el objetivo de los ataques de fuerza bruta. Los endpoints que llaman a PokeAPI consumen un servicio externo y gratuito. El resto solo trabaja con nuestra base de datos y lo usan usuarios ya identificados, así que admite más uso.
- **¿Cómo protege esto la API?** Hace mucho más lentos los ataques de fuerza bruta contra contraseñas, limita la creación masiva de cuentas, reduce el riesgo de que un solo cliente sature el servidor y evita que nuestra API abuse de PokeAPI.
- **¿Qué pasa si un usuario excede el límite?** La API responde `429 Too Many Requests` con un `detail` y la cabecera `Retry-After`, y registra el exceso en el log. No se ejecuta el endpoint. El cliente puede volver a intentarlo cuando se renueve la ventana del límite.
- **Limitaciones conocidas:** los contadores se guardan en memoria, así que se pierden al reiniciar y no se comparten entre varios procesos. Detrás de un proxy, todas las peticiones parecerían venir de la misma IP. Para producción habría que usar un almacenamiento compartido, como Redis, y leer la IP real del cliente.

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
- **CORS:** solo se admiten los orígenes de `CORS_ORIGINS` y los métodos y cabeceras que usa la API, nunca `*`. Los navegadores bloquean las peticiones desde cualquier otro origen; CORS no protege frente a clientes que no son navegadores, como `curl`, y para eso están la autenticación y el rate limiting.
- **Tests:** el enunciado pide `tests/test_api.py` con al menos 15 tests, pero su estructura de carpetas reparte los tests en `test_auth.py`, `test_pokedex.py` y `test_pokeapi_service.py`. Seguimos la estructura: cada bloque tiene su fichero y los 15 se cuentan entre todos. `tests/test_api.py` reúne los tests transversales y `tests/test_models.py` prueba las reglas de la base de datos.
- **Schemas:** `UserCreate`, `UserRead`, `Token` y `RefreshRequest` están en `app/models.py`, junto a las tablas, porque la estructura del enunciado no tiene un fichero de schemas.
- **Login por formulario** (`OAuth2PasswordRequestForm`) y no por JSON, para que funcione el botón *Authorize* de Swagger.
- **Usuario o email duplicado → 409 Conflict:** el recurso ya existe; no es un error de formato (422).
- **Pokédex:**
  - **Tipos del Pokémon:** se guardan en `PokedexEntry.pokemon_types` como texto separado por comas (`"fire,flying"`), copiados de PokeAPI al añadirlo. Así las estadísticas no llaman a PokeAPI. Es un campo añadido al modelo del enunciado.
  - **Sin duplicados:** además de comprobarlo antes de insertar (409), la tabla tiene una restricción única `(owner_id, pokemon_id)`, que lo garantiza aunque lleguen dos peticiones a la vez.
  - **Entradas de otro usuario → 404, no 403:** así no se revela que existen.
  - **Fecha de captura:** si se marca como capturado sin fecha, se pone la actual; si se desmarca, se borra la fecha.
  - **Borrar un Pokémon que está en un equipo → 409:** no se deja el equipo con un hueco; primero hay que sacarlo del equipo.
  - **Exportación:** por defecto incluye toda la Pokédex del usuario; `captured` y `favorite` la filtran. El enunciado dice "todos los Pokémon capturados" y también "opción de filtrar por capturados", así que el filtro es opcional. En el CSV, los valores que empiezan por `=`, `+`, `-` o `@` llevan un `'` delante para que Excel no los ejecute como fórmulas (inyección CSV).
  - **Rate limiting:** el alta llama a PokeAPI (30/min); el resto solo usa la base de datos (100/min).

## Decisiones de Seguridad
- **Contraseñas:** se guardan solo como hash bcrypt (`passlib`), nunca en claro. La API nunca devuelve la contraseña ni su hash.
- **Política de contraseñas:** 8 caracteres o más, al menos 1 mayúscula y 1 número (422 si no se cumple). El email se valida con regex.
- **JWT:** firmados con HS256 y la `SECRET_KEY` del entorno (nunca en el código). Llevan `sub`, `user_id`, `iat`, `exp` y `type`. El token de acceso dura 24 h y el refresh token 7 días.
- **Tipos de token:** un refresh token no abre endpoints protegidos y un token de acceso no sirve para `/refresh`.
- **Sin revocación de tokens:** los JWT no se guardan en la base de datos, así que un refresh token ya usado sigue valiendo hasta que caduca (7 días), igual que un token de acceso hasta sus 24 h. Es el precio de no consultar la base de datos en cada petición. Desactivar al usuario (`is_active=False`) invalida todos sus tokens al momento. La revocación queda como mejora futura.
- **Longitud de la contraseña:** máximo 72 bytes, porque bcrypt ignora el resto. Se cuentan bytes, no caracteres.
- **Errores genéricos:** el login responde siempre "Usuario o contraseña incorrectos" y tarda lo mismo exista o no el usuario, para no revelar qué usernames están registrados.
- **Usuarios inactivos** (`is_active=False`): no pueden hacer login y sus tokens dejan de valer.
- **Registro de fallos:** cada fallo de autenticación se apunta en el log, sin escribir nunca el token ni la contraseña.
- **HTTPS:** en producción la API debe servirse siempre por HTTPS, porque los tokens viajan en la cabecera `Authorization`.

## Mejoras Futuras
- **Revocación de refresh tokens:** guardar un identificador (`jti`) de cada refresh token y anular el anterior al renovarlo (rotación), o mantener una lista de tokens revocados.
