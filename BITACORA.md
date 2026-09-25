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
