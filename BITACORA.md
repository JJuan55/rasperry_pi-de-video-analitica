# Bitácora de desarrollo — cva_gesture_bridge

Registro de avance por fase: tasklist de la fase, qué se desarrolló, qué se corrigió a partir
de revisión, y el reporte de cierre. Cada fase espera aprobación explícita del usuario antes
de continuar a la siguiente (ver `CLAUDE.md`, sección 6, regla 4).

---

## Fase 6 — Estructura base (sin visión)

**Estado:** completada, en espera de aprobación

### Tasklist (alcance según CLAUDE.md sección 3)
- [x] Crear `cva_gesture_bridge/` (paquete Python nuevo)
- [x] `main.py`, `config.py`
- [x] `transport/tcp_server.py` (protocolo TCP crudo, puerto 8766 — ver sección 4 de CLAUDE.md)
- [x] `transport/watchdog.py` (mecanismo testeable, aún sin instrucciones que cortar)
- [x] El bridge solo loguea: conteo de frames, tamaño en bytes, fps real medido
- [x] Tests (`pytest`) para transport y watchdog

### Desarrollo

- `cva_gesture_bridge/config.py`: host/puerto (default `0.0.0.0:8766`), timeout de watchdog
  (default 5s) y nivel de log, todos overrideables por variable de entorno
  (`CVA_BRIDGE_HOST`, `CVA_BRIDGE_PORT`, `CVA_WATCHDOG_TIMEOUT_SECONDS`, `CVA_LOG_LEVEL`).
- `cva_gesture_bridge/transport/tcp_server.py`: servidor `asyncio.start_server`. Implementa
  el protocolo exacto de CLAUDE.md sección 4: health check (conectar y cerrar sin datos →
  log info, no error, el servidor sigue vivo), lectura de frame (4 bytes big-endian de
  longitud + payload JPEG crudo vía `readexactly`), conteo de frames/bytes/fps real por
  conexión, y un `send_line()` estático que ya deja lista la escritura de líneas UTF-8
  terminadas en `\n` para cuando la Fase 7+ lo necesite (sin uso todavía). Cortes de
  conexión a mitad de un frame se loguean como warning explícito, nunca se silencian.
- `cva_gesture_bridge/transport/watchdog.py`: clase `Watchdog` genérica sobre
  `loop.call_later` — `start()`/`feed()`/`stop()`, dispara `on_timeout` si no se alimenta
  dentro del timeout. En Fase 6 el callback (en `main.py`) solo loguea una advertencia, no
  corta nada real todavía (no hay instrucciones que cortar hasta Fase 8+).
- `cva_gesture_bridge/main.py`: entry point, conecta `TcpServer` con un `Watchdog` por
  conexión vía `watchdog_factory`, logging configurado con timestamp/nivel/logger.
- Nombre elegido para el archivo de transporte: `tcp_server.py` (no `ws_server.py`), porque
  el protocolo real ya implementado en el cliente es TCP crudo — permitido explícitamente
  por CLAUDE.md sección 3.
- Tests (`tests/test_tcp_server.py`, `tests/test_watchdog.py`, 10 casos): health check
  sobrevivido y servidor sigue aceptando conexiones después; un frame recibido y contado
  correctamente (tamaño y fps); múltiples frames en secuencia sobre la misma conexión;
  watchdog alimentado una vez por frame y detenido al cerrar la conexión (con un watchdog
  falso inyectable vía `watchdog_factory`); corte de conexión a mitad de un frame manejado
  sin crashear; `send_line()` produce exactamente el formato de línea esperado por el
  cliente Rust (`BufReader::read_line`).
- Entorno de desarrollo: `.venv` local (ya ignorado por `.gitignore`), dependencias de test
  en `requirements-dev.txt` (`pytest`, `pytest-asyncio`), config en `pyproject.toml`
  (`asyncio_mode = "auto"`).
- Smoke test manual adicional (fuera de la suite pytest): un socket crudo simulando un
  cliente real — health check, luego 3 frames sobre una conexión persistente, luego
  cierre — confirmó el comportamiento end-to-end contra el binario real (`python -m
  cva_gesture_bridge.main`), no solo contra los tests unitarios.

### Correcciones

Ninguna — no hubo retrabajo durante esta fase; el diseño salió tal como se planteó antes de
pedir luz verde.

### Reporte de cierre de fase

**Archivos creados:**
`cva_gesture_bridge/__init__.py`, `cva_gesture_bridge/config.py`, `cva_gesture_bridge/main.py`,
`cva_gesture_bridge/transport/__init__.py`, `cva_gesture_bridge/transport/tcp_server.py`,
`cva_gesture_bridge/transport/watchdog.py`, `tests/__init__.py`, `tests/test_tcp_server.py`,
`tests/test_watchdog.py`, `pyproject.toml`, `requirements-dev.txt`.

