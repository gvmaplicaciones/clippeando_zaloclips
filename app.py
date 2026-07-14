"""Frontend Streamlit del pipeline de clips, para no depender de la terminal.

No duplica logica de negocio: solo arma la interfaz y llama a las mismas
funciones de src/ que usan los scripts de linea de comandos
(src.pipeline.run_pipeline, src.content_types.list_available_content_types,
etc.). Los prints ya existentes en cada modulo (descarga, transcripcion,
deteccion de momentos, generacion de clips) se capturan tal cual via
redireccion de stdout, sin rehacer el logging.

Sin marca de agua: este pipeline no aplica watermark (ver src/watermark.py
para eso, aparte, sobre clips ya generados).

Lanzar con: streamlit run app.py (ver README, seccion "Interfaz web").
"""
from __future__ import annotations

import contextlib
import traceback
from pathlib import Path

import streamlit as st

from src.config import INPUT_DIR
from src.content_types import get_content_type_prompt, list_available_content_types
from src.ffmpeg_utils import probe_duration
from src.pipeline import run_pipeline
from src.video_filters import FILTER_LABELS

st.set_page_config(page_title="Clip Pipeline", page_icon="🎬", layout="wide")

AUTO_CONTENT_TYPE_LABEL = "Automático (el modelo clasifica solo)"
NO_FILTER_LABEL = "Ningún filtro"


class _LiveLogWriter:
    """Redirige stdout a un placeholder de Streamlit, actualizandolo en vivo.

    Los distintos pasos del pipeline (src.download, src.transcribe,
    src.detect_moments, src.clip) ya imprimen su progreso por consola; en
    vez de rehacer ese logging, contextlib.redirect_stdout apunta stdout
    aca durante la corrida y cada linea completa se vuelca al placeholder,
    lo que Streamlit manda al navegador de inmediato (no hace falta
    esperar a que termine el script para verlo).
    """

    def __init__(self, placeholder) -> None:
        self._placeholder = placeholder
        self._buffer = ""

    def write(self, text: str) -> int:
        if not text:
            return 0
        self._buffer += text
        if "\n" in text:
            # Ultimos ~8000 caracteres: suficiente contexto sin dejar
            # crecer el mensaje sin limite en corridas largas.
            self._placeholder.code(self._buffer[-8000:], language=None)
        return len(text)

    def flush(self) -> None:
        pass


def _content_type_options() -> dict[str, str | None]:
    """label visible -> content_type ("invitado", ...) o None para "automatico"."""
    options: dict[str, str | None] = {AUTO_CONTENT_TYPE_LABEL: None}
    for content_type in list_available_content_types():
        options[content_type] = content_type
    return options


def _filter_options() -> dict[str, str | None]:
    """label visible -> nombre del filtro (clave de VIDEO_FILTERS) o None para "sin filtro"."""
    options: dict[str, str | None] = {NO_FILTER_LABEL: None}
    for name, label in FILTER_LABELS.items():
        options[label] = name
    return options


def _save_uploaded_file(uploaded_file) -> Path:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = INPUT_DIR / uploaded_file.name
    dest.write_bytes(uploaded_file.getbuffer())
    return dest


def _validate_source(url: str | None, uploaded_file) -> str | None:
    """Mensaje de error si la combinacion URL/archivo no es valida, o None si esta OK."""
    has_url = bool(url and url.strip())
    has_file = uploaded_file is not None
    if has_url and has_file:
        return "Completa la URL O subi un archivo, no ambos a la vez."
    if not has_url and not has_file:
        return "Falta la URL del video o un archivo para subir."
    return None


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "(duración desconocida)"
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}:{secs:02d}"


def _render_content_types_tab() -> None:
    content_types = list_available_content_types()
    if not content_types:
        st.info("No hay tipos de contenido definidos en prompts/.")
        return
    st.write(
        "Sin elegir uno (modo automático), el modelo clasifica el transcript solo "
        "entre estos mismos criterios más uno genérico para streamers sin invitados."
    )
    for content_type in content_types:
        prompt_text = get_content_type_prompt(content_type)
        first_line = prompt_text.strip().splitlines()[0]
        st.markdown(f"**{content_type}**")
        st.write(first_line)
        with st.expander("Ver prompt completo"):
            st.text(prompt_text)
        st.divider()


