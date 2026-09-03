# CLAUDE.md — cva_gesture_bridge (Raspberry Pi 5)

Este archivo es tu fuente principal de contexto en esta máquina. El resto de la
planificación del proyecto (spec, arquitectura, plan por fases, constitución) vive en
una carpeta personal en la máquina de JD que **no** está en este repositorio — no la
busques, no existe aquí. Todo lo que necesitas saber para este componente está en este
documento.

Además, este repo incluye una carpeta `reference/` con el código Rust real del cliente
(`session.rs`, `bridge.rs`, `mod.rs`) y los archivos de configuración reales
(`domotica.json`, `robot.json`). Son de **solo lectura** — no se editan desde aquí,
el original vive en el repo del cliente. Úsalos para verificar cualquier duda sobre el
protocolo directamente contra el código fuente, en vez de asumir. La sección 4 de este
documento ya está verificada contra ese código, pero si algo no cuadra, `reference/`
es la fuente de verdad, no la memoria de nadie.

## 1. Qué es este proyecto

LaboRemoto es un cliente de escritorio (Tauri + Rust + React) que deja a estudiantes
de la Universidad Piloto de Colombia operar remotamente laboratorios físicos por SSH.
El módulo CVA ("Control de Video Analítica") es una práctica nueva: el estudiante
mueve la mano frente a su propia cámara, el gesto se reconoce en esta Raspberry Pi
(YOLO + OpenCV) y se traduce en una instrucción que mueve hardware real del
laboratorio (un robot Eve3, o un módulo de domótica con Arduino).

El cliente (frontend + backend Rust) ya está construido y probado — Parte 1 completa.
Lo que falta es la Parte 2: este componente, `cva_gesture_bridge`, que corre aquí, en
la Pi.

## 2. Tu rol: `cva_gesture_bridge`

Servicio Python que corre en esta Raspberry Pi 5. Su trabajo, cuando esté completo:

1. Aceptar una conexión TCP persistente desde el túnel SSH que abre el cliente.
2. Recibir frames de video (JPEG) a intervalos de ~6-8 fps.
3. Correr YOLO + OpenCV sobre cada frame para reconocer un gesto de mano.
4. Mapear el gesto reconocido a una instrucción, según el módulo activo (`domotica` o
   `robot`) y el archivo de configuración correspondiente.
5. Ejecutar la instrucción: llamando a `arduino_bridge.py` (ya existe en esta Pi,
   escucha en `localhost:8765`, **no se toca**) para Domótica, o al servicio del robot
   Eve3 (protocolo aún por investigar — no es parte de esta fase) para Robot.
6. Mandar de vuelta al cliente, por la misma conexión, un mensaje corto por cada gesto
   procesado, para que se vea en el panel de log del cliente.
7. Watchdog: si no llegan frames por un tiempo, dejar de emitir instrucciones.

**Importante — la lógica de decisión vive aquí, no en el cliente.** El cliente nunca
decide qué instrucción ejecutar; solo manda video y muestra lo que tú le devuelvas.

## 3. Alcance de ESTA fase (Fase 6) — no construyas más que esto todavía

Fase 6 es solo la estructura base, **sin YOLO ni reconocimiento todavía**:

- [ ] Crear `cva_gesture_bridge/` desde cero (paquete Python nuevo).
- [ ] `main.py`, `config.py`.
- [ ] `transport/ws_server.py` — el nombre es heredado de un plan anterior que
      contemplaba WebSocket; **el protocolo real ya implementado en el cliente es TCP
      crudo, no WebSocket** (ver sección 4). Puedes renombrar el archivo si prefieres
      (ej. `transport/tcp_server.py`) — lo que importa es que hable el protocolo exacto
      de la sección 4, no el nombre del archivo.
- [ ] `transport/watchdog.py` — mecanismo de "sin frames en N segundos → cortar
      instrucciones", aunque en esta fase todavía no hay instrucciones que cortar; solo
      debe existir el mecanismo y ser testeable.
- [ ] El bridge **solo loguea** lo que recibe: conteo de frames, tamaño en bytes, fps
      real medido. Nada de visión todavía.

**Criterio de salida de esta fase:** los frames que el cliente ya envía (Parte 1,
completa y probada) llegan aquí y se loguean correctamente, con la conexión persistente
funcionando en ambos sentidos (lectura y escritura) tal como espera el cliente.

