# Pokédex API

![CI](https://github.com/ufv-2526-pokedex-pikachu/pokedex-api/actions/workflows/ci.yml/badge.svg)

## Descripción
API REST de Pokédex personal hecha con FastAPI. Consume la [PokeAPI](https://pokeapi.co/) y permite a cada usuario gestionar su Pokédex, sus estadísticas y sus equipos de batalla.

## Equipo
| Miembro | Usuario Git | Partes desarrolladas |
|---|---|---|
| Adrián Zúñiga García | [@adrianzuga](https://github.com/adrianzuga) | Esqueleto inicial y estructura base (config, base de datos, modelos, `/health` y sus tests) |
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
_Pendiente._

## Decisiones de Diseño

### Rate limiting
Se aplica con `slowapi`, con una instancia única en `app/limiter.py`. Se cuenta por IP y por endpoint.

| Tipo de endpoint | Límite | Constante |
|---|---|---|
| Registro | 5/hora | `LIMIT_REGISTER` |
| Login | 10/minuto | `LIMIT_LOGIN` |
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
- **Tests:** además de los ficheros de la estructura del enunciado, `tests/test_api.py` reúne tests transversales y `tests/test_models.py` prueba las reglas de la base de datos.

## Decisiones de Seguridad
_Pendiente._

## Mejoras Futuras
_Pendiente._
