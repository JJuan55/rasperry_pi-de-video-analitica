# `main_cva.py`: servidor del robot EV3 acotado para CVA

Copia mínima del `main.py` que corre en el ladrillo EV3. Sirve para las pruebas
de CVA, con dos objetivos: que la rueda derecha (outD) **sí se mueva** y que el
servidor rechace lo que no debe mover el robot. No reemplaza a `main.py` ni lo
modifica.

Base: el `main.py` del robot (16 629 bytes, sha256
`830c6e51b58fb8b55856608e541bb233e85869afe500039c2a1b28a3fd466c35`).

## Por qué

Medido en el robot real (2026-10-29):

- outC es un motor **grande** (oruga izquierda; +grados = adelante).
- outD es un motor **mediano**.
- `main.py` crea los cuatro motores como `LargeMotor` dentro de un `try/except`
  que deja en `None` el que falla. outD queda en `None`, `_ejecutar_pasos` lo
  ignora sin avisar, `POST /ejecutar_ruta` responde `ok:true` y la rueda D no se
  mueve.
- `/status` marca outD con `connected:true` porque lo lee de sysfs, aunque
  `main.py` no lo pueda mover.

## Qué cambia frente a `main.py`

| | `main.py` | `main_cva.py` |
|---|---|---|
| Motores | los cuatro `LargeMotor` | outC `LargeMotor`, outD `MediumMotor`; outA y outB no se usan (`None`) |
| `/status`, `connected` de outC y outD | solo sysfs | sysfs **y** que el motor se haya creado |
| `/ejecutar_ruta` | arranca cualquier cosa; un motor `None` se ignora | valida todo antes de mover nada; si algo falla, 400 y no se mueve ningún motor |
| Potencia por defecto | 40 | 20 |
| Arranque de ruta | comprobación sin lock; `ejecutando` lo pone el hilo | atómico con un lock; `ejecutando` queda en true antes de responder |
| **`POST /stop`** | apaga motores, pero **la ruta sigue** con el paso siguiente | **igual que `/detener_ejecucion`**: marca el paro, apaga con freno y **cancela la ruta en curso**; ignora el cuerpo |
| `/move`, `GET /?cmd=` (incluido `killpid`) | sí | eliminados: 404 |
| Quién puede conectarse | cualquiera | 127.0.0.1 y las IP de `--permitir`; las demás reciben 403 sin que se lea el cuerpo |
| Puerto | 8080 | 8081 (`--puerto`) |

`detener_ejecucion` y la lógica de `_mover_dos_motores` no cambian. `/status`
conserva el mismo formato.

### Reglas de `POST /ejecutar_ruta`

Si se incumple cualquiera, la respuesta es HTTP 400 `{"ok": false, "error": "..."}` y
no se mueve nada:

- El cuerpo es un objeto JSON legible con `pasos`, una lista de 1 a 4 pasos.
- Paso de movimiento:
  - `motorIzq` y `motorDer` son textos que, normalizados como en `normalize_port`
    (`"C"`, `"outC"`, `"ev3-ports:outC"`), dan `outC` u `outD`, y son distintos.
  - `gradosIzq` y `gradosDer` son enteros (no booleanos) entre −180 y 180, y al
    menos uno es distinto de 0.
  - `potencia`, si está, es un entero entre 1 y 30. Si falta, se usa 20.
- Paso de pausa: exactamente `{"pausaMs": n}`, con `n` entero entre 0 y 5000.
- Cada motor usado debe existir. Si no, el error es `motor no disponible: outX`.

Las claves desconocidas en un paso de movimiento se ignoran, como en `main.py`.

## Uso en el robot

Antes de arrancarlo, **detén `main.py`**. Si los dos corren a la vez, ambos
podrían mover outC.

```bash
cd /home/robot/cva && timeout 1800 python3 main_cva.py --permitir IP_PI5 --permitir IP_PI4
```

- `--permitir` es obligatorio y se puede repetir. Sin ninguna IP, el servidor no
  arranca. 127.0.0.1 siempre se acepta, para probar desde el propio robot.
- `--puerto` vale 8081 por defecto.
- Las IP no se escriben en ningún archivo ni en el registro: al arrancar solo se
  imprime cuántas hay. Sí se ven en la línea de comandos del proceso (`ps`) y en
  el historial del shell.
- `timeout 1800` lo detiene a los 30 min con SIGTERM. El servidor no captura esa
  señal: si hay un paso en curso, ese paso termina sus grados (`on_for_degrees` lo
  completa el firmware), pero los pasos siguientes no arrancan.

Para copiarlo al robot (desde la Pi5; cambia `USUARIO` y `EV3`):

```bash
scp ~/video_analitica/cva-pi-repo-etapa0/tools/robot_ev3/main_cva.py USUARIO@EV3:/home/robot/cva/
```

## Pruebas

```bash
cd ~/video_analitica/cva-pi-repo-etapa0
python -m pytest tests/test_main_cva.py
```

Usan un `ev3dev2` falso y un sysfs simulado, sin robot. La compatibilidad con
Python 3.5 se comprueba sobre el código fuente (análisis con
`feature_version=(3, 5)` y búsqueda de f-strings, anotaciones y otra sintaxis de
3.6 o posterior), no ejecutándolo en 3.5.
