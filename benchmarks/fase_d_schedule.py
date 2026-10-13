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

Uso: `.venv-fase-c2-fix/bin/python benchmarks/fase_d_schedule.py [condicion] [--repeats N]`
`condicion` es una etiqueta libre (ej. "cerca", "50cm", "persona2") usada tanto
para identificar la corrida en pantalla como para el nombre del archivo de CUE
guardado. `--repeats` (default 10) permite baterías cortas (ej. 5 para la
batería de liberación de Fase D sesión 2, 4 gestos x 5 = 20 repeticiones).

Guarda SIEMPRE las señales en `benchmarks/fase_d_cue_<condicion>_<fecha_hora>.log`
(modo "x": nunca sobreescribe ni mezcla corridas -- cada corrida cae en su propio
archivo nuevo por el sello de fecha/hora en el nombre), con el mismo formato de
timestamp que usa este script en pantalla, para que `analyze_fase_d_run.py` pueda
cruzar ambos archivos.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fase_d_common import make_announce, open_cue_log  # noqa: E402

GESTURES = ["puño_cerrado", "palma_abierta", "dedo_pulgar", "dedo_menique"]
HOLD_SECONDS = 3
# Margen real sobre GESTURE_RELEASE_AFTER_MISSES_NO_HAND=16 (~16*143ms=2.3s, ver
# BITACORA.md "Fase C2") + margen de reacción humana para sacar la mano del cuadro.
GAP_SECONDS = 5
COUNTDOWN_SECONDS = 10


def build_sequence(gestures, repeats_per_gesture):
    """Función pura (sin sleep ni I/O) que arma la secuencia de repeticiones --
    separada del bucle de ejecución para poder testearla sin esperar segundos
    reales. Devuelve una lista plana de `repeats_per_gesture` copias de cada
    gesto, en bloques, en el orden de `gestures`."""
    sequence = []
    for gesture in gestures:
        sequence.extend([gesture] * repeats_per_gesture)
    return sequence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("condicion", nargs="?", default="sin_etiqueta")
    parser.add_argument(
        "--repeats", type=int, default=10,
        help="Repeticiones por gesto (default 10; ej. 5 para la batería corta de liberación).",
    )
    args = parser.parse_args()

    sequence = build_sequence(GESTURES, args.repeats)
    total = len(sequence)

    here = os.path.dirname(os.path.abspath(__file__))
    log_file, log_path = open_cue_log(here, f"fase_d_cue_{args.condicion}")

    with log_file:
        announce = make_announce(log_file)

        print(f"=== Guion de Fase D -- condición: {args.condicion} ===")
        print(f"Guardando señales en: {log_path}")
        print(f"{total} repeticiones totales ({args.repeats} x {len(GESTURES)} gestos)")
        print(f"Orden: {', '.join(GESTURES)} (bloques de {args.repeats} seguidos)")
        print(f"Sostener {HOLD_SECONDS}s por gesto, {GAP_SECONDS}s con la mano FUERA de cuadro entre cada una.")
        print(f"Arrancando en {COUNTDOWN_SECONDS}s -- confirma que el bridge ya está corriendo.")
        time.sleep(COUNTDOWN_SECONDS)

        for rep_num, gesture in enumerate(sequence, start=1):
            announce(f"[{rep_num}/{total}] HAZ: {gesture} (sostener {HOLD_SECONDS}s)")
            time.sleep(HOLD_SECONDS)
            announce(f"[{rep_num}/{total}] QUITA la mano de cuadro ({GAP_SECONDS}s)")
            time.sleep(GAP_SECONDS)

        announce(f"FIN del guion -- condición {args.condicion} completa ({total} repeticiones)")


if __name__ == "__main__":
    main()