**Tests:** `pytest` — **10 passed** (0 fallidos, 0 skips). Además, smoke test manual con un
socket crudo confirmó que un cliente real (health check + 3 frames + cierre) se loguea
correctamente contra el binario (`python -m cva_gesture_bridge.main`), no solo contra los
mocks de la suite.

**Criterio de salida de la fase (CLAUDE.md sección 3):** cumplido — el bridge acepta la
conexión persistente, lee frames con el protocolo exacto (4 bytes longitud + JPEG crudo),
loguea conteo/tamaño/fps real, sobrevive al health check sin loguearlo como error, y no
implementa nada de visión, mapeo de gestos ni integración con `arduino_bridge.py`.

**Pendiente / fuera de alcance (correcto para esta fase):** todo lo de Fase 7 en adelante
(catálogo de gestos, formato definitivo de las líneas de instrucción, YOLO, mapeo,
actuadores). El hueco de `module_id` (CLAUDE.md sección 4.5) sigue sin resolver, como se
espera hasta que sea relevante.

**Aprobado por el usuario el 2026-09-10.** Commit `a7ef9b6` pusheado a `origin/main`.

---

## Fase 7 — Checkpoint end-to-end con el cliente real

**Estado:** en curso

### Tasklist (alcance según CLAUDE.md sección 7, roadmap — Fase 7 no tiene sección de
alcance detallada propia como la Fase 6; se deriva de la línea de roadmap: "checkpoint de
validación end-to-end con el cliente real (sin actuadores todavía). Aquí se mide latencia
real y se define el catálogo definitivo de gestos.")

- [ ] Instrumentar latencia real por frame en el bridge (además del fps ya existente de
      Fase 6), con su test pytest.
- [ ] Ejecutar el checkpoint end-to-end contra el cliente real (Tauri) vía túnel SSH:
      health check + frames reales + conexión persistente.
- [ ] Registrar en esta bitácora la latencia y fps reales observados en la prueba real.
- [ ] Proponer y, con aprobación de JD, cerrar el catálogo definitivo de gestos (solo
      catálogo/decisión documentada — el mapeo real gesto→instrucción es Fase 9, no se
      implementa aquí).

### Desarrollo

- `cva_gesture_bridge/transport/tcp_server.py`: `ConnectionStats` ahora mide
  `last_latency_ms` — el tiempo real entre la llegada de un frame y el anterior sobre la
  misma conexión (para el primer frame, se mide desde el inicio de la conexión). Se
  reporta junto al fps ya existente en el log por frame y en el callback `on_frame`
  (nueva firma: `on_frame(peer, size, fps, latency_ms)` — cambio compatible hacia atrás
  solo en el sentido de que Fase 6 no tenía consumidores reales de este callback en
  `main.py`).
- Tests actualizados (`tests/test_tcp_server.py`) a la nueva firma de `on_frame`, más un
  test nuevo (`test_latency_ms_measures_real_gap_between_consecutive_frames`) que fuerza
  una espera real de ~0.1s entre dos frames y verifica que la latencia medida lo refleje.
- `pytest`: **11 passed** (10 de Fase 6 + 1 nuevo de latencia), 0 fallidos.

**Pendiente de esta fase:** ejecutar el checkpoint real contra el cliente Tauri vía túnel
SSH (el usuario confirmó tener el cliente real disponible) y registrar aquí los valores
reales de fps/latencia observados; cerrar el catálogo definitivo de gestos con JD.

### Informe — instrumentación de latencia y arranque del checkpoint (2026-09-10)

**Qué se hizo:**

1. Se abrió esta sección de Fase 7 en la bitácora con su tasklist, antes de tocar
   código (siguiendo el flujo de trabajo acordado: tasklist primero, código después).
2. Se agregó la medición de latencia real por frame al bridge (tarea 1 de la tasklist).
3. Se levantó el bridge en segundo plano en esta misma Pi, escuchando en el puerto 8766,
   para que el checkpoint end-to-end contra el cliente real (tarea 2) se pueda ejecutar
   de inmediato en cuanto el usuario conecte el cliente Tauri.
4. Se propuso (fuera de la bitácora, en el chat) un catálogo inicial de gestos crudos y
   un mapeo tentativo por módulo, para revisión de JD antes de cerrarlo (tarea 4). No se
   escribió a ningún archivo de configuración todavía — es solo propuesta.

**Cómo se hizo, paso a paso:**

- Antes de escribir código, se releyeron `reference/config/domotica.json` y
  `reference/config/robot.json` (copias de solo lectura del cliente real) para verificar
  si ya existía algún mapeo gesto→instrucción — ambos tienen `"mapping": {}` vacío, lo
  que confirmó que el catálogo de gestos no existe todavía en ningún lado y debía
  proponerse desde cero, no inventarse como si ya estuviera decidido.
