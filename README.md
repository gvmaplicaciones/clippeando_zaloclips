# clip-pipeline

Pipeline para convertir VODs largos en clips cortos listos para TikTok:
descarga el video, lo transcribe con Whisper (con timestamps por palabra),
detecta los mejores momentos con Claude (Anthropic) — clasificando solo el
tipo de contenido (invitados, viaje, podcast, narrativo de streamer) para
aplicar el criterio de selección correcto, sin que haga falta indicarlo — y
genera los clips finales en formato vertical 9:16 con subtítulos karaoke
quemados. Los momentos largos (>90s) se dividen automáticamente en varias
partes con un aviso de cliffhanger al final de cada una. Pensado para
correr con un solo comando de punta a punta (ej. `python -m src.pipeline
--file input/video.mp4`) sin marca de agua ni pasos intermedios.

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
  prompts/        # system prompts por tipo de contenido (invitado.txt, viajes.txt, podcast.txt)
  app.py          # interfaz web (Streamlit) - reutiliza las funciones de src/, no duplica logica
  src/            # código del pipeline
    clip.py        # orquesta: division en partes -> crop vertical -> filtro -> subtitulos
    vertical.py    # crop centrado a 9:16 (1080x1920)
    video_filters.py  # filtros visuales opcionales (espejo, vintage, TV a rayas, etc.)
    subtitles.py   # genera y quema el .ass de subtitulos karaoke
    naming.py      # sanitiza nombres de archivo/carpeta para que sean validos en Windows
    detect_moments.py  # deteccion de momentos: clasifica el tipo de contenido y aplica su criterio
    content_types.py   # carga prompts/*.txt por tipo de contenido
    watermark.py   # script independiente: quema logo+texto sobre clips ya generados (no forma parte del pipeline)
  notebooks/
    pipeline_colab.ipynb   # notebook para correr todo en Google Colab
  requirements.txt
  .env.example
```

`campaigns.json`, `src/campaigns.py`, `src/watermarks.py` y `src/add_campaign.py`
siguen existiendo en el repo (de una iteración anterior donde el pipeline sí
aplicaba marca de agua por campaña) pero **ya no los usa** `src.pipeline` ni
`src.clip` — el pipeline actual nunca aplica watermark. Quedan ahí por si en
el futuro se vuelve a necesitar esa integración; `src/watermark.py` (marca de
agua manual sobre clips ya generados) sigue siendo independiente de todo esto.

## Setup local / VPS

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# editar .env y completar ANTHROPIC_API_KEY

# ffmpeg debe estar instalado en el sistema (apt install ffmpeg / brew install ffmpeg)
```

## Interfaz web

Para no depender de la terminal, `app.py` (Streamlit) da una pantalla
simple para generar clips. Es solo una interfaz: no reimplementa nada,
llama directo a `src.pipeline.run_pipeline()`,
`src.content_types.list_available_content_types()`, etc. — las mismas
funciones que usan los comandos de línea de arriba.

```bash
streamlit run app.py
```

Abre automáticamente `http://localhost:8501` en el navegador (puerto
default de Streamlit; si está ocupado, corré `streamlit run app.py --server.port 8502`
o el que prefieras).

La pantalla principal ("Generar clips") tiene:

- **Origen del video**: una URL de YouTube o un archivo para subir (uno de
  los dos, no ambos a la vez — la interfaz avisa si falta o sobra alguno).
- **Tipo de contenido**: dropdown con "Automático (el modelo clasifica
  solo)" como primera opción, y los tipos de `prompts/*.txt` debajo para
  forzar uno a mano (ver "Tipos de contenido" más abajo).
- **Filtro de video** (opcional): espejo, vintage, TV a rayas, blanco y
  negro o cinemático (ver `--filter` más abajo), con "Ningún filtro" como
  primera opción.
- **Nombre del video** (opcional): si lo completás, se usa como nombre de
  la carpeta de salida en vez del título automático de yt-dlp.
- **Máximo de clips** (opcional, vacío = sin límite): si lo completás, se
  procesan solo los N momentos de mayor score (ver `--max-clips` más abajo).
