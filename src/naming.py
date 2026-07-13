"""Sanitizacion de nombres de archivo/carpeta validos en Windows.

Se sanitiza para las reglas de Windows (el subconjunto mas estricto de los
SO soportados) para que las mismas rutas generadas en Colab/Linux sirvan
sin cambios si el usuario despues mueve/sincroniza esos archivos a Windows
(ej. una carpeta de Google Drive Desktop).
"""
from __future__ import annotations

# Caracteres no permitidos en nombres de archivo/carpeta en Windows.
_FORBIDDEN_CHARS = '\\/:*?"<>|'
_FORBIDDEN_TABLE = str.maketrans("", "", _FORBIDDEN_CHARS)


def sanitize_filename(text: str, max_len: int = 150, fallback: str = "sin_titulo") -> str:
    """Sanitiza `text` para usarlo como nombre de archivo o carpeta en Windows."""
    cleaned = text.translate(_FORBIDDEN_TABLE)
    cleaned = " ".join(cleaned.split())  # colapsa espacios/saltos de linea multiples
    cleaned = cleaned[:max_len]
    # Windows no permite espacios ni puntos al final del nombre.
    cleaned = cleaned.rstrip(" .")
    return cleaned or fallback
