"""Utilidad compartida para correr comandos de ffmpeg-python con errores visibles.

``.run(quiet=True)`` oculta stdout/stderr y ante un fallo ffmpeg-python solo
lanza ``Error('ffmpeg error (see stderr output for detail)')`` sin el motivo
real. Esta funcion captura la salida igual (para no ensuciar la consola en
el caso exitoso) pero imprime el stderr completo de ffmpeg antes de
relanzar la excepcion si algo falla.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import ffmpeg


def run(stream) -> None:
    try:
        ffmpeg.run(stream, capture_stdout=True, capture_stderr=True)
    except ffmpeg.Error as e:
        stderr = (e.stderr or b"").decode(errors="replace")
        print("ERROR de ffmpeg. stderr completo:", file=sys.stderr, flush=True)
        print(stderr, file=sys.stderr, flush=True)
        raise


def run_command(args: list[str]) -> None:
    """Corre un comando de ffmpeg armado a mano (lista de argv).

    Para casos con multiples inputs y ``-map`` explicitos (ej. overlay de un
    logo + drawtext + audio del input original) el DSL de ffmpeg-python no
    expresa bien un ``-map`` repetido, asi que se arma el comando directo en
    vez de forzarlo por ese DSL (mismo motivo que ``escape_filter_path``:
    evitar el escapado automatico de ffmpeg-python).
    """
    result = subprocess.run(args, capture_output=True)
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")
        print("ERROR de ffmpeg. stderr completo:", file=sys.stderr, flush=True)
        print(stderr, file=sys.stderr, flush=True)
        raise RuntimeError(f"ffmpeg fallo (codigo {result.returncode})")


def escape_filter_path(path: Path | str) -> str:
    """Escapa una ruta para usarla como valor de un filtro de ffmpeg (ass,
    subtitles, drawtext fontfile, etc.).

    Dentro del mini-lenguaje de filtros de ffmpeg, ':' separa opciones y
    '\\' es caracter de escape, asi que una ruta de Windows como
    ``C:\\Users\\x\\archivo`` rompe el parseo si se pasa tal cual.

    Verificado empiricamente contra ffmpeg real (no solo por doc, ver
    historial de src/subtitles.py): reemplazar '\\' por '/' y escapar los
    ':' literales como '\\:' es lo unico que funciono de forma confiable,
    combinado con pasar el valor entre comillas simples en el filtro
    (``clave='<escapado>'``) y evitar el escapado automatico de
    ffmpeg-python (que multiplica los backslashes en vez de manejarlos).
    """
    escaped = str(path).replace("\\", "/")
    escaped = escaped.replace("'", "'\\''")  # por si la ruta tuviera comillas simples
    escaped = escaped.replace(":", "\\:")
    return escaped
