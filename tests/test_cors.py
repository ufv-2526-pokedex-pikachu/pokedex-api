"""Tests de la configuración CORS de la app real."""

from app.config import settings
from tests.test_rate_limiting import limited_client  # noqa: F401  (fixture compartida)

ALLOWED_ORIGIN = settings.CORS_ORIGINS[0]
OTHER_ORIGIN = "https://sitio-malicioso.example"


def preflight(client, origin: str, method: str = "GET", headers: str | None = None):
    """Simula la petición OPTIONS que hace el navegador antes de la petición real."""
    request_headers = {"Origin": origin, "Access-Control-Request-Method": method}
    if headers:
        request_headers["Access-Control-Request-Headers"] = headers
    return client.options("/health", headers=request_headers)


def test_preflight_allowed_origin_returns_cors_headers(client):
    response = preflight(client, ALLOWED_ORIGIN, headers="Authorization, Content-Type")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    assert response.headers["access-control-max-age"] == "3600"


def test_preflight_other_origin_is_rejected(client):
    response = preflight(client, OTHER_ORIGIN)

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_preflight_method_not_allowed_is_rejected(client):
    assert preflight(client, ALLOWED_ORIGIN, method="TRACE").status_code == 400


def test_preflight_header_not_allowed_is_rejected(client):
    assert preflight(client, ALLOWED_ORIGIN, headers="X-Cabecera-Rara").status_code == 400


def test_get_allowed_origin_returns_cors_header(client):
    response = client.get("/health", headers={"Origin": ALLOWED_ORIGIN})

    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_get_other_origin_has_no_cors_header(client):
    response = client.get("/health", headers={"Origin": OTHER_ORIGIN})

    assert "access-control-allow-origin" not in response.headers


def test_wildcard_origin_is_not_configured():
    assert "*" not in settings.CORS_ORIGINS


def test_rate_limit_response_keeps_cors_headers(limited_client):  # noqa: F811
    """Un 429 también lleva CORS: si no, el navegador lo vería como un error de red."""
    responses = [
        limited_client.post("/_test/login", headers={"Origin": ALLOWED_ORIGIN}) for _ in range(11)
    ]

    assert responses[-1].status_code == 429
    assert responses[-1].headers["access-control-allow-origin"] == ALLOWED_ORIGIN
