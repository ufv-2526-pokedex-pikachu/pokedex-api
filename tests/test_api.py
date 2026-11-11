"""Tests transversales de la API (salud, documentación)."""


def test_health(client):
    """/health responde 200 con status ok."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_disponible(client):
    """La documentación OpenAPI (base de /docs) se genera con el título de la API."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Pokédex API"
