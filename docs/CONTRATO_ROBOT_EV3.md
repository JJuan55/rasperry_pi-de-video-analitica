# Contrato del servicio HTTP del robot EV3 (solo los endpoints que usa el bridge)

Fuente: JD, leído del código del servicio del robot y confirmado con respuestas reales
(2026-10). Este archivo es la referencia para `cva_gesture_bridge/actuators/robot_client.py`
y para el simulador de pruebas `tests/robot_sim.py`.

No contiene direcciones, credenciales ni otras funciones del servicio: la dirección del
robot va solo en la configuración de la Pi (`robot_url` en `config/robot_actuador.json`,
sin valor por defecto) y nunca en el repositorio.

## POST /ejecutar_ruta

Cuerpo:

```json
{"pasos": [{"motorIzq": "outC", "motorDer": "outD", "gradosIzq": 90, "gradosDer": 90, "potencia": 20}]}
```

- Un elemento de `pasos` también puede ser `{"pausaMs": N}`. El bridge **no** lo usa.
- Si no se manda `potencia`, el robot usa **40**. El bridge siempre la manda (tope duro
  30).
- Respuesta correcta: `{"ok": true, "totalPasos": N}`.
- Si ya hay una ruta en ejecución: **HTTP 409** `{"ok": false, "error": "ya hay una ruta en ejecución"}`.
- **Un JSON malformado se trata como cuerpo vacío** y responde `{"ok": true, "totalPasos": 0}`.
  Por eso el cliente **exige `totalPasos` == número de pasos enviados**; si no coincide,
  es un fallo.
- Al procesar esta petición el servidor ejecuta `detener.clear()`: **borra una parada
  anterior**. Una orden de ruta retrasada que llegue después de un `/detener_ejecucion`
  lo anula.

## GET /estado_ejecucion

`{"pasoActual": N, "totalPasos": N, "ejecutando": true|false, "error": ...}`

## POST /detener_ejecucion

`{"ok": true}`. Marca el paro y apaga los motores con freno; cancela la ruta en curso.

## POST /stop

`{"ok": true, "stopped": [...]}`. Para los motores, pero **en una ruta en curso /stop
solo NO la cancela**: hay que mandar antes `/detener_ejecucion`.

## GET /status

`{"motors": [{"port", "speed", "position", "connected"}], "battery": <voltios>, "sensors": [...], "connected": true}`

Forma de cada motor (medida en el robot real, 2026-10-29):
`{"port": "outC", "connected": true, "position": 0, "speed": 0}`.

- `position`: grados acumulados del encoder, entero; **no se reinicia** entre rutas.
- Motores reales: **outC conectado (rueda izquierda)** y **outD conectado (rueda
  derecha)**; outA y outB no están conectados.
- **Grados positivos = adelante en las dos ruedas.** Medido con outC: +90 grados = la
  oruga izquierda hacia el frente.
- **Un motor no conectado se ignora SIN avisar.** Por eso, al arrancar en modo real, el
  cliente exige `connected: true` en outC y outD.
- **`connected: true` NO garantiza que la rueda se mueva.** Se lee de sysfs y solo dice
  que hay un motor en ese puerto. outD es un motor **mediano**, y el servicio del robot
  crea todos los motores como `LargeMotor`, así que no lo puede mover: un
  `POST /ejecutar_ruta` con grados para outD responde `{"ok": true, "totalPasos": 1}` y
  esa rueda **no se mueve** (medido 2026-10-29). Ni `connected` ni `ok:true` ni
  `totalPasos` sirven para saber si una rueda se movió; solo `position`.

## Verificación de las dos ruedas tras cada paso (cliente, solo en modo real)

Por lo anterior, el cliente verifica cada paso AVANZAR / GIRAR_* en
`RouteDispatcher` (`robot_client.py`):

1. `GET /status` antes de enviar: guarda `position` de outC y outD. Si falla, no envía
   el paso.
2. Envía el paso como siempre y consulta `GET /estado_ejecucion` hasta
   `ejecutando: false`, como mucho `verificacion_espera_s` (5 s de partida).
3. `GET /status` otra vez. Cada rueda debe haberse movido lo enviado (signo × grados)
   ± `máx(verificacion_tol_grados, verificacion_tol_frac × |esperado|)` (de partida
   máx(10°, 15 %): ±13,5° para 90°).
4. Una rueda fuera de tolerancia, un dato faltante o ilegible, o el tiempo agotado son
   un **paso fallido**: PARAR, controlador BLOQUEADO y un error en el registro con el
   movimiento de cada rueda (`outD (derecha) se movió +0° (esperado +90° ±13.5°) FUERA
   DE TOLERANCIA`).
5. PARAR sigue con su hilo y su conexión propios y no espera a la verificación; si el
   paso se corta por un PARAR del propio cliente, no se informa como fallo de rueda. Una
   parada hecha por OTRO cliente (por ejemplo `ev3-parar.sh`) a mitad del paso sí se
   informa como fallo, porque este cliente no la pidió.

En ENSAYO no hay verificación: no hay robot.

## Comportamiento observado

- El servidor atiende cada petición en su propio hilo.
- Observado una vez: una petición de ruta movió el robot y su respuesta no llegó en 5 s.
  Por eso el cliente nunca interpreta "sin respuesta" como "no ejecutado", nunca
  reenvía un paso y, al parar, vigila `/estado_ejecucion` hasta un margen después del
  último envío de ruta.