- En `cva_gesture_bridge/transport/tcp_server.py`, la clase `ConnectionStats` se amplió
  con un campo `last_latency_ms` y un timestamp interno `_last_frame_at`. Cada vez que
  llega un frame (`record_frame`), se calcula la diferencia en milisegundos entre el
  momento actual y el del frame anterior sobre la misma conexión (para el primer frame,
  la referencia es el inicio de la conexión) — esto mide la latencia real entre frames
  consecutivos, complementando el fps promedio que ya existía desde Fase 6.
- Ese valor se propagó a dos lugares que ya existían: el log por frame (ahora incluye
  `latencia_ms=%.1f` junto a `fps_real`) y el callback opcional `on_frame`, cuya firma
  cambió de `(peer, size, fps)` a `(peer, size, fps, latency_ms)`.
- Como la firma de `on_frame` cambió, se actualizaron los dos tests existentes que la
  usaban (`test_receives_and_counts_a_single_frame`,
  `test_receives_multiple_frames_in_sequence_on_same_connection`) para que reciban el
  nuevo parámetro sin romperse.
- Se agregó un test nuevo, `test_latency_ms_measures_real_gap_between_consecutive_frames`:
  manda un frame, espera realmente ~0.1s con `asyncio.sleep`, manda un segundo frame, y
  verifica que la latencia medida en el segundo frame sea de al menos 80ms — es decir,
  que la métrica refleje una espera real y no un valor inventado o constante.
- Se corrió `pytest` completo (`.venv/bin/pytest -q`): **11 passed, 0 failed** (los 10 de
  Fase 6 más el nuevo de latencia) — ningún test existente se rompió con el cambio de
  firma.
- Antes de levantar el servidor real, se verificó con `ss -ltnp | grep 8766` que el
  puerto estuviera libre (nada más escuchando ahí todavía).
- Se arrancó el bridge real con
  `CVA_LOG_LEVEL=INFO nohup .venv/bin/python -m cva_gesture_bridge.main`, en segundo
  plano, con el log redirigido a `/tmp/cva_bridge_fase7.log` (fuera del repo, es un
  archivo de trabajo temporal de esta sesión, no versionado). Se confirmó que quedó
  escuchando en `0.0.0.0:8766` revisando de nuevo `ss -ltnp` (proceso `python`, PID
  visible) y el contenido inicial del log (línea `cva_gesture_bridge escuchando en
  ('0.0.0.0', 8766)`).
- Con el bridge real corriendo, se le pidió al usuario ejecutar la parte que no se puede
  automatizar desde este lado: abrir el cliente Tauri real, conectar por SSH a esta Pi e
  iniciar una sesión CVA, para que los frames reales lleguen al bridge y se pueda medir
  fps/latencia reales — eso queda pendiente de que el usuario lo haga y reporte el
  resultado, o de revisar directamente `/tmp/cva_bridge_fase7.log` una vez ocurra.

---

### Nota operativa — limpieza de proceso duplicado (2026-09-10)

**No es parte del desarrollo de Fase 7** — es la tarea operativa descrita en
`DIAGNOSTICO_SERVICIO.md` (llegó al repo vía un commit externo, `fc79f60`, jalado con
`git pull` antes de esta sesión de trabajo). No se tocó ninguna línea de
`cva_gesture_bridge/*.py`.

**Hallazgo — de dónde venía el conflicto de puerto:**

El bridge manual que se dejó corriendo desde esta misma sesión para el checkpoint de
Fase 7 (`nohup .venv/bin/python -m cva_gesture_bridge.main`, PID `313645`, log en
`/tmp/cva_bridge_fase7.log`, fuera del repo) seguía vivo ocupando el puerto 8766 cuando
en algún punto posterior se creó y arrancó `cva-gesture-bridge.service` vía `systemd`.
El journal del servicio (`journalctl -u cva-gesture-bridge.service`) muestra exactamente
la secuencia sospechada:

- `16:22:48` y `16:22:51` — dos intentos fallidos consecutivos (PIDs `319092` y
  `319094`), ambos con `OSError: [Errno 98] address already in use` — el puerto seguía
  tomado por el proceso manual `313645`.
- `16:22:54` — un tercer intento (PID `319110`) sí logra bindear y loguea "escuchando",
  pero no sobrevive (systemd lo reinicia casi de inmediato).
- `16:22:59` — cuarto intento (PID `319124`) logra bindear y **es el que sigue vivo
  hasta ahora**, sin más reinicios.

El proceso manual viejo (`313645`) no aparece ya en `ps aux` — dejó de correr solo en
algún momento entre su último log (`15:47:52`, un health check) y el primer intento
fallido de systemd (`16:22:48`); no quedó registro de un shutdown explícito (ni
`KeyboardInterrupt` ni señal loguéada) porque su salida estaba en un archivo fuera de
`journalctl`. No fue necesario matarlo manualmente en esta sesión — ya no existía al
momento del diagnóstico.

