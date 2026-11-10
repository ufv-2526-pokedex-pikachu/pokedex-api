# Consideraciones de Seguridad

Este documento explica cómo protege la Pokédex API a sus usuarios y a sí misma, y qué
limitaciones conocemos. Todo lo que se describe está en el código de `develop`; cuando algo
depende de un *pull request* todavía abierto, se indica con su número.

## Autenticación

**Implementación JWT** (`app/auth.py`, `app/dependencies.py`):
- Al hacer login (`POST /api/v1/auth/login`) la API devuelve un **token de acceso** y un
  **refresh token**, ambos JWT firmados con **HS256** y la `SECRET_KEY` del entorno.
- Claims: `sub` (username), `user_id`, `iat`, `exp` (los que pide el enunciado) y `type`
  (`access` o `refresh`).
- Cada endpoint protegido usa la dependencia `get_current_user`, que lee la cabecera
  `Authorization: Bearer <token>` y **verifica firma, caducidad y tipo**. Si algo falla, o
  el usuario ya no existe o está desactivado (`is_active=False`), responde **401** con
  `WWW-Authenticate: Bearer`.
- El tipo impide mezclar tokens: un refresh token no abre endpoints protegidos y un token
  de acceso no sirve para `/auth/refresh`.
- Los tokens no válidos, caducados o de otro tipo y los logins fallidos se registran en el
  log **sin escribir nunca el token ni la contraseña**. Una petición **sin** token la corta
  `OAuth2PasswordBearer` antes de llegar a `get_current_user`; ese 401 lo registra el
  middleware de peticiones (`app/logging_config.py`), con método, ruta, código y duración.

**Política de expiración de tokens:**
| Token | Duración | Variable |
|---|---|---|
| Acceso | 24 horas | `ACCESS_TOKEN_EXPIRE_MINUTES=1440` |
| Refresh (bonus) | 7 días | `REFRESH_TOKEN_EXPIRE_DAYS=7` |

- `POST /api/v1/auth/refresh` cambia un refresh token válido por un par nuevo.
- **No hay revocación**: los JWT no se guardan en la base de datos, así que un token sigue
  valiendo hasta que caduca aunque se haya renovado. Desactivar al usuario invalida todos sus
  tokens al momento. Ver «Vulnerabilidades conocidas».

## Contraseñas

**Algoritmo de hashing:** **bcrypt** (`passlib`, esquema `$2b$`, **coste 12**, es decir
2¹² iteraciones), con una *sal* aleatoria distinta para cada contraseña. La contraseña
nunca se guarda ni se devuelve en claro; tampoco su hash (`UserRead` no lo incluye).
`bcrypt` está fijado a la versión 4.0.1 porque `passlib` 1.7.4 falla con las 4.1+ y 5.x.

**Política de contraseñas** (validada en `UserCreate`, 422 si no se cumple):
- Mínimo **8 caracteres**, al menos **1 mayúscula** y **1 número** (lo que pide el enunciado).
- Máximo **72 bytes** (no caracteres: la «ñ» ocupa 2), porque bcrypt ignora lo que pasa de ahí
  y dos contraseñas distintas darían el mismo hash.
- El email se valida con una expresión regular y se guarda en minúsculas.

**Contra la enumeración de usuarios:** el login responde siempre «Usuario o contraseña
incorrectos» y, si el usuario no existe, compara igualmente con un hash de relleno, para que
el tiempo de respuesta no delate qué usernames están registrados.

## Rate Limiting

