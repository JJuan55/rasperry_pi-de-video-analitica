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

---

### Ajuste de catálogo y throttling, pedido por JD tras la prueba manual (2026-09-17)

JD corrió una sesión real contra el bridge desplegado hoy (ver el historial de logs
revisado en esta misma sesión de chat: 60 gestos detectados, 44 `puño_cerrado` + 16
`palma_abierta`, sesión de `12:35:26` a `12:37:10`). A partir de esa prueba pidió dos
ajustes explícitos, antes de seguir con la validación de AC3:

#### Tasklist

- [x] **Reducir el catálogo de esta fase** de `{puño_cerrado, palma_abierta,
      dedo_indice, dedo_anular}` a `{puño_cerrado, palma_abierta ("mano abierta"),
      dedo_pulgar, dedo_menique}` — se sacan `dedo_indice`/`dedo_anular`, entran
      `dedo_pulgar`/`dedo_menique`. Solo afecta el subconjunto de Fase 8 (código +
      esta bitácora); `GESTOS.md` (el catálogo completo de 7 gestos aprobado para
      fases futuras) no se toca.
- [x] **Throttling entre detecciones:** dejar de procesar un gesto por cada frame
      (hoy corre la detección completa en cada frame que llega, ~2.5-2.6 fps real
      medido hoy). En vez de eso, esperar 5 segundos entre que se procesa un gesto y
      se intenta procesar el siguiente — configurable por variable de entorno, mismo
      patrón que el resto de `config.py`.
- [x] Actualizar los tests afectados por el rename de gestos y agregar tests nuevos
      para el throttling.
- [x] Reporte de cierre de este ajuste puntual.

#### Desarrollo

- `cva_gesture_bridge/vision/detector.py`: se renombraron las constantes
  `GESTURE_DEDO_INDICE`/`GESTURE_DEDO_ANULAR` a `GESTURE_DEDO_PULGAR`
  (`"dedo_pulgar"`) / `GESTURE_DEDO_MENIQUE` (`"dedo_menique"`). La lógica geométrica
  del caso "1 dedo extendido" no cambió (sigue siendo la posición horizontal de la
  punta del dedo respecto al centro del bounding box) — solo se re-etiquetó qué lado
  es cuál gesto. **Nota honesta:** pulgar y meñique sí son, geométricamente, los dos
  dedos más laterales de la mano — este heurístico de "izquierda/derecha" encaja mejor
  con ellos que con índice/anular (que son más centrales), pero el riesgo de fondo
  sigue siendo el mismo que ya estaba documentado: la heurística de aspect-ratio para
  detectar "1 dedo extendido" se diseñó asumiendo un dedo apuntando hacia arriba desde
  la base de la mano, y un pulgar extendido lateralmente (como un "thumbs up" girado,
  o un autoestop) puede no producir el mismo bounding box alto-y-angosto — esto es
  nuevo respecto a lo que había antes (índice/anular sí apuntan hacia arriba de forma
  natural) y debe verificarse explícitamente en la validación manual de AC3, no
  asumirse resuelto.
- `cva_gesture_bridge/config.py`: nueva variable `GESTURE_COOLDOWN_SECONDS`
  (`CVA_GESTURE_COOLDOWN_SECONDS`, default `"5"`) — mismo patrón de override por
  entorno que las demás.
- `cva_gesture_bridge/main.py`: `_make_on_jpeg_frame` ahora recibe también
  `cooldown_seconds` y mantiene un `last_processed_at` (closure, `time.monotonic()`).
  Si un frame llega antes de que pase el cooldown desde el último frame *procesado*
  (haya dado gesto o no), se descarta sin correr el detector — no se llama a
  `detector.detect()`, no se loguea, no se manda nada. El conteo de fps/bytes por
  frame (`on_frame`, Fase 6/7) sigue corriendo sobre todos los frames igual que antes
  — el throttling es solo del lado de visión, no de la lectura del socket. Efecto
  esperado además del pedido de JD: baja notablemente la carga de CPU de esta Pi,
  porque ya no corre YOLO+OpenCV en cada frame (~2.5 veces por segundo) sino como
  máximo una vez cada 5s.
  **Simplificación consciente:** `last_processed_at` vive en el closure de
  `_make_on_jpeg_frame`, que se crea una sola vez por arranque del bridge (no por
  conexión) — el cooldown es global al proceso, no por sesión/conexión. Para el uso
  real de este proyecto (una sola conexión persistente por sesión de práctica, sección
  4.1 de `CLAUDE.md`) es equivalente a un cooldown por sesión, pero si en el futuro
  hubiera conexiones concurrentes activas, una competiría por el mismo cooldown de la
  otra. No se consideró necesario resolverlo ahora (agregaría una factory como la que
  ya existe para `watchdog_factory`), pero queda anotado por si se vuelve relevante.
- `tests/test_detector.py`: se renombraron los tests y asserts que usaban
  `GESTURE_DEDO_INDICE`/`GESTURE_DEDO_ANULAR` a `GESTURE_DEDO_PULGAR`/
  `GESTURE_DEDO_MENIQUE` — la lógica de los tests no cambió, solo las constantes
  importadas y el texto esperado de `format_line`.
- `tests/test_main.py` (nuevo, 4 casos): cooldown con un detector falso (sin YOLO
  real) — el primer frame siempre se procesa, un frame que llega dentro de la ventana
  de cooldown se descarta sin llamar a `detector.detect()`, un frame que llega después
  de que pasa el cooldown sí se procesa, y el cooldown cuenta desde el último frame
  *procesado* aunque no haya dado gesto (no solo desde el último gesto mandado).
- `pytest` completo: **30 passed, 0 failed** (26 previos + 4 nuevos de cooldown).

**Estado del despliegue:** este ajuste está en el working tree, todavía **no
commiteado ni desplegado** — el servicio `systemd` en producción (`cva-gesture-bridge`,
el que generó el historial de gestos revisado hoy) sigue corriendo el código anterior
(catálogo de 4 dedos viejo, sin cooldown) hasta que se commitee y se reinicie el
servicio. No se tocó el servicio en esta sesión.

---

### Fix de falsos positivos: gestos reportados sin mano presente (2026-09-18)

JD confirmó con `journalctl` en vivo en esta Pi, sin ninguna mano frente a la cámara,
que el detector desplegado reportaba gestos activamente (`puño_cerrado`,
`palma_abierta`, `dedo_indice` variando sin patrón). **Causa raíz:** `segment_hand()`
segmenta por color de piel dentro de toda la región "persona" que devuelve YOLO —
región que incluye cara/cuello — y sin mano presente toma la cara (u otra piel visible)
como el contorno más grande, alimentando esa forma a la clasificación como si fuera una
mano real.

#### Tasklist

- [x] Filtro geométrico de plausibilidad sobre el contorno segmentado, antes de
      clasificar — aspect ratio y solidity fuera de rango razonable → rechazar (sin
      gesto), no forzar una clasificación.
- [x] Acotar la ROI de segmentación de piel dentro de la caja "persona" de YOLO,
      excluyendo la franja donde normalmente está la cara.
- [x] Estabilidad temporal: exigir el mismo gesto en N frames consecutivos (2-3) antes
      de loguear "Gesto detectado" o llamar a `send_line` — ruido de un solo frame no
      debe disparar nada hacia el cliente.
- [x] Test nuevo en `tests/test_detector.py`: un contorno tipo "cara" (redondo, alta
      solidity) no se clasifica como ninguno de los 4 gestos.
- [x] Documentar aquí qué medida(s) se implementaron y por qué, incluyendo las que se
      consideraron y no se usaron.
- [ ] Repetir la prueba de `journalctl -u cva-gesture-bridge.service -f` sin mano frente
      a la cámara — criterio de aceptación: 0 líneas "Gesto detectado" y 0 `send_line`
      durante al menos 1 minuto. **Pendiente** — requiere desplegar (commit + reinicio
      del servicio) y una cámara real apuntando a la Pi, que esta sesión no tiene.

#### Medidas implementadas y por qué

**Se implementaron las tres, son complementarias — cada una cubre el punto ciego de
la otra:**

1. **Acotar la ROI (`_FACE_EXCLUSION_TOP_FRACTION = 0.35` en `detector.py`).** Es la
   defensa más preventiva: si la cara nunca entra a `segment_hand()`, no puede ganar
   como "el contorno más grande". Se excluye el 35% superior de la caja "persona" de
   YOLO antes de segmentar piel — valor estimado (no calibrado contra fotos reales de
   esta Pi), documentado como ajustable. Limitación reconocida: solo aplica cuando YOLO
   detecta una `person` — si no detecta a nadie (frame de acercamiento solo de la mano,
   sin torso/cara visible) se usa el frame completo sin recortar, porque cortar el
   frame completo arriesgaría recortar una mano levantada cerca del borde superior en
   ese encuadre distinto.

2. **Filtro de plausibilidad (`_is_plausible_hand_contour`, con `classify_contour` como
   punto de entrada que lo aplica antes de clasificar).** Defensa de respaldo para
   cuando la exclusión de ROI no alcanza (ej. sin `person` detectado, o piel visible
   por debajo de la franja excluida). Rechaza por aspect ratio (`0.4`-`4.0`) y, sobre
   todo, por un **techo de solidity (`0.95`)** — calibrado contra los contornos
   sintéticos de los tests: los 4 gestos del catálogo caen en solidity ~0.69-0.88,
   mientras que un óvalo liso tipo cara cae en ~0.99. Un dato importante que salió al
   escribir el test pedido explícitamente por JD ("contorno tipo cara... no se
   clasifica como ninguno de los 4 gestos"): un **círculo geométrico perfecto** (que es
   como estaba modelado el fixture de "puño" hasta ahora, `cv2.circle`) es
   indistinguible de una cara lisa por aspect ratio y solidity — ambos dan solidity
   ~0.98+. Por eso se reemplazó el fixture `_fist_contour()` de los tests por una
   silueta con textura leve (nudillos, solidity ~0.88, generada con una perturbación
   sinusoidal del radio), más fiel a un puño real. **Esto es honesto también sobre el
   límite del filtro:** distinguir un puño real de una cara real solo por geometría del
   contorno es un problema genuinamente difícil — la defensa principal contra ese caso
   específico es la exclusión de ROI (punto 1), no este filtro.