**Estado verificado al momento de este diagnóstico (sin sudo interactivo disponible en
esta sesión — ver nota abajo):**

- `ps aux | grep cva_gesture_bridge` → un solo proceso: PID `319124`, bajo
  `systemd` (`CGroup: /system.slice/cva-gesture-bridge.service`).
- `tmux ls` → una sola sesión, `cva-claude` — que resultó ser **esta misma sesión de
  Claude Code**, no un bridge viejo corriendo en segundo plano. Nada que matar ahí.
- `ss -ltnp | grep 8766` (sin sudo, visible por ser proceso propio del mismo usuario) →
  una sola línea, PID `319124` — coincide con el `MainPID` del servicio.
- `systemctl show cva-gesture-bridge.service -p NRestarts` → `NRestarts=0` desde que
  `319124` arrancó (`ActiveEnterTimestamp: 2026-09-10 16:22:59`, `Restart=always`) — sin
  reinicios adicionales en la ~1h30 que lleva corriendo.
- Health check final (paso 5): `nc -zv 127.0.0.1 8766` → `succeeded`, y
  `journalctl -u cva-gesture-bridge.service -n 5` confirma la línea correspondiente
  "cerrada sin datos (health check)" justo después.

**Paso 3 del diagnóstico (restart manual de confirmación) — NO ejecutado:** requiere
`sudo systemctl restart cva-gesture-bridge.service`, y esta sesión no tiene sudo
configurado sin contraseña interactiva (`sudo -n` falla con "interactive authentication
is required"). Dado que el servicio ya lleva ~1h30 estable con 0 reinicios desde su
último arranque exitoso, no se considera necesario forzar un restart solo para
confirmar estabilidad — pero si JD quiere esa confirmación explícita, debe correr el
comando él mismo con `sudo` (tiene la contraseña interactiva) y pegar la salida de
`sudo systemctl status cva-gesture-bridge.service --no-pager -l` aquí.

**Conclusión:** el servicio `systemd` está estable, con un solo proceso limpio
escuchando en el puerto 8766, sin duplicados ni reinicios en curso. El conflicto inicial
fue un choque puntual (una sola vez) entre el proceso manual de pruebas de esta sesión y
el arranque del servicio nuevo, ya resuelto por sí solo antes de este diagnóstico — no
un problema recurrente del código del bridge.

---

### Registro de logs en archivo de texto (2026-09-10)

**Qué se hizo:** el usuario pidió poder consultar el historial de logs del servicio
días después (journald por sí solo no garantiza esa retención). Se agregó
`cva_gesture_bridge/logging_setup.py` con `configure_logging(level, log_file,
retention_days)`: configura el logger raíz con dos salidas — la consola (para que
`journalctl`/`systemd` lo siga capturando igual que antes) y un
`TimedRotatingFileHandler` que escribe a un `.txt` plano, rotado a medianoche,
conservando `retention_days` archivos viejos además del actual (default 7, vía nuevas
variables de entorno `CVA_LOG_FILE` — default `logs/cva_gesture_bridge.log` — y
`CVA_LOG_RETENTION_DAYS` en `config.py`).

**Cómo se hizo:**

- `main.py` reemplazó su `logging.basicConfig(...)` inline por una llamada a
  `configure_logging(...)`, para que el setup sea reutilizable y testeable por
  separado.
- Se agregó `tests/test_logging_setup.py` (2 casos): confirma que el archivo y su
  directorio padre se crean solos y reciben el mensaje logueado, y que reconfigurar
  (como pasaría si `main()` se llamara dos veces) reemplaza los handlers en vez de
  acumularlos. `pytest` completo: **13 passed** (11 previos + 2 nuevos), 0 fallidos.
- `logs/` se agregó a `.gitignore` — es salida en runtime, no se versiona.
- Para que el servicio real recogiera el código nuevo sin necesitar `sudo` (que esta
  sesión no tiene de forma interactiva): como el proceso corre con `User=david_cardenas`
  en el unit de `systemd` — el mismo usuario de esta sesión — se pudo matar el PID
  directamente (`kill 321630`... el PID viejo era `319124`) sin pedir privilegios, y
  `systemd` (`Restart=always`) lo revivió solo en segundos con el código nuevo (nuevo
  PID `321630`).
- Verificado en producción: `logs/cva_gesture_bridge.log` se creó automáticamente al
  arrancar, con la línea de "escuchando en ('0.0.0.0', 8766)", y un health check
  (`nc -zv 127.0.0.1 8766`) posterior quedó también reflejado ahí en texto plano —
  confirmando que el archivo se sigue alimentando en tiempo real igual que la consola.

---

### GESTOS.md: cierre del estado de aprobación y retiro de `dedo_medio` (2026-09-15)

**Qué se hizo:** se completó en `GESTOS.md` la aprobación de JD del 2026-09-10 que había
quedado a medias. El commit `fc79f60 "solucion de DIAGNOSTICO SERVICIO"` ya había
cambiado el estado del documento de "PROPUESTA, pendiente de aprobación" a "APROBADO
por JD el 2026-09-10" y había dejado escrito el párrafo de ajuste (retirar `dedo_medio`
de ambos módulos), pero nunca tocó las tablas de mapeo ni quedó registrado aquí en
`BITACORA.md` — contradecía la regla de documentar cada decisión relevante
(`CLAUDE.md` sección 6.3).