- Botón **Generar clips**: corre el pipeline completo (descarga →
  transcripción → detección de momentos → generación de clips) y muestra
  el progreso en vivo — es el mismo texto que ya imprime cada paso por
  consola, capturado y volcado a la interfaz a medida que llega, sin
  rehacer el logging. Los clips salen siempre sin marca de agua.

Al terminar, lista cada clip generado con su nombre y duración (vía
`ffprobe`) y la carpeta final donde quedaron. Si algo falla en cualquier
paso (descarga, transcripción, API de Claude, ffmpeg), el error se
muestra en la interfaz con el traceback completo, sin que la app se
cierre — podés corregir y volver a intentar sin reiniciar nada.

La pestaña **Tipos de contenido** lista los prompts disponibles en
`prompts/`, con el rol de cada uno y el prompt completo en un desplegable.

## Uso

```bash
# el caso simple: un solo comando, todo automatico, sin marca de agua
python -m src.pipeline --file input/mi_video.mp4
python -m src.pipeline --url "https://..."

# forzando el tipo de contenido en vez de dejar que el modelo clasifique solo
# (ver "Tipos de contenido" mas abajo)
python -m src.pipeline --url "https://..." --content-type invitado

# forzando el nombre de la carpeta de salida en vez del titulo automatico de yt-dlp
python -m src.pipeline --url "https://..." --video-title "Nombre que yo elijo"

# limitando cuantos momentos se procesan (te quedas con los N de mayor score)
python -m src.pipeline --url "https://..." --max-clips 3

# aplicando un filtro visual a todo el clip (ver "Filtros de video" mas abajo)
python -m src.pipeline --url "https://..." --filter vintage
```

`--max-clips N` se aplica a **momentos**, no a archivos finales: los
momentos detectados se ordenan por score descendente y se descartan todos
salvo los N mejores ANTES de generar nada (no se gasta tiempo de ffmpeg en
los que no se van a usar). Si uno de los N momentos elegidos es largo y se
divide en varias partes (`PARTE 1`/`PARTE 2`, ver más abajo), esas partes
cuentan como un solo momento para el límite — podés terminar con más de N
archivos `.mp4` en total, pero siempre de como mucho N momentos distintos.
Sin `--max-clips`, sin límite (como hasta ahora).

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

(`src.clip` no llama a `src.detect_moments`, así que no tiene `--content-type`
— ese flag solo aplica en `src.pipeline`, que es el que detecta los momentos.)

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

### Marca de agua (opcional, aparte del pipeline)

El pipeline (`src.pipeline`/`src.clip`) **nunca** aplica marca de agua —
los clips siempre salen limpios. Si en algún momento querés marcarlos,
`src/watermark.py` es un script aparte que opera sobre clips **ya
generados**, sin volver a correr el pipeline ni tocar nada de lo de
arriba. El watermark combina dos elementos, presentes de forma estática
durante el 100% de la duración del clip (sin animación), en la esquina
inferior derecha con margen respecto al borde para no chocar con la UI
de TikTok:

- Un logo (PNG con transparencia), escalado a ~50px de alto manteniendo
  su proporción original.
- Un texto (con contorno y sombra para leerse sobre cualquier fondo)
  inmediatamente a la izquierda del logo, centrado verticalmente con él.

Corre en batch sobre toda una carpeta de clips ya generados:

```bash
python -m src.watermark --folder "output/<título del video>"

# En Windows, sobre una carpeta de Google Drive Desktop:
python -m src.watermark --folder "G:\Mi unidad\ZaleteClips\<carpeta del video>"

# Para probar primero en un solo clip antes de correr el batch completo:
python -m src.watermark --folder "output/<título del video>" --limit 1
```

Por default genera una copia nueva `<nombre>_wm.mp4` junto a cada original
(no destructivo) y salta los `.mp4` que ya terminan en `_wm` si corrés el
comando de nuevo. Para sobreescribir los originales en lugar de crear
copias, agregá `--overwrite` (renderiza a un archivo temporal y recién
reemplaza el original si ffmpeg termina bien, para no perder el clip si
algo falla a mitad de camino). Otras opciones: `--text "..."` para cambiar
el texto, `--logo-file /ruta/a/logo.png` para usar otra imagen,
`--font-file /ruta/a/fuente.ttf` si la autodetección de fuente (Arial en
Windows, DejaVu/Liberation en Linux, Arial en macOS) no encuentra ninguna
instalada en tu máquina.

