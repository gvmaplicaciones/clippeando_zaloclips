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
| `ANTHROPIC_MODEL` | `claude-haiku-4-5` | Modelo usado para detectar momentos |
| `PROJECT_ROOT` | raíz del repo | Raíz para resolver `input/`, `transcripts/`, etc. |
| `INPUT_DIR`, `TRANSCRIPTS_DIR`, `MOMENTS_DIR`, `OUTPUT_DIR` | subcarpetas de `PROJECT_ROOT` | Override individual de cada carpeta |
| `WHISPER_MODEL_SIZE` | `medium` | Tamaño del modelo de faster-whisper |
| `WHISPER_DEVICE` | `auto` | `cpu`, `cuda` o `auto` |
| `WHISPER_LANGUAGE` | *(autodetección)* | Forzar idioma de transcripción |
| `YTDLP_COOKIES_FILE` | *(ninguno)* | Ruta a un `cookies.txt` para autenticar la descarga (ver abajo) |
| `YTDLP_COOKIES_FROM_BROWSER` | *(ninguno)* | Nombre del navegador (`chrome`, `firefox`, ...) para leer cookies localmente. No funciona en Colab. |
| `YTDLP_PLAYER_CLIENT` | `web,web_creator,tv` si hay cookies configuradas | Clientes de YouTube a usar, separados por coma. Los clientes moviles (`android`, `ios`, `android_vr`) ignoran las cookies de sesion. |

## Google Colab

Abrí `notebooks/pipeline_colab.ipynb` en Colab. El notebook clona (o
actualiza) este repo, instala dependencias, verifica ffmpeg, pide la API
key de forma segura (no queda hardcodeada en el notebook) y corre
`run_pipeline` de ejemplo.

## Problemas conocidos

### `ERROR: [youtube] ...: Sign in to confirm you're not a bot`

YouTube bloquea la descarga cuando la IP no parece un navegador real (muy
común en Colab, VPS y otros entornos cloud). Hay dos causas independientes,
y normalmente hace falta resolver ambas:

**1. Cookies de sesión.** yt-dlp puede autenticarse con las cookies de una
sesión de YouTube ya logueada:

1. En tu navegador (logueado en YouTube), exportá las cookies con una
   extensión como "Get cookies.txt LOCALLY" (Chrome/Firefox) en formato
   Netscape (`cookies.txt`).
2. Subí ese archivo a tu entorno (en Colab: panel de archivos, o
   `files.upload()`).
3. Configurá `YTDLP_COOKIES_FILE=/ruta/a/cookies.txt` en tu `.env` (en el
   notebook de Colab, la sección "Autenticar la descarga" hace esto por
   vos). `src/download.py` relee el `.env` en cada descarga, así que no
   hace falta reiniciar el kernel para que tome efecto.

En una máquina local o VPS donde el navegador está instalado, alternativamente
podés usar `YTDLP_COOKIES_FROM_BROWSER=chrome` para que yt-dlp lea las cookies
directamente del navegador (esto no funciona en Colab, que no tiene un
navegador con sesión iniciada).

**2. Cliente de YouTube usado por yt-dlp.** Aunque las cookies sean válidas,
si yt-dlp termina probando un cliente móvil (`android`, `ios`, `android_vr` —
se ve en el log como "Downloading android vr player API JSON") el bot-check
va a fallar igual, porque esos clientes no usan cookies de sesión. Cuando
hay cookies configuradas, el pipeline fuerza automáticamente clientes que sí
las respetan (`web`, `web_creator`, `tv`); podés override-earlo con
`YTDLP_PLAYER_CLIENT=web,tv` (lista separada por comas) si hace falta ajustar.

También instalá **deno** (el notebook de Colab lo hace en la sección 4) —
yt-dlp lo necesita para resolver los desafíos anti-bot de YouTube; sin un
runtime de JS, la extracción puede degradar a esos mismos clientes móviles.

Las cookies de YouTube expiran; si el error reaparece después de un tiempo,
volvé a exportarlas.
