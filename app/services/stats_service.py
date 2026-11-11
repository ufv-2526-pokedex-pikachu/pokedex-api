"""Estadísticas de la Pokédex de un usuario (`GET /api/v1/pokedex/stats`).

Es una función pura: recibe las entradas y la fecha de hoy y no consulta la base de datos
ni el reloj. Así se prueba con fechas fijas. La interpretación de cada campo está en el
README → «Otras decisiones».
"""

from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.models import PokedexEntry


def _utc_date(value: datetime) -> date:
    """Día (en UTC) de una fecha; si no trae zona horaria, se asume UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).date()


def _most_common_type(entries: list[PokedexEntry]) -> Optional[str]:
    """Tipo que más se repite entre las entradas ("fire,flying" cuenta fire y flying).

    Si hay empate, gana el primero en orden alfabético, para que el resultado no dependa
    del orden de las filas. `None` si no hay tipos.
    """
    counts = Counter(
        type_name.strip()
        for entry in entries
        for type_name in (entry.pokemon_types or "").split(",")
        if type_name.strip()
    )
    if not counts:
        return None
    return min(counts, key=lambda type_name: (-counts[type_name], type_name))


def _capture_streak_days(capture_days: set[date], today: date) -> int:
    """Días seguidos con al menos una captura que terminan hoy o ayer.

    Si hoy todavía no hay capturas, la racha de ayer sigue viva (aún puedes mantenerla).
    Si el último día con capturas es anterior a ayer, la racha es 0.
    """
    if today in capture_days:
        day = today
    elif today - timedelta(days=1) in capture_days:
        day = today - timedelta(days=1)
    else:
        return 0
    streak = 0
    while day in capture_days:
        streak += 1
        day -= timedelta(days=1)
    return streak


def compute_pokedex_stats(entries: Iterable[PokedexEntry], today: date) -> dict[str, Any]:
    """Calcula las estadísticas del enunciado a partir de las entradas de UNA Pokédex.

    - `total_pokemon`: entradas de la Pokédex.
    - `captured`: entradas con `is_captured`.
    - `favorites`: entradas con `favorite`.
    - `completion_percentage`: captured / total × 100 con 1 decimal (0.0 si está vacía).
    - `most_common_type`: tipo más repetido (empate → alfabético) o `None`.
    - `capture_streak_days`: racha de días seguidos con capturas, según `capture_date`.
    """
    entries = list(entries)
    total = len(entries)
    captured = [entry for entry in entries if entry.is_captured]
    capture_days = {_utc_date(entry.capture_date) for entry in captured if entry.capture_date}
    return {
        "total_pokemon": total,
        "captured": len(captured),
        "favorites": sum(1 for entry in entries if entry.favorite),
        "completion_percentage": round(len(captured) / total * 100, 1) if total else 0.0,
        "most_common_type": _most_common_type(entries),
        "capture_streak_days": _capture_streak_days(capture_days, today),
    }
