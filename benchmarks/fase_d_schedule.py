"""Fase D -- guion de medición con verdad conocida.

No corre contra el bridge ni importa nada de cva_gesture_bridge -- solo imprime,
con el reloj de ESTA Pi, cuándo hacer cada gesto y cuándo quitar la mano de
cuadro. JD lo mira en una terminal SSH aparte (no es la terminal donde corre el
bridge de prueba) y sigue las instrucciones en tiempo real.

Por qué el mismo reloj importa: el bridge de prueba loguea con
"%(asctime)s %(levelname)s %(name)s: %(message)s" (ver logging_setup.py), que
imprime con milisegundos en la misma máquina -- al imprimir las señales de este
guion con el mismo formato de timestamp, se pueden cruzar directamente las líneas
de este guion con las líneas "Gesto detectado"/"Gesto liberado"/"[diag]" del log
real del bridge sin depender de relojes de dos máquinas distintas (la Pi y la
máquina de JD) ni de sincronización NTP entre ellas.

Uso: `.venv-fase-c2-fix/bin/python benchmarks/fase_d_schedule.py [condicion]`
`condicion` es solo una etiqueta libre para el nombre del archivo de log que JD
reporte aparte (ej. "cerca", "lejos", "persona2") -- este script no escribe
ningún archivo, solo imprime a pantalla.
"""

import sys
import time
from datetime import datetime

GESTURES = ["puño_cerrado", "palma_abierta", "dedo_pulgar", "dedo_menique"]
REPEATS_PER_GESTURE = 10
HOLD_SECONDS = 3
# Margen real sobre GESTURE_RELEASE_AFTER_MISSES_NO_HAND=16 (~16*143ms=2.3s, ver
# BITACORA.md "Fase C2") + margen de reacción humana para sacar la mano del cuadro.
# Subido a 5s (revisión del plan, 2026-10) -- 4s dejaba poco margen sobre los
# 2.3s teóricos antes de empezar a mover la mano de vuelta para la repetición
# siguiente.
GAP_SECONDS = 5
COUNTDOWN_SECONDS = 10


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]


def _announce(msg: str) -> None:
    print(f"\a{_now()} CUE: {msg}", flush=True)  # \a: pitido audible en cada señal


def main() -> None:
    condicion = sys.argv[1] if len(sys.argv) > 1 else "sin_etiqueta"
    total = len(GESTURES) * REPEATS_PER_GESTURE

    print(f"=== Guion de Fase D -- condición: {condicion} ===")
    print(f"{total} repeticiones totales ({REPEATS_PER_GESTURE} x {len(GESTURES)} gestos)")
    print(f"Orden: {', '.join(GESTURES)} (bloques de {REPEATS_PER_GESTURE} seguidos)")
    print(f"Sostener {HOLD_SECONDS}s por gesto, {GAP_SECONDS}s con la mano FUERA de cuadro entre cada una.")
    print(f"Arrancando en {COUNTDOWN_SECONDS}s -- confirma que el bridge de prueba ya está corriendo.")
    time.sleep(COUNTDOWN_SECONDS)

    rep_num = 0
    for gesture in GESTURES:
        for _ in range(REPEATS_PER_GESTURE):
            rep_num += 1
            _announce(f"[{rep_num}/{total}] HAZ: {gesture} (sostener {HOLD_SECONDS}s)")
            time.sleep(HOLD_SECONDS)
            _announce(f"[{rep_num}/{total}] QUITA la mano de cuadro ({GAP_SECONDS}s)")
            time.sleep(GAP_SECONDS)

    _announce(f"FIN del guion -- condición {condicion} completa ({total} repeticiones)")


if __name__ == "__main__":
    main()
