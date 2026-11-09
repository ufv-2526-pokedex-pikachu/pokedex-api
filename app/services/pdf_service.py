"""Generación de PDF con reportlab: ficha de un Pokémon y exportación de equipos.

Este módulo solo dibuja: nunca llama a la red. Los datos de PokeAPI se piden antes
(en el router) y se pasan ya obtenidos, para que sea fácil de probar y reutilizar
(por ejemplo, en la ficha `/pokemon/{id_or_name}/card`).
"""

import io
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

# Stats base de PokeAPI en el orden en que se muestran, con su etiqueta.
STAT_LABELS: dict[str, str] = {
    "hp": "HP",
    "attack": "Ataque",
    "defense": "Defensa",
    "special-attack": "At. esp.",
    "special-defense": "Def. esp.",
    "speed": "Velocidad",
}
# Máximo de una stat base en los juegos: sirve de escala para las barras.
_MAX_BASE_STAT = 255

_FONT = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"
_HEADER_COLOR = colors.HexColor("#CC0000")
_BAR_COLOR = colors.HexColor("#3B4CCA")
_BAR_BACKGROUND = colors.HexColor("#E0E0E0")
_MARGIN = 40


def _fit(text: str, font: str, size: float, max_width: float) -> str:
    """Recorta el texto con «…» si no cabe en una línea de `max_width` puntos."""
    if stringWidth(text, font, size) <= max_width:
        return text
    while text and stringWidth(text + "…", font, size) > max_width:
        text = text[:-1]
    return text + "…"


