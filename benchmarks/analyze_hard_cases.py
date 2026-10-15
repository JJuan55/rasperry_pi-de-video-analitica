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
  NO aceptable. Tampoco es aceptable un gesto que ya estaba ACTIVO al empezar el
  bloque (confirmado antes y no liberado todavía): para el cliente, esa
  instrucción sigue vigente dentro del bloque aunque no se reenvíe (corrección
  de la revisión, ver BITACORA.md "Fase D, sesión 2 -- correcciones de
  herramientas").

En todos los casos se reporta además (sin cambiar el veredicto de los casos 1-4):
- "gesto activo al empezar el bloque": estado enviado al cliente en el instante
  ARRANCA = última línea `Gesto detectado`/`Gesto liberado` ANTERIOR al inicio
  (main.py manda `Gesto detectado: B` al cambiar de A a B sin liberar en medio,
  y `Gesto liberado` solo al soltar). Se calcula sobre el log COMPLETO
  disponible, no solo sobre --desde/--hasta, para no perder un gesto confirmado
  antes de la ventana.
- "liberación dentro del bloque": líneas `Gesto liberado` dentro de la ventana.
- Caso 4: reporta cuál de las dos manos eligió el detector SOLO si el log lo
  permite -- no lo permite (MediaPipe con num_hands=1 no distingue cuál mano
  física eligió), así que se dice explícitamente que no es verificable.

Repetición guiada (después de la primera corrida, ver BITACORA.md "Fase D,
sesión 2 -- repetición guiada"): si el CUE trae señales `CAMBIA [i/n] a: <gesto>`
dentro del caso 1, el caso 1 SÍ se evalúa, con un criterio definido ANTES de
medir: cada envío se asigna a la señal CAMBIA más reciente del MISMO gesto,
anterior al envío y a no más de MATCH_TOLERANCE_SECONDS. OK = ningún envío sin
asignar (gesto no pedido o fuera de tolerancia) y ninguna señal con más de un
envío. Las señales sin envío (cambio no confirmado) se cuentan y se reportan
pero no hacen FALLA: el criterio fijado es "no más de un envío por cambio
real, nada fuera de la ventana", no "detectar todos". Sin señales CAMBIA (CUE
viejo), el caso 1 sigue como no verificable. Las señales `METE` (caso 4) y
`PASA` (caso 5) solo se reportan con su hora; no cambian el veredicto.

Reutiliza el descubrimiento de rotación y el parseo de eventos de
`analyze_fase_d_run.py` -- no reimplementa esa lógica aparte. No se importa
desde el paquete.

Uso:
    .venv-fase-c2-fix/bin/python benchmarks/analyze_hard_cases.py <log_file> <hard_cases_cue_log>
        [--desde "YYYY-MM-DD HH:MM:SS"] [--hasta "YYYY-MM-DD HH:MM:SS"]

--desde/--hasta limitan qué detecciones/liberaciones se consideran (además de
la ventana de cada caso); el estado "activo al empezar" siempre se calcula con
todo lo anterior disponible en el log.
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
    parse_window_ts,
    read_lines_with_rotation,
)

CAMBIA_RE = re.compile(r"CUE: CAMBIA \[(\d+)/(\d+)\] a: (\S+)")
INNER_RE = re.compile(r"CUE: (METE|PASA)\b(.*)$")
ARRANCA_RE = re.compile(r"CUE: ARRANCA caso '(\w+)' \(objetivo=(\S+)\)")
TERMINA_RE = re.compile(r"CUE: TERMINA caso '(\w+)'")

NO_VERIFICABLE_AUTOMATICO = "movimiento_rapido"

# Tolerancia del caso 1 guiado: 2 x el intervalo entre señales CAMBIA del guion.
from fase_d_hard_cases import CHANGE_SECONDS  # noqa: E402

MATCH_TOLERANCE_SECONDS = 2 * CHANGE_SECONDS
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


def parse_inner_cues(path):
    """Señales DENTRO de los bloques: (ts, "CAMBIA", gesto) y (ts, "METE"|"PASA",
    texto), en orden de archivo."""
    inner = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            ts = parse_ts(line)
            if ts is None:
                continue
            m = CAMBIA_RE.search(line)
            if m:
                inner.append((ts, "CAMBIA", m.group(3)))
                continue
            m = INNER_RE.search(line)
            if m:
                inner.append((ts, m.group(1), m.group(2).strip()))
    return inner