Al arrancar, el script chequea que el PNG del logo tenga transparencia
real (no solo modo RGBA — también que existan píxeles con alpha < 255) y
avisa si no la tiene, antes de aplicar el watermark a ningún clip.

**Mensaje de call-to-action (opcional)**: `--cta-text` agrega un mensaje
chico, en un par de líneas, justo arriba del logo+nombre de canal, para
redirigir al público (ej. "Puedes ver el video completo en" — el texto se
parte solo en líneas cortas, no hace falta escribirlo ya partido):

```bash
# usa el texto default ("Puedes ver el video completo en")
python -m src.watermark --folder "output/<título>" --cta-text

# o un mensaje propio
python -m src.watermark --folder "output/<título>" --cta-text "Mirá el video completo en"
```

Sin `--cta-text`, no se agrega nada (como hasta ahora).

> **Nota:** `campaigns.json`, `src/campaigns.py` y `src/add_campaign.py`
> (perfiles con marca + `allow_split` + estilo de subtítulos por campaña,
> con un ID numérico) quedan en el repo de una iteración anterior, pero
> `src.pipeline`/`src.clip` ya no los usan — el pipeline actual nunca aplica
> marca de agua ni varía el estilo de subtítulos por campaña. Si en el
> futuro hace falta esa integración de nuevo, esos archivos siguen ahí como
> punto de partida.

### Filtros de video

`--filter <nombre>` aplica un filtro visual sobre todo el clip. Se aplica
DESPUÉS del crop vertical pero ANTES de quemar subtítulos
(`src/clip.py`, `src/video_filters.py`), para que el texto siempre quede
nítido encima del filtro, no filtrado también:

```bash
python -m src.pipeline --url "..." --filter vintage
python -m src.clip --video ... --moments ... --filter tv_scanlines
python -m src.clip --list-filters
```

| Nombre | Efecto |
|---|---|
| `mirror` | Espejo (flip horizontal) |
| `vintage` | Colores cálidos desaturados + viñeta |
| `tv_scanlines` | Líneas horizontales estilo CRT/TV vieja |
| `grayscale` | Blanco y negro |
| `cinematic` | Alto contraste, colores más punchy |

Sin `--filter`, sin cambios (como hasta ahora). Un nombre de filtro que no
existe falla con un mensaje claro listando los nombres válidos, antes de
generar ningún clip.

### Tipos de contenido

`src.detect_moments` (llamado por `src.pipeline`) elige el criterio de
selección de momentos según el tipo de contenido del video. Por default
**no hace falta indicarlo**: el modelo lee el transcript y clasifica solo,
en la misma llamada a la API, a cuál de estas 4 categorías pertenece, y
aplica el criterio correspondiente:

- **Entretenimiento con invitados** (formato Ibai, Sidemen, retos,
  "adivina quién", debates, dinámicas sociales entre varias personas).
- **Viaje/aventura** (país, presupuesto, hoteles, comida local, choque
  cultural).
- **Podcast/entrevista** (una persona entrevistando a otra, sin dinámica
  de grupo ni componente de viaje).
- **Narrativo de un solo streamer** (sin invitados, sin viaje, sin
  formato entrevista — ej. gaming, fútbol, reacción en solitario).

Si preferís forzar uno a mano en vez de dejar que el modelo clasifique,
`--content-type <nombre>` carga ese criterio específico de
`prompts/<nombre>.txt` en vez de la clasificación automática:

```bash
python -m src.pipeline --url "..." --content-type invitado
python -m src.pipeline --url "..." --content-type viajes
python -m src.pipeline --url "..." --content-type podcast
python -m src.pipeline --list-content-types
```

Agregar un tipo de contenido nuevo es agregar un archivo
`prompts/<nombre>.txt` (system prompt completo: rol, criterios de
selección, formato de salida) — no hace falta tocar código
(`src/content_types.py` los descubre solos). Un `--content-type` que no
existe falla con un mensaje claro listando los nombres válidos, antes de
descargar/transcribir nada. La deduplicación de momentos solapados
(`dedupe_highlights()`) y la validación de duración (20-180s) se siguen
aplicando igual sin importar qué prompt se haya usado — no cambiaron.

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