No implementes YOLO, mapeo de gestos, ni la integración con `arduino_bridge.py` todavía
— eso es de Fase 8 en adelante (ver sección 7, roadmap).

## 4. Protocolo de comunicación — verificado directamente contra el código Rust ya
   implementado y aprobado en el cliente (`backend/src/cmd/cva_gestures/session.rs` y
   `bridge.rs`). Esto no es una propuesta, es lo que el cliente YA hace.

### 4.1 Puerto y transporte

- Puerto: **8766**, en `127.0.0.1` de esta Pi (o `0.0.0.0` si prefieres, pero el
  tráfico llega vía túnel SSH `direct-tcpip`, no necesita estar expuesto a la red).
- Es un **socket TCP crudo, no WebSocket ni HTTP**. Nada de handshake HTTP, nada de
  framing WebSocket.
- El cliente abre **una sola conexión persistente por sesión de práctica** y la
  reutiliza para todos los frames — no abre una conexión nueva por cada frame. Tu
  servidor debe aceptar la conexión y mantenerla abierta, leyendo y escribiendo sobre
  el mismo socket mientras dure la sesión.

### 4.2 Health check (antes de que empiece la sesión real)

Antes de abrir la sesión, el cliente hace un chequeo de salud que consiste en: abrir un
canal TCP hacia `127.0.0.1:8766` y cerrarlo inmediatamente, sin mandar ningún dato. Si
el `connect()` tiene éxito, el cliente considera que el bridge está disponible — no
espera ninguna respuesta ni protocolo especial. **Tu servidor debe aceptar esa conexión
y sobrevivir a que se cierre de inmediato sin datos**, sin loguearlo como error (es
tráfico normal, no un cliente real fallando).

### 4.3 Cliente → Pi: envío de frames

Cada frame se manda así, en este orden exacto, sobre la conexión persistente:

1. **4 bytes**, entero sin signo de 32 bits, **big-endian** — la longitud en bytes del
   JPEG que sigue.
2. Los bytes crudos del JPEG (longitud exacta indicada arriba). **No viene en base64
   en el wire** — el cliente decodifica base64 a bytes antes de enviarlo; lo que tú
   recibes por el socket ya son bytes JPEG crudos.

En Python, leer un frame se ve así:

```python
import struct

def read_frame(sock_reader):
    len_bytes = read_exact(sock_reader, 4)
    frame_len = struct.unpack(">I", len_bytes)[0]  # big-endian unsigned int
    jpeg_bytes = read_exact(sock_reader, frame_len)
    return jpeg_bytes
```

(`read_exact` = tu propia función que sigue leyendo hasta juntar exactamente N bytes —
un solo `recv()` no garantiza recibir todo de una vez.)

### 4.4 Pi → Cliente: mensajes de instrucción/log

Sobre la **misma conexión**, en el sentido contrario, el cliente lee **líneas de texto
UTF-8 terminadas en `\n`** (`BufReader::read_line` del lado Rust). No es JSON
obligatoriamente — el cliente toma la línea completa (recortando espacios/el salto de
línea) y la muestra tal cual en el panel de log. El único ejemplo ya probado contra el
código real del cliente (test `test_cva_send_frame_success_with_real_listener`) es:

```
gesto: dedo anular, instruccion: mover adelante\n
```

**El formato exacto del contenido de esa línea todavía no está cerrado** — se termina
de definir junto con JD en la Fase 7/8, cuando se defina el catálogo real de gestos. Lo
que sí está fijo y no debe cambiar sin coordinar con el cliente es el *mecanismo*: una
línea de texto por mensaje, terminada en `\n`, sobre la misma conexión TCP.

Por ahora (Fase 6), no tienes que mandar nada por este canal todavía — es información
para que el `transport` que construyas ya soporte escritura en este formato cuando
haga falta (Fase 7+, cuando actives el watchdog o cualquier mensaje de estado).

### 4.5 Cosa pendiente de resolver — `module_id` no viaja por este socket

