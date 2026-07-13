"""Registro de marcas de agua conocidas (texto + logo) por nombre de campaña.

Cada campaña/marca de clipping puede pedir un watermark distinto. Agregar
una marca nueva es agregar una entrada a WATERMARKS, sin tocar el resto
del codigo (src.clip, src.pipeline y src.watermark la resuelven por nombre
via get_watermark()).
"""
from __future__ import annotations

from src.config import PROJECT_ROOT

WATERMARKS: dict[str, dict[str, object]] = {
    "ampeter": {
        "text": "ampeterby7",
        "logo": PROJECT_ROOT / "Youtube_logo.png",
    },
}


def get_watermark(name: str) -> dict[str, object]:
    """Devuelve la config `{"text": ..., "logo": ...}` de la marca `name`.

    Lanza ValueError con la lista de marcas validas si `name` no existe.
    """
    try:
        return WATERMARKS[name]
    except KeyError:
        valid = ", ".join(sorted(WATERMARKS)) or "(ninguna marca definida)"
        raise ValueError(f"Marca de agua desconocida: {name!r}. Marcas validas: {valid}") from None


def list_watermarks() -> None:
    """Imprime las marcas de agua disponibles (nombre, texto, logo)."""
    if not WATERMARKS:
        print("No hay marcas de agua definidas en src/watermarks.py")
        return
    print("Marcas de agua disponibles:")
    for name, cfg in sorted(WATERMARKS.items()):
        print(f"  {name}: texto={cfg['text']!r} logo={cfg['logo']}")