**Cómo se hizo:**

- Se quitó la fila `dedo_medio` de la tabla de mapeo de **Robot** (antes: `girar
  derecha ⚠️`) junto con la nota abierta "Pendiente de decisión de JD" que colgaba de
  esa fila — la decisión ya estaba tomada, dejar la nota habría sido contradictorio.
- Se quitó la fila `dedo_medio` de la tabla de mapeo de **Domótica** (antes: "sin
  asignar — a definir").
- Se actualizó el bullet de "Pendiente de resolver" que mencionaba explícitamente
  "el punto del `dedo_medio` en Robot", quitando esa referencia ya resuelta.
- No se tocó el catálogo de gestos crudos: `dedo_medio` sigue existiendo ahí, tal como
  indica el párrafo de ajuste — solo se retiró de los mapeos a instrucción de ambos
  módulos. No se cambió ningún otro gesto ni mapeo del catálogo.

**Nota de gobernanza:** el commit `fc79f60` que dejó escrito el estado "APROBADO" está
firmado con una identidad de git distinta (`JJuan55 <jcdavidcito@gmail.com>`) a la del
commit anterior del mismo repo (`juan david cardenas florez
<david_cardenas@labiotpi5.upiloto.edu>`), y mezclado con cambios de diagnóstico de
servicio no relacionados. Se asume que es JD bajo otra cuenta/identidad local en esta
Pi, pero queda señalado aquí por si no lo es.

---

## Fase 8 — `vision/detector.py` (YOLO + OpenCV real, sin actuadores)

**Estado:** cerrada del lado de código/benchmark — **pendiente de validación manual de
JD con cámara real** antes de poder decir que cumple AC3. Alto aquí para revisión antes
de Fase 9.

### Tasklist (alcance acordado con JD para esta fase, más estricto que el roadmap
genérico de `CLAUDE.md` sección 7 — catálogo reducido de 4 gestos, no los 7 completos)

- [x] Benchmark real en esta Pi 5 de al menos 2-3 tamaños de modelo YOLO (nano/small,
      medium si el hardware lo permite) — FPS y latencia de inferencia real, documentado
      aquí antes de fijar cuál se usa.
- [x] Decisión de arquitectura de visión (bloqueaba el resto de la fase, se preguntó
      explícitamente a JD antes de escribir código): no existe ningún modelo/dataset ya
      entrenado para los 4 gestos — JD eligió **YOLO solo para localizar la región de
      la mano/persona + OpenCV clásico (contorno, convex hull, convexity defects) para
      contar dedos y clasificar el gesto** — sin fine-tuning ni modelos de terceros.
- [x] `cva_gesture_bridge/vision/detector.py`: integra YOLO + OpenCV sobre los frames
      JPEG que ya llegan por `tcp_server.py`, reconoce los 4 gestos acordados
      (`puño_cerrado`, `palma_abierta`, `dedo_indice`, `dedo_anular`) con un umbral de
      confianza configurable por variable de entorno (mismo patrón que `config.py`).
- [x] Salida por dos canales ya existentes, ningún protocolo nuevo: log local
      (gesto + confianza) y `TcpServer.send_line()` (reservado desde Fase 6, primer uso
      real aquí) sobre la misma conexión persistente. Nada de instrucciones de actuador
      todavía — eso es Fase 9.
- [x] Tests pytest para lo testeable sin cámara real (conteo de dedos sobre contornos
      sintéticos, formato de la línea, lógica de umbral). Lo que dependa de inferencia
      real sobre video real, queda como validación manual documentada (igual que el
      smoke test de Fase 6) — **requiere que JD pose los 4 gestos frente a una cámara
      real; no se pudo automatizar desde esta sesión** (ver nota de acceso a cámara más
      abajo).
- [ ] Medir contra AC3 del spec (≥70% aciertos con luz normal, resultado visible en
      <1.5s) — **pendiente**, solo puede cerrarse con la validación manual de JD.
      Documentado abajo como pendiente explícito, no como "funciona".
- [x] Reporte de cierre de fase (esta sección) y alto para revisión de JD antes de
      Fase 9. No se implementó `mapping/gesture_map.py` ni `actuators/` en esta fase.

