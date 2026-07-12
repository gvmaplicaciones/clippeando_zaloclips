# clip-pipeline

Pipeline para convertir VODs largos en clips cortos listos para redes:
descarga el video, lo transcribe con Whisper, detecta los mejores momentos
con Claude (Anthropic) y recorta los clips finales con ffmpeg.

El código en `src/` es agnóstico del entorno: no tiene rutas hardcodeadas
de Google Colab (`/content/...`). Todas las rutas son relativas a la raíz
del proyecto o configurables por variable de entorno, así el mismo código
corre en Colab, en un VPS o en tu laptop sin cambios.

## Estructura

```
clip-pipeline/
  input/          # VODs descargados (ignorado por git)
  transcripts/    # JSON de whisper (ignorado por git)
  moments/        # JSON de momentos detectados por Claude
  output/         # clips finales (ignorado por git)
  src/            # código del pipeline
  notebooks/
    pipeline_colab.ipynb   # notebook para correr todo en Google Colab
  requirements.txt
  .env.example
```

## Setup local / VPS

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# editar .env y completar ANTHROPIC_API_KEY

# ffmpeg debe estar instalado en el sistema (apt install ffmpeg / brew install ffmpeg)
```

## Uso

```bash
# a partir de una URL (yt-dlp)
python -m src.pipeline --url "https://..."

# a partir de un archivo ya descargado en input/
python -m src.pipeline --file input/mi_video.mp4
```

Esto genera:
1. `input/<id>.mp4` (si se usó `--url`)
2. `transcripts/<video>.json`
3. `moments/<video>.json`
4. `output/<video>_01_<titulo>.mp4`, `output/<video>_02_<titulo>.mp4`, ...

## Configuración

Todo se configura mediante variables de entorno (ver `.env.example`):

| Variable | Default | Descripción |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(requerida)* | API key de Anthropic |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | Modelo usado para detectar momentos |
| `PROJECT_ROOT` | raíz del repo | Raíz para resolver `input/`, `transcripts/`, etc. |
| `INPUT_DIR`, `TRANSCRIPTS_DIR`, `MOMENTS_DIR`, `OUTPUT_DIR` | subcarpetas de `PROJECT_ROOT` | Override individual de cada carpeta |
| `WHISPER_MODEL_SIZE` | `medium` | Tamaño del modelo de faster-whisper |
| `WHISPER_DEVICE` | `auto` | `cpu`, `cuda` o `auto` |
| `WHISPER_LANGUAGE` | *(autodetección)* | Forzar idioma de transcripción |

## Google Colab

Abrí `notebooks/pipeline_colab.ipynb` en Colab. El notebook clona (o
actualiza) este repo, instala dependencias, verifica ffmpeg, pide la API
key de forma segura (no queda hardcodeada en el notebook) y corre
`run_pipeline` de ejemplo.