3. **Estabilidad temporal (`GestureStabilizer`, `required_streak` configurable vía
   `CVA_GESTURE_STABILITY_STREAK`, default 3).** Explica por sí sola gran parte del
   síntoma que reportó JD ("puño_cerrado, palma_abierta, dedo_indice variando sin
   patrón"): una máscara de piel ruidosa (cara mal segmentada, jitter de iluminación)
   hace que el conteo de convexity defects varíe frame a frame, saltando entre
   categorías — un gesto real y sostenido, en cambio, debería clasificar igual en
   frames consecutivos. Exigir 3 detecciones seguidas iguales antes de loguear/mandar
   filtra ese ruido con alta probabilidad, sin necesitar que sea geométricamente
   perfecto.

   **Interacción importante con el cooldown de 5s (pedido el 2026-09-17):** cada
   detección *procesada* ya está ~5s separada de la anterior por el cooldown, así que
   confirmar un gesto sostenido toma ahora **~15s** (3 × 5s), no ~1.2s como sería sin
   cooldown. Esto entra en tensión directa con AC3 (<1.5s) — no se resolvió en este
   ajuste porque el criterio de aceptación de JD para esta tarea es solo "sin falsos
   positivos", no latencia. **Queda pendiente de decisión de JD:** si 15s resulta
   demasiado lento en la validación real, la corrección es bajar `GESTURE_COOLDOWN_SECONDS`
   y/o `GESTURE_STABILITY_STREAK` (ambos configurables por variable de entorno) — no
   se debe ajustar ninguno de los dos a ciegas sin medir contra la cámara real primero.

**Medida considerada y NO usada:** detección de cara con un clasificador Haar
(`cv2.CascadeClassifier`) para recortarla explícitamente en vez de una fracción fija de
la caja "persona". Es la opción técnicamente más precisa, pero el paquete
`opencv-python` instalado en esta Pi **no trae empaquetados los XML de Haar cascades**
(`cv2.data.haarcascades` apunta a un directorio vacío) — usarla requeriría bajar ese
archivo por separado (un asset adicional que gestionar, aunque es oficial de OpenCV) y
no fue necesario para cumplir lo pedido con las tres medidas de arriba. Queda anotada
como mejora futura si la fracción fija (35%) no resulta suficiente en la validación
real.

#### Desarrollo

- `cva_gesture_bridge/vision/detector.py`: nuevas constantes
  `_MIN_PLAUSIBLE_ASPECT`/`_MAX_PLAUSIBLE_ASPECT`/`_MIN_PLAUSIBLE_SOLIDITY`/
  `_MAX_PLAUSIBLE_SOLIDITY`/`_FACE_EXCLUSION_TOP_FRACTION`; funciones nuevas
  `_is_plausible_hand_contour()` y `classify_contour()` (contorno → `GestureResult`
  final, pasando por el filtro — punto de entrada testeable sin YOLO); clase nueva
  `GestureStabilizer`. `GestureDetector.detect()` ahora recorta la franja de la cara
  de la ROI antes de segmentar y usa `classify_contour()` en vez de llamar
  `count_extended_fingers`/`classify_gesture` directo.
- `cva_gesture_bridge/config.py`: nueva variable `GESTURE_STABILITY_STREAK`
  (`CVA_GESTURE_STABILITY_STREAK`, default `3`).
- `cva_gesture_bridge/main.py`: `_make_on_jpeg_frame` ahora mantiene un
  `GestureStabilizer` por closure (mismo alcance global-al-proceso que
  `last_processed_at`, ver nota ya documentada sobre el cooldown) y solo loguea
  "Gesto detectado"/llama `send_line` cuando el stabilizer confirma. Las detecciones
  crudas no confirmadas se loguean a nivel `DEBUG` como "Gesto candidato" para
  seguir teniendo visibilidad durante pruebas.
- `tests/test_detector.py`: se reemplazó `_fist_contour()` (antes un círculo
  perfecto — indistinguible de una cara, ver arriba) por una silueta con textura
  leve; se agregó `_face_contour()` (óvalo liso); 13 tests nuevos: el filtro de
  plausibilidad rechaza la cara y acepta los 4 fixtures de gestos legítimos,
  `classify_contour` end-to-end (cara → sin gesto, puño/palma siguen reconociéndose),
  y 5 casos de `GestureStabilizer` (no confirma antes de la racha, confirma al
  llegarla, se resetea si cambia el gesto o si aparece un frame sin gesto).
- `tests/test_main.py`: los 4 tests de cooldown existentes ahora pasan
  `stability_streak=1` explícito (para aislar el comportamiento del cooldown del de
  estabilidad); 5 tests nuevos de la integración del stabilizer en el wiring real
  (`cooldown_seconds=0.0` para aislarlo del cooldown): no se manda nada con una sola
  detección, se manda una vez confirmado, y se resetea con cambio de gesto o ruido.
- `pytest` completo: **43 passed, 0 failed** (30 previos + 13 nuevos).
- Validación manual (smoke test con YOLO real, sin cámara — mismo patrón que el resto
  de Fase 8): se corrió `GestureDetector.detect()` contra `zidane.jpg` (foto de
  personas real, no una escena "solo cara" limpia). Resultado: sí devolvió un gesto
  nominal (`puño_cerrado`, confianza 0.39) — **por debajo del umbral de confianza
  (0.5)**, así que `format_line` igual no lo manda, pero esto NO es una confirmación
  limpia del fix, porque la foto no reproduce la escena exacta del bug (JD probó
  específicamente "sin ninguna mano frente a la cámara", probablemente con su propia
  cara ocupando gran parte del encuadre real de la Pi, distinto a esta foto de stock
  con múltiples personas y fondo complejo). **No se debe interpretar este smoke test
  como que el bug ya está confirmado resuelto en producción.**

#### Qué queda pendiente — validación real, no simulada

Lo único que de verdad confirma este fix es la prueba que pidió JD:
`journalctl -u cva-gesture-bridge.service -f` en la Pi, sin mano frente a la cámara,
durante al menos 1 minuto, sin ninguna línea "Gesto detectado". Esta sesión no tiene
cámara ni acceso físico a la Pi para generar esa escena — los tests unitarios (contorno
sintético tipo cara, rechazado) dan confianza en que la lógica está bien encadenada,
pero no reemplazan esa prueba en vivo. **Nada de este ajuste está commiteado ni
desplegado** — sigue en el working tree. Para cerrar este punto hace falta: commitear,
reiniciar el servicio (`systemctl restart cva-gesture-bridge.service`, necesita `sudo`
interactivo que esta sesión no tiene), y que JD corra la prueba de `journalctl` y
reporte aquí el resultado.

---

### Despliegue y corrección de sobre-ajuste (2026-09-18, misma sesión de chat)

**Despliegue:** JD commiteó el fix directo (`c0edf61 "segunda implmentacion de fase
8"`). El servicio no tiene `sudo` interactivo en esta sesión para
`systemctl restart`, pero el proceso corre como el mismo usuario (`User=david_cardenas`
en la unit) y tiene `Restart=always` — se reinició matando el PID viejo (`11799`)
directamente; systemd levantó uno nuevo (`22447`) con el código del commit, confirmado
en el log (`"Detector de gestos precalentado"`, `"escuchando en ('0.0.0.0', 8766)"`).

**Prueba real de JD (sosteniendo un puño cerrado ~30s):** resultado inesperado — **0
líneas "Gesto detectado"** en el log, a pesar de varias sesiones reales después del
reinicio (una de 1496 frames, otra de 485, ~15 minutos de video real en total, más
otras conexiones cortas). El fix de falsos positivos parecía estar funcionando (0
falsos positivos también), pero a costa de bloquear gestos reales.

**Causa más probable:** `_FACE_EXCLUSION_TOP_FRACTION = 0.35` — en un encuadre típico
de webcam (cabeza y hombros), recortar el 35% superior de la caja "persona" de YOLO
muy probablemente elimina también la mano si se sostiene cerca de la cara/hombro para
mostrarla a la cámara (justo donde alguien sostendría un puño para demostrarlo), no
solo la cara. No se pudo confirmar con certeza porque el nivel de log en producción es
`INFO` (las líneas "Gesto candidato" a nivel `DEBUG` que mostrarían qué pasó frame a
frame no quedaron registradas), y esta sesión no tiene `sudo` para cambiar el nivel de
log del servicio y volver a probar con más detalle.

**Ajuste:** `_FACE_EXCLUSION_TOP_FRACTION` bajado de `0.35` a `0.15` en
`cva_gesture_bridge/vision/detector.py`, a pedido explícito de JD ("bájalo porque creo
que sí quedó muy agresivo"). Con esto, la exclusión de ROI pasa a ser una ayuda ligera
en vez de la defensa principal contra falsos positivos — esa responsabilidad recae
ahora sobre todo en el filtro de plausibilidad (`_is_plausible_hand_contour`, techo de
solidity 0.95) y el `GestureStabilizer` (3 frames seguidos), que no dependen de
suposiciones sobre dónde se sostiene la mano en el encuadre. `pytest`: **43 passed**
(sin cambios en los tests — el valor es un parámetro interno, no está testeado por
número exacto). **Sigue sin estar calibrado contra fotos/video reales de esta Pi** —
es una corrección basada en una hipótesis razonable, no en una medición directa del
punto exacto donde falló; si 0.15 todavía corta manos reales o ya no es suficiente
contra falsos positivos, hay que volver a medir con visibilidad real (idealmente JD
corriendo el servicio manualmente con `CVA_LOG_LEVEL=DEBUG` para ver las líneas "Gesto
candidato"), no seguir ajustando el número a ciegas.

**Pendiente:** commitear este ajuste, redesplegar (mismo mecanismo: matar el PID,
systemd lo revive), y que JD repita la prueba del puño sostenido — si ahora se detecta
y sigue sin haber falsos positivos sin mano, se cierra este ajuste.

---

### Bajar a 0.15 NO fue suficiente — diagnóstico temporal en INFO (2026-09-18)

Se commiteó (`c36a844`) y redesplegó el ajuste anterior (mismo mecanismo: matar el PID
del proceso, `Restart=always` lo revive). JD repitió la prueba del puño sostenido: **0
líneas "Gesto detectado" otra vez**, en dos sesiones reales más (265 y 565 frames, ~38s
y ~81s respectivamente). Bajar la fracción de exclusión no arregló nada — la hipótesis
del turno anterior (la ROI cortaba la mano) no está confirmada ni descartada, seguía
siendo una suposición sin visibilidad real.

**Decisión: dejar de ajustar números a ciegas.** Se instrumentó `detector.py` (dentro
de `GestureDetector.detect()`) y `main.py` con logging de diagnóstico **a propósito en
nivel `INFO`, no `DEBUG`** — el servicio en producción corre con `CVA_LOG_LEVEL=INFO`
y esta sesión no tiene `sudo` para subirlo sin tocar código, así que subir el nivel del
logging mismo (marcado `[diag]`, fácil de grep y de revertir) es la forma de obtener
visibilidad real sin necesitar privilegios que no están disponibles.

**Qué queda instrumentado, en orden del pipeline:**
1. Tamaño del frame decodificado.
2. Si YOLO detectó una `person` (bbox) o no (fallback a frame completo).
3. La ROI resultante tras excluir la franja superior.
4. Si `segment_hand()` encontró o no un contorno de piel suficientemente grande.
5. Del contorno encontrado: área, bbox, aspect ratio, solidity, y si pasó
   `_is_plausible_hand_contour()`.
6. El resultado final de `classify_contour()`.
7. En `main.py`: el gesto candidato crudo y si el `GestureStabilizer` lo confirmó.

Con esto, la próxima corrida de JD debería mostrar exactamente en cuál de esos 7 pasos
se está perdiendo el puño (¿YOLO no detecta persona? ¿la ROI recortada queda vacía o
sin la mano? ¿no se segmenta contorno de piel? ¿se segmenta pero se rechaza por
plausibilidad? ¿se clasifica bien pero el stabilizer nunca junta 3 seguidos?).

**Es temporal — marcado explícitamente `[diag]` y con comentarios `DIAGNÓSTICO
TEMPORAL` en el código.** Hay que revertirlo (volver a `logger.debug`) una vez que se
entienda la causa real y se corrija; dejarlo en INFO permanentemente sería demasiado
ruido para operación normal. `pytest`: **43 passed** (no se tocó ninguna lógica, solo
logging).

**Pendiente:** commitear, redesplegar, que JD repita la prueba del puño sostenido, y
traer aquí (o pegar) las líneas `[diag]` de esa corrida para diagnosticar la causa real
antes de tocar ningún número más.

---

### Diagnóstico confirmado con datos reales — plan de remediación (2026-09-18)

JD repitió la prueba dos veces más con el diagnóstico `[diag]` ya desplegado. **Datos
reales, no hipótesis:**

- Cerrando y reabriendo el cliente, acercando más la mano, **sí reconoció los 3 gestos
  probados** (puño, palma abierta, pulgar) — con confianza real (0.48 a 1.00).
- El log confirma que el proceso **nunca se cuelga ni deja de procesar** — sigue
  corriendo un frame cada ~5s (cooldown) sin falta durante toda la sesión.
- **Causa real:** la clasificación de dedos extendidos es inestable entre muestras
  separadas 5s — la misma mano sostenida alterna entre `0 dedos` (puño), `2 dedos` y
  `3 dedos` (ninguno de los 4 gestos) de una muestra a la siguiente. Ejemplo real del
  log (sesión 16:53-16:55, todo con la misma mano en la misma zona del encuadre):
  ```
  16:53:42 puño_cerrado(0.89) → 16:53:47 sin persona → 16:53:52 sin persona →
  16:53:57 puño_cerrado(0.77) → 16:54:02 dedo_pulgar(0.64) → 16:54:07 puño_cerrado(0.88)
  → 16:54:12 puño_cerrado(0.88) → 16:54:17 dedo_pulgar(0.71) → 16:54:22 dedo_pulgar(0.69)
  → 16:54:27 None(2 dedos) → 16:54:32 palma_abierta(0.48) →
  16:54:38..16:55:03 None(2 dedos) x6 seguidas → 16:55:08 puño_cerrado(0.66)
  ```
  El `GestureStabilizer` exige 3 iguales **seguidas**; con esta inestabilidad esa
  racha casi nunca se completa — de ahí el patrón real de JD: "funciona, luego después
  de 15-20 eventos deja de responder" (no se cuelga, solo deja de *confirmar*).
- **Dato nuevo:** los frames reales del cliente llegan a **320×240**, más bajo que los
  640×480 usados en el benchmark de tamaño de modelo — a esa resolución el ruido de la
  máscara de piel pesa proporcionalmente más sobre el conteo de convexity defects.

#### Plan de remediación (aprobado por JD, "implementa las dos")

- [x] **Reducir el ruido de conteo de dedos**: subir `_MIN_DEFECT_DEPTH_RATIO` (0.15 →
      más alto) y agrandar el kernel morfológico de `segment_hand()` — para que jitter
      normal de la máscara de piel no cruce el umbral de "dedo extra".
- [x] **Relajar el `GestureStabilizer`**: de "N iguales exactas seguidas" a una
      ventana deslizante tolerante a 1 fallo (ej. "2 de las últimas 3"), que hubiera
      confirmado varias de las rachas de 2 vistas en el log de arriba.
- [x] Actualizar tests afectados (fixtures de `test_detector.py` calibrados contra el
      nuevo umbral de profundidad; tests de `GestureStabilizer` reescritos para la
      nueva semántica de ventana).
- [ ] Redesplegar (con el diagnóstico `[diag]` todavía activo) y que JD repita la
      prueba de los 3 gestos — medir cuántos de los eventos candidatos terminan
      confirmándose, no solo si al final hubo algún "Gesto detectado". **Pendiente.**
- [ ] Una vez validado, revertir el logging `[diag]` de INFO a DEBUG (es temporal,
      ver commit `8f8b5aa`). **Pendiente.**

#### Implementación

- `cva_gesture_bridge/vision/detector.py`:
  - `_MIN_DEFECT_DEPTH_RATIO`: `0.15` → `0.20`. Calibrado con margen contra
    `tests/test_detector.py` (el defect más débil de la palma abierta sintética queda
    en ~0.23-0.27, por encima del nuevo umbral).
  - Kernel morfológico de `segment_hand()`: `(5,5)` → `(7,7)` — más suavizado de la
    máscara de piel a la resolución real (320x240).
  - `GestureStabilizer` reescrito por completo: de una racha exacta
    (`required_streak`, `_streak`/`_last_gesture`) a una ventana deslizante
    (`window_size`, `min_matches`, `collections.deque`). `observe()` ahora confirma si
    el gesto aparece `min_matches` veces dentro de las últimas `window_size`
    detecciones, sin importar el orden ni si hay otro gesto de por medio.
- `cva_gesture_bridge/config.py`: `GESTURE_STABILITY_STREAK` reemplazado por
  `GESTURE_STABILITY_WINDOW` (`CVA_GESTURE_STABILITY_WINDOW`, default `3`) y
  `GESTURE_STABILITY_MIN_MATCHES` (`CVA_GESTURE_STABILITY_MIN_MATCHES`, default `2`)
  — "2 de las últimas 3" por defecto.
- `cva_gesture_bridge/main.py`: `_make_on_jpeg_frame` recibe los dos parámetros nuevos
  en vez de `stability_streak`; instancia `GestureStabilizer(window_size=...,
  min_matches=...)`.
- Tests:
  - `tests/test_detector.py`: `_open_palm_contour` con `valley_y=155` (antes 140) para
    tener margen real contra el nuevo umbral de profundidad; los 4 tests de
    `GestureStabilizer` con racha exacta se reemplazaron por 5 con la nueva
    semántica de ventana — incluyendo uno que reproduce exactamente el patrón real
    visto en producción ("2 de 3, con un gesto distinto de por medio, sí confirma").
  - `tests/test_main.py`: los 4 tests de cooldown ahora usan
    `stability_window=1, stability_min_matches=1` (equivalente al viejo streak=1,
    para seguir aislando el cooldown); los tests de estabilidad se reescribieron para
    la ventana, con el mismo caso real reproducido a nivel de wiring completo.
  - `pytest` completo: **46 passed, 0 failed** (43 previos, +3 netos tras el rediseño).

**No se pudo re-verificar contra fotos/video reales todavía en esta sesión** — falta
commitear, redesplegar, y que JD repita la prueba de los 3 gestos con el diagnóstico
`[diag]` (todavía activo) para confirmar con datos si esto resuelve el patrón real
documentado arriba.

---

### Segundo fix de falsos positivos: torso confundido con puño (2026-09-18)

JD redesplegó y probó de nuevo. Resultado inesperado: **sin ninguna mano frente a la
cámara, viendo solo su torso hacia arriba y el fondo**, el servicio empezó a detectar
`puño_cerrado` con confianza alta (0.68-0.94), de forma sostenida.

**Causa confirmada con datos del `[diag]`:** con JD cerca de la cámara, YOLO detecta la
caja "persona" cubriendo casi todo el frame (320x240) — ej. `bbox=(2, 2, 275, 239)`. El
recorte del 15% superior (pensado para la cara) deja el resto: básicamente todo el
pecho/torso. `segment_hand()` encuentra ahí un contorno de piel real (área ~11000-15000,
aspect ~0.6-1.4, solidity ~0.7-0.9) que pasa el filtro de plausibilidad y se clasifica
como puño_cerrado con confianza alta.

**Por qué esta vez no es un ajuste de número:** se comparó el área de este torso falso
positivo contra las áreas de puños reales confirmados ayer (misma resolución) —
**se solapan casi exactamente** (~18% del área de la caja "persona" en ambos casos).
Aspect ratio y solidity también se solapan. Geométricamente estático, un torso visible
de cerca y un puño real son casi indistinguibles con las señales usadas hasta ahora
(aspect, solidity, área) — subir el umbral de profundidad de defects y relajar el
`GestureStabilizer` (que sí ayudaron a detectar gestos reales) también hicieron más
fácil confirmar esto.

**Decisión (JD, preguntado explícitamente entre 3 opciones):** agregar **detección de
movimiento** — un modelo de fondo (promedio móvil exponencial) del frame completo; solo
se considera "mano" la piel que cambió recientemente respecto a ese fondo, no la que
siempre está en cuadro (torso, cuello). Descartadas: (a) volver a endurecer los
parámetros de hoy (ya sabíamos que eso dejaba de detectar gestos reales sostenidos), (b)
acotar la ROI a un recuadro central fijo (asume una posición de mano consistente, más
frágil).

#### Riesgo conocido de esta implementación (documentado antes de escribir código)

Un modelo de fondo por definición "olvida" lo que deja de cambiar — si JD sostiene el
mismo gesto sin moverse muchos minutos seguidos, el fondo eventualmente podría
adaptarse a la mano y dejar de marcarla como "movimiento", reintroduciendo el síntoma
de "deja de confirmar" que se acaba de arreglar con el `GestureStabilizer`. Se mitiga
con una tasa de adaptación lenta (`alpha` bajo) para que sobreviva la ventana de
confirmación (10-15s) y la prueba de 30s ya usada — pero una sesión real de varios
minutos sosteniendo el mismo gesto sin pausa es un caso no cubierto todavía. Si eso
pasa en la validación real, la solución correcta es pausar la actualización del fondo
mientras hay un gesto confirmado activo (no implementado en esta primera versión, para
no aumentar más el alcance de este ajuste puntual).

#### Implementación

- `cva_gesture_bridge/vision/detector.py`: clase nueva `MotionGate` — modelo de fondo
  por promedio móvil exponencial (`alpha=0.08`) sobre el **frame completo** (no la
  ROI, para que el fondo sea consistente aunque la caja "persona" de YOLO se mueva de
  un frame a otro); `update_and_get_motion_mask()` devuelve la máscara de píxeles que
  cambiaron respecto al fondo, o `None` mientras el fondo no está establecido (primer
  frame). `reset()` para olvidar el fondo aprendido explícitamente.
  - `segment_hand()` ahora acepta `motion_mask` opcional — si se pasa, se hace AND con
    la máscara de piel antes de buscar contornos (piel estática queda descartada).
  - `GestureDetector` mantiene un `MotionGate` propio (`self._motion_gate`), nuevo
    método público `reset_motion_background()`. `detect()` calcula la máscara de
    movimiento sobre el frame completo, la recorta a la misma ROI (con exclusión de
    franja superior) antes de pasarla a `segment_hand()`. Si el fondo todavía no está
    establecido, `detect()` devuelve directamente "sin gesto" — no confía en el primer
    frame de una sesión.
- `cva_gesture_bridge/main.py`: `_warm_up()` llama a
  `detector.reset_motion_background()` después de correr el detect() de warmup — el
  frame en negro del warmup no es la escena real, no debe quedar como "fondo aprendido".
- Tests nuevos en `tests/test_detector.py` (4 casos, con frames sintéticos de color
  sólido — sin cámara): primer frame establece fondo y no da máscara; el mismo frame
  repetido dos veces no marca ningún píxel como movimiento; un parche de color
  distinto sí se marca (centro del parche = 255, esquina sin cambios = 0); `reset()`
  hace que el siguiente frame vuelva a comportarse como el primero.
- `pytest` completo: **50 passed, 0 failed** (46 previos + 4 nuevos de `MotionGate`).
- Smoke test manual (sin cámara real, mismo patrón que el resto de Fase 8): se corrió
  `GestureDetector.detect()` tres veces seguidas contra el mismo frame estático
  (`zidane.jpg`, simulando una escena sin cambios como el torso quieto de JD) — las
  tres veces devolvió `gesture=None`. Antes de este fix, una imagen estática con piel
  visible sí producía falsos positivos (torso → puño_cerrado); ahora no.

**Pendiente:** commitear, redesplegar (el diagnóstico `[diag]` sigue activo), y que JD
repita ambas pruebas: (a) sin mano, torso/fondo visible, al menos 1 minuto sin ningún
"Gesto detectado"; (b) sosteniendo puño/palma/pulgar, que sigan confirmándose como
antes de este segundo fix. Si (b) falla porque el gesto real no se distingue lo
suficiente del fondo que tenía la mano antes de levantarla, es la señal de que
`_MOTION_BACKGROUND_ALPHA`/`_MOTION_DIFF_THRESHOLD` necesitan ajuste con datos reales,
no a ciegas.

---

## PAUSA — JD va a tomar decisiones de ingeniería y calidad (2026-09-18)

JD pidió parar aquí, documentar el problema actual y todo lo implementado hoy, y no
seguir tocando código hasta que decida un mejor enfoque de ingeniería. Esta sección es
ese cierre — sin cambios de código nuevos a partir de aquí.

### Resultado de la prueba (b) — con datos reales

**Lo bueno confirmado:** el rostro de JD **ya no se confunde con un puño** — el primer
fix del día (exclusión de franja superior + filtro de plausibilidad) y el segundo
(`MotionGate`, torso) siguen funcionando para lo que se diseñaron.

**Lo nuevo, reportado por JD:** con la mano real al frente, **sí detecta puño_cerrado**,
pero **no logra reconocer los otros gestos** (palma_abierta, dedo_pulgar).

**Evidencia real del `[diag]`** (sesión `17:36:28`-`17:38:15`, conexión `50734`, 748
frames, 29 detecciones procesadas):

- `dedos_extendidos` solo tomó dos valores en toda la sesión: **0 (puño), 21 veces**, y
  **2 (fuera de catálogo), 8 veces**. **Nunca** se registró 1 (pulgar/meñique) ni 4-5
  (palma) — ni siquiera como candidato crudo no confirmado. Esto es evidencia directa
  de que el problema no es el `GestureStabilizer` ni el umbral de confianza: la
  geometría del contorno nunca llega a parecer "palma" o "1 dedo" en primer lugar.
- Los "píxeles en movimiento en la ROI" se mantuvieron altos y estables todo el tiempo
  (20,000-37,000 px, nunca cerca de cero) — el `MotionGate` **sí sigue viendo
  movimiento**, no es que haya dejado de detectar nada.
- Pero el contorno resultante (después del AND entre máscara de piel y máscara de
  movimiento) es muy inconsistente para lo que debería ser el mismo gesto sostenido:
  aspect ratio saltando entre 0.53 y 1.24, solidity entre 0.63 y 0.88, área saltando
  entre 6,000 y 20,745 px en cuestión de segundos.

### Hipótesis de causa raíz (no implementada, solo documentada)

El `MotionGate` actual hace un AND **píxel a píxel** entre la máscara de piel y la
máscara de "cambió respecto al fondo reciente". Este diseño asume implícitamente que
una mano real siempre aparece como un bloque completo y nuevo — pero no es así cuando
la mano **ya lleva un rato en cuadro** y cambia de forma (puño → palma, por ejemplo):

- El fondo se actualiza en cada detección procesada (~cada 5s, `alpha=0.08`) usando el
  frame completo tal cual llega — sin distinguir si lo que hay en esa zona es "mano" o
  no. Si la mano se queda en la misma posición general entre una detección y la
  siguiente, parte de su silueta (la que no cambió lo suficiente entre esos ~5s) se
  va fundiendo con el fondo aprendido.
- Al pasar de un gesto a otro, **no toda la mano se mueve de la misma manera** — la
  base/palma puede quedarse relativamente en el mismo lugar mientras solo los dedos
  cambian de posición. El AND píxel a píxel entonces deja pasar solo fragmentos de la
  mano (los que sí superaron el umbral de diferencia), no la silueta completa —
  rompiendo exactamente la geometría de la que depende `count_extended_fingers`
  (convex hull + convexity defects necesita un contorno *completo y continuo* de la
  mano real, no un recorte parcial).
- Esto explica por qué **puño sí funciona**: es plausible que sea el primer gesto que
  JD mostró en la sesión, cuando la mano recién entraba al cuadro (todavía muy
  distinta del fondo previo sin mano) — la silueta completa del puño sí se marcó como
  movimiento de punta a punta. Los gestos mostrados *después*, con la mano ya en
  cuadro y el fondo ya parcialmente adaptado a su posición, solo se ven parcialmente.

**En otras palabras:** el fix de hoy resolvió el falso positivo (torso/cara sin mano)
pero, tal como está implementado, introdujo un nuevo problema — degrada la calidad de
la segmentación de una mano real que ya está en cuadro y cambia de gesto. Es la razón
por la que JD quiere parar a repensar el enfoque en vez de seguir ajustando parámetros
sueltos sobre este mismo diseño.

### Resumen completo de lo hecho en Fase 8 hoy (2026-09-18), para referencia

1. **Ajuste de catálogo y cooldown** (pedido explícito de JD, commits previos a esta
   pausa): catálogo reducido a puño_cerrado/palma_abierta/dedo_pulgar/dedo_menique;
   cooldown de 5s entre detecciones procesadas.
2. **Primer fix de falsos positivos** (cara sin mano presente): exclusión de franja
   superior de la caja "persona" (`_FACE_EXCLUSION_TOP_FRACTION`, bajado de 0.35 a
   0.15 tras una primera prueba real), filtro de plausibilidad geométrica
   (`_is_plausible_hand_contour`, aspect ratio + techo de solidity), `GestureStabilizer`
   (primero como racha exacta, luego rediseñado a ventana deslizante "2 de últimas 3"
   tras evidencia real de que una racha exacta casi nunca confirmaba con ruido normal
   de una mano real sostenida).
3. **Reducción de ruido de conteo de dedos**: `_MIN_DEFECT_DEPTH_RATIO` subido de 0.15
   a 0.20, kernel morfológico de `segment_hand()` de (5,5) a (7,7) — para que jitter
   normal de la máscara de piel no cruzara el umbral de "dedo extra".
4. **Segundo fix de falsos positivos** (torso/pecho confundido con puño cuando la
   persona está cerca de la cámara y la caja "persona" cubre casi todo el frame):
   `MotionGate`, modelo de fondo por promedio móvil exponencial sobre el frame
   completo, AND píxel a píxel con la máscara de piel antes de buscar contornos.
   **Este es el punto donde se encontró el problema nuevo descrito arriba.**

Todo esto está commiteado (`c36a844`, `8f8b5aa`, `d06ccad`, `e963884`) y desplegado en
producción — el servicio corre con el código del punto 4 ahora mismo, con el
diagnóstico `[diag]` en nivel `INFO` todavía activo (marcado como temporal, pendiente
de revertir a `DEBUG` cuando se cierre esta investigación).

### Estado: en pausa, esperando decisión de JD

No se toca más código hasta que JD decida el enfoque. Puntos abiertos que quedan sobre
la mesa para esa decisión (sin resolver aquí):

- Si el AND píxel a píxel es demasiado frágil para una mano que cambia de forma en
  cuadro, ¿la alternativa es un enfoque de movimiento más tosco (ej. exigir que la
  *caja delimitadora* del contorno de piel se haya movido/cambiado de tamaño lo
  suficiente, en vez de exigirlo píxel por píxel)? ¿O abandonar el `MotionGate` y
  volver a apoyarse más en geometría (aceptando el riesgo de falsos positivos de
  torso) combinado con otra señal distinta?
- El diagnóstico `[diag]` en `INFO` sigue en producción — decidir si se revierte antes
  de la próxima ronda de pruebas o se deja mientras se sigue investigando.
- Ningún commit de hoy se ha pusheado (`git push`) — siguen solo locales en esta Pi,
  a la espera de autorización explícita si JD quiere respaldarlos en el remoto.

---

## Decisión: explorar MediaPipe HandLandmarker como alternativa a YOLO+OpenCV (2026-09-21)

Tras la pausa de arriba, JD decidió no seguir ajustando el `MotionGate` sobre el mismo
diseño (AND píxel a píxel) y en vez de eso evaluar si **MediaPipe HandLandmarker**
(landmarks de mano reales, no segmentación por color de piel) es una alternativa
viable al stack actual YOLO+OpenCV para el reconocimiento de gestos en esta Pi 5.

El plan completo (7 mecanismos de control de falsos positivos considerados, Fases B-E,
comparación detallada contra YOLO) vive en un documento del proyecto,
`CVA_deteccion-gestos_plan.md`, fuera de este repo — no se busca ni se asume su
contenido aquí; si hace falta ese detalle durante el spike, se le pide a JD.

**Alcance autorizado ahora: solo Fase A — un spike de viabilidad, aislado y
reversible.** No se toca `cva_gesture_bridge/vision/detector.py` de producción ni se
despliega nada. Corre en una rama aparte, `spike/fase8-mediapipe-viabilidad`, creada
desde `main` en este mismo punto (incluye todo el trabajo de `MotionGate` ya pausado).

Fase A mide, con datos reales en esta Pi 5 (no en teoría): si el paquete `mediapipe`
instala en esta arquitectura/versión de Python, latencia real de inferencia contra un
banco de imágenes fijas (luz normal, 2-3 tonos de piel, varias distancias, al menos un
caso sin mano), CPU/memoria bajo inferencia sostenida de varios minutos, estabilidad
de los landmarks frame a frame con una mano real quieta, y si el modelo
(`hand_landmarker.task`) se puede empaquetar localmente en el repo (como ya está
`yolov8n.pt`) sin que producción dependa de internet. Resultado esperado: datos crudos
para decidir si se justifica seguir a las Fases B en adelante — no una implementación
funcional todavía.

---

## Capturador temporal de frames reales para el spike de MediaPipe (2026-09-25)

Autorizado explícitamente por JD para destrabar los puntos 2 y 4 de la Fase A del
spike (banco de imágenes reales y estabilidad temporal de landmarks), que llevaban
bloqueados desde el 2026-09-21 por falta de acceso a cámara. En vez de darle acceso de
cámara a esta sesión, se agrega un capturador temporal a `main.py` de producción,
gateado por una variable de entorno que por defecto está apagada.

### Tasklist

- [x] `config.CAPTURE_FRAMES_DIR` (env `CVA_CAPTURE_FRAMES_DIR`) — sin setear, cero
      cambio de comportamiento.
- [x] `main.py`: al procesar cada frame, si la variable está seteada, guardar además
      una copia del JPEG crudo a disco, con nombre `{epoch_ms}__{etiqueta}.jpg`. Sin
      tocar la lógica de cooldown/detección/stabilizer existente.
- [x] Tests nuevos (`tests/test_main.py`, 4 casos) — capturador apagado no escribe
      nada; frame procesado se guarda con su gesto real o `sin_gesto`; frame saltado
      por cooldown se guarda como `sin_evaluar` (no `sin_gesto` — nunca se evaluó,
      etiquetarlo como "sin gesto" sería un dato falso). `pytest`: **54 passed**
      (50 previos + 4 nuevos).
- [x] Setear la variable, reiniciar el servicio, confirmar con `systemctl status` que
      sigue corriendo normal y que la carpeta se creó.
- [x] Avisar (a través de JD) cuando esté listo para la sesión de prueba real.
- [x] Al terminar JD: apagar la variable, reiniciar de nuevo, confirmar que volvió al
      estado normal. Sesión real: 2026-09-25, **2132 frames** capturados en ~5.2 min
      (2070 `sin_evaluar`, 43 `sin_gesto`, 19 con gesto del sistema actual: 8
      `dedo_pulgar`, 6 `dedo_menique`, 5 `puño_cerrado`, 0 `palma_abierta` — esa
      etiqueta es solo del detector YOLO+OpenCV actual al momento de capturar, no
      condiciona el análisis real con MediaPipe que sigue).
- [x] Mover (no copiar) los frames capturados al worktree del spike — confirmado que
      `captured_frames/` ya no existe en el directorio de producción.
- [ ] Correr HandLandmarker sobre los frames reales: confianza por condición,
      variación frame a frame de landmarks en tramos sostenidos (número concreto),
      confirmar 0 detecciones en los frames "sin mano".
- [ ] Documentar en `BITACORA.md` (esta, o la del worktree del spike) los números
      crudos y al menos una imagen de ejemplo por gesto con los landmarks dibujados.

### Detalle de la implementación

- `config.py`: `CAPTURE_FRAMES_DIR = os.environ.get("CVA_CAPTURE_FRAMES_DIR")` — sin
  default, `None` si no se setea.
- `main.py`:
  - `_prepare_capture_dir_if_configured()`: crea la carpeta al arrancar (no de forma
    perezosa en el primer frame) para poder confirmarla enseguida tras el reinicio;
    loguea un `WARNING` explícito ("CAPTURA TEMPORAL ACTIVA") recordando apagarla.
  - `_capture_frame_to_disk()`: escribe el JPEG crudo tal cual llegó (sin
    recodificar). Un fallo de escritura (disco lleno, permisos) se loguea como
    `ERROR` explícito pero no interrumpe la sesión de prueba real — no vale la pena
    tumbar la conexión del cliente por un problema de captura, que es solo
    instrumentación temporal.
  - Dentro de `on_jpeg_frame`, la captura corre en `run_in_executor` (I/O de disco,
    igual criterio que la inferencia) tanto para el frame que sí se procesa (etiqueta
    = gesto real o `sin_gesto`) como para el que cae en cooldown (etiqueta
    `sin_evaluar`, para no mezclar "no se detectó nada" con "nunca se evaluó").
  - `capture_dir` se lee de `config` **una sola vez**, al armar el closure
    (`_make_on_jpeg_frame`), no en cada frame — si se cambiara la variable de entorno
    en caliente no se notaría hasta el próximo reinicio, que es exactamente el
    comportamiento esperado (systemd solo relee el entorno al reiniciar el proceso).

**Pendiente:** commitear esto, luego los pasos 2 en adelante de la tasklist (activar,
coordinar con JD, capturar, apagar, mover, medir, documentar).

### Mejora de infraestructura — `EnvironmentFile` en el drop-in del servicio (2026-09-25)

Para activar `CVA_CAPTURE_FRAMES_DIR` en el proceso real (corre bajo `systemd`) hacía
falta editar `/etc/systemd/system/cva-gesture-bridge.service`, propiedad de `root` —
esta sesión no tiene `sudo` interactivo (mismo bloqueo que ya había con el nivel de
log). En vez de pedirle a JD `sudo` cada vez que haga falta una variable nueva, se le
pidió un cambio de infraestructura de una sola vez: JD corrió
`sudo systemctl edit cva-gesture-bridge.service` y agregó un drop-in
(`/etc/systemd/system/cva-gesture-bridge.service.d/override.conf`):

```ini
[Service]
EnvironmentFile=-/home/david_cardenas/video_analitica/rasperry_pi-de-video-analitica/.capture.env
```

(el `-` inicial = "si el archivo no existe, seguir sin error"). Tomó dos intentos: el
primero guardó el drop-in vacío (no se escribió nada en la sección editable), el
segundo tuvo un typo (`EnviromentFile`, sin la "n" de "Environment"). Confirmado en el
tercer intento con `systemctl show -p EnvironmentFiles` (mostraba la ruta correcta) y
`systemctl status` (sección `Drop-In:` visible).

**De ahora en adelante**, activar/desactivar variables de entorno para este servicio
(esta captura, o futuros ajustes como el nivel de log) no necesita `sudo` — alcanza con
escribir/editar `.capture.env` (archivo propio de `david_cardenas`, gitignorado) y
reiniciar matando el PID (`Restart=always` lo revive leyendo el archivo actualizado).

**Confirmación real de la activación:**
- `cat /proc/<PID>/environ` del proceso nuevo (PID `71566`) muestra
  `CVA_CAPTURE_FRAMES_DIR=/home/david_cardenas/video_analitica/rasperry_pi-de-video-analitica/captured_frames`.
- La carpeta `captured_frames/` se creó al arrancar (confirmado con `ls`).
- El log real tiene la línea `WARNING ... [CAPTURA TEMPORAL ACTIVA] Guardando copia de
  cada frame en .../captured_frames -- desactivar...`.
- `systemctl status`: `Active: active (running)`, sin errores.

**Esperando que JD confirme que está listo para hacer la sesión de prueba real** (los
4 gestos, distintas distancias, un tono de piel distinto si consigue a alguien, y unos
segundos sin mano) — avisar apenas termine para apagar la captura de inmediato.

---

## Fase B — reemplazar YOLO+OpenCV por MediaPipe HandLandmarker (2026-09-25)

Con la Fase A cerrada con datos reales (ambas sesiones de captura, ver `BITACORA.md`
de la rama `spike/fase8-mediapipe-viabilidad`, commits `1730153` y `10d71de`), JD
aprobó avanzar a Fase B: esto ya es implementación real sobre
`cva_gesture_bridge/vision/detector.py`, no un spike descartable — pero **todavía no
se toca el `.venv` de producción ni se reinicia el servicio real sin autorización
explícita**, igual que con el capturador temporal.

### Housekeeping antes de tocar código

1. **Rama huérfana en origin (`fase8-mediapipe-viabilidad`, sin el prefijo `spike/`):**
   quedó abandonada cuando el trabajo real se continuó bajo `spike/fase8-mediapipe-viabilidad`
   (la correctamente nombrada, con todos los commits). Pedido borrarla. **Bloqueado:**
   esta sesión no tiene credenciales de push (mismo problema de siempre) — se le pidió
   a JD el comando `git push origin --delete fase8-mediapipe-viabilidad` para que lo
   corra él. **Pendiente de confirmación.**
2. **Rama y worktree nuevos para Fase B:** `fase8-fase-b-mediapipe-pipeline`, creada
   desde `main` (commit `109a112`, el mismo que `origin/main`). Worktree en
   `~/video_analitica/cva-pi-repo-fase-b`, venv propio `.venv-fase-b/` (mismo patrón
   que el spike — aislado del `.venv` de producción). **Pendiente de push inicial**
   (mismo bloqueo de credenciales) — JD tiene los comandos para correrlo.

### Investigación de dependencias — con evidencia real, no supuesta

**¿`opencv-contrib-python` sirve como reemplazo drop-in de `opencv-python` en este
proyecto?** Se listaron todos los símbolos de `cv2` realmente usados en
`detector.py`/`main.py`/tests (`imencode`, `imdecode`, `cvtColor`, `inRange`,
`morphologyEx`, `findContours`, `contourArea`, `convexHull`, `convexityDefects`,
`boundingRect`, `threshold`, `dilate`, `absdiff`, `bitwise_and`,
`getStructuringElement`, `ellipse`, `fillPoly`, `rectangle`, más las constantes) — se
instaló `mediapipe` en el venv nuevo (que trae `opencv-contrib-python` como
dependencia, **sin `opencv-python` instalado en absoluto**), se confirmó que los 28
símbolos existen (`hasattr`, ninguno faltante), y **se corrió la suite completa de
tests existente contra ese venv: 54 passed, 0 failed.** Confirmado empíricamente, no
solo por documentación de que "contrib es superset" — **sí sirve como reemplazo
drop-in** para el uso real de este proyecto.

**¿Se puede sacar `torch`/`torchvision`/`ultralytics` de `requirements.txt`?** Sí,
confirmado: ningún archivo del repo fuera de `detector.py` (que se va a reescribir
para no usar YOLO) los importa, y el propio `GestureDetector.__init__` es el único
punto que hace `from ultralytics import YOLO` — con MediaPipe localizando la mano
directamente (sin necesitar YOLO para acotar la región "persona" primero), ese import
desaparece por completo. `requirements.txt` de esta rama quedó reducido a
`mediapipe==1.0.1` + `opencv-contrib-python==5.0.0.93` (esta última ya viene como
dependencia transitiva de mediapipe, se fija la versión explícita de todos modos por
reproducibilidad, mismo criterio que el resto del proyecto).

**Nota:** esto es una decisión para *esta rama* (`fase8-fase-b-mediapipe-pipeline`) —
`requirements.txt` de `main` no se toca todavía; el servicio real sigue con
torch/torchvision/ultralytics/opencv-python hasta que Fase B se apruebe y se
despliegue.

### Implementación — `GestureDetector` con `HandLandmarker` (2026-09-25)

Reescrito `cva_gesture_bridge/vision/detector.py` completo: `HandLandmarker` en
`RunningMode.VIDEO` (timestamps estrictamente crecientes, `_next_timestamp_ms()`
fuerza el mínimo incremento de 1ms si el reloj de pared se repite), clasificación
sobre `hand_world_landmarks` (coordenadas de mundo 3D en metros, normalizadas por el
tamaño real de la mano — no depende de qué tan cerca esté del lente), `OneEuroFilter`
+ `LandmarkSmoother` nuevos (suavizado de las 63 coordenadas de la secuencia
temporal), y **retirados `MotionGate`, la segmentación HSV de piel y el recorte de
franja superior** — el docstring del módulo documenta en detalle por qué (conflicto
estructural real de `MotionGate` con una mano que cambia de gesto sin salir de
cuadro, confusión geométrica cara/torso/mano de HSV a 320x240 — ambos ya
confirmados con datos reales en Fase 8, no una corazonada). `GestureResult` y
`GestureStabilizer` quedaron sin cambios (compatibles con `main.py` tal cual).
`config.py`/`main.py` actualizados para instanciar `GestureDetector` con el modelo y
los 3 umbrales propios de MediaPipe (`min_hand_detection_confidence`,
`min_hand_presence_confidence`, `min_tracking_confidence`, default 0.5 cada uno,
ajustables por variable de entorno). Modelo (`models/hand_landmarker.task`, 7,819,105
bytes, mismo binario oficial de Google usado en el spike) descargado a esta rama,
gitignorado (`*.task`).

### Bug real encontrado y corregido: umbral de "pulgar extendido" en coordenadas de mundo

Los ratios heredados del spike (`_THUMB_EXTENDED_RATIO=1.3`, medido contra el nudillo
base del índice en vez de la muñeca) se habían marcado explícitamente en el código
como "sujetos a ajuste con fixtures reales, no se dan por buenos a ciegas" — y en
efecto, al probarlos contra un pulgar-arriba real (fixture real, ver abajo), el ratio
medido fue **1.253, por debajo del umbral de 1.3** → se clasificaba como
`puño_cerrado` en vez de `dedo_pulgar` (falso negativo real). Solución: medir el
pulgar contra la **muñeca**, igual que los otros 4 dedos (antes era el único que se
medía contra el nudillo del índice), con umbral recalibrado a **1.7** —
midpoint entre el ratio real de un puño (1.424) y el de un pulgar-arriba real
(2.184), con margen amplio a ambos lados. Verificado que esto no rompe la
clasificación de puño ni de palma abierta contra los mismos fixtures reales (ver
tabla abajo). El campo `INDEX_MCP` quedó sin uso tras el cambio y se eliminó.

### Hallazgo real: las etiquetas de nombre de archivo de Fase A (sistema viejo,
YOLO+OpenCV) NO son ground truth confiable

Al elegir fixtures reales para los tests, se tomaron inicialmente los frames con
mayor confianza por gesto según la etiqueta embebida en el nombre de archivo (ese
nombre lo pone el sistema VIEJO en tiempo real durante la captura, no una anotación
manual). Al inspeccionar las imágenes reales una por una (no solo confiar en el
nombre), se encontró que **al menos 3 de los 4 archivos "mejor etiquetados" tenían la
etiqueta equivocada**: los 3 candidatos con mayor confianza de "puño_cerrado" eran en
realidad fotos reales de **pulgar arriba** (thumbs-up claro, visualmente inequívoco),
y un candidato "dedo_menique" resultó ser un puño cerrado real. Esto es evidencia
adicional, independiente de todo lo ya documentado en Fase 7/8, de por qué el sistema
viejo era poco confiable — y una advertencia concreta para el futuro: **nunca usar la
etiqueta de nombre de archivo de estos frames como ground truth sin inspección
visual**, incluso dentro de esta misma carpeta de fixtures.

### Fixtures reales — `tests/fixtures_real/` (gitignorado, fotos reales de JD, nunca al repo)

Confirmados uno por uno visualmente (no por su nombre de archivo original) y luego
verificados con el `GestureDetector` real de esta fase, cada uno con una instancia
**nueva** (no compartida — ver más abajo por qué):

| archivo                        | gesto real (confirmado a ojo) | `GestureDetector.detect()` | confianza |
|---------------------------------|-------------------------------|------------------------------|-----------|
| `fixture_puno_cerrado.jpg`      | puño cerrado                  | `puño_cerrado`               | 0.9983    |
| `fixture_palma_abierta.jpg`     | palma abierta                 | `palma_abierta`              | 0.9678    |
| `fixture_dedo_pulgar.jpg`       | pulgar arriba                 | `dedo_pulgar`                | 0.9894    |
| `fixture_dedo_menique.jpg`      | meñique (reemplazado 2026-10-02, ver sección abajo) | `dedo_menique` | 0.9952 |
| `persona_cara_sin_mano.jpg`     | cara+torso real, sin mano posada, redimensionado a 320x240 (resolución real del cliente) | `None` | 0.0 |
| `persona_torso_sin_mano_1.jpg`  | torso real, sin mano en cuadro (frame real de una racha de 96 frames consecutivos con 0 manos, sesión 2 de Fase A) | `None` | 0.0 |
| `persona_torso_sin_mano_2.jpg`  | ídem, otro frame de la misma racha | `None` | 0.0 |
| frame negro sintético (240x320) | vacío                          | `None`                        | 0.0       |

**Nota histórica sobre `fixture_dedo_menique.jpg` (resuelta, ver sección siguiente):**
la primera versión de este fixture (confianza 0.9992) quedó con una salvedad
documentada — el dedo levantado no se podía confirmar a ojo con certeza total desde
la foto plana, por la rotación de la mano en ese frame. JD pidió una sesión nueva
específicamente para resolver esto, y el fixture fue reemplazado — ver "Recaptura de
`dedo_menique` sin ambigüedad visual (2026-10-02)" más abajo.

**Caso histórico investigado y resuelto — cara/torso como falso positivo (pedido
explícito de JD, `persona_cara_sin_mano.jpg` es la misma imagen que `zidane.jpg` del
spike):** en Fase A, el benchmark original reportó "1 mano detectada" para esta foto.
Investigado a fondo en esta fase: el detector **crudo** de MediaPipe, alimentado con
la imagen a la resolución real del cliente (320x240, no a resolución completa —
la discrepancia con corridas anteriores era justamente esa, resolución completa daba
0 manos, 320x240 sí detecta algo), **sí encuentra un "hand" en esta cara/torso real**,
con score de handedness alto (0.795–0.912 en 3 corridas). Esto **no es un falso
negativo del modelo** — es un verdadero landmark geométrico que MediaPipe interpreta
como mano en esa región de piel/tela. Lo que evita que esto llegue como un gesto real
al cliente es la capa de clasificación de esta app: el patrón de dedos extendidos que
resulta de esos landmarks (2 dedos "extendidos" en la corrida final verificada) no
coincide con ninguno de los 4 patrones del catálogo (ni puño, ni palma, ni pulgar
solo, ni meñique solo) — así que `classify_world_landmarks` devuelve `None`,
`GestureResult.confidence` queda en 0.0, y `GestureStabilizer` nunca lo confirmaría
aunque se repitiera (necesita el mismo gesto no-None 2 de 3 veces). **Resuelto y
entendido, no ignorado** — con una salvedad honesta para el futuro: esta protección
depende de que el patrón geométrico de un falso positivo de piel/cara no coincida
por casualidad con uno de los 4 patrones específicos del catálogo; no es una garantía
absoluta para cualquier imagen posible, es lo verificado contra los fixtures reales
disponibles hoy.

**Torso solo:** dos frames reales de una racha de 96 frames consecutivos con
`num_hands==0` (confirmado por el propio análisis de Fase A, sesión 2, CSV
`captured_frames_analysis_2026-09-25_v2.csv`) — JD sentado frente a cámara sin mano
en cuadro. Ambos dan `None`/0.0 con el detector de esta fase, sin necesitar
`MotionGate` para lograrlo.

**Meta de JD ("cero falsos positivos de los tres tipos históricos: cara-como-puño,
ruido de piel, torso-como-puño") — cumplida contra estos fixtures reales**, con la
salvedad explícita de arriba sobre por qué (clasificación geométrica, no ausencia de
detección cruda).

### Descubrimiento de testing: `RunningMode.VIDEO` con fotos sueltas sin relación temporal

Durante la investigación se encontró que reusar una misma instancia de
`GestureDetector`/`HandLandmarker` para varias fotos reales sin relación temporal
(no una secuencia de video real) da resultados **distintos según el orden y los
timestamps** en que se le pasan — el mismo frame de `palma_abierta` pasó de "0 manos
detectadas" a clasificar correctamente solo por usar una instancia nueva en vez de
reusar una ya "contaminada" por el frame anterior. Tiene sentido: `VIDEO` mode asume
continuidad temporal real entre llamadas (para eso existe el tracking interno) y
fotos sueltas violan esa asunción. **No afecta el uso real en producción** (los
frames del cliente sí son una secuencia real) — pero si afecta cómo se deben escribir
tests contra fixtures reales: `tests/test_detector.py` usa una fixture
`fresh_detector` (function-scoped, instancia nueva por test) por esta razón,
documentado en el docstring del archivo.

### Resultado de tests

`tests/test_detector.py` reescrito completo (35 tests: geometría sintética de
`extended_fingers_pattern`/`classify_world_landmarks`, `OneEuroFilter`/
`LandmarkSmoother`, `format_line`, `GestureStabilizer` sin cambios, y 8 tests contra
los fixtures reales de arriba). Suite completa del repo: **63 passed, 0 failed**
(`.venv-fase-b/bin/pytest -q`).

### Recaptura de `dedo_menique` sin ambigüedad visual (2026-10-02)

JD pidió resolver la salvedad documentada arriba con una sesión de captura nueva,
corta y puntual (mismo procedimiento ya usado dos veces en Fase A: descomentar
`CVA_CAPTURE_FRAMES_DIR` en `.capture.env`, `sudo systemctl restart
cva-gesture-bridge.service`, grabar, comentar de nuevo, reiniciar otra vez).

**Primeros dos intentos no sirvieron** — JD hizo el gesto (puño con el meñique
estirado) pero con la mano de canto/rotada hacia la cámara, igual que el fixture
original: sin ver el pulgar en el cuadro, seguía sin poder confirmarse a ojo cuál
dedo era. Se le pidió un tercer método, más simple y sin depender de rotar la
muñeca: **mano abierta de frente a la cámara (palma visible) → ir doblando un dedo a
la vez (pulgar, índice, medio, anular) hasta dejar solo el meñique**, repetido con
ambas manos. Esto sí funcionó: dio una secuencia completa con un frame de referencia
de palma abierta, lo que permite rastrear la posición del dedo que queda al final
contra esa referencia, en vez de depender de una sola foto aislada.

**Verificación, no solo inspección visual de una foto:** se comparó la posición del
dedo levantado en el frame final contra la posición del meñique en el frame de
palma abierta de la misma secuencia (mismo encuadre, mismo brazo levantado) — cae
exactamente en el lugar del dedo más alejado del pulgar. Además, se corrió el
`GestureDetector` real contra 14 frames candidatos (7 por mano, vecinos del momento
de "1 dedo" en cada secuencia): **13 de 14 clasificaron `dedo_menique` con confianza
entre 0.97 y 0.995** (el único que no, dio 2 dedos extendidos — frame de transición,
descartado). Se eligió el de mayor confianza de la primera mano:
`1790997084005__sin_gesto.jpg` (confianza 0.9952).

**Reemplazado `tests/fixtures_real/fixture_dedo_menique.jpg`** con este frame.
Vuelto a correr `GestureDetector.detect()` contra el nuevo fixture (instancia
fresca, mismo criterio que el resto): `gesture=dedo_menique, confidence=0.9952,
extended_fingers=1`. Quitada la salvedad de ambigüedad del comentario del test
correspondiente en `tests/test_detector.py`, reemplazada por la explicación de cómo
se verificó esta vez.

**Suite completa: 63 passed, 0 failed** — mismo conteo que antes del reemplazo, sin
romper nada.

**Los 1946 frames de la sesión** (dos intentos fallidos + el exitoso, todos juntos
porque el capturador no se desactivó entre intentos) se **movieron** (no copiaron) a
`~/video_analitica/cva-pi-repo-spike/spike_mediapipe/captured_frames_2026-10-02_menique/`
— mismo patrón que las dos sesiones de Fase A, gitignorado, nunca al repo. El
patrón de `.gitignore` de esa rama (`spike/fase8-mediapipe-viabilidad`) solo cubría
la fecha `2026-09-25` explícitamente — corregido a cualquier fecha
(`captured_frames_*/`) antes de hacer cualquier `git add`, confirmado con `git
status --ignored` que las 3 carpetas de sesiones reales quedan ignoradas. Commit
`ee6eba1` en esa rama.

Capturador desactivado y confirmado contra el proceso real (`CVA_CAPTURE_FRAMES_DIR`
ya no está en el entorno del PID activo) al cierre de esta sesión.

### Pendiente antes de cerrar Fase B

- Push de ambas ramas (`fase8-fase-b-mediapipe-pipeline` y el fix de `.gitignore` en
  `spike/fase8-mediapipe-viabilidad`) — bloqueado por credenciales, igual que el
  housekeeping anterior; JD debe correrlo directamente.
- Autorización explícita de JD antes de: tocar el `.venv` de producción, reiniciar
  `cva-gesture-bridge.service` para desplegar (el reinicio para
  activar/desactivar el capturador ya fue autorizado y usado, eso es distinto), o
  desplegar este código — nada de eso se hizo ni se hará sin ese aviso previo.

## Fase C2 — bajar el tiempo de confirmación de ~15s hacia AC3 (<1.5s) (2026-10-04)

JD aprobó seguir con la Fase C2 del plan (`CVA_deteccion-gestos_plan.md` §3, no vive
en este repo — ver CLAUDE.md) antes de medir contra AC3 o considerar despliegue:
reconsiderar `GESTURE_COOLDOWN_SECONDS` y rediseñar la confirmación con datos reales,
no a ojo. Sigue sobre `fase8-fase-b-mediapipe-pipeline`, mismo worktree. Nada de esto
tocó el `.venv` de producción ni el servicio real.

### 1. Costo real sostenido sin cooldown — `benchmarks/sustained_load.py`

Corrido contra los 7805 frames reales disponibles (las 2 sesiones de Fase A + la
sesión de recaptura de meñique), una sola instancia de `GestureDetector` (igual que
producción), espalda con espalda sin ningún cooldown artificial, ~4.5 min seguidos:

- Latencia por frame: avg=34.5ms, p50=31.9ms, p95=46.0ms, p99=68.7ms, max=111.9ms.
- CPU: avg=100.7%, max=101.8% de 4 núcleos (1 solo núcleo saturado, sostenido, sin
  degradarse en los 4.5 min).
- RSS: 209.2MB → 214.2MB (estable, sin fuga).
- El cliente real manda frames a ~7fps (~143ms entre frames, CLAUDE.md sección 2) —
  margen de la Pi sobre ese ritmo: **4.1x en el caso típico (avg), 3.1x en el peor
  caso medido (p95)**; incluso el frame más lento observado (111.9ms) queda dentro
  del intervalo real entre frames.

**Decisión, con el número que la respalda:** `GESTURE_COOLDOWN_SECONDS` pasa de `5`
a **`0.0`** (eliminado, no solo bajado) — la Pi sostiene evaluar cada frame real sin
saturarse, con margen real medido, no supuesto.

### 2. Ruido real de clasificación por frame — `benchmarks/analyze_raw_stability.py` + `inspect_noise_composition.py`

Corrido el detector real (sin cooldown, sin estabilizador) sobre las 3 sesiones
completas, detectando automáticamente 40 tramos de gesto genuinamente sostenido
(≥15 frames con el mismo gesto dominante, detectado por moda en una ventana de
adelanto, no por la etiqueta del nombre de archivo del sistema viejo).

- 31 de 40 tramos: **cero ruido**, el frame crudo coincidió con el gesto dominante
  el 100% del tiempo.
- Tasa de ruido global: avg=1.81%, máximo en un tramo=30.0% (el tramo más ruidoso,
  de la sesión de recaptura de meñique).
- Racha de ruido más larga observada en cualquier tramo: **14 frames seguidos**.
- **Composición del ruido (88 frames de ruido en total, los 40 tramos): 100% fueron
  `None`** (mano perdida un instante) — **0% fueron otro gesto real en conflicto**.
  Confirmado explícitamente, no asumido (`inspect_noise_composition.py` desglosa
  cada tramo con ruido y qué valor tomó cada frame de ruido).

**Por qué esto importa para el diseño:** si el ruido real nunca es "otro gesto
consistente", la histéresis (quedarse en el último gesto confirmado mientras la
señal cruda se pierde, sin exigir que el gesto nuevo gane una mayoría para
*mantenerse* confirmado) no corre el riesgo real de "pegar" un gesto incorrecto —
solo necesita sobrevivir rachas de `None`.

### 3. `GestureStabilizer` rediseñado — ventana por mayoría CON histéresis

Reemplaza el esquema de Fase 8 (ventana de 3, 2 coincidencias, **sin** histéresis —
un solo frame de ruido ya tiraba la confirmación a `None`, y con
`GESTURE_COOLDOWN_SECONDS=5` eso eran ~15s en el peor caso: 3 detecciones procesadas
× 5s). Dos reglas separadas, en `cva_gesture_bridge/vision/detector.py`:

- **Confirmar/cambiar de gesto**: `min_matches` (default **5**) de las últimas
  `window_size` (default **7**) observaciones crudas — no exige racha exacta.
- **Soltar un gesto ya confirmado (histéresis)**: solo tras
  `release_after_misses` (default **20**) observaciones SEGUIDAS que no sean el
  gesto confirmado — con margen real (20) sobre la racha de ruido más larga medida
  (14, punto 2 arriba), no un número arbitrario.

`config.py` expone los tres como `GESTURE_STABILITY_WINDOW`,
`GESTURE_STABILITY_MIN_MATCHES`, `GESTURE_RELEASE_AFTER_MISSES` (variables de
entorno `CVA_*` correspondientes). `main.py` actualizado para pasar el tercer
parámetro a `GestureStabilizer`.

### 4. Tiempo de confirmación real, simulado con datos reales — `benchmarks/simulate_confirmation_time.py`

No es una medición con hardware real todavía (eso es Fase D) — es una simulación
honesta: la MISMA secuencia cruda de gestos que salió del detector real sobre los
3727+2132+1946 frames capturados, con sus timestamps REALES de llegada (no un fps
asumido), pasada por el `GestureStabilizer` nuevo (cooldown≈0, se evalúa cada
frame), un solo stabilizer por sesión (igual que producción: una instancia por
conexión).

- **39 de 40 tramos confirmados dentro de AC3 (<1500ms).**
- Tiempo de confirmación: avg=597ms, p50=573ms, p95=825ms, min=0ms (2 tramos
  llegaron ya confirmados por histéresis desde un tramo anterior del mismo gesto,
  sin necesitar reconfirmar nada).
- **1 tramo superó AC3: 2205ms** (el primer tramo de la sesión de recaptura de
  meñique). Investigado, no descartado sin más: entre los frames 35 y 36 de esa
  sesión hay un salto real de **~1.75s** en la llegada de frames — un hueco real de
  arranque de sesión, no una lentitud del algoritmo. **Hallazgo adicional, honesto,
  no buscado a propósito:** ese mismo hueco (~1.7-1.8s) aparece en el mismo índice
  de frame (~35) en las **3** sesiones grabadas independientemente, y después
  reaparecen huecos más chicos (~500-600ms) cada ~34 frames durante toda cada
  sesión — un patrón sistemático en la cadencia real de llegada de frames del
  capturador/cliente viejo, no algo que `GestureStabilizer` pueda arreglar ni que
  esta fase introdujo. Si AC3 necesita cumplirse sin excepción, esto es candidato a
  investigarse aparte (posible tema de Fase D) — no se investiga más a fondo aquí
  porque excede el alcance de "rediseñar la confirmación".

### 5. Verificación de falsos positivos contra el esquema nuevo (no solo el detector crudo)

Pedido explícito de JD: correr la batería de fixtures contra el **esquema de
confirmación nuevo**, porque la histéresis es justo el mecanismo que podría hacer
que un falso positivo aislado se "pegara" más tiempo si estuviera mal diseñado.
Nuevos tests en `tests/test_detector.py` (detector real + `GestureStabilizer` real
juntos, mismo fixture alimentado 30 veces seguidas simulando una vista sostenida):

- Cara real sin mano (`persona_cara_sin_mano.jpg`, 30 observaciones): **nunca
  confirma ningún gesto.**
- Torso real sin mano (`persona_torso_sin_mano_1.jpg`, 30 observaciones): **nunca
  confirma ningún gesto.**
- Frame negro sintético (30 observaciones): **nunca confirma ningún gesto.**
- Control positivo (`fixture_puno_cerrado.jpg`, 10 observaciones): confirma
  `puño_cerrado` correctamente — confirma que el esquema nuevo sí reconoce el caso
  válido, no solo que rechaza los falsos positivos.

**Meta de JD cumplida: cero falsos positivos de los 3 tipos históricos contra el
esquema de confirmación nuevo**, con el mismo criterio de honestidad que el resto
del proyecto (ver también la salvedad ya documentada en Fase B sobre por qué esta
protección depende del patrón geométrico, no es una garantía absoluta para
cualquier imagen posible).

### Resultado de tests

`tests/test_detector.py`: stabilizer reescrito completo (8 tests nuevos de
histéresis, reemplazan los 6 de Fase 8) + 4 tests nuevos de integración
detector+stabilizer contra fixtures reales. `tests/test_main.py`: las 13 llamadas a
`_make_on_jpeg_frame` actualizadas con el parámetro nuevo
(`stability_release_after_misses`), comportamiento verificado sin cambios para los
casos que ya cubrían. **Suite completa: 69 passed, 0 failed**
(`.venv-fase-b/bin/pytest -q`).

### Scripts de esta fase (no se importan desde `cva_gesture_bridge`, no son parte del paquete)

`benchmarks/sustained_load.py`, `analyze_raw_stability.py`,
`cache_raw_sequences.py` (caché intermedio, gitignorado, regenerable),
`inspect_noise_composition.py`, `simulate_confirmation_time.py` — todos corren con
`.venv-fase-b/bin/python`, documentados con su propio docstring explicando qué
miden y por qué.

### Pendiente antes de cerrar Fase C2 / para Fase D

- Push de la rama — bloqueado por credenciales, igual que siempre.
- Autorización explícita de JD antes de tocar el `.venv` de producción o el
  servicio real — nada de eso se hizo aquí.
- El hallazgo del punto 4 (huecos sistemáticos de llegada de frames, ~1.7-1.8s cerca
  del frame 35 de cada sesión, ~500-600ms cada ~34 frames después) no se investigó
  a fondo — queda anotado para cuando llegue la medición end-to-end real de Fase D
  contra AC3 con hardware real, donde si vuelve a aparecer sí bloquearía el
  criterio de aceptación.
- La simulación del punto 4 usa frames ya capturados por el sistema VIEJO (Fase 8)
  con su propia cadencia real de llegada — no es lo mismo que medir end-to-end con
  el bridge de Fase B corriendo de verdad contra un cliente real. Es la mejor
  aproximación disponible sin tocar producción; la medición definitiva es Fase D.

## Fase C2 — corrección antes de Fase D (2026-10)

Revisión externa de `afe5f7b` (ya fusionado en `main` vía PR #2) encontró que
`main.py` no usaba bien el `GestureStabilizer` nuevo: mandaba el resultado CRUDO de
cada frame, no el gesto ya CONFIRMADO, y con `GESTURE_COOLDOWN_SECONDS≈0` eso
significaba una línea por frame mientras se sostenía un gesto. Trabajo hecho sobre
una rama nueva desde `main` (`fase-c2-fix-confirmed-output`, worktree
`~/video_analitica/cva-pi-repo-fase-c2-fix`) — no toca el `.venv` de producción ni
el servicio real.

### 1. Bug real confirmado antes de tocar nada

`cva_gesture_bridge/main.py` línea 130 (antes del fix): `detector.format_line(result)`
usaba `result` (el `GestureResult` crudo de ESE frame), no `confirmed_gesture` (lo
que de verdad gatea el envío un poco más arriba). Con un gesto sostenido y
`cooldown≈0`, un solo frame ruidoso de otro gesto (ej. `dedo_pulgar` en medio de un
`puño_cerrado` sostenido) se mandaba al cliente tal cual, aunque el gesto
*confirmado* siguiera siendo `puño_cerrado` por histéresis. Confirmado leyendo el
código real antes de asumir que el reporte externo tenía razón.

### 2. Gesto confirmado + confianza asociada

`GestureStabilizer.observe()` ahora recibe `confidence` además de `gesture`, y
expone `confirmed_confidence` (propiedad de solo lectura): la confianza del
**último frame que coincidió con el gesto confirmado**, no la del frame crudo
actual (que puede ser de otro gesto por ruido aislado). Elegida esta opción (vs.
promediar toda la ventana) porque refleja la evidencia más reciente real del gesto
sostenido, sin arrastrar confianzas viejas de muy atrás en una sesión larga de
histéresis, y es la más simple de razonar/testear. `main.py` arma la línea con
`detector.format_line(confirmed_gesture, stabilizer.confirmed_confidence)`, no con
`result`. `format_line()` (función libre y método de `GestureDetector`) cambiaron
de firma: toman `gesture`/`confidence` sueltos, ya no un `GestureResult` completo.

### 3. Enviar solo en el cambio, con evento explícito de liberación

Con `cooldown≈0` (Fase C2), antes de este fix se mandaba una línea por cada frame
procesado mientras un gesto seguía confirmado. Ahora `main.py` guarda
`last_sent_gesture` en el closure y solo llama a `TcpServer.send_line` cuando
`confirmed_gesture` cambia respecto al último valor mandado:

- Gesto nuevo confirmado (o cambio de gesto): se manda `"gesto: X, confianza: Y"`
  una sola vez.
- Gesto confirmado se suelta (histéresis libera): se manda `RELEASE_LINE =
  "gesto: ninguno"` una sola vez — evento explícito para que el cliente, y la Fase
  9 cuando ejecute instrucciones reales, sepan que terminó en vez de inferirlo por
  silencio.
- Nada se manda al arrancar si nunca hubo nada confirmado (el chequeo
  `confirmed_gesture == last_sent_gesture` con ambos en `None` lo cubre).

**Sobre el "latido periódico" que planteaba el reporte** (reenviar la línea cada
cierto intervalo mientras se sostiene, útil si un actuador real necesita
refrescarse continuamente): **no se implementó en esta corrección.** No hay datos
reales todavía para justificar un intervalo — el único caso de uso que lo
necesitaría (Fase 9, accionar un actuador de forma continua mientras se sostiene un
gesto, ej. un robot que debe seguir moviéndose) no está construido, así que
cualquier número que se elija ahora sería inventado, no medido. Queda anotado como
decisión pendiente para cuando Fase 9 defina los requisitos reales de control
continuo — si hace falta, se agrega entonces con el mismo criterio de "con datos,
no a ojo" del resto de este proyecto.

### 4. Tiempo de liberación — dos umbrales, con un hallazgo real que corrigió la intuición inicial

Con un solo `release_after_misses=20`, retirar la mano de cuadro de verdad dejaba
un "gesto fantasma" confirmado ~2.86s de más (20 misses × ~143ms) — problema real
si esto llega a controlar un actuador. La intuición inicial (y la del reporte
externo) era: "mano ausente no tiene ambigüedad que proteger, se puede soltar casi
de inmediato" — un umbral corto, separado del que protege contra ruido con mano
presente.

**Esa intuición no se sostuvo contra los datos reales.** Se extendió el caché de
secuencias (`cache_raw_sequences.py`, ahora guarda también `extended_fingers`) y se
corrió `benchmarks/inspect_noise_by_hand_presence.py` sobre los mismos 40 tramos de
gesto genuinamente sostenido: de 87 frames de ruido total, 23 (26.4%) fueron mano
AUSENTE (`gesture=None`, `extended_fingers==0` — el único caso donde eso pasa
junto, ver `classify_world_landmarks`) y 64 (73.6%) mano presente pero
ambigua/conflicto. **La racha de ruido más larga con mano ausente fue 13 frames
seguidos — casi igual que los 14 de mano presente.** Un umbral corto (se había
puesto un placeholder de 3 mientras llegaban los datos) habría soltado gestos
reales genuinamente sostenidos por error, cada vez que MediaPipe perdiera el
tracking un instante por un micro-ajuste de la mano.

**Decisión final, con margen real sobre el peor caso de cada tipo, no una
asimetría grande:**

- `GESTURE_RELEASE_AFTER_MISSES` (mano presente/ambigua) = **20** (margen sobre 14,
  sin cambios respecto al valor original de Fase C2).
- `GESTURE_RELEASE_AFTER_MISSES_NO_HAND` (mano realmente ausente) = **16** (margen
  sobre 13).

**Compromiso explícito, no resuelto a fondo:** esto da una mejora real pero
modesta en el caso de mano retirada de verdad (2.86s → 2.29s), no la mejora grande
que la intuición inicial sugería — los datos no la soportan sin arriesgar
liberaciones falsas durante sostenimientos reales. Una liberación más rápida
todavía podría valer la pena, pero necesitaría una señal mejor que "cuadros
seguidos sin mano cruda" (ej. alguna noción de tendencia/confianza acumulada) —
queda anotado para Fase D si JD lo considera necesario, no inventado aquí.

`hand_present` se calcula en `main.py` como
`not (result.gesture is None and result.extended_fingers == 0)` y se pasa a
`stabilizer.observe()` en cada frame.

### 5. Validación de punta a punta contra datos reales (no solo tests sintéticos)

`benchmarks/simulate_full_pipeline.py` reproduce el pipeline completo (observe con
confidence/hand_present, envío solo en el cambio, evento de liberación) sobre las
3 sesiones reales cacheadas:

| sesión | frames reales | líneas que se habrían mandado (antes: 1 por frame) | gestos | liberaciones |
|---|---|---|---|---|
| `captured_frames_2026-09-25` | 2132 | 17 | 14 | 3 |
| `captured_frames_2026-09-25_v2` | 3727 | 35 | 18 | 17 |
| `captured_frames_2026-10-02_menique` | 1946 | 19 | 12 | 7 |

Inspección manual de la secuencia completa (no solo el conteo): cada cambio de
gesto real produce exactamente una línea `"gesto: X"`, y en la sesión `_v2`
(protocolo "mano fuera de cuadro entre gestos") cada retiro real de mano produce su
`"gesto: ninguno"` antes del siguiente gesto — patrón alternado limpio, consistente
con el protocolo real de esa sesión.

### 6. Tests

`tests/test_detector.py`: `format_line` actualizado a la firma nueva (3 tests);
`GestureStabilizer` — 4 tests nuevos (`confirmed_confidence` sigue al último frame
que coincide, se resetea a 0.0 al soltar, los dos umbrales se usan por separado
según `hand_present`, el default de mano ausente (16) sobrevive el peor caso real
medido (13)).

`tests/test_main.py` reescrito: además de actualizar las 13 llamadas a
`_make_on_jpeg_frame` (parámetro nuevo), se agregaron los 4 casos pedidos
explícitamente en la revisión — `test_sustained_gesture_sends_exactly_one_message_not_one_per_frame`
(puño sostenido 10 frames → 1 solo mensaje, con `detector.calls==10` confirmando
que sí se siguió evaluando cada frame), `test_a_single_noisy_frame_while_already_confirmed_does_not_resend`
(frame ruidoso ya confirmado → no reenvía), `test_gesture_switch_sends_exactly_once_and_only_after_reaching_majority`
(cambio de gesto → un solo envío, y recién al alcanzar mayoría, verificado paso a
paso) y `test_removing_the_hand_emits_release_event_after_the_configured_misses`
(retirar la mano → evento de liberación en el tiempo configurado, no antes). Se
agregó además `test_hand_present_but_ambiguous_noise_uses_the_long_release_threshold_not_the_fast_one`
para blindar explícitamente la distinción de los dos umbrales.

**Suite completa: 78 passed, 0 failed** (`.venv-fase-c2-fix/bin/pytest -q`,
antes de esta corrección: 69).

### Pendiente

- Push de esta rama — bloqueado por credenciales, igual que siempre.
- Autorización explícita de JD antes de tocar el `.venv` de producción o el
  servicio real — nada de eso se hizo aquí.
- El "latido periódico" para Fase 9 (punto 3) queda sin implementar, a propósito,
  por falta de datos reales de requisitos de control continuo.
- El umbral de liberación con mano ausente (punto 4) quedó con una mejora modesta,
  no la agresiva que se había planteado al principio — una señal mejor que "cuadros
  seguidos sin mano cruda" podría ajustarlo más en el futuro, con datos.

## Fase D — plan de medición end-to-end contra AC3 (2026-10, PASO 1: revisado, sin ejecutar la medición todavía)

Revisión externa de `fd3a288` (rama `fase-c2-fix-confirmed-output`): corrección
aprobada, 78 tests confirmados. JD autorizó Fase D por el **camino A**: el cliente
tiene el puerto fijo en 8766 (`DEFAULT_CVA_BRIDGE_PORT`, `reference/bridge.rs`), así
que no se puede correr la instancia de prueba en otro puerto en paralelo — se
necesita parar el servicio real un rato y correr la instancia de prueba en el mismo
puerto, mientras dure la medición.

**Esto es el plan, todavía NO ejecutado.** Documentado acá primero para revisión de
JD antes de tocar el servicio real, por pedido explícito.

**Revisión del plan (2026-10, después del commit `a77fadb`):** aprobado CON
CAMBIOS. Los puntos 1-8 de abajo son los cambios pedidos, ya aplicados en este
commit — **la medición en sí (Paso 2) sigue sin ejecutarse.**

### Reglas fijas de esta fase

1. El código que se mide sale de `fase-c2-fix-confirmed-output`, desde su propio
   worktree (`~/video_analitica/cva-pi-repo-fase-c2-fix`) y su propio venv
   (`.venv-fase-c2-fix`). La carpeta de producción
   (`~/video_analitica/rasperry_pi-de-video-analitica`), su `.venv` y el archivo del
   servicio no se tocan — nada de `git pull` ahí.
2. La instancia de prueba la arranca Claude a mano, en el puerto 8766, **solo
   mientras JD haya detenido el servicio real**. Los únicos comandos `sudo` los
   corre JD: detener el servicio al empezar, volver a arrancarlo al terminar.
3. Si aparece un defecto durante la medición, no se arregla en silencio — se
   reporta primero, y el arreglo (si corresponde) va en un commit aparte con su
   propio test, no mezclado con los datos de la medición.
4. **Git, aclarado en la revisión del plan:** cualquier push de este trabajo va
   únicamente a `origin/fase-c2-fix-confirmed-output` — nunca a `main` ni a
   ninguna otra rama directamente desde acá. Fusionar a `main` (si corresponde,
   cuando Fase D cierre) es un PR aparte, decisión de JD, igual que con Fase C2.

### 1. Comandos exactos para JD (copiables, sin interpretación)

**Al empezar — detener el servicio real y confirmar el puerto libre:**
```bash
sudo systemctl stop cva-gesture-bridge.service
sudo ss -ltnp | grep 8766
```
Si el segundo comando **no imprime nada**, el puerto quedó libre — avísame y
arranco la instancia de prueba. Si imprime algo, pégame la salida antes de seguir
(no continuar sin confirmar esto).

**Importante — orden de arranque del cliente:** JD abre su cliente real (el que se
conecta por el túnel SSH a 8766) recién **DESPUÉS** de que yo confirme que la
instancia de prueba ya está escuchando en 8766 (lo verifico con `ss`, igual que en
el ensayo en seco de abajo). Abrirlo antes arriesga que el cliente haga su chequeo
de salud (sección 4.2 de `CLAUDE.md`) contra un puerto todavía cerrado y falle la
conexión.

**Al terminar — reiniciar el servicio real (después de que yo confirme que la
instancia de prueba ya se detuvo):**
```bash
sudo systemctl start cva-gesture-bridge.service
sudo systemctl status cva-gesture-bridge.service --no-pager
sudo ss -ltnp | grep 8766
```
El `status` debe decir `active (running)`, y el `ss` debe mostrar el PID del
proceso real (el que corre desde
`/home/david_cardenas/video_analitica/rasperry_pi-de-video-analitica/.venv/bin/python`,
no desde el worktree de prueba). Pégame las tres salidas — las reviso contra el
sistema real antes de dar la fase por cerrada (ver "Paso 3" más abajo), no me
conformo con que los comandos se hayan corrido.

**Plan de reversa, si algo de esto no sale limpio:**
- Si `systemctl status` **no** dice `active (running)` después del `start`: JD
  pega la salida completa de
  ```bash
  sudo journalctl -u cva-gesture-bridge -n 40 --no-pager
  ```
  para diagnosticar antes de reintentar nada a ciegas.
- Si la instancia de prueba **no** cierra con `kill -INT <pid>` (SIGINT) en unos
  segundos: forzar con `kill -KILL <pid>` (SIGKILL) y confirmar igual con
  `ps -p <pid>` que ya no existe antes de pedirle a JD que reinicie el servicio
  real — nunca dejar que el real arranque mientras la de prueba todavía podría
  tener el puerto tomado.

### 2. Cómo arranco la instancia de prueba

```bash
cd ~/video_analitica/cva-pi-repo-fase-c2-fix
CVA_LOG_FILE=benchmarks/fase_d_run_<condicion>.log \
  .venv-fase-c2-fix/bin/python -m cva_gesture_bridge.main
```
En segundo plano, guardando el PID. El archivo de log usa el formato ya existente
del proyecto (`%(asctime)s %(levelname)s %(name)s: %(message)s`,
`logging_setup.py`) — milisegundos por línea, sin cambios de código para esta
fase.

**Para detenerla limpio:** `kill -INT <pid>` (SIGINT, el mismo que Ctrl+C) — el
`except KeyboardInterrupt` ya existente en `main.py` loguea
"cva_gesture_bridge detenido por el usuario" y cierra ordenado. Confirmo que el
proceso ya no existe (`ps -p <pid>`) antes de pedirle a JD que reinicie el
servicio real.

**Capturador temporal (opcional, para documentar casos difíciles sin tener que
describirlos de memoria):** si hace falta, se activa con
`CVA_CAPTURE_FRAMES_DIR=benchmarks/fase_d_frames_<condicion>` al arrancar — mismo
mecanismo ya usado en Fase A/recaptura de meñique, gitignorado, nunca fotos reales
al repo.

**Ensayo en seco (corrido antes de pedirle nada a JD, puerto 8767 -- no toca
producción ni el puerto real):**
```
$ CVA_BRIDGE_PORT=8767 CVA_LOG_FILE=benchmarks/fase_d_dry_run.log \
    .venv-fase-c2-fix/bin/python -m cva_gesture_bridge.main &
PID: 877256

$ ss -ltnp | grep 8767
LISTEN 0 100 0.0.0.0:8767 0.0.0.0:* users:(("python",pid=877256,fd=13))

$ cat benchmarks/fase_d_dry_run.log
2026-10-07 16:48:33,285 INFO __main__: Detector de gestos precalentado (warmup de arranque)
2026-10-07 16:48:33,286 INFO cva_gesture_bridge.transport.tcp_server: cva_gesture_bridge escuchando en ('0.0.0.0', 8767)

$ kill -INT 877256
$ cat benchmarks/fase_d_dry_run.log   # línea nueva tras el SIGINT
2026-10-07 16:48:42,234 INFO __main__: cva_gesture_bridge detenido por el usuario

$ ps -p 877256
    PID CMD        # (vacío -- confirmado, el proceso ya no existe)

$ ss -ltnp | grep 8767   # (vacío -- puerto liberado)
```
Modelo cargado correctamente (`models/hand_landmarker.task` ya estaba en este
worktree, copiado al armarlo en Fase B — no hizo falta copiarlo de nuevo ni
versionarlo), warmup confirmado, cierre con SIGINT limpio y verificado contra el
sistema real (`ps`, `ss`), no solo asumido. Log de este ensayo descartado después
(gitignorado, `benchmarks/*.log`).

### 3. Guion de medición con verdad conocida

`benchmarks/fase_d_schedule.py` (ya escrito, no se importa desde el paquete) —
imprime, con el reloj de la Pi (mismo formato de timestamp que el log del bridge,
para poder cruzar las dos fuentes sin depender de sincronización entre máquinas),
cuándo hacer cada gesto y cuándo quitar la mano. JD lo corre en una terminal SSH
**aparte** de la que usa su cliente real, y sigue las señales en tiempo real
mientras opera el cliente normalmente (la práctica real, por el túnel SSH, como
siempre).

- 4 gestos del catálogo × 10 repeticiones cada uno = 40 repeticiones, en bloques
  (10 seguidas del mismo gesto, no intercaladas — más simple de analizar).
- 3s sosteniendo cada gesto, **5s** con la mano fuera de cuadro entre cada
  repetición (subido de 4s a 5s en la revisión del plan — margen real sobre
  `GESTURE_RELEASE_AFTER_MISSES_NO_HAND=16` ≈ 2.3s, más margen de reacción humana
  para sacar la mano Y volver a prepararla para la siguiente repetición).
- Cada señal (`CUE:`) va precedida de un pitido audible (`\a`) además del texto —
  más fácil de seguir sin tener que estar mirando la pantalla todo el tiempo.
- Duración de una batería completa: ~40 × (3+5)s = 320s ≈ 5.3 min, más los 10s de
  cuenta regresiva inicial.

### 4. Qué se mide, con advertencia de método

Dos tiempos distintos, reportados **por separado** — no promediados entre sí:

- **Tiempo de sistema** (lo que de verdad evalúa AC3 <1.5s): desde el **inicio de
  la racha continua** de líneas `[diag] Gesto candidato: <gesto correcto>` que
  termina en la confirmación — **no** desde la primera línea suelta con ese gesto
  si hubo una aislada más atrás seguida de ruido (corrección del plan, 2026-10: una
  coincidencia temprana aislada que no formó parte de la racha que realmente
  confirmó exageraría el tiempo medido, o lo subestimaría si se ignora que hubo
  ruido en el medio — la racha continua que efectivamente llevó a la confirmación
  es la que importa). Hasta la línea `Gesto detectado` confirmada. Esto mide el
  pipeline (MediaPipe + GestureStabilizer), no la reacción humana.
- **Tiempo total desde la señal**: desde el timestamp de la línea `CUE: HAZ: X` del
  guion hasta `Gesto detectado`. Incluye el tiempo que JD tarda en reaccionar y
  mover la mano a posición — **no mide el sistema**, se reporta aparte y nunca se
  usa para evaluar AC3, para no mezclar reacción humana con rendimiento real.

**Regla de análisis — repeticiones CONTAMINADAS (agregada en la revisión del
plan):** el bridge manda una línea de gesto solo cuando `confirmed_gesture`
*cambia* respecto al último valor mandado (fix de Fase C2, ver esa sección de esta
misma bitácora). Eso significa que si la liberación (`gesto: ninguno`) de la
repetición anterior no llegó a tiempo o no llegó por algún motivo, la repetición
siguiente del MISMO gesto puede confirmar correctamente por dentro sin generar
ninguna línea nueva (porque ya "coincide" con lo último mandado) — verla sin línea
de gesto no significa que el sistema falló en detectarla. **Toda repetición cuyo
log no tenga un `gesto: ninguno` INMEDIATAMENTE ANTES de su tramo de `HAZ:` se
marca CONTAMINADA y se excluye del % de aciertos** (no cuenta como acierto ni como
fallo — queda fuera de la muestra, reportada aparte). Al analizar, reportar
explícitamente cuántas repeticiones quedaron contaminadas por condición.

Además, por repetición y en agregado (sobre las repeticiones NO contaminadas):
- % de aciertos: gesto correcto confirmado **sin** haber mandado antes un gesto
  equivocado en esa misma repetición (una repetición donde se manda el gesto
  correcto pero precedido de un envío incorrecto NO cuenta como acierto limpio).
- Líneas enviadas por repetición (esperado: 1 línea de gesto + 1 `gesto: ninguno`
  al quitar la mano — más de eso es señal de un problema real, no de ruido
  esperado, dado el fix de Fase C2).
- Tiempo hasta `gesto: ninguno` tras retirar la mano (debería rondar el valor real
  medido para `GESTURE_RELEASE_AFTER_MISSES_NO_HAND`, ~2.3s, más el margen que
  tome MediaPipe en reportar "sin mano" de verdad).
- Cero líneas durante los tramos de "mano fuera de cuadro" (aparte de la única
  `gesto: ninguno` esperada al principio de cada tramo).

### 5. Condiciones

- **3 distancias**, medidas con cinta métrica y anotadas en cm exactos antes de
  empezar cada batería: cerca, media, lejos (JD define los valores concretos al
  momento, documentados en el reporte final, no fijados de antemano a ciegas).
- **Segunda persona, de tono de piel distinto, si JD consigue una** — misma
  batería completa. **Si no la consigue, se documenta explícitamente como brecha
  abierta** en el reporte final (no se cierra en silencio ni se asume cubierta).

### 6. Casos difíciles — resultado esperado definido ANTES de probar

| caso | resultado esperado |
|---|---|
| Movimiento rápido (cambiar de gesto rápido, sin sostener) | No debe confirmarse ningún gesto incorrecto — puede no confirmar nada (aceptable), pero cero envíos equivocados. |
| Mano tapada a medias | Puede no confirmar el gesto real (aceptable) — pero cero envíos de un gesto distinto al que se intenta. |
| Mano en el borde del cuadro | Igual que arriba: aceptable no confirmar, inaceptable confirmar algo incorrecto. |
| Dos manos en cuadro | El detector usa `num_hands=1` (detector.py) — debe seguir tratando una sola mano sin crashear; se documenta cuál de las dos eligió MediaPipe, sin asumir que es un bug si elige la "equivocada" (no hay forma de indicarle cuál priorizar en esta fase). |
| Una persona pasando detrás | Cero gestos confirmados por la persona de fondo — mismo criterio que los fixtures de cara/torso de Fase B (geométricamente no debe calzar con ningún patrón del catálogo). |

### 7. Qué hace JD y cuánto dura cada bloque

1. **Preparación** (~2 min): correr los 2 comandos de "detener servicio" de la
   sección 1, confirmar puerto libre, avisarme.
2. **Batería principal** (~5 min × 3 distancias ≈ 15 min): por cada distancia,
   JD corre `fase_d_schedule.py <distancia>` en una terminal SSH aparte, sigue las
   señales con su cliente real abierto y operando normalmente.
3. **Segunda persona** (~15 min adicionales, si aplica): misma batería completa.
4. **Casos difíciles** (~5-10 min): JD improvisa cada caso de la tabla de arriba
   cuando se lo pida, sin guion cronometrado (son puntuales, no repeticiones).
5. **Cierre** (~2 min, checklist):
   - [ ] Confirmo que mi instancia de prueba ya no tiene proceso corriendo.
   - [ ] JD corre los 3 comandos de "reiniciar servicio" de la sección 1.
   - [ ] Reviso las 3 salidas contra el sistema real (PID, puerto, estado) antes
         de dar la fase por cerrada.

### Limitaciones de esta medición (agregado en la revisión del plan)

- **La medición llega solo hasta el envío desde la Pi** — mide desde que el frame
  entra al detector hasta que `TcpServer.send_line` escribe en el socket. **No
  incluye** el tiempo de red del túnel SSH hasta la máquina de JD, ni el tiempo
  que tarda el cliente en leer la línea y pintarla en pantalla. El AC3 real
  "de punta a punta, visible para el estudiante" es por lo tanto siempre
  **mayor o igual** al tiempo de sistema medido acá, nunca menor.
- **`Gesto detectado` en el log es un proxy de "línea enviada", no una garantía.**
  Caso borde real (ver `main.py`): el log "Gesto detectado" se escribe apenas el
  `GestureStabilizer` confirma, ANTES de chequear si `confirmed_confidence` supera
  `MIN_CONFIDENCE` — si no la supera, `format_line` devuelve `None` y nunca se
  llama a `send_line`, aunque el log ya haya dicho "Gesto detectado". Con los
  datos reales medidos hasta ahora las confianzas rondan 0.9-1.0 y `MIN_CONFIDENCE`
  es 0.5 (margen amplio, este caso no se ha visto en la práctica) — pero si al
  analizar aparece un "Gesto detectado" sin la línea `gesto: X` correspondiente
  en el tráfico real, es este caso borde, no un bug nuevo.

### Pendiente de aprobación

Este plan está escrito, no ejecutado. Falta la aprobación explícita de JD sobre
este documento antes de pasar al Paso 2 (ejecutar con JD, analizar, documentar
resultados reales con tabla por condición).

**Aprobado por JD (con los 8 cambios del commit `e35f3be`).** Paso 2 ejecutado
parcialmente el 2026-10-07/08 — ver resultados abajo.

## Fase D — Paso 2: resultados reales (2026-10-07/08, condición 50cm)

Instancia de prueba arrancada en el puerto 8766 (servicio real detenido por JD,
puerto verificado libre antes de arrancar — `systemctl is-active` dio `inactive`,
`ss -ltn` sin ninguna coincidencia para 8766, confirmado independientemente, no
solo de palabra). Cliente real de JD conectado por el túnel SSH de siempre,
practicando con el módulo CVA normalmente.

**Primer intento (sin seguir el guion):** JD probó los 4 gestos libremente frente
al cliente real antes de correr `fase_d_schedule.py` — sirvió como chequeo rápido
de que la cadena completa funciona (los 4 gestos se reconocieron con confianza
alta), pero no sigue el guion con verdad conocida, así que no se usó para los
números de abajo.

**Segundo intento (guion completo, pero con 1 repetición perdida):** JD corrió el
guion inmediatamente después del chequeo libre, sin pausa — la transición desde
la actividad anterior se "comió" la primera repetición (puño_cerrado): quedó
confirmada por el `GestureStabilizer` pero el bridge nunca mandó una línea nueva
porque ya coincidía con lo último confirmado de la prueba libre anterior (ver la
regla de repeticiones CONTAMINADAS de la sección 4 de este plan — exactamente el
caso que esa regla anticipaba). Resultado: 39 de 40 repeticiones con línea
propia, 0 contaminadas de esas 39, 0 fuera de AC3 — pero JD prefirió repetir
completo para tener las 40 limpias en vez de aceptar el 39/40.

**Repetición, condición 50cm (medido con cinta), la que se reporta:** esta vez con
una pausa de ~3-4s con la mano fuera de cuadro antes de arrancar el guion, para
que no arrastrara nada de la prueba anterior. 40 de 40 repeticiones con línea
propia, 0 contaminadas.

**Corrección (revisión externa de `c292efe`):** la tabla original de esta sección
afirmaba "100% de aciertos (40/40)", "exactamente 2 líneas por repetición" y "0
líneas durante los tramos sin mano" — **ninguna de esas tres estaba respaldada
por un cómputo real.** `analyze_fase_d_run.py` nunca comparó contra la señal del
guion (el CUE no se guardó, ver más abajo), así que no hay forma de verificar
"precisión contra verdad conocida" con este log solo. Reescrito con lo que sí se
pudo verificar realmente:

| métrica | valor | ¿cómo se verificó? |
|---|---|---|
| Repeticiones totales (eventos "Gesto detectado") | 40 | conteo directo del log |
| Repeticiones contaminadas (regla de la sección 4) | 0 | `analyze_fase_d_run.py` |
| Tiempo de sistema — avg / p50 / p95 / max | 526ms / 543ms / 594ms / **617ms** | `analyze_fase_d_run.py`, racha continua de `[diag] Gesto candidato` |
| Repeticiones que superan AC3 (<1500ms) | **0/40** | `analyze_fase_d_run.py` |
| Tiempo detectado→liberado — avg / min / max | 4494ms / 4156ms / 5902ms | `analyze_fase_d_run.py` (desde la detección, NO desde la señal QUITA del guion — ver nota de método abajo) |
| Secuencia de eventos | estrictamente alternada `detectado, liberado, detectado, liberado...` (80 eventos, 40 pares), sin excepciones | verificado por script aparte, pegado abajo |

**Lo que esto SÍ dice, con precisión:** 40 gestos quedaron confirmados y
mostrados, cada uno detectado exactamente una vez, sin ningún envío adicional o
fuera de secuencia (la alternancia estricta D-L-D-L lo confirma mecánicamente).
**Lo que esto NO dice:** si esos 40 gestos coinciden con los 40 que el guion
realmente pidió, en el orden y momento que los pidió — **la precisión contra
verdad conocida NO está verificada**, porque el CUE del guion solo se imprimió en
la pantalla de JD y no se guardó en ningún archivo (ver limitación ya documentada
en la sección 4 del plan). Verificación de la alternancia:

```
$ python3 -c "... cuenta eventos D/L en orden ..."
secuencia: DLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDLDL
longitud: 80
alterna estrictamente D,L,D,L...: True
```

**Tabla "Detalle por repetición" completa (las 40), salida real de `analyze_fase_d_run.py`:**

```
  [  144] 13:43:50,444 puño_cerrado   conf=0.99 t_sistema=   556ms
  [  203] 13:43:58,578 puño_cerrado   conf=1.00 t_sistema=   511ms
  [  262] 13:44:06,745 puño_cerrado   conf=1.00 t_sistema=   522ms
  [  321] 13:44:14,884 puño_cerrado   conf=1.00 t_sistema=   532ms
  [  380] 13:44:23,069 puño_cerrado   conf=1.00 t_sistema=   596ms
  [  434] 13:44:30,496 puño_cerrado   conf=1.00 t_sistema=   530ms
  [  493] 13:44:38,646 puño_cerrado   conf=1.00 t_sistema=   588ms
  [  551] 13:44:46,658 puño_cerrado   conf=1.00 t_sistema=   581ms
  [  608] 13:44:54,533 puño_cerrado   conf=1.00 t_sistema=   549ms
  [  666] 13:45:02,512 puño_cerrado   conf=1.00 t_sistema=   496ms
  [  727] 13:45:10,982 palma_abierta  conf=0.96 t_sistema=   514ms
  [  782] 13:45:18,516 palma_abierta  conf=0.98 t_sistema=   553ms
  [  840] 13:45:26,538 palma_abierta  conf=0.98 t_sistema=   510ms
  [  898] 13:45:34,523 palma_abierta  conf=0.98 t_sistema=   480ms
  [  955] 13:45:42,551 palma_abierta  conf=0.96 t_sistema=   528ms
  [ 1011] 13:45:50,558 palma_abierta  conf=0.98 t_sistema=   543ms
  [ 1071] 13:45:58,833 palma_abierta  conf=0.97 t_sistema=   543ms
  [ 1128] 13:46:06,735 palma_abierta  conf=0.98 t_sistema=   555ms
  [ 1184] 13:46:14,495 palma_abierta  conf=0.97 t_sistema=   583ms
  [ 1243] 13:46:22,659 palma_abierta  conf=0.99 t_sistema=   579ms
  [ 1301] 13:46:30,636 palma_abierta  conf=0.98 t_sistema=   551ms
  [ 1362] 13:46:39,046 dedo_pulgar    conf=0.98 t_sistema=   518ms
  [ 1416] 13:46:46,487 dedo_pulgar    conf=0.92 t_sistema=   544ms
  [ 1474] 13:46:54,480 dedo_pulgar    conf=1.00 t_sistema=   489ms
  [ 1531] 13:47:02,336 dedo_pulgar    conf=0.98 t_sistema=   513ms
  [ 1595] 13:47:11,211 dedo_pulgar    conf=0.97 t_sistema=   525ms
  [ 1648] 13:47:18,500 dedo_pulgar    conf=0.96 t_sistema=   461ms
  [ 1707] 13:47:26,664 dedo_pulgar    conf=0.99 t_sistema=   536ms
  [ 1765] 13:47:34,652 dedo_pulgar    conf=0.99 t_sistema=   490ms
  [ 1822] 13:47:42,563 dedo_pulgar    conf=0.93 t_sistema=   558ms
  [ 1879] 13:47:50,540 dedo_pulgar    conf=0.98 t_sistema=   470ms
  [ 1937] 13:47:58,562 dedo_menique   conf=1.00 t_sistema=   594ms
  [ 1995] 13:48:06,535 dedo_menique   conf=1.00 t_sistema=   579ms
  [ 2054] 13:48:14,696 dedo_menique   conf=1.00 t_sistema=    85ms
  [ 2111] 13:48:22,567 dedo_menique   conf=1.00 t_sistema=   511ms
  [ 2168] 13:48:30,562 dedo_menique   conf=1.00 t_sistema=   417ms
  [ 2226] 13:48:38,593 dedo_menique   conf=1.00 t_sistema=   579ms
  [ 2284] 13:48:46,618 dedo_menique   conf=1.00 t_sistema=   592ms
  [ 2341] 13:48:54,653 dedo_menique   conf=1.00 t_sistema=   617ms
  [ 2396] 13:49:02,488 dedo_menique   conf=1.00 t_sistema=   571ms

=== Resumen ===
Repeticiones NO contaminadas con tiempo de sistema calculable: 40
Tiempo de sistema -- avg=526ms p50=543ms p95=594ms max=617ms min=85ms
Repeticiones que superan AC3 (<1500ms): 0/40

Tiempo detectado->liberado -- n=40 avg=4494ms min=4156ms max=5902ms

Repeticiones NO contaminadas por gesto: {'puño_cerrado': 10, 'palma_abierta': 11, 'dedo_pulgar': 10, 'dedo_menique': 9}
```

**Nota de conteo (10/11/10/9 en vez de 10/10/10/10) — HIPÓTESIS, no hecho
confirmado.** El guion manda exactamente 10 repeticiones por gesto en orden fijo
(el código no puede por sí solo producir 11) — dos hipótesis, ninguna descartada
con certeza porque no hay CUE guardado:

1. **Error humano de ejecución** (conteo propio de JD adelantado/atrasado en la
   transición palma→pulgar o pulgar→menique). A favor: los 11 espaciados entre
   detecciones de `palma_abierta` son perfectamente regulares (~8s cada uno, sin
   ningún hueco corto que sugiera un glitch de tracking) — son 11 ciclos
   completos y limpios, no un artefacto de un solo evento duplicado.
2. **Clasificación incorrecta** (un intento real de `dedo_menique` leído como
   `palma_abierta`). Investigado explícitamente: se revisó TODA la ventana cruda
   `[diag] Gesto candidato`/`[diag] Sin gesto reconocido` alrededor de las 11
   detecciones de palma (13:46:14 a 13:46:45) — **cero candidatos crudos de
   `dedo_menique` aparecen en esa ventana**, todos son `dedos_extendidos=5`
   (palma genuina) o ausencia de mano. Esto hace la hipótesis 2 menos probable
   para este tramo específico, pero no la descarta con certeza para el resto de
   la secuencia sin el CUE real.

**No se elige entre las dos — queda documentado como abierto**, no resuelto. La
suma total (40) y la secuencia alternada D-L-D-L no cambian por esto.

**Segunda persona / otras distancias / casos difíciles: pendientes — brecha
abierta, no cerrada.** JD y Claude decidieron cerrar esta sesión después de la
condición 50cm (el servicio real llevaba ~21 horas detenido, priorizar
restaurarlo sobre seguir midiendo). El resto de condiciones del plan (media,
lejos, segunda persona de otro tono de piel, los 5 casos difíciles de la sección
6) queda para una sesión nueva de Fase D, repitiendo el mismo procedimiento.

### Cierre de esta sesión — verificado contra el sistema real

```
$ pgrep -f "cva_gesture_bridge.main"
878539   # (antes de apagar)

$ kill -INT 878539
$ ps -p 878539
# vacío -- confirmado, el proceso ya no existe

$ ss -ltn | grep 8766
# vacío -- puerto liberado
```
Recién después de esta verificación se le avisó a JD para que reiniciara el
servicio real (`sudo systemctl start cva-gesture-bridge.service`).

### Scripts de esta sesión

`benchmarks/analyze_fase_d_run.py` (nuevo) — parsea un log real del bridge de
prueba y aplica las reglas del plan (tiempo de sistema desde la racha continua de
`[diag] Gesto candidato`, repeticiones contaminadas, tiempo de liberación). No se
importa desde el paquete.

**Corrección (revisión externa):** `fase_d_schedule.py` no guardaba ningún
archivo (solo imprimía a pantalla) — por eso la corrida de 50cm no pudo
verificar nada contra la señal real del guion. Ahora guarda siempre las señales
en `benchmarks/fase_d_cue_<condición>.log` (mismo formato de timestamp que el
log del bridge). `analyze_fase_d_run.py` acepta ese archivo como segundo
argumento opcional y, cuando está presente, calcula el tiempo de liberación
real **desde la señal QUITA** (no desde la propia detección, que es lo único
medible sin esa señal) y el tiempo total desde la señal HAZ — emparejado por
orden, con aviso explícito si los conteos no coinciden en vez de alinear a
ciegas. Probado con datos sintéticos antes de usarlo (ver commit) — esta
corrida de 50cm no tiene CUE guardado (se grabó antes de este fix), así que
estas dos métricas quedan disponibles recién para la próxima sesión de Fase D.

### Defecto real encontrado al restaurar producción — reportado y corregido con su test

Al reiniciar el servicio real después de cerrar la sesión de medición, JD había
hecho `git pull` de `main` en producción en algún momento (trae el merge de Fase
B/C2, confirmado: `9a249fa`) **sin actualizar el `.venv` de producción ni
descargar el modelo** — `ModuleNotFoundError: No module named 'mediapipe'` y
`models/` inexistente. Autorizado por JD, corregido (`.venv/bin/pip install -r
requirements.txt` + copiar `hand_landmarker.task`) — esto solo, sin tocar código.

**Tras corregir eso, apareció un segundo problema real, de código esta vez — no
solo de despliegue.** El servicio seguía crasheando en bucle de reinicio
(`systemctl` mostraba `activating (auto-restart)`, puerto 8766 nunca llegaba a
escuchar). `sudo journalctl -u cva-gesture-bridge -n 60 --no-pager` (pedido a JD,
sin acceso propio a journalctl) mostró la causa real:

```
sounddevice.PortAudioError: Error initializing PortAudio: Unanticipated host
error [PaErrorCode -9999]: 'PulseAudio_Initialize: Can't connect to server'
```
con el traceback completo pasando por
`mediapipe/__init__.py` → `mediapipe.tasks.python` → `audio` → `audio_classifier`
→ `audio_record` → `import sounddevice` (que inicializa PortAudio en el momento
mismo del import, no de forma perezosa).

**Por qué no se había visto esto en ninguna prueba anterior** (Fase B, C2, ni los
ensayos en seco de esta misma Fase D): todas esas pruebas corrieron desde una
sesión interactiva por SSH, que sí tiene una sesión de PulseAudio de usuario
alcanzable. `cva-gesture-bridge.service` es una unidad de *sistema* de systemd
(no de usuario) — no tiene ninguna sesión de audio asociada, así que
`sounddevice` falla ahí aunque funcione perfecto en una terminal interactiva.
Esto nunca se habría encontrado sin medir contra el servicio real -- exactamente
el tipo de cosa que Fase D existe para descubrir.

**Arreglo** (`cva_gesture_bridge/vision/detector.py`): este proyecto nunca usa
ninguna función de audio de mediapipe (solo `vision.HandLandmarker`) — pero
`mediapipe.tasks.python.__init__.py` importa su submódulo de audio
incondicionalmente, sin forma de evitarlo desde afuera sin tocar la librería.
`_stub_sounddevice_if_unavailable()` intenta el import real de `sounddevice`
primero; **solo si ese import real falla**, instala un módulo en blanco en
`sys.modules["sounddevice"]` antes de importar mediapipe, para que la cadena de
imports de `mediapipe.tasks.python` encuentre ese stub en vez de ejecutar el
archivo real de `sounddevice.py` (que es donde se dispara la inicialización de
PortAudio). Si el import real SÍ funciona (como en toda sesión interactiva hasta
ahora), no se toca nada — el stub nunca oculta un fallo real de audio en un
entorno donde sí hay sesión disponible, porque ninguna parte de este proyecto usa
audio para nada.

**3 tests nuevos** en `tests/test_detector.py` (stub se instala cuando el import
real falla de verdad -- simulado forzando `OSError` en `builtins.__import__` solo
para el nombre `sounddevice`, no con un valor en `sys.modules` que habría
disparado el chequeo de salida temprana de la función por otro motivo; stub NO se
instala cuando el import real funciona; la función no pisa un `sounddevice` que
ya estuviera importado de antes). **Suite completa: 81 passed, 0 failed**
(antes de este fix: 78).

**Corrección (revisión externa de `c292efe`) a estos 3 tests:**
`test_stub_not_installed_when_real_sounddevice_import_succeeds` dependía de que
ESTA máquina tuviera audio disponible de verdad (justo la condición que el
incidente de producción demostró que varía según el contexto) — reescrito para
simular un import real exitoso con un módulo falso (mismo mecanismo de
`builtins.__import__`, pero devolviendo un módulo con `__file__` en vez de
levantar una excepción), ya no depende del entorno. Además, el primer test dejaba
el stub en blanco pegado en `sys.modules["sounddevice"]` después de correr
(la función lo muta directamente, no vía `monkeypatch`, así que no se deshacía
solo) — ahora se guarda el valor original antes y se restaura en un `finally`.

**Verificación bajo condiciones reales del servicio (pedida en la revisión,
antes de dar el fix por bueno):** el entorno interactivo por SSH tiene
`XDG_RUNTIME_DIR` y `DBUS_SESSION_BUS_ADDRESS` seteados (confirmado con `env |
grep`) — justo lo que una unidad de *sistema* de systemd no tiene. Se construyó
un entorno restringido con `env -i HOME=... PATH=...` (sin esas dos variables ni
`PULSE_SERVER`) para reproducir la condición real sin tocar el servicio real:

```
$ env -i HOME="$HOME" PATH="$PATH" .venv-fase-c2-fix/bin/python -c "import mediapipe"
...
sounddevice.PortAudioError: Error initializing PortAudio: Unanticipated host
error [PaErrorCode -9999]: 'PulseAudio_Initialize: Can't connect to server'
```
**El bug reproduce exacto** (mismo error que journalctl mostró en producción) en
este entorno restringido, sin el fix. Con el fix (`GestureDetector` real,
construido y corriendo `detect()` contra un fixture real, mismo entorno
restringido):

```
$ env -i HOME="$HOME" PATH="$PATH" .venv-fase-c2-fix/bin/python -c "..."
OK: GestureDetector se construyo correctamente (con el fix) en entorno sin
XDG_RUNTIME_DIR/DBUS_SESSION_BUS_ADDRESS/PULSE_SERVER
OK: detect() corrio sobre un fixture real -- gesture=puño_cerrado confidence=0.9983
```

**Estado del fix en producción:** ya se aplicó como hotfix directo (autorizado
por JD, antes de que llegara esta revisión) mientras el servicio real estaba
caído — commit local `4a4f793` en el `main` de producción, servicio verificado
`active (running)` escuchando en 8766. El mismo fix, con sus tests y ahora la
verificación bajo condiciones reales de arriba, vive en
`fase-c2-fix-confirmed-output` — pendiente fusionarlo a `main` por PR para que
el hotfix local de producción quede reconciliado con el historial real (debería
ser un `git pull` limpio, sin conflicto, mismo contenido).