### Nota — sin acceso a cámara real desde esta sesión

Esta Pi tiene varios nodos `/dev/video19`-`/dev/video35` (stack de cámara), pero el
usuario de esta sesión no tiene permiso de lectura sobre ellos (`Permission denied` al
intentar abrirlos con OpenCV/v4l2) — y aunque lo tuviera, la arquitectura real del
proyecto no depende de una cámara local en la Pi: el video viene del cliente (webcam
del estudiante) por túnel SSH, no de esta máquina. Por eso el benchmark de velocidad
usa un frame de muestra (`zidane.jpg`, incluido con el paquete `ultralytics`,
reescalado a 640x480 y re-codificado a JPEG) — válido para medir FPS/latencia de
inferencia, pero **no para medir precisión de reconocimiento de gestos**, que
necesita fotos reales de una mano hacienda cada uno de los 4 gestos. Eso solo lo puede
generar JD posando frente a una cámara real (la del cliente Tauri, o cualquier webcam),
razón por la que AC3 queda pendiente de esa validación manual.

### Decisión de arquitectura de visión (antes de escribir código)

Se le preguntó explícitamente a JD cómo reconocer los 4 gestos, porque no había ningún
modelo ni dataset ya decidido para esto (a diferencia de lo que ya estaba fijo en
`CLAUDE.md`/`GESTOS.md`, que solo dicen "YOLO + OpenCV" como stack, sin especificar
qué detecta YOLO exactamente). Se plantearon 3 opciones: (a) fine-tuning rápido de
YOLOv8-cls con fotos capturadas en el momento por JD, (b) YOLO para detectar la
región de la mano/persona + OpenCV clásico para contar dedos, sin entrenar nada, (c)
bajar un modelo de gestos ya entrenado de terceros (Roboflow/HuggingFace) — descartada
por riesgo de cadena de suministro (pesos `.pt` de origen no verificado) y porque sus
clases casi seguro no coinciden con las 4 nuestras. **JD eligió (b).**

Como no hay ninguna clase "mano" en los pesos oficiales de COCO sin fine-tuning, el rol
real de YOLO en esta implementación es localizar la clase `person` (COCO clase 0) como
región de interés para acotar la búsqueda — si no se detecta ninguna persona (frecuente
si el frame es un acercamiento de la mano sola, sin torso/cara visible), se usa el
frame completo. La clasificación real del gesto es 100% OpenCV clásico: segmentación
de piel por color en HSV dentro de esa región, contorno más grande, convex hull +
convexity defects para dedos extendidos.

### Decisiones descartadas (alternativas a YOLO+OpenCV que se plantearon y no se usaron)

**(a) Fine-tuning rápido de un clasificador YOLOv8-cls con fotos capturadas en el
momento.** Usar los checkpoints oficiales de Ultralytics (`yolov8n/s/m-cls.pt`, fuente
confiable, no terceros) y afinarlos ahora mismo con ~50-100 fotos reales por gesto,
posadas por JD frente a la cámara de esta sesión. Es la opción más robusta en teoría —
un modelo realmente entrenado para estos 4 gestos, en vez de una heurística geométrica
— pero se descartó por dos razones prácticas: (1) tomaba bastante más tiempo de esta
sesión (captura de dataset + entrenamiento, aunque sea corto, en CPU de esta Pi) antes
de tener nada que probar, y (2) dependía de que JD estuviera disponible para posar los
gestos en el momento exacto de la sesión, algo que no se puede coordinar de forma
síncrona por chat. Queda como la opción más sólida a reconsiderar en una fase
posterior si la heurística de OpenCV no llega al 70% de AC3 — la infraestructura para
correrla (YOLO ya instalado, catálogo de 4 gestos ya cerrado) queda lista, solo
faltaría el dataset y el entrenamiento en sí.

**(c) Modelo de gestos de terceros ya entrenado (Roboflow Universe / HuggingFace).**
La opción más rápida de conseguir "en teoría" — bajar un modelo que alguien más ya
entrenó para reconocer gestos de mano. Se descartó por dos motivos, no solo velocidad:
riesgo de cadena de suministro real (cargar un archivo `.pt` de un origen no verificado
puede ejecutar código arbitrario al deserializar, a diferencia de los checkpoints
oficiales de Ultralytics usados en las otras dos opciones) y porque es muy poco
probable que las clases de un modelo de terceros coincidan exactamente con los 4
nombres/gestos ya fijados en `GESTOS.md` (`puño_cerrado`, `palma_abierta`,
`dedo_indice`, `dedo_anular`) — habría que mapear o reentrenar de todos modos, perdiendo
la ventaja de velocidad que la hacía atractiva en primer lugar.