def draw_pokemon_card(
    canvas: Canvas,
    pokemon: dict[str, Any],
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    description: Optional[str] = None,
    sprite: Optional[bytes] = None,
    subtitle: Optional[str] = None,
) -> None:
    """Dibuja la ficha de un Pokémon en el rectángulo con esquina inferior izquierda (x, y).

    `pokemon` es el dict de `PokeAPIService.get_pokemon` (usa `id`, `name`, `types`,
    `abilities` y `stats`). Opcionales: `description` (la de `get_species`), `sprite`
    (bytes de la imagen) y `subtitle` (p. ej. el mote o la posición en el equipo).
    """
    padding = 8
    inner_width = width - 2 * padding
    top = y + height

    # Marco y cabecera con nombre y número.
    canvas.setStrokeColor(colors.black)
    canvas.roundRect(x, y, width, height, radius=8, stroke=1, fill=0)
    header_height = 24
    canvas.setFillColor(_HEADER_COLOR)
    canvas.roundRect(x, top - header_height, width, header_height, radius=8, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont(_FONT_BOLD, 12)
    name = str(pokemon.get("name", "?")).capitalize()
    canvas.drawString(x + padding, top - 17, _fit(name, _FONT_BOLD, 12, inner_width - 50))
    canvas.drawRightString(x + width - padding, top - 17, f"#{pokemon.get('id', '?')}")

    cursor = top - header_height - 14
    canvas.setFillColor(colors.black)
    if subtitle:
        canvas.setFont(_FONT_BOLD, 9)
        canvas.drawString(x + padding, cursor, _fit(subtitle, _FONT_BOLD, 9, inner_width))
        cursor -= 12

    # Sprite (opcional) a la derecha, debajo de la cabecera.
    sprite_size = 56
    text_width = inner_width
    if sprite:
        canvas.drawImage(
            ImageReader(io.BytesIO(sprite)),
            x + width - padding - sprite_size,
            cursor - sprite_size + 10,
            width=sprite_size,
            height=sprite_size,
            preserveAspectRatio=True,
            mask="auto",
        )
        text_width = inner_width - sprite_size - 4

    canvas.setFont(_FONT, 9)
    types = ", ".join(pokemon.get("types", [])) or "—"
    abilities = ", ".join(pokemon.get("abilities", [])) or "—"
    canvas.drawString(x + padding, cursor, _fit(f"Tipos: {types}", _FONT, 9, text_width))
    cursor -= 12
    canvas.drawString(
        x + padding, cursor, _fit(f"Habilidades: {abilities}", _FONT, 9, text_width)
    )
    cursor -= 16
    if sprite:
        cursor = min(cursor, top - header_height - sprite_size - 10)

    # Barras de stats.
    stats = pokemon.get("stats", {})
    label_width = 52
    value_width = 22
    bar_width = inner_width - label_width - value_width
    for key, label in STAT_LABELS.items():
        value = int(stats.get(key, 0))
        canvas.setFillColor(colors.black)
        canvas.setFont(_FONT, 8)
        canvas.drawString(x + padding, cursor, label)
        canvas.drawRightString(x + padding + label_width + value_width - 4, cursor, str(value))
        bar_x = x + padding + label_width + value_width
        canvas.setFillColor(_BAR_BACKGROUND)
        canvas.rect(bar_x, cursor - 1, bar_width, 7, stroke=0, fill=1)
        canvas.setFillColor(_BAR_COLOR)
        filled = bar_width * min(value, _MAX_BASE_STAT) / _MAX_BASE_STAT
        canvas.rect(bar_x, cursor - 1, filled, 7, stroke=0, fill=1)
        cursor -= 11

    # Descripción de la especie (opcional), hasta donde quepa.
    if description:
        canvas.setFillColor(colors.black)
        canvas.setFont(_FONT, 8)
        cursor -= 4
        for line in simpleSplit(description, _FONT, 8, inner_width):
            if cursor < y + padding:
                break
            canvas.drawString(x + padding, cursor, line)
            cursor -= 10


def build_team_pdf(
    *,
    name: str,
    description: Optional[str],
    trainer: str,
    members: Sequence[tuple[int, Optional[str], dict[str, Any]]],
    combined_stats: dict[str, dict[str, float]],
) -> bytes:
    """Genera el PDF de un equipo y devuelve sus bytes.

    - Página 1: nombre, descripción, entrenador, miembros y estadísticas combinadas.
    - Página 2: las fichas de los miembros (hasta 6, en una cuadrícula de 2 × 3).

    `members` son tuplas `(posición, mote, pokemon)` con `pokemon` como el dict de
    `get_pokemon`; `combined_stats` es `{stat: {"total": …, "average": …}}`.
    """
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=A4)
    canvas.setTitle(f"Equipo {name}")
    page_width, page_height = A4
    usable_width = page_width - 2 * _MARGIN

    # --- Página 1: resumen ---
    cursor = page_height - _MARGIN - 10
    canvas.setFont(_FONT_BOLD, 20)
    canvas.drawString(_MARGIN, cursor, _fit(name, _FONT_BOLD, 20, usable_width))
    cursor -= 18
    canvas.setFont(_FONT, 10)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    canvas.drawString(_MARGIN, cursor, f"Entrenador: {trainer} · Generado: {generated}")
    cursor -= 22
    canvas.setFont(_FONT, 11)
    for line in simpleSplit(description or "Sin descripción.", _FONT, 11, usable_width):
        canvas.drawString(_MARGIN, cursor, line)
        cursor -= 14

    cursor -= 12
    canvas.setFont(_FONT_BOLD, 13)
    canvas.drawString(_MARGIN, cursor, "Miembros")
    cursor -= 16
    canvas.setFont(_FONT, 10)
    for position, nickname, pokemon in members:
        types = ", ".join(pokemon.get("types", []))
        alias = f" «{nickname}»" if nickname else ""
        line = f"{position}. {str(pokemon.get('name', '?')).capitalize()}{alias} ({types})"
        canvas.drawString(_MARGIN, cursor, _fit(line, _FONT, 10, usable_width))
        cursor -= 13

    cursor -= 16
    canvas.setFont(_FONT_BOLD, 13)
    canvas.drawString(_MARGIN, cursor, "Estadísticas combinadas")
    cursor -= 18
    columns = (_MARGIN, _MARGIN + 160, _MARGIN + 260)
    canvas.setFont(_FONT_BOLD, 10)
    canvas.drawString(columns[0], cursor, "Stat")
    canvas.drawRightString(columns[1] + 40, cursor, "Suma")
    canvas.drawRightString(columns[2] + 40, cursor, "Media")
    canvas.line(_MARGIN, cursor - 4, columns[2] + 40, cursor - 4)
    cursor -= 16
    for key, values in combined_stats.items():
        canvas.setFont(_FONT_BOLD if key == "total" else _FONT, 10)
        label = "Total" if key == "total" else STAT_LABELS.get(key, key)
        canvas.drawString(columns[0], cursor, label)
        canvas.drawRightString(columns[1] + 40, cursor, f"{values['total']:g}")
        canvas.drawRightString(columns[2] + 40, cursor, f"{values['average']:.1f}")
        cursor -= 14
    canvas.setFont(_FONT, 8)
    canvas.drawString(
        _MARGIN,
        cursor - 6,
        "Suma y media de las stats base de PokeAPI de los miembros del equipo.",
    )

    # --- Página 2: fichas en una cuadrícula de 2 columnas × 3 filas ---
    canvas.showPage()
    gap = 12
    card_width = (usable_width - gap) / 2
    card_height = (page_height - 2 * _MARGIN - 2 * gap) / 3
    for index, (position, nickname, pokemon) in enumerate(members):
        column, row = index % 2, index // 2
        card_x = _MARGIN + column * (card_width + gap)
        card_y = page_height - _MARGIN - (row + 1) * card_height - row * gap
        subtitle = f"Posición {position}" + (f" · «{nickname}»" if nickname else "")
        draw_pokemon_card(
            canvas, pokemon, card_x, card_y, card_width, card_height, subtitle=subtitle
        )

    canvas.save()
    return buffer.getvalue()
