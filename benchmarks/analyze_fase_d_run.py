"""Fase D -- analiza un log real del bridge de prueba contra las reglas definidas
en el plan (BITACORA.md "Fase D"): tiempo de sistema (racha continua de [diag]
Gesto candidato hasta la confirmación), % de aciertos sobre repeticiones NO
contaminadas, repeticiones contaminadas (sin "Gesto liberado" inmediatamente
antes, salvo la primera de toda la batería), líneas por repetición, tiempo de
liberación.

No se importa desde el paquete. Uso:
    .venv-fase-c2-fix/bin/python benchmarks/analyze_fase_d_run.py <log_file>
"""

import re
import sys
from datetime import datetime

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")
DIAG_CANDIDATE_RE = re.compile(r"\[diag\] Gesto candidato: (\S+) \(confianza=([\d.]+)")
DIAG_NONE_RE = re.compile(r"\[diag\] Sin gesto reconocido")
DETECTADO_RE = re.compile(r"Gesto detectado: (\S+) \(confianza=([\d.]+)\)")
LIBERADO_RE = re.compile(r"Gesto liberado \(antes: (\S+)\)")


def _parse_ts(line):
    m = TS_RE.match(line)
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S,%f") if m else None


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "benchmarks/fase_d_run_sin_condicion.log"
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    # --- 1) Reconstruir eventos: diag (candidato o ninguno), detectado, liberado ---
    events = []  # (timestamp, kind, data)
    for line in lines:
        ts = _parse_ts(line)
        if ts is None:
            continue
        m = DETECTADO_RE.search(line)
        if m:
            events.append((ts, "detectado", (m.group(1), float(m.group(2)))))
            continue
        m = LIBERADO_RE.search(line)
        if m:
            events.append((ts, "liberado", m.group(1)))
            continue
        m = DIAG_CANDIDATE_RE.search(line)
        if m:
            events.append((ts, "diag_candidato", (m.group(1), float(m.group(2)))))
            continue
        if DIAG_NONE_RE.search(line):
            events.append((ts, "diag_none", None))

    # --- 2) Agrupar en "repeticiones": cada Gesto detectado es el final de una
    # repeticion; el Gesto liberado que la precede (o el arranque del log, solo
    # para la primera) marca el comienzo de la ventana de busqueda hacia atras
    # para el tiempo de sistema. ---
    detections = [(i, e) for i, e in enumerate(events) if e[1] == "detectado"]
    releases = [(i, e) for i, e in enumerate(events) if e[1] == "liberado"]

    print(f"Archivo: {path}")
    print(f"Eventos: {len(events)} totales -- {len(detections)} 'Gesto detectado', {len(releases)} 'Gesto liberado'")
    print()

    reps = []
    last_release_idx = None
    first_detection_seen = False
    for idx, (ts, kind, data) in enumerate(events):
        if kind == "liberado":
            last_release_idx = idx
        elif kind == "detectado":
            gesture, confidence = data
            contaminated = (last_release_idx is None) and first_detection_seen
            # La primera deteccion de todo el archivo NO cuenta como contaminada
            # aunque no tenga liberacion previa (regla del plan).
            reps.append({
                "detect_idx": idx,
                "detect_ts": ts,
                "gesture": gesture,
                "confidence": confidence,
                "since_release_idx": last_release_idx,
                "contaminated": contaminated,
            })
            first_detection_seen = True
            last_release_idx = None  # se consume -- la proxima deteccion necesita su propia liberacion

    n_contaminated = sum(1 for r in reps if r["contaminated"])
    print(f"Repeticiones (= 'Gesto detectado'): {len(reps)}")
    print(f"Contaminadas (sin liberacion previa, salvo la primera): {n_contaminated}")
    print()

    # --- 3) Tiempo de sistema por repeticion: racha continua de diag_candidato
    # con el MISMO gesto, terminando justo antes del evento "detectado". ---
    print("=== Detalle por repetición ===")
    clean_system_times = []
    wrong_gesture_sent_before = {}  # detect_idx -> bool

    for r in reps:
        # Buscar hacia atras desde justo antes de detect_idx la racha continua de
        # diag_candidato con el gesto correcto.
        gesture = r["gesture"]
        i = r["detect_idx"] - 1
        streak_start_ts = None
        wrong_sent = False
        while i >= 0:
            ts_i, kind_i, data_i = events[i]
            if kind_i == "detectado" or kind_i == "liberado":
                break  # tope: el evento anterior de la repeticion previa
            if kind_i == "diag_candidato" and data_i[0] == gesture:
                streak_start_ts = ts_i
                i -= 1
                continue
            # cualquier otra cosa (diag_none, diag_candidato de otro gesto) corta
            # la racha continua -- pero seguimos retrocediendo por si hay otra
            # racha mas atras (no la usamos para el tiempo de sistema, solo
            # confirmamos que no se mando un gesto incorrecto antes).
            break
        if streak_start_ts is not None:
            system_ms = (r["detect_ts"] - streak_start_ts).total_seconds() * 1000
        else:
            system_ms = None

        if not r["contaminated"] and system_ms is not None:
            clean_system_times.append(system_ms)

        marca = "CONTAMINADA" if r["contaminated"] else ""
        print(
            f"  [{r['detect_idx']:5d}] {r['detect_ts'].strftime('%H:%M:%S,%f')[:-3]} "
            f"{gesture:14s} conf={r['confidence']:.2f} "
            f"t_sistema={'%.0fms' % system_ms if system_ms is not None else 'n/d':>8s} "
            f"{marca}"
        )

    print()
    print("=== Resumen ===")
    n = len(clean_system_times)
    print(f"Repeticiones NO contaminadas con tiempo de sistema calculable: {n}")
    if n:
        clean_system_times.sort()
        avg = sum(clean_system_times) / n
        p50 = clean_system_times[n // 2]
        p95 = clean_system_times[int(0.95 * n) - 1] if n >= 5 else max(clean_system_times)
        print(f"Tiempo de sistema -- avg={avg:.0f}ms p50={p50:.0f}ms p95={p95:.0f}ms max={max(clean_system_times):.0f}ms min={min(clean_system_times):.0f}ms")
        over = sum(1 for t in clean_system_times if t > 1500)
        print(f"Repeticiones que superan AC3 (<1500ms): {over}/{n}")

    # Tiempo de liberacion: delta entre cada 'detectado' y su 'liberado' siguiente.
    release_times = []
    for idx, (ts, kind, data) in enumerate(events):
        if kind != "detectado":
            continue
        for j in range(idx + 1, len(events)):
            ts2, kind2, data2 = events[j]
            if kind2 == "detectado":
                break
            if kind2 == "liberado":
                release_times.append((ts2 - ts).total_seconds() * 1000)
                break
    if release_times:
        print(f"\nTiempo detectado->liberado -- n={len(release_times)} avg={sum(release_times)/len(release_times):.0f}ms min={min(release_times):.0f}ms max={max(release_times):.0f}ms")

    # Conteo por gesto
    from collections import Counter
    gcount = Counter(r["gesture"] for r in reps if not r["contaminated"])
    print(f"\nRepeticiones NO contaminadas por gesto: {dict(gcount)}")


if __name__ == "__main__":
    main()