**Límites por endpoint** (`slowapi`, contados por IP y por endpoint, en `app/limiter.py`):
| Tipo de endpoint | Límite | Endpoints |
|---|---|---|
| Registro | 5/hora | `POST /auth/register` |
| Login y refresh | 10/minuto | `POST /auth/login`, `POST /auth/refresh` |
| Llaman a PokeAPI | 30/minuto | `POST /pokedex`, `GET /teams/{id}/export`; `GET /api/v2/pokedex`; búsqueda, detalle y `/card` de `/pokemon` (PR #25) |
| Resto de autenticados | 100/minuto | `/auth/me`, el resto de `/pokedex` (incluidos `/stats` y `/export`) y de `/teams` |

**Justificación:**
- **Registro**: es poco frecuente y se puede abusar de él para crear cuentas falsas en masa.
- **Login y refresh**: son el objetivo de los ataques de fuerza bruta contra contraseñas y
  emiten tokens. Con 10 intentos por minuto, probar un diccionario se vuelve muy lento.
- **PokeAPI**: es un servicio externo y gratuito; si no limitamos, un solo cliente podría
  hacer que nuestra API abusara de ella (y que nos bloqueara).
- **Resto**: solo trabajan con nuestra base de datos y los usan usuarios ya identificados,
  así que admiten más uso.

Al superar el límite, la API responde **429 Too Many Requests** con `Retry-After`, no ejecuta
el endpoint y registra el exceso en el log. Los límites se pueden desactivar con
`RATE_LIMIT_ENABLED=false` (solo para desarrollo).

**Limitaciones:** los contadores se guardan en memoria, así que se pierden al reiniciar y no
se comparten entre varios procesos; detrás de un proxy, todas las peticiones parecerían venir
de la misma IP. En producción habría que usar un almacenamiento compartido (Redis) y leer la
IP real del cliente (ver «Limitaciones conocidas»).

## CORS

**Orígenes permitidos:** los de la variable `CORS_ORIGINS` (lista JSON). Por defecto,
`http://localhost:3000` (React) y `http://localhost:5173` (Vite). En producción se añade el
dominio real del frontend, siempre con `https://`. **Nunca `*`**: además de abrir la API a
cualquier web, `*` no admite credenciales.

También se restringen los métodos (`GET`, `POST`, `PUT`, `PATCH`, `DELETE`) y las cabeceras
(`Authorization`, `Content-Type`) a los que usa la API, y la respuesta *preflight* se guarda
1 hora (`max_age=3600`).

**Por qué es necesario:** los navegadores aplican la *política del mismo origen*: una web de
otro dominio no puede leer las respuestas de nuestra API salvo que la API lo permita con CORS.
Así, una web maliciosa abierta en el navegador del usuario no puede usar la API en su nombre
y leer sus datos. CORS **no** protege frente a clientes que no son navegadores (como `curl` o
un script): para eso están la autenticación y el rate limiting.

## Variables de Entorno

**Qué información sensible se guarda:**
| Variable | Sensible | Por qué |
|---|---|---|
| `SECRET_KEY` | **Sí, crítica** | Con ella cualquiera podría firmar tokens válidos y hacerse pasar por cualquier usuario |
| `DATABASE_URL` | **Sí** (en producción) | Con PostgreSQL incluye usuario y contraseña de la base de datos |
| `ALGORITHM`, `*_EXPIRE_*`, `CORS_ORIGINS`, `POKEAPI_*`, `LOG_LEVEL`, `RATE_LIMIT_ENABLED` | No | Configuración, pero cambiarlas mal debilita la seguridad (p. ej., tokens que no caducan) |

**Cómo protegerla:**
- Se leen del entorno o de un archivo `.env` (`pydantic-settings`). El `.env` real **nunca
  se sube**: está en `.gitignore` y en el repositorio solo hay `.env.example` con valores de
  ejemplo. Un `.env` subido por error queda en el historial aunque se borre después: habría
  que cambiar la clave.
- `SECRET_KEY` **no tiene valor por defecto**: si falta, la API no arranca, en vez de
  funcionar con una clave conocida. Se genera con
  `python -c "import secrets; print(secrets.token_hex(32))"` (256 bits aleatorios).
- La CI usa una clave de pruebas propia (`clave-solo-para-ci`), nunca la real.
- En un despliegue, las variables se configuran en el panel de la plataforma o en los
  *secrets* de GitHub Actions, nunca en el código.
- El `.gitignore` excluye también `*.db`, `*.log`, `.venv/`, `.coverage` y `htmlcov/`.

## Vulnerabilidades Conocidas

### OWASP API Security Top 10 (2023) consideradas

| Riesgo | Mitigación en esta API |
|---|---|
| **API1 Broken Object Level Authorization** | Cada consulta filtra por el usuario del token (`owner_id`, `trainer_id`). Las entradas de la Pokédex y los equipos de otro usuario devuelven **404**, no 403, para no revelar que existen. Un equipo solo admite Pokémon de la Pokédex de su dueño. |
| **API2 Broken Authentication** | JWT con firma, caducidad y tipo comprobados; bcrypt; rate limiting en login, refresh y registro; mensajes de error genéricos y tiempo de respuesta constante en el login. |
| **API3 Broken Object Property Level Authorization** | Schemas de entrada y salida separados de las tablas: el cliente solo puede enviar los campos permitidos (no puede fijar `owner_id` ni `is_active`) y las respuestas nunca incluyen `hashed_password`. |
| **API4 Unrestricted Resource Consumption** | Rate limiting; `limit` máximo de 100 en los listados; equipos de 6 Pokémon como máximo; longitud máxima en textos (`nickname` 50, `notes` 500, nombre de equipo 100); timeout de PokeAPI (10 s), tope de 20 conexiones simultáneas y caché con un máximo de 1000 respuestas; el sprite de la ficha `/card` se lee por partes y se descarta en cuanto pasa de 1 MB (PR #25). |
| **API5 Broken Function Level Authorization** | No hay funciones de administración: todos los endpoints actúan solo sobre los datos del propio usuario. |
| **API6 Unrestricted Access to Sensitive Business Flows** | Registro limitado a 5 por hora y por IP. |
| **API7 Server Side Request Forgery** | Los identificadores que van a la URL de PokeAPI se validan con una expresión regular (solo letras, números y guiones), así que `../` no llega nunca a la URL. La cadena de evolución usa solo el número de la URL que da PokeAPI y la pide siempre a `POKEAPI_BASE_URL`, nunca a la URL que venga en la respuesta. Los sprites de la ficha solo se descargan por `https` desde `raw.githubusercontent.com`, sin usuario ni puerto y sin seguir redirecciones (PR #25). |
| **API8 Security Misconfiguration** | CORS con orígenes, métodos y cabeceras concretos; `SECRET_KEY` obligatoria; `.env` fuera del repositorio; versiones de dependencias fijadas en `requirements.txt`. |
| **API9 Improper Inventory Management** | Versiones en la ruta (`/api/v1`, `/api/v2`), documentadas en `/docs` y en el README, con una estrategia de deprecación (README → «Versionado»). |
| **API10 Unsafe Consumption of APIs** | Las respuestas de PokeAPI se validan y se transforman antes de usarlas: si cambian de formato, la API responde 502 en vez de fallar o devolver datos raros. Timeouts → 504; errores de red → 502. |

**Otras mitigaciones:**
- **Inyección SQL:** todas las consultas usan SQLModel/SQLAlchemy con parámetros, nunca SQL
  construido con texto del usuario.
- **Inyección de fórmulas en CSV:** al exportar la Pokédex, los valores que empiezan por `=`,
  `+`, `-` o `@` llevan un `'` delante para que Excel no los ejecute.
- **Integridad de datos:** claves foráneas activadas en SQLite (`PRAGMA foreign_keys=ON`) y
  restricción única `(owner_id, pokemon_id)` en la Pokédex.

### Limitaciones conocidas (riesgo aceptado y cómo se resolvería)
- **Sin revocación de tokens:** un token robado vale hasta que caduca (24 h el de acceso,
  7 días el refresh). Mejora: guardar un identificador (`jti`) de cada refresh token, rotarlo
  al renovar y mantener una lista de tokens revocados.
- **HS256 con clave compartida:** quien tenga la `SECRET_KEY` puede firmar tokens. Es
  aceptable con un único servicio; con varios servicios convendría RS256 (clave privada
  para firmar y pública para verificar).
- **Rate limiting en memoria:** los contadores se pierden al reiniciar y no se comparten
  entre varios procesos. Detrás de un proxy, todas las peticiones parecerían venir de la
  misma IP. En producción: almacenamiento compartido (Redis) y leer la IP real del cliente.
- **Sin bloqueo de cuenta:** tras muchos intentos fallidos no se bloquea la cuenta, solo se
  limita por IP. Un atacante con muchas IP podría seguir probando, aunque despacio.
- **Sin verificación del email:** la expresión regular solo comprueba el formato.
- **HTTPS:** la API no lo gestiona por sí misma. **En producción debe servirse siempre por
  HTTPS** (la plataforma de despliegue o un proxy inverso), porque los tokens viajan en la
  cabecera `Authorization` y por HTTP se podrían interceptar.

## Cómo informar de un problema de seguridad
El repositorio es privado: abre una *issue* en él o escribe a cualquier miembro del equipo.
No publiques los detalles fuera del repositorio y no incluyas tokens, contraseñas ni
contenido del `.env`.