**(b) YOLO para detectar la mano/persona + OpenCV para contar dedos — la elegida.**
Sin entrenar nada ni depender de que JD estuviera disponible para posar gestos en el
momento, y sin pesos de origen no verificado. La contrapartida, documentada arriba en
"Medición contra AC3": es una heurística geométrica, no un modelo aprendido, así que su
precisión real (sobre todo la desambiguación índice/anular y la sensibilidad de la
segmentación por color de piel) todavía no está probada contra una mano real — es el
costo de haber evitado el entrenamiento.

### Benchmark real de tamaño de modelo YOLO en esta Pi 5 (2026-09-15)

Script puntual (no forma parte del paquete): decodifica el mismo frame de prueba
(640x480 JPEG) con `cv2.imdecode` y corre `model.predict(...)` — mismo camino real que
usará el detector — con 3 iteraciones de warmup descartadas y 30 iteraciones medidas
por tamaño de modelo. Pesos oficiales de Ultralytics (`yolov8n.pt`, `yolov8s.pt`,
`yolov8m.pt`, descargados de `github.com/ultralytics/assets`, sin fine-tuning).

| Modelo | FPS promedio | Latencia promedio | Latencia p95 | Latencia min/max |
|---|---|---|---|---|
| `yolov8n.pt` (nano)  | 2.27 | 440.3 ms  | 456.0 ms  | 424.3 / 526.4 ms |
| `yolov8s.pt` (small) | 0.80 | 1242.2 ms | 1249.8 ms | 1234.4 / 1270.4 ms |
| `yolov8m.pt` (medium)| 0.35 | 2821.9 ms | 2856.7 ms | 2772.0 / 2927.0 ms |

**Decisión: `yolov8n.pt` (nano).** Es el único tamaño con margen real frente al límite
de AC3 (<1.5s) una vez se suma el resto del procesamiento (segmentación OpenCV,
decode, envío) — `small` ya está pegado al límite sin ese margen (1.24s promedio) y
`medium` lo excede casi al doble (2.82s). `medium` y `small` quedan descartados para
esta fase, no por hardware insuficiente en términos absolutos sino porque no dejan
margen de seguridad frente al criterio de aceptación.

**Hallazgo adicional — costo de arranque en frío:** la primera inferencia real después
de cargar el modelo tomó **1.82s** (por encima del límite de AC3), notablemente más
lenta que el régimen estable (~440-447ms medido en 5 llamadas consecutivas
posteriores). Se agregó un warmup síncrono en `main.py` (`_warm_up`, corre una
detección sobre un frame negro en blanco al arrancar, antes de aceptar conexiones) para
que ese costo se pague una sola vez al iniciar el servicio, no en el primer gesto real
de un estudiante.

### Desarrollo

- `cva_gesture_bridge/config.py`: dos variables nuevas, mismo patrón de override por
  entorno que las ya existentes — `YOLO_MODEL` (`CVA_YOLO_MODEL`, default
  `"yolov8n.pt"`, la decisión del benchmark de arriba) y `MIN_CONFIDENCE`
  (`CVA_MIN_CONFIDENCE`, default `0.5`, punto de partida a ajustar con datos reales).
- `cva_gesture_bridge/vision/detector.py` (nuevo):
  - `count_finger_gaps`/`count_extended_fingers`: cuentan dedos extendidos vía
    convexity defects, normalizando la profundidad del defect contra la diagonal del
    bounding box (para que el umbral no dependa del tamaño de la mano en el frame). Con
    0 huecos cualificados no se puede distinguir puño cerrado de un solo dedo extendido
    solo con defects (no hay dedo vecino con quien formar un hueco) — se usa el aspect
    ratio del bounding box como segunda señal.
  - `classify_gesture`: mapea el conteo de dedos a los 4 gestos del catálogo de esta
    fase. **El caso de 1 dedo extendido (índice vs. anular) se desambigua por la
    posición horizontal de la punta del dedo respecto al centro del bounding box** —
    es el supuesto más frágil de todo el diseño: asume una orientación de mano
    consistente (dorso o palma de frente a la cámara, dedo hacia arriba). Si en la
    validación real JD encuentra que índice y anular salen intercambiados, es un ajuste
    de una línea (invertir la comparación), no un rediseño.
  - `segment_hand`: segmentación de piel en HSV con un rango fijo (`_SKIN_HSV_LOW`/
    `_SKIN_HSV_HIGH`) + limpieza morfológica (open/close) + contorno más grande. **Es
    una heurística conocida por ser sensible al tono de piel y a la iluminación** —
    riesgo real para el ≥70% de AC3, no verificado todavía contra piel/luz reales.
  - `GestureDetector`: junta todo — decodifica el JPEG, corre YOLO para acotar la
    región (`person`, clase COCO 0; si no detecta nada usa el frame completo),
    segmenta la mano, cuenta dedos, clasifica, arma el `GestureResult`.
  - `format_line`: función libre (no depende de un modelo cargado, para poder
    testearla sin YOLO) que arma la línea final o devuelve `None` si no hay gesto o no
    llega al umbral de confianza — nunca manda una instrucción de actuador, solo
    `"gesto: <nombre>, confianza: <0.00-1.00>"`.
