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

**Esperando aprobación explícita del usuario antes de tocar Fase 7.**

---