def _run_and_render(
    *,
    url: str | None,
    file_path: str | None,
    content_type: str | None,
    video_title: str | None,
    max_clips: int | None,
    video_filter: str | None,
) -> None:
    log_placeholder = st.empty()
    with st.status("Procesando…", expanded=True) as status_box:
        try:
            writer = _LiveLogWriter(log_placeholder)
            with contextlib.redirect_stdout(writer):
                clip_paths = run_pipeline(
                    url=url,
                    file=file_path,
                    content_type=content_type,
                    video_title_override=video_title or None,
                    max_clips=max_clips,
                    video_filter=video_filter,
                )
        except Exception as e:
            status_box.update(label="Falló", state="error")
            st.error(f"Ocurrió un error durante el pipeline: {e}")
            st.code(traceback.format_exc(), language=None)
            return

        status_box.update(label="Listo", state="complete")

    if not clip_paths:
        st.warning("El pipeline terminó sin generar ningún clip (revisá el log de arriba).")
        return

    output_folder = clip_paths[0].parent
    st.success(f"{len(clip_paths)} clip(s) generados en:\n\n`{output_folder}`")

    rows = [
        {"Clip": clip_path.name, "Duración": _format_duration(probe_duration(clip_path))}
        for clip_path in clip_paths
    ]
    st.table(rows)


def _render_main_tab() -> None:
    st.subheader("Generar clips")

    source_mode = st.radio("Origen del video", ["URL de YouTube", "Subir archivo"], horizontal=True)

    url = None
    uploaded_file = None
    if source_mode == "URL de YouTube":
        url = st.text_input("URL del video", placeholder="https://www.youtube.com/watch?v=...")
    else:
        uploaded_file = st.file_uploader("Archivo de video", type=["mp4", "mkv", "mov", "webm"])

    content_type_options = _content_type_options()
    content_type_label = st.selectbox(
        "Tipo de contenido",
        list(content_type_options.keys()),
        help="Fuerza el criterio de selección de momentos. En modo automático, el "
        "modelo detecta solo si es entretenimiento con invitados, viaje, podcast o "
        "narrativo de un streamer, sin que tengas que indicarlo.",
    )
    content_type = content_type_options[content_type_label]

    filter_options = _filter_options()
    filter_label = st.selectbox(
        "Filtro de video",
        list(filter_options.keys()),
        help="Se aplica sobre todo el clip antes de los subtítulos.",
    )
    video_filter = filter_options[filter_label]

    video_title = st.text_input(
        "Nombre del video (opcional)",
        help="Si lo completás, se usa como nombre de la carpeta de salida en vez del "
        "título automático de yt-dlp (útil si el título real es muy largo o preferís "
        "organizar las carpetas a mano).",
    )

    max_clips = st.number_input(
        "Máximo de clips (opcional)",
        min_value=1,
        step=1,
        value=None,
        help="Si lo completás, se procesan solo los N momentos de mayor score (el resto "
        "se descarta antes de generar nada, ahorrando tiempo de ffmpeg). "
        "Vacío = sin límite. Un momento largo dividido en PARTE 1/PARTE 2 cuenta como uno solo.",
    )

    if not st.button("Generar clips", type="primary"):
        return

    error = _validate_source(url, uploaded_file)
    if error:
        st.error(error)
        return

    file_path = str(_save_uploaded_file(uploaded_file)) if uploaded_file is not None else None
    _run_and_render(
        url=(url or None) if uploaded_file is None else None,
        file_path=file_path,
        content_type=content_type,
        video_title=video_title,
        max_clips=int(max_clips) if max_clips else None,
        video_filter=video_filter,
    )


def main() -> None:
    st.title("🎬 Clip Pipeline")
    tab_main, tab_content_types = st.tabs(["Generar clips", "Tipos de contenido"])
    with tab_main:
        _render_main_tab()
    with tab_content_types:
        _render_content_types_tab()


main()
