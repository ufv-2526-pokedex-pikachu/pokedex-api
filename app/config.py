"""Configuración de la aplicación, leída de variables de entorno o del archivo .env."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Ajustes de la API. Cada atributo se lee de la variable de entorno con el mismo nombre."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Sin valor por defecto: si falta, la app no arranca en vez de usar una clave insegura.
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 horas, como pide el enunciado
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    DATABASE_URL: str = "sqlite:///./pokedex.db"

    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    POKEAPI_BASE_URL: str = "https://pokeapi.co/api/v2"
    POKEAPI_TIMEOUT: float = 10.0
    POKEAPI_CACHE_TTL: int = 3600  # segundos; 0 desactiva la caché

    LOG_LEVEL: str = "INFO"
    RATE_LIMIT_ENABLED: bool = True


settings = Settings()
