"""Prompts de sistema por tipo de contenido, usados por src.detect_moments
para elegir el criterio de seleccion de momentos correcto.

Cada archivo en ``prompts/<nombre>.txt`` (raiz del repo) es un system prompt
completo para la API de Claude (rol + criterios + formato de salida).
Agregar un tipo de contenido nuevo es agregar un archivo ahi - no hace
falta tocar codigo en ningun otro lado.
"""
from __future__ import annotations

from pathlib import Path

from src.config import PROJECT_ROOT

PROMPTS_DIR = PROJECT_ROOT / "prompts"


def _prompt_path(content_type: str) -> Path:
    return PROMPTS_DIR / f"{content_type}.txt"


def list_available_content_types() -> list[str]:
    """Nombres de los tipos de contenido disponibles (nombre de archivo sin .txt), ordenados."""
    if not PROMPTS_DIR.exists():
        return []
    return sorted(p.stem for p in PROMPTS_DIR.glob("*.txt"))


def get_content_type_prompt(content_type: str) -> str:
    """Contenido del system prompt para `content_type` (ej. "invitado").

    Lanza ValueError con la lista de tipos validos si `content_type` no
    tiene un archivo prompts/<content_type>.txt correspondiente.
    """
    path = _prompt_path(content_type)
    if not path.exists():
        valid = ", ".join(list_available_content_types())
        raise ValueError(
            f"Tipo de contenido desconocido: {content_type!r}. Tipos validos: {valid or '(ninguno en prompts/)'}"
        )
    return path.read_text(encoding="utf-8")


def list_content_types() -> None:
    """Imprime los tipos de contenido disponibles (para --list-content-types)."""
    types = list_available_content_types()
    if not types:
        print(f"No hay prompts definidos en {PROMPTS_DIR}")
        return
    print("Tipos de contenido disponibles:")
    for content_type in types:
        print(f"  {content_type}")
