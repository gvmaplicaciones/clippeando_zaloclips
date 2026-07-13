"""Utilidad compartida para correr comandos de ffmpeg-python con errores visibles.

``.run(quiet=True)`` oculta stdout/stderr y ante un fallo ffmpeg-python solo
lanza ``Error('ffmpeg error (see stderr output for detail)')`` sin el motivo
real. Esta funcion captura la salida igual (para no ensuciar la consola en
el caso exitoso) pero imprime el stderr completo de ffmpeg antes de
relanzar la excepcion si algo falla.
"""
from __future__ import annotations

import sys

import ffmpeg


def run(stream) -> None:
    try:
        ffmpeg.run(stream, capture_stdout=True, capture_stderr=True)
    except ffmpeg.Error as e:
        stderr = (e.stderr or b"").decode(errors="replace")
        print("ERROR de ffmpeg. stderr completo:", file=sys.stderr, flush=True)
        print(stderr, file=sys.stderr, flush=True)
        raise
