# Herramientas de la Etapa 0 del robot (prueba física SIN gestos)

Tres scripts para dar un paso, parar y probar la parada en pleno movimiento, con el robot
elevado o con espacio libre. Solo usan `bash`, `curl` y `python3` (biblioteca estándar):
no dependen de `cva_gesture_bridge/`, así que funcionan aunque falle la cadena de gestos.
Solo usan rutas de `docs/CONTRATO_ROBOT_EV3.md`: `GET /status`, `GET /estado_ejecucion`,
`POST /ejecutar_ruta` y `POST /detener_ejecucion`.

| Archivo | Qué hace |
|---|---|
| `ev3-paso.sh` | Un paso: `avanzar`, `izquierda` o `derecha`. Topes: grados 1–180 (90), potencia 1–30 (20). Comprueba `/status` (batería, outC y outD conectados) y `/estado_ejecucion`, pide **SI** y exige `ok:true` con `totalPasos:1`. Si no hay respuesta, **no reintenta**. `--ensayo` muestra lo que enviaría sin conectar. |
| `ev3-parar.sh` | Parada inmediata, sin preguntar: `/detener_ejecucion` (hasta 3 intentos) y vigila `/estado_ejecucion` hasta ver `ejecutando:false` dos veces (máx. 10 s). Dice los ms. Código 0 solo si la parada quedó confirmada. No usa `/status`. |
| `ev3-prueba-parada.sh` | Con la misma confirmación SI: una ruta de 4 pasos de 180° a potencia 20 (`--pasos 1..4`), espera 1,0 s y para con el mismo mecanismo. Lee `/status` antes y después y dice **PARÓ en X ms** o **NO PARÓ** (incluye si reanuda). Guarda un registro en `~/robot_pruebas/`. |
| `ev3-comun.sh` | Funciones comunes (se carga desde los otros tres; hay que copiarlo con ellos). |
| `pruebas/pruebas_sim.py` | Pruebas contra el simulador en 127.0.0.1 (no contra el robot). |

Códigos de salida: `0` bien; `1` el robot no aceptó, no respondió, no paró o falló una
comprobación; `2` uso o URL; `3` no se escribió SI.

## Dirección del robot

No hay valor por defecto (la IP cambia por DHCP). Se toma, en este orden, de:

1. `--url http://HOST:PUERTO`
2. la variable `EV3_URL`
3. el archivo `~/.ev3_url` (una línea)

Si no hay ninguna, el script lo dice y no hace nada. La dirección no se escribe en pantalla
ni en los registros. Ejemplo: `echo 'http://HOST:PUERTO' > ~/.ev3_url` (con la dirección
del día; no la subas al repositorio).

## Copiarlos a la Pi4

Desde la Pi5 (cambia `USUARIO` y `PI4` por los de la Pi4):

```bash
scp ~/video_analitica/cva-pi-repo-etapa0/tools/etapa0_robot/ev3-*.sh USUARIO@PI4:~/etapa0_robot/
```

Antes, en la Pi4: `mkdir -p ~/etapa0_robot`. Después: `chmod +x ~/etapa0_robot/ev3-*.sh`.
La Pi4 necesita bash 5, curl y python3; nada más.

## Pruebas contra el simulador

```bash
cd ~/video_analitica/cva-pi-repo-etapa0
python3 tools/etapa0_robot/pruebas/pruebas_sim.py
```

## Hoja de la prueba física

Antes de empezar: robot elevado (ruedas al aire) o con espacio libre, laboratorista presente
con el corte de energía a mano, y `ev3-parar.sh` listo en otra terminal.

Fecha: ____________  Hora inicio: ______  Batería (V, de `/status`): ______
Personas: ______________________________  Robot: elevado ☐ / en el suelo ☐

| # | Prueba | Comando | Criterio | Resultado |
|---|---|---|---|---|
| 1 | Parar sin ruta en curso | `ev3-parar.sh` | código 0, PARADA CONFIRMADA | ____ ms ☐ bien |
| 2 | Paso adelante | `ev3-paso.sh avanzar` | las dos ruedas giran hacia delante | ☐ bien |
| 3 | Giro a la derecha | `ev3-paso.sh derecha` | izquierda adelante, derecha atrás | ☐ bien |
| 4 | Giro a la izquierda | `ev3-paso.sh izquierda` | izquierda atrás, derecha adelante | ☐ bien |
| 5 | Parada en movimiento | `ev3-prueba-parada.sh` | **PARÓ en < 1000 ms** | ____ ms ☐ bien |
| 6 | No reanuda | (en la misma prueba 5) | quieto 3 s después, a la vista y en `/status` | ☐ bien |
| 7 | Parada en movimiento, repetición | `ev3-prueba-parada.sh` | < 1000 ms | ____ ms ☐ bien |
| 8 | Parada en movimiento, repetición | `ev3-prueba-parada.sh` | < 1000 ms | ____ ms ☐ bien |

Calibración del paso con cinta (en el suelo, potencia 20). Marca la posición, ejecuta y
mide lo que avanzó (o el ángulo que giró):

| Comando | Avance o giro medido | Repetición 2 | Repetición 3 |
|---|---|---|---|
| `ev3-paso.sh avanzar --grados 90` | ______ cm | ______ cm | ______ cm |
| `ev3-paso.sh avanzar --grados 180` | ______ cm | ______ cm | ______ cm |
| `ev3-paso.sh derecha --grados 90` | ______ ° | ______ ° | ______ ° |
| `ev3-paso.sh derecha --grados 180` | ______ ° | ______ ° | ______ ° |
| `ev3-paso.sh izquierda --grados 90` | ______ ° | ______ ° | ______ ° |
| `ev3-paso.sh izquierda --grados 180` | ______ ° | ______ ° | ______ ° |

Observaciones (ruidos, tirones, una rueda más lenta, respuestas que no llegaron):

______________________________________________________________________________

______________________________________________________________________________
