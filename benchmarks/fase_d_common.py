"""Fase D -- utilidades compartidas entre los guiones de medición
(`fase_d_schedule.py`, `fase_d_hard_cases.py`). No se importa desde el paquete.

Reloj de la Pi + mismo formato de timestamp que `logging_setup.py`, para poder
cruzar las señales de estos guiones contra el log real del bridge sin depender
de sincronizar relojes entre máquinas (ver docstring de `fase_d_schedule.py`).
"""

import os
from datetime import datetime

TS_FORMAT = "%Y-%m-%d %H:%M:%S,%f"


def now_str() -> str:
    return datetime.now().strftime(TS_FORMAT)[:-3]


def make_announce(log_file):
    """Devuelve una función que imprime a pantalla (con pitido) Y guarda en
    `log_file` la misma línea con timestamp -- un solo punto de verdad para las
    dos formas en que se consume la señal (JD la ve/oye en vivo, el análisis
    posterior la lee del archivo)."""

    def _announce(msg: str) -> None:
        line = f"{now_str()} CUE: {msg}"
        print(f"\a{line}", flush=True)  # \a: pitido audible en cada señal
        log_file.write(line + "\n")
        log_file.flush()

    return _announce


def open_cue_log(here: str, name: str):
    """Abre (modo "x", falla explícito si ya existe -- nunca sobreescribe ni
    mezcla corridas) el archivo de CUE para esta sesión, con fecha y hora de
    arranque en el nombre para que corridas repetidas de la misma condición o
    el mismo guion no choquen entre sí."""
    start_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(here, f"{name}_{start_stamp}.log")
    return open(log_path, "x", encoding="utf-8"), log_path
