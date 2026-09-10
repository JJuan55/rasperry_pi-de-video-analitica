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