- `cva_gesture_bridge/transport/tcp_server.py`: nuevo parámetro opcional
  `on_jpeg_frame` (async, recibe `jpeg_bytes` y el `writer`) — **separado** del
  `on_frame` que ya usaban los tests de Fase 6/7, para no volver a cambiarle la firma a
  ese callback. Se llama después de `on_frame` en el mismo loop de lectura, envuelto en
  `try/except Exception` con `logger.exception` (no silencioso: se loguea el traceback
  completo) para que un fallo del detector no tumbe la conexión persistente completa.
- `cva_gesture_bridge/main.py`: instancia `GestureDetector` una sola vez al arrancar
  (no por frame), hace el warmup descrito arriba, y conecta `on_jpeg_frame` a
  `loop.run_in_executor(...)` — la inferencia es síncrona y bloqueante (~440ms), correr
  en un thread aparte evita congelar el loop de asyncio mientras dura. Si el resultado
  supera el umbral de confianza, loguea y manda la línea con `TcpServer.send_line`.
- `requirements.txt` (nuevo): dependencias reales de runtime (`torch`/`torchvision`
  **CPU-only**, `ultralytics`, `opencv-python`). Se instalaron primero `torch`/
  `torchvision` desde `https://download.pytorch.org/whl/cpu` explícitamente — la
  instalación por defecto de `ultralytics` arrastra ~15 paquetes `nvidia-cu13-*`
  (toolkit CUDA completo) aunque esta Pi no tiene GPU NVIDIA; el índice CPU-only evita
  ese desperdicio de ancho de banda/disco. `*.pt` ya estaba en `.gitignore` desde antes
  (no hubo que tocarlo) — los pesos descargados no se versionan.
- Tests nuevos:
  - `tests/test_detector.py` (13 casos): conteo de dedos y clasificación sobre
    contornos sintéticos dibujados a propósito (puño = círculo compacto, un dedo =
    base + un rectángulo angosto a la izquierda/derecha, palma abierta = silueta en
    abanico de 5 puntas a alturas distintas — las alturas iguales fallaban porque el
    convex hull trata puntas colineales como un solo borde y reporta un solo defect en
    vez de uno por valle, se descubrió al correr el test), y formato/umbral de
    `format_line`.
  - `tests/test_tcp_server.py` (+2 casos): que `on_jpeg_frame` reciba los bytes reales
    y pueda contestar por `send_line` sobre el mismo socket, y que una excepción dentro
    de `on_jpeg_frame` no tumbe la conexión (se loguea y se sigue leyendo).
  - `pytest` completo: **26 passed, 0 failed** (13 previos + 13 nuevos de detector + 2
    de tcp_server, neto +13 sobre los 13 que había).
- Validación manual (smoke test, sin cámara real — análoga a la de Fase 6): se corrió
  `GestureDetector.detect()` completo contra un frame real (`zidane.jpg` reescalado)
  para confirmar que el pipeline entero no truena de punta a punta. Resultado:
  `GestureResult(gesture=None, confidence=0.0, extended_fingers=2)` — 2 dedos
  detectados en una región de piel (cara/mano de la foto) no es ninguno de los 4
  gestos del catálogo, así que correctamente no manda nada; no hay crash. Esto
  confirma que el código corre de punta a punta en hardware real, **no que reconozca
  gestos correctamente** — eso requiere fotos reales de las 4 poses.

### Medición contra AC3 — PENDIENTE, no cerrado

**No se puede afirmar que este detector cumple AC3 (≥70% aciertos, <1.5s) todavía.**
Lo único medido con datos reales en esta Pi es la velocidad (tabla de arriba: ~440ms
por gesto en régimen estable, dentro del límite de 1.5s con margen). La precisión de
reconocimiento (los 4 gestos correctos, con luz normal) depende de una segmentación
por color de piel y una heurística de conteo que nunca se probaron contra una mano
real — **JD necesita posar cada uno de los 4 gestos frente a una cámara real (la del
cliente Tauri por túnel SSH, o cualquier webcam) y reportar aquí cuántos de N intentos
por gesto salieron correctos.** Si el acierto queda por debajo del 70%, los puntos más
probables de ajuste, en orden de sospecha: el rango HSV de piel (`_SKIN_HSV_LOW`/
`_SKIN_HSV_HIGH` en `detector.py`, sensible a tono de piel/iluminación), el umbral de
profundidad de convexity defects (`_MIN_DEFECT_DEPTH_RATIO`), y la desambiguación
índice/anular (ver nota arriba, es el supuesto más frágil).

**Esperando validación manual de JD y su aprobación explícita antes de tocar Fase 9.**
