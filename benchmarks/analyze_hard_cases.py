"""Fase D, sesión 2 -- analiza los 5 casos difíciles (`fase_d_hard_cases.py`)
contra el log real del bridge, con los criterios de aceptación definidos ANTES
de medir (ver BITACORA.md "Fase D, sesión 2"):

- Casos 2, 3, 4 (mano tapada, mano en el borde, dos manos): aceptable = no
  confirmar nada o confirmar el gesto objetivo; NO aceptable = confirmar un
  gesto distinto al objetivo.
- Caso 1 (movimiento rápido): cualquier gesto del catálogo es aceptable (JD
  los cambia a propósito) -- el criterio real ("no más de un envío por cambio
  real, nada fuera de la ventana") NO es verificable automáticamente sin saber
  cada cambio real de gesto (no se grabó esa señal intermedia) -- se reporta
  lo observado para juicio humano, no se fuerza un veredicto.
- Caso 5 (persona detrás): CUALQUIER línea `gesto: X` dentro de la ventana es
  NO aceptable.
- Caso 4: reporta cuál de las dos manos eligió el detector SOLO si el log lo
  permite -- no lo permite (MediaPipe con num_hands=1 no distingue cuál mano
  física eligió), así que se dice explícitamente que no es verificable.

Reutiliza el descubrimiento de rotación y el parseo de eventos de
`analyze_fase_d_run.py` -- no reimplementa esa lógica aparte. No se importa
desde el paquete.

Uso:
    .venv-fase-c2-fix/bin/python benchmarks/analyze_hard_cases.py <log_file> <hard_cases_cue_log>
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_fase_d_run import (  # noqa: E402
    build_events,
    discover_rotated_logs,
    parse_ts,
    read_lines_with_rotation,
)

ARRANCA_RE = re.compile(r"CUE: ARRANCA caso '(\w+)' \(objetivo=(\S+)\)")
TERMINA_RE = re.compile(r"CUE: TERMINA caso '(\w+)'")

NO_VERIFICABLE_AUTOMATICO = "movimiento_rapido"
CUALQUIER_ENVIO_NO_ACEPTABLE = "persona_detras_sin_gesto"


def parse_hard_case_cue_log(path):
    """Devuelve una lista de (nombre, objetivo_o_None, inicio_ts, fin_ts), en
    el orden en que aparecen en el log (el mismo orden fijo del guion)."""
    cases = []
    pending_start = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            ts = parse_ts(line)
            if ts is None:
                continue
            m = ARRANCA_RE.search(line)
            if m:
                name, target = m.group(1), m.group(2)
                pending_start[name] = (ts, None if target == "None" else target)
                continue
            m = TERMINA_RE.search(line)
            if m:
                name = m.group(1)
                if name in pending_start:
                    start_ts, target = pending_start.pop(name)
                    cases.append((name, target, start_ts, ts))
    return cases


def detections_in_window(events, start_ts, end_ts):
    """Líneas 'Gesto detectado' dentro de [start_ts, end_ts] -- proxy de "línea
    gesto: X enviada" (mismo proxy y misma salvedad ya documentados en Fase D:
    el log no distingue si format_line() de verdad mandó la línea cuando la
    confianza queda justo por debajo de MIN_CONFIDENCE)."""
    return [
        (ts, data[0], data[1])
        for ts, kind, data in events
        if kind == "detectado" and start_ts <= ts <= end_ts
    ]


def evaluate_case(name, target, dets):
    gestures_sent = [g for _, g, _ in dets]

    if name == CUALQUIER_ENVIO_NO_ACEPTABLE:
        ok = len(dets) == 0
        detail = (
            "ninguna línea enviada -- correcto" if ok
            else f"NO ACEPTABLE: {len(dets)} línea(s) enviada(s) ({gestures_sent}) -- "
                 f"cualquier envío en este caso es no aceptable"
        )
        return ok, detail

    if name == NO_VERIFICABLE_AUTOMATICO:
        return None, (
            f"{len(dets)} envío(s): {gestures_sent} -- criterio real (no más de un "
            f"envío por cambio real de gesto, nada fuera de la ventana) NO verificable "
            f"automáticamente: no se grabó cuándo JD cambiaba de gesto dentro del "
            f"bloque. Reportado para juicio humano, no se fuerza un veredicto."
        )

    wrong = [g for g in gestures_sent if g != target]
    ok = len(wrong) == 0
    if ok:
        detail = f"{len(dets)} envío(s): {gestures_sent} -- aceptable (nada, o el objetivo {target})"
    else:
        detail = f"{len(dets)} envío(s): {gestures_sent} -- NO ACEPTABLE: se envió {wrong} (distinto al objetivo {target})"
    return ok, detail


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log_path")
    parser.add_argument("cue_path")
    args = parser.parse_args()

    cases = parse_hard_case_cue_log(args.cue_path)
    files_used = discover_rotated_logs(args.log_path) or [args.log_path]
    lines = read_lines_with_rotation(args.log_path)
    events = build_events(lines)

    print(f"Archivo(s) de log: {', '.join(files_used)}")
    print(f"CUE de casos difíciles: {args.cue_path} ({len(cases)} casos encontrados)")
    print()

    for name, target, start_ts, end_ts in cases:
        dets = detections_in_window(events, start_ts, end_ts)
        ok, detail = evaluate_case(name, target, dets)
        veredicto = "N/A (no verificable)" if ok is None else ("OK" if ok else "FALLA")
        print(f"=== {name} (objetivo={target}) -- {veredicto} ===")
        print(f"  ventana: {start_ts.strftime('%H:%M:%S,%f')[:-3]} -- {end_ts.strftime('%H:%M:%S,%f')[:-3]}")
        print(f"  {detail}")
        if name == "dos_manos_dedo_pulgar":
            print(
                "  Cuál de las dos manos eligió el detector: NO verificable -- "
                "MediaPipe corre con num_hands=1 y no distingue ni reporta cuál de "
                "las dos manos físicas presentes fue la que procesó."
            )
        print()


if __name__ == "__main__":
    main()
