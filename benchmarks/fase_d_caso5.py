"""Fase D, sesión 2 -- repetición SOLO del caso 5 (persona detrás), pendiente
desde el cierre de la sesión (ver BITACORA.md "Fase D, sesión 2 -- reporte de
cierre"). Atajo sin argumentos sobre `fase_d_hard_cases.py`: mismo guion,
mismas señales (PREPARA, ARRANCA, PASA [1/2] a los 5s, PASA [2/2] a los 18s,
TERMINA) y mismo archivo de CUE (`fase_d_cue_hard_cases_<fecha_hora>.log`),
así que `analyze_hard_cases.py` lo analiza sin cambios.

Uso: `.venv-fase-c2-fix/bin/python benchmarks/fase_d_caso5.py`
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fase_d_hard_cases  # noqa: E402

ARGS = ["--casos", "persona_detras_sin_gesto"]

if __name__ == "__main__":
    fase_d_hard_cases.main(ARGS)