def evaluate_guided_changes(changes, dets, tolerance_s=None):
    """Caso 1 guiado. `changes`: [(ts, gesto_pedido)] en orden; `dets`:
    [(ts, gesto_enviado, confianza)]. Función pura.

    Cada envío se asigna a la señal CAMBIA más reciente del MISMO gesto,
    anterior o igual al envío, y a no más de `tolerance_s`. Devuelve
    (ok, resumen) con resumen = dict(asignados=[(i_cambio, ms)],
    sin_asignar=[(ts, gesto)], duplicados=[(i_cambio, ts, gesto)],
    sin_envio=[i_cambio]). Índices de cambio desde 1."""
    if tolerance_s is None:
        tolerance_s = MATCH_TOLERANCE_SECONDS
    matched = {}
    unmatched = []
    duplicates = []
    for ts, gesture, _ in sorted(dets, key=lambda d: d[0]):
        candidate = None
        for i in range(len(changes) - 1, -1, -1):
            c_ts, c_gesture = changes[i]
            if c_ts <= ts and c_gesture == gesture:
                candidate = i
                break
        if candidate is None or (ts - changes[candidate][0]).total_seconds() > tolerance_s:
            unmatched.append((ts, gesture))
        elif candidate in matched:
            duplicates.append((candidate + 1, ts, gesture))
        else:
            matched[candidate] = (ts - changes[candidate][0]).total_seconds() * 1000
    summary = {
        "asignados": sorted((i + 1, ms) for i, ms in matched.items()),
        "sin_asignar": unmatched,
        "duplicados": duplicates,
        "sin_envio": [i + 1 for i in range(len(changes)) if i not in matched],
    }
    ok = not unmatched and not duplicates
    return ok, summary


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


def active_gesture_at(events, ts):
    """Gesto que el cliente tiene como vigente en el instante `ts`: el de la
    última línea `Gesto detectado` anterior a `ts`, salvo que después haya un
    `Gesto liberado` (entonces None). Usa solo eventos estrictamente anteriores
    a `ts`. Mismo proxy de envío que detections_in_window."""
    active = None
    for ev_ts, kind, data in events:
        if ev_ts >= ts:
            break
        if kind == "detectado":
            active = data[0]
        elif kind == "liberado":
            active = None
    return active


def releases_in_window(events, start_ts, end_ts):
    """Líneas 'Gesto liberado' dentro de [start_ts, end_ts]: (ts, gesto_liberado)."""
    return [
        (ts, data)
        for ts, kind, data in events
        if kind == "liberado" and start_ts <= ts <= end_ts
    ]


