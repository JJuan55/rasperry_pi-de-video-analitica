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

- Motores reales: **outC conectado (rueda izquierda)** y **outD conectado (rueda
  derecha)**; outA y outB no están conectados.
- **Grados positivos = adelante en las dos ruedas.**
- **Un motor no conectado se ignora SIN avisar.** Por eso, al arrancar en modo real, el
  cliente exige `connected: true` en outC y outD.

## Comportamiento observado

- El servidor atiende cada petición en su propio hilo.
- Observado una vez: una petición de ruta movió el robot y su respuesta no llegó en 5 s.
  Por eso el cliente nunca interpreta "sin respuesta" como "no ejecutado", nunca
  reenvía un paso y, al parar, vigila `/estado_ejecucion` hasta un margen después del
  último envío de ruta.
