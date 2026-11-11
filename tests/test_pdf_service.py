"""Tests de la ficha suelta de `pdf_service`. Solo genera PDF: no usa la red."""

import base64

from app.services.pdf_service import build_pokemon_card_pdf

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


# Mismo formato que devuelve `PokeAPIService.get_pokemon`.
PIKACHU = {
    "id": 25,
    "name": "pikachu",
    "sprite": "https://img.test/25.png",
    "sprites": {"front_default": "https://img.test/25.png"},
    "types": ["electric"],
    "stats": {"hp": 35, "attack": 55, "defense": 40, "speed": 90},
    "abilities": ["static", "lightning-rod"],
    "height": 4,
    "weight": 60,
}


def _pages(pdf: bytes) -> int:
    return pdf.count(b"/Type /Page") - pdf.count(b"/Type /Pages")


def test_build_pokemon_card_pdf_is_one_page_with_image():
    pdf = build_pokemon_card_pdf(PIKACHU, description="Ratón eléctrico.", sprite=PNG)

    assert pdf.startswith(b"%PDF")
    assert _pages(pdf) == 1
    assert b"/Subtype /Image" in pdf


def test_build_pokemon_card_pdf_ignores_invalid_sprite():
    pdf = build_pokemon_card_pdf(PIKACHU, sprite=b"no soy una imagen")

    assert pdf.startswith(b"%PDF")
    assert b"/Subtype /Image" not in pdf


def test_build_pokemon_card_pdf_with_long_description_stays_on_one_page():
    """Una descripción muy larga se corta dentro de la ficha, sin crear páginas nuevas."""
    pdf = build_pokemon_card_pdf(PIKACHU, description="Texto muy largo. " * 400)

    assert _pages(pdf) == 1