def evaluate_case(name, target, dets, active_at_start=None):
    gestures_sent = [g for _, g, _ in dets]

    if name == CUALQUIER_ENVIO_NO_ACEPTABLE:
        problems = []
        if active_at_start is not None:
            problems.append(
                f"gesto '{active_at_start}' ya ACTIVO al empezar el bloque (cruza el "
                f"inicio sin liberarse antes)"
            )
        if dets:
            problems.append(f"{len(dets)} línea(s) enviada(s) ({gestures_sent})")
        ok = not problems
        detail = (
            "ningún gesto activo al empezar y ninguna línea enviada -- correcto" if ok
            else "NO ACEPTABLE: " + "; ".join(problems) + " -- en este caso cualquier "
                 "gesto vigente es no aceptable"
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
    parser.add_argument("--desde", default=None, help="Timestamp inicial ('YYYY-MM-DD HH:MM:SS[,mmm]'), inclusive.")
    parser.add_argument("--hasta", default=None, help="Timestamp final ('YYYY-MM-DD HH:MM:SS[,mmm]'), inclusive.")
    args = parser.parse_args()

    desde = parse_window_ts(args.desde) if args.desde else None
    hasta = parse_window_ts(args.hasta) if args.hasta else None

    cases = parse_hard_case_cue_log(args.cue_path)
    inner_cues = parse_inner_cues(args.cue_path)
    files_used = discover_rotated_logs(args.log_path) or [args.log_path]
    all_events = build_events(read_lines_with_rotation(args.log_path))
    window_events = [
        e for e in all_events
        if (desde is None or e[0] >= desde) and (hasta is None or e[0] <= hasta)
    ]

    print(f"Archivo(s) de log: {', '.join(files_used)}")
    if desde or hasta:
        print(f"Ventana: desde={desde or '(sin límite)'} hasta={hasta or '(sin límite)'}")
    print(f"CUE de casos difíciles: {args.cue_path} ({len(cases)} casos encontrados)")
    print()

    for name, target, start_ts, end_ts in cases:
        dets = detections_in_window(window_events, start_ts, end_ts)
        rels = releases_in_window(window_events, start_ts, end_ts)
        active = active_gesture_at(all_events, start_ts)
        ok, detail = evaluate_case(name, target, dets, active_at_start=active)
        inner_here = [c for c in inner_cues if start_ts <= c[0] <= end_ts]
        changes = [(ts, g) for ts, kind, g in inner_here if kind == "CAMBIA"]
        guided_summary = None
        if name == NO_VERIFICABLE_AUTOMATICO and changes:
            ok, guided_summary = evaluate_guided_changes(changes, dets)
            detail = (
                f"{len(dets)} envío(s) para {len(changes)} cambios pedidos -- "
                + ("aceptable: ningún envío fuera de lo pedido y ninguno repetido por cambio" if ok
                   else "NO ACEPTABLE: hay envíos sin asignar a un cambio pedido o repetidos")
            )
        veredicto = "N/A (no verificable)" if ok is None else ("OK" if ok else "FALLA")
        print(f"=== {name} (objetivo={target}) -- {veredicto} ===")
        print(f"  ventana: {start_ts.strftime('%H:%M:%S,%f')[:-3]} -- {end_ts.strftime('%H:%M:%S,%f')[:-3]}")
        print(f"  gesto activo al empezar el bloque: {active or 'ninguno'}")
        for ts, kind, txt in inner_here:
            if kind != "CAMBIA":
                print(f"  señal    {ts.strftime('%H:%M:%S,%f')[:-3]}  {kind} {txt}")
        for ts, g, conf in dets:
            print(f"  enviado {ts.strftime('%H:%M:%S,%f')[:-3]}  Gesto detectado: {g} (confianza={conf:.2f})")
        for ts, g in rels:
            print(f"  liberado {ts.strftime('%H:%M:%S,%f')[:-3]}  Gesto liberado (antes: {g})")
        print(f"  liberaciones dentro del bloque: {len(rels)}")
        print(f"  {detail}")
        if guided_summary is not None:
            print(f"  tolerancia de asignación: {MATCH_TOLERANCE_SECONDS:.1f}s desde la señal CAMBIA del mismo gesto")
            for i, ms in guided_summary["asignados"]:
                c_ts, c_g = changes[i - 1]
                print(f"    cambio [{i:2d}] {c_ts.strftime('%H:%M:%S,%f')[:-3]} {c_g:14s} -> enviado a los {ms:5.0f}ms (incluye reacción humana)")
            for i in guided_summary["sin_envio"]:
                c_ts, c_g = changes[i - 1]
                print(f"    cambio [{i:2d}] {c_ts.strftime('%H:%M:%S,%f')[:-3]} {c_g:14s} -> SIN envío")
            for ts, g in guided_summary["sin_asignar"]:
                print(f"    envío SIN ASIGNAR {ts.strftime('%H:%M:%S,%f')[:-3]} {g}")
            for i, ts, g in guided_summary["duplicados"]:
                print(f"    envío REPETIDO para el cambio [{i}] {ts.strftime('%H:%M:%S,%f')[:-3]} {g}")
            print(
                f"  resumen: {len(guided_summary['asignados'])}/{len(changes)} cambios con envío, "
                f"{len(guided_summary['sin_envio'])} sin envío, "
                f"{len(guided_summary['sin_asignar'])} envío(s) sin asignar, "
                f"{len(guided_summary['duplicados'])} repetido(s)"
            )
        if name == "dos_manos_dedo_pulgar":
            print(
                "  Cuál de las dos manos eligió el detector: NO verificable -- "
                "MediaPipe corre con num_hands=1 y no distingue ni reporta cuál de "
                "las dos manos físicas presentes fue la que procesó."
            )
        print()


if __name__ == "__main__":
    main()
