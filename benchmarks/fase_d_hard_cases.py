"""Fase D -- guion de los 5 casos difíciles (sesión 2), mismo estilo y reloj que
`fase_d_schedule.py` (ver su docstring para por qué importa el mismo reloj/
formato de timestamp). No corre contra el bridge ni importa nada de
cva_gesture_bridge.

Cada caso: 10s de cuenta regresiva (para que JD se reacomode/consiga a la
segunda persona del caso 5 antes de que arranque el bloque medido) + 30s de
bloque medido, con el gesto objetivo (si aplica) nombrado explícitamente en la
instrucción. Orden fijo, no aleatorio -- los criterios de aceptación de cada
caso (sección E, `analyze_hard_cases.py`) se definieron ANTES de correr esto,
mirando este mismo orden.

Uso: `.venv-fase-c2-fix/bin/python benchmarks/fase_d_hard_cases.py`
Guarda las señales en `benchmarks/fase_d_cue_hard_cases_<fecha_hora>.log` (modo
"x", igual que `fase_d_schedule.py`).
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fase_d_common import make_announce, open_cue_log  # noqa: E402

COUNTDOWN_SECONDS = 10
BLOCK_SECONDS = 30

# (nombre_corto, gesto_objetivo_o_None, instrucción exacta para JD)
CASES = [
    (
        "movimiento_rapido",
        None,
        "CASO 1/5 -- movimiento rápido: cambia entre puño_cerrado, palma_abierta, "
        "dedo_pulgar y dedo_menique SIN sostener ninguno, lo más rápido que puedas.",
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
        "CASO 4/5 -- dos manos en cuadro: pon las DOS manos en cuadro, intenta "
        "sostener dedo_pulgar con una de ellas.",
    ),
    (
        "persona_detras_sin_gesto",
        None,
        "CASO 5/5 -- persona detrás: saca tu mano COMPLETAMENTE del cuadro. Otra "
        "persona (no tú) pasa caminando detrás, SIN hacer ningún gesto, mientras "
        "tu mano sigue fuera de cuadro todo el bloque. Ten a esa persona lista "
        "ANTES de que arranque la cuenta regresiva de este caso.",
    ),
]


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    log_file, log_path = open_cue_log(here, "fase_d_cue_hard_cases")

    with log_file:
        announce = make_announce(log_file)

        print("=== Guion de Fase D -- casos difíciles (5 casos, orden fijo) ===")
        print(f"Guardando señales en: {log_path}")
        print(f"Cada caso: {COUNTDOWN_SECONDS}s de cuenta regresiva + {BLOCK_SECONDS}s de bloque medido.")
        print("Confirma que el bridge ya está corriendo antes de seguir.")

        for name, target, instruction in CASES:
            announce(f"PREPARA -- {instruction}")
            print(f"  (cuenta regresiva de {COUNTDOWN_SECONDS}s para este caso...)")
            time.sleep(COUNTDOWN_SECONDS)
            announce(f"ARRANCA caso '{name}' (objetivo={target}) -- sostener {BLOCK_SECONDS}s")
            time.sleep(BLOCK_SECONDS)
            announce(f"TERMINA caso '{name}'")

        announce("FIN del guion de casos difíciles -- 5/5 completos")


if __name__ == "__main__":
    main()