El cliente sabe si la práctica activa es `domotica` o `robot` (se lo pasa a
`cva_gestures_session_start` en Rust), pero **hoy ese dato no se transmite al bridge
por ningún canal** — ni por el socket TCP, ni por ningún otro mecanismo. Esto es un
hueco real en el diseño actual, no algo que ya esté resuelto. Antes de que el mapeo de
gestos dependa del módulo activo (Fase 9), hay que decidir cómo se entera el bridge de
qué módulo está activo — opciones a evaluar (no implementar todavía, solo tenerlo en
mente para cuando llegue el momento): un mensaje inicial de "handshake" al abrir la
conexión, un archivo/socket de estado separado, o pasarlo por query al abrir el túnel.
Avisa a JD/Claude explícitamente cuando esto se vuelva relevante (Fase 7 en adelante)
en vez de asumir una solución.

## 5. Fail-safes esperados (ver criterios de aceptación completos en Fase 10)

- Heartbeat implícito: sin frames en un tiempo definido (punto de partida: 5s, ajustable
  con pruebas reales) → dejar de emitir instrucciones.
- Umbral de confianza mínimo antes de traducir un gesto a instrucción (viene del
  `min_confidence` de la config del módulo — Fase 9 en adelante).
- Rate limit de instrucciones por segundo hacia el hardware.
- Parada de emergencia: un mensaje explícito de `STOP` desde el cliente corta cualquier
  instrucción en curso de inmediato.
- **Principio del proyecto que aplica aquí:** cualquier función decorativa del lado del
  cliente (hay un overlay cosmético de esqueleto de mano, 100% client-side) nunca debe
  ser algo que el bridge tenga en cuenta — el bridge decide solo con lo que YOLO
  detecta en el frame real, nunca con nada que venga "sugerido" del cliente.

## 6. Reglas de gobernanza (aplican igual que en el resto del proyecto)

1. **Nada de `git push` sin autorización explícita de JD en esa sesión**, aunque
   commits locales sí pueden ir avanzando. Esta regla es tan válida aquí como en el
   cliente.
2. Cada pieza nueva lleva su test (`pytest`) — reconocimiento contra fixtures fijas
   (cuando exista visión), watchdog, umbral de confianza. No se da por terminada una
   fase con tests rotos o ausentes.
3. Documenta cada decisión relevante y avisa explícitamente si algo de lo que se te pide
   contradice lo ya construido en el cliente (protocolo, formatos) — no lo cambies por
   tu cuenta sin avisar, porque el cliente ya está aprobado y en producción de pruebas.
4. **Una fase a la vez.** Al terminar Fase 6, te detienes y esperas revisión antes de
   avanzar a Fase 7 — no sigas de largo.
5. Errores explícitos, nunca silenciosos — si algo falla, se loguea con claridad, nunca
   un `except: pass`.

## 7. Roadmap completo (para orientarte — no construyas nada de esto todavía salvo
   Fase 6, sección 3)

- **Fase 6 (esta fase):** estructura base, sin visión, solo recibe y loguea frames.
- **Fase 7:** checkpoint de validación end-to-end con el cliente real (sin actuadores
  todavía). Aquí se mide latencia real y se define el catálogo definitivo de gestos.
- **Fase 8:** `vision/detector.py` — YOLO + OpenCV real, benchmark de tamaño de modelo
  en esta Pi 5, catálogo mínimo de gestos, resultado impreso en terminal (sin mover
  hardware todavía). Aquí también se cierra el formato exacto de la sección 4.4.
- **Fase 9 (Domótica primero):** `actuators/arduino_client.py` hacia
  `arduino_bridge.py` (`localhost:8765`, ya existe, no se toca), mapeo real
  gesto→instrucción en `config/cva-gestures/domotica.json`.
- **Fase 10:** fail-safes end-to-end probados con hardware real (heartbeat, rate
  limit, parada de emergencia).
- **Fase 11 (Robot EV3):** mismo trabajo que Fase 8-10 pero para el robot — el
  protocolo real del servicio del robot todavía no se ha investigado.
- **Fase 12:** documentación, fuera del alcance de desarrollo.

## 8. Al terminar Fase 6

Reporta explícitamente: qué archivos creaste, confirmación de que los frames de un
cliente real (o un test que simule uno) llegan y se loguean con conteo/tamaño/fps
correctos, y los resultados de los tests. Espera la revisión antes de tocar Fase 7.
