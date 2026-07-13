"""Utilidad compartida para correr comandos de ffmpeg-python con errores visibles.

``.run(quiet=True)`` oculta stdout/stderr y ante un fallo ffmpeg-python solo
lanza ``Error('ffmpeg error (see stderr output for detail)')`` sin el motivo
real. Esta funcion captura la salida igual (para no ensuciar la consola en
el caso exitoso) pero imprime el stderr completo de ffmpeg antes de
relanzar la excepcion si algo falla.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
import threading
import time
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


def run_command(args: list[str], progress_interval: float = 3.0) -> None:
    """Corre un comando de ffmpeg armado a mano (lista de argv).

    Para casos con multiples inputs y ``-map`` explicitos (ej. overlay de un
    logo + drawtext + audio del input original) el DSL de ffmpeg-python no
    expresa bien un ``-map`` repetido, asi que se arma el comando directo en
    vez de forzarlo por ese DSL (mismo motivo que ``escape_filter_path``:
    evitar el escapado automatico de ffmpeg-python).

    Imprime el comando exacto antes de correrlo (para poder diagnosticar
    lentitud inspeccionando el preset/filtros reales) y progreso en vivo
    (tiempo procesado, fps, velocidad) cada ``progress_interval`` segundos en
    vez de bloquear en silencio hasta que termine - asi se puede distinguir
    "esta avanzando lento" de "esta colgado".
    """
    print("Comando ffmpeg:", " ".join(shlex.quote(a) for a in args), flush=True)

    # "-progress pipe:1" hace que ffmpeg escriba pares clave=valor por stdout
    # en cada frame procesado (out_time, speed, fps, ...) ademas del log
    # normal por stderr; "-nostats" apaga la linea de progreso default que
    # ffmpeg ya escribe por stderr para no duplicarla.
    full_args = [args[0], "-progress", "pipe:1", "-nostats", *args[1:]]

    proc = subprocess.Popen(
        full_args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    stderr_lines: list[str] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line)

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    assert proc.stdout is not None
    fields: dict[str, str] = {}
    last_print = 0.0
    for raw_line in proc.stdout:
        key, _, value = raw_line.strip().partition("=")
        if not key:
            continue
        fields[key] = value
        if key == "progress":
            now = time.monotonic()
            if value == "end" or now - last_print >= progress_interval:
                print(
                    f"  ffmpeg: tiempo={fields.get('out_time', '?')} "
                    f"fps={fields.get('fps', '?')} speed={fields.get('speed', '?')}",
                    flush=True,
                )
                last_print = now

    proc.wait()
    stderr_thread.join()

    if proc.returncode != 0:
        print("ERROR de ffmpeg. stderr completo:", file=sys.stderr, flush=True)
        print("".join(stderr_lines), file=sys.stderr, flush=True)
        raise RuntimeError(f"ffmpeg fallo (codigo {proc.returncode})")


def has_audio_stream(path: Path | str) -> bool:
    """True si `path` tiene al menos una pista de audio (via ffprobe)."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=index",
                "-of", "csv=p=0",
                str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def probe_duration(path: Path | str) -> float | None:
    """Duracion en segundos de un archivo de audio/video via ffprobe, o None si falla."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


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
