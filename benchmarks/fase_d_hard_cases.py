"""Fase D -- guion de los 5 casos difíciles (sesión 2), mismo estilo y reloj que
`fase_d_schedule.py` (ver su docstring para por qué importa el mismo reloj/
formato de timestamp). No corre contra el bridge ni importa nada de
cva_gesture_bridge.

Cada caso: 10s de cuenta regresiva (para que JD se reacomode/consiga a la
segunda persona del caso 5 antes de que arranque el bloque medido) + bloque
medido, con el gesto objetivo (si aplica) nombrado explícitamente en la
instrucción. Orden fijo, no aleatorio -- los criterios de aceptación de cada
caso (`analyze_hard_cases.py`) se definieron ANTES de correr esto, mirando este
mismo orden.

Repetición guiada (Fase D sesión 2, después de la primera corrida): los casos
1, 4 y 5 ahora tienen señales DENTRO del bloque, para que lo que hace JD quede
con hora en el CUE y el análisis no dependa de la memoria de nadie:
- Caso 1: una señal `CAMBIA [i/16] a: <gesto>` cada CHANGE_SECONDS, 2 rondas
  de 8 cambios en orden fijo. Con esto el criterio "no más de un envío por
  cambio real" pasa a ser verificable (antes no se grababa cuándo cambiaba JD).
- Caso 4: el detector corre con `num_hands=1` en modo VIDEO (seguimiento): se
  queda con la primera mano detectada mientras siga en cuadro. Por eso el caso
  se hace en orden: primero SOLO la mano del pulgar, y a los
  SECOND_HAND_AT_SECONDS una señal `METE la segunda mano`. Lo que se prueba es
  que la segunda mano no robe el seguimiento ni produzca envíos.
- Caso 5: señales `PASA` para cada cruce de la segunda persona.

`--casos` permite correr solo algunos casos (por nombre corto, separados por
comas), en el mismo orden fijo de CASES.

Uso: `.venv-fase-c2-fix/bin/python benchmarks/fase_d_hard_cases.py [--casos a,b,c]`
Guarda las señales en `benchmarks/fase_d_cue_hard_cases_<fecha_hora>.log` (modo
"x", igual que `fase_d_schedule.py`).
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fase_d_common import make_announce, open_cue_log  # noqa: E402

COUNTDOWN_SECONDS = 10
BLOCK_SECONDS = 30

# Caso 1 guiado: 2 rondas x 8 cambios, en orden fijo (los 4 gestos del
# catálogo, dos veces por ronda). Un cambio cada CHANGE_SECONDS -- con ~143ms
# por cuadro y confirmación 5 de 7, el sistema necesita ~0,6-0,7s más la
# reacción de JD; 2s deja margen para que cada cambio alcance a confirmarse.
CHANGE_ORDER = ["puño_cerrado", "palma_abierta", "dedo_pulgar", "dedo_menique"]
CHANGES_PER_ROUND = 8
ROUNDS = 2
CHANGE_SECONDS = 2.0

# Caso 4: segundos desde ARRANCA hasta la señal de meter la segunda mano.
SECOND_HAND_AT_SECONDS = 8.0

# Caso 5: segundos desde ARRANCA de cada cruce de la segunda persona.
PERSON_PASSES_AT_SECONDS = (5.0, 18.0)

# (nombre_corto, gesto_objetivo_o_None, instrucción exacta para JD)
CASES = [
    (
        "movimiento_rapido",
        None,
        "CASO 1 -- movimiento rápido GUIADO: deja la mano en cuadro TODO el bloque. "
        "Cada 2s sale una señal 'CAMBIA [i/16] a: <gesto>': cambia a ESE gesto en "
        "cuanto la veas y quédate en él hasta la siguiente señal. 2 rondas de 8 "
        "cambios (puño, palma, pulgar, meñique, x2 por ronda). Si te pierdes, NO "
        "adivines: sigue la siguiente señal.",
    ),
    (
        "mano_tapada_palma_abierta",
        "palma_abierta",
        "CASO 2/5 -- mano tapada a medias: tapa parcialmente tu mano (con la otra "
        "mano o un objeto) e intenta sostener palma_abierta.",
    ),
    (
        "mano_en_el_borde_puno_cerrado",
        "puño_cerrado",
        "CASO 3/5 -- mano en el borde del cuadro: pon tu mano en el BORDE de la "
        "cámara (que se vea solo parcialmente) e intenta sostener puño_cerrado.",
    ),
    (
        "dos_manos_dedo_pulgar",
        "dedo_pulgar",
        "CASO 4 -- dos manos, EN ORDEN: ahora deja AMBAS manos fuera de cuadro. Al "
        "ARRANCA mete SOLO una mano haciendo dedo_pulgar y sostenlo. Cuando salga "
        "'METE la segunda mano', mete la otra mano ABIERTA, separada de la primera "
        "(sin taparla), y deja las dos quietas hasta TERMINA. No saques la mano del "
        "pulgar en ningún momento.",
    ),
    (
        "persona_detras_sin_gesto",
        None,
        "CASO 5 -- persona detrás: saca tus DOS manos del cuadro YA (bajo la mesa o "
        "detrás de la espalda) y no las metas hasta FIN. Cada vez que salga 'PASA', "
        "la otra persona cruza caminando detrás de ti, de lado a lado, con los "
        "brazos abajo pegados al cuerpo (sin levantar ni mostrar las manos).",
    ),
]

CASE_NAMES = [c[0] for c in CASES]


def build_case_steps(name):
    """Función pura (sin sleep ni I/O): señales DENTRO del bloque de un caso,
    como (segundos_desde_ARRANCA, mensaje), y la duración total del bloque.
    Devuelve (steps, block_seconds)."""
    if name == "movimiento_rapido":
        total = CHANGES_PER_ROUND * ROUNDS
        steps = []
        for i in range(total):
            gesture = CHANGE_ORDER[i % len(CHANGE_ORDER)]
            steps.append((i * CHANGE_SECONDS, f"CAMBIA [{i + 1}/{total}] a: {gesture}"))
        return steps, total * CHANGE_SECONDS
    if name == "dos_manos_dedo_pulgar":
        return [(SECOND_HAND_AT_SECONDS, "METE la segunda mano ABIERTA (sin sacar la del pulgar)")], BLOCK_SECONDS
    if name == "persona_detras_sin_gesto":
        steps = [
            (t, f"PASA [{i + 1}/{len(PERSON_PASSES_AT_SECONDS)}] -- la otra persona cruza detrás, brazos abajo")
            for i, t in enumerate(PERSON_PASSES_AT_SECONDS)
        ]
        return steps, BLOCK_SECONDS
    return [], BLOCK_SECONDS


def select_cases(casos_arg):
    """Filtra CASES por los nombres cortos dados (separados por comas),
    conservando el orden fijo de CASES. None = todos. Nombre desconocido =
    error explícito, nunca se ignora en silencio."""
    if not casos_arg:
        return list(CASES)
    wanted = [n.strip() for n in casos_arg.split(",") if n.strip()]
    unknown = [n for n in wanted if n not in CASE_NAMES]
    if unknown:
        raise ValueError(f"Caso(s) desconocido(s): {unknown}. Válidos: {CASE_NAMES}")
    return [c for c in CASES if c[0] in wanted]


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--casos", default=None, help=f"Nombres cortos separados por comas. Válidos: {','.join(CASE_NAMES)}")
    args = parser.parse_args(argv)
    try:
        cases = select_cases(args.casos)
    except ValueError as e:
        parser.error(str(e))

    here = os.path.dirname(os.path.abspath(__file__))
    log_file, log_path = open_cue_log(here, "fase_d_cue_hard_cases")

    with log_file:
        announce = make_announce(log_file)

        print(f"=== Guion de Fase D -- casos difíciles ({len(cases)} caso(s), orden fijo) ===")
        print(f"Casos: {', '.join(c[0] for c in cases)}")
        print(f"Guardando señales en: {log_path}")
        print(f"Cada caso: {COUNTDOWN_SECONDS}s de cuenta regresiva + bloque medido.")
        print("Confirma que el bridge ya está corriendo antes de seguir.")

        for name, target, instruction in cases:
            steps, block_seconds = build_case_steps(name)
            announce(f"PREPARA -- {instruction}")
            print(f"  (cuenta regresiva de {COUNTDOWN_SECONDS}s para este caso...)")
            time.sleep(COUNTDOWN_SECONDS)
            start = time.monotonic()
            announce(f"ARRANCA caso '{name}' (objetivo={target}) -- bloque de {block_seconds:.0f}s")
            for offset, msg in steps:
                delay = start + offset - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                announce(msg)
            delay = start + block_seconds - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            announce(f"TERMINA caso '{name}'")

        announce(f"FIN del guion de casos difíciles -- {len(cases)}/{len(cases)} completos")


if __name__ == "__main__":
    main()
