# clip-pipeline

Pipeline para convertir VODs largos en clips cortos listos para TikTok:
descarga el video, lo transcribe con Whisper (con timestamps por palabra),
detecta los mejores momentos con Claude (Anthropic) y genera los clips
finales en formato vertical 9:16 con subtítulos karaoke quemados. Los
momentos largos (>90s) se dividen automáticamente en varias partes con un
aviso de cliffhanger al final de cada una.

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
  metadata/       # id/titulo/etc. de cada VOD descargado (ignorado por git)
  output/         # clips finales por defecto (ignorado por git; configurable con OUTPUT_DIR)
  src/            # código del pipeline
    clip.py        # orquesta: division en partes -> crop vertical -> subtitulos
    vertical.py    # crop centrado a 9:16 (1080x1920)
    subtitles.py   # genera y quema el .ass de subtitulos karaoke
    naming.py      # sanitiza nombres de archivo/carpeta para que sean validos en Windows
    watermark.py   # script independiente: quema un watermark de texto sobre clips ya generados
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
2. `transcripts/<video>.json` (con timestamps por palabra)
3. `moments/<video>.json`
4. `metadata/<id>.json` (id, título real del video, uploader, etc. — solo si se usó `--url`)
5. `<OUTPUT_DIR>/<título del video>/<hook_title>.mp4`,
   `<OUTPUT_DIR>/<título del video>/<hook_title> PARTE 1.mp4`, ... — cada
   clip ya en 9:16, con subtítulos karaoke quemados y nombrado con el
   `hook_title` del momento (no el id del video). Los momentos de más de
   90s se dividen en partes de ~60-70s con un cliffhanger
   ("PARTE N PRÓXIMAMENTE") al final de cada parte salvo la última. Ejemplo:

   ```
   output/
     Organicé un Mundial de Fútbol con Youtubers/
       GOLAZO DESDE AFUERA DEL AREA.mp4
       TANDA DE PENALES DEFINITORIA PARTE 1.mp4
       TANDA DE PENALES DEFINITORIA PARTE 2.mp4
   ```

   Si dos momentos generan el mismo nombre de archivo, el segundo se
   guarda como `<hook_title> (2).mp4` para no pisar al primero.

### Regenerar clips sin re-descargar ni re-transcribir

Si ya tenés `input/<video>.mp4`, `transcripts/<video>.json` y
`moments/<video>.json` de una corrida anterior (por ejemplo, después de
actualizar `src/clip.py`), podés regenerar solo los clips finales:

```bash
python -m src.clip \
  --video input/<video>.mp4 \
  --moments moments/<video>.json \
  --transcript transcripts/<video>.json
```

`--transcript` es opcional: sin él se sigue aplicando el crop vertical y la
división en partes, pero sin subtítulos. Si el transcript es de antes de
esta actualización (sin timestamps por palabra), los subtítulos caen a
mostrar la frase completa del segmento en vez de resaltar palabra por
palabra — para tener el karaoke real hay que volver a transcribir.

El nombre de la carpeta sale del título real del video guardado en
`metadata/<id>.json`. Si descargaste ese video antes de que existiera esta
funcionalidad, no hay metadata todavía y la carpeta cae al id de YouTube en
vez del título; para completarla sin volver a descargar el video:

```bash
python -m src.download --url "https://www.youtube.com/watch?v=<id>" --metadata-only
```

Esto solo pide el título a yt-dlp (no descarga video ni audio) y lo guarda
en `metadata/<id>.json`, listo para que `src.clip` lo use la próxima vez.

### Watermark sobre clips ya generados

`src/watermark.py` es un script aparte del pipeline: no reprocesa nada
desde el video original, solo quema un texto pequeño y permanente
("YT: ampeterby7" por defecto) en la esquina inferior derecha de clips
`.mp4` que ya existen, con margen respecto al borde para no chocar con la
UI de TikTok. Corre en batch sobre toda una carpeta:

```bash
python -m src.watermark --folder "output/<título del video>"

# En Windows, sobre una carpeta de Google Drive Desktop:
python -m src.watermark --folder "G:\Mi unidad\ZaleteClips\<carpeta del video>"
```

Por default genera una copia nueva `<nombre>_wm.mp4` junto a cada original
(no destructivo) y salta los `.mp4` que ya terminan en `_wm` si corrés el
comando de nuevo. Para sobreescribir los originales en lugar de crear
copias, agregá `--overwrite` (renderiza a un archivo temporal y recién
reemplaza el original si ffmpeg termina bien, para no perder el clip si
algo falla a mitad de camino). Otras opciones: `--text "..."` para cambiar
el texto, `--font-file /ruta/a/fuente.ttf` si la autodetección de fuente
(Arial en Windows, DejaVu/Liberation en Linux, Arial en macOS) no encuentra
ninguna instalada en tu máquina.

Por ahora es solo texto — si más adelante querés el logo real de YouTube
superpuesto, se puede agregar un filtro `overlay` con un PNG.

## Formato vertical, división en partes y subtítulos

- **Vertical 9:16** (`src/vertical.py`): crop centrado (sin face-tracking)
  a 1080x1920, aplicado a todos los clips.
- **División en partes** (`src/clip.py`): momentos de más de 90s (
  `SPLIT_THRESHOLD`) se dividen en partes de ~60-70s (`PART_MIN_DURATION`/
  `PART_MAX_DURATION`). Si el transcript tiene timestamps por palabra, el
  corte entre partes se ajusta al fin de la palabra más cercana (±5s,
  `SNAP_WINDOW`) en vez de cortar a mitad de frase.
- **Subtítulos karaoke** (`src/subtitles.py`): una palabra a la vez,
  centrada, con margen suficiente del borde inferior para no quedar tapada
  por la UI de TikTok (que cubre ~20% inferior de la pantalla). Incluye un
  título al inicio (`hook_title` del momento, o "PARTE N - Sigue: ..." para
  partes 2+) y, si no es la última parte, un aviso "PARTE N+1 PRÓXIMAMENTE"
  en los últimos ~2.5s. Estas constantes están hardcodeadas (no son env
  vars) porque son parámetros de diseño del formato, no de infraestructura.
- **Carpeta y nombres de archivo** (`src/clip.py`, `src/naming.py`): cada
  video procesado crea su propia subcarpeta dentro de `OUTPUT_DIR`, nombrada
  con el título real del video (sacado de `metadata/<id>.json`, ver arriba).
  Cada clip se nombra con el `hook_title` del momento —
  `<hook_title>.mp4`, o `<hook_title> PARTE N.mp4` para partes— usando
  siempre el `hook_title` original del momento, no un texto distinto por
  parte. `src.naming.sanitize_filename()` quita los caracteres invalidos en
  nombres de Windows (`\ / : * ? " < > |`) para que la misma carpeta sirva
  si después se sincroniza a Windows (ej. Google Drive Desktop).

## Configuración

Todo se configura mediante variables de entorno (ver `.env.example`):

| Variable | Default | Descripción |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(requerida)* | API key de Anthropic |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5` | Modelo usado para detectar momentos |
| `PROJECT_ROOT` | raíz del repo | Raíz para resolver `input/`, `transcripts/`, etc. |
| `INPUT_DIR`, `TRANSCRIPTS_DIR`, `MOMENTS_DIR`, `METADATA_DIR` | subcarpetas de `PROJECT_ROOT` | Override individual de cada carpeta |
| `OUTPUT_DIR` | `output/` del repo | Dónde se guardan los clips finales. Acepta cualquier ruta absoluta fuera del repo, ej. `G:\Mi unidad\ZaleteClips` (una carpeta de Google Drive Desktop en Windows) |
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
