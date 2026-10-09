"""Fase D -- analiza un log real del bridge de prueba contra las reglas definidas
en el plan (BITACORA.md "Fase D"): tiempo de sistema, % de aciertos sobre
repeticiones NO contaminadas, repeticiones contaminadas (sin "Gesto liberado"
inmediatamente antes, salvo la primera de toda la batería), líneas por
repetición, tiempo de liberación.

Tiempo de sistema -- corregido (revisión externa, 2026-10): la primera versión
medía la racha CONTINUA de `[diag] Gesto candidato` con el mismo gesto hasta la
confirmación. Error real: `GestureStabilizer` confirma con 5 coincidencias de 7
y TOLERA frames intercalados (no exige racha exacta, ver su propio docstring en
detector.py) -- una racha continua subestima el tiempo real siempre que hubo
ruido intercalado antes de la coincidencia más antigua de la ventana real. Esto
se confirmó con un caso imposible en los datos: una repetición midió 85ms, pero
5 frames a ~7fps (≈143ms entre frames) necesitan como mínimo ~572ms entre el
primero y el último, así que 85ms no podía ser real.

Ahora se REPRODUCE el algoritmo real (se importa `GestureStabilizer` tal cual,
no se reimplementa su lógica aparte) sobre la secuencia cruda completa de
observaciones (`[diag] Gesto candidato`/`[diag] Sin gesto reconocido`, en orden,
incluyendo `None`) -- al momento exacto en que el stabilizer real confirma, se
busca la coincidencia MÁS ANTIGUA dentro de los últimos `window_size` (7)
observaciones evaluadas (la ventana real que el propio algoritmo usó), no la
racha continua más reciente.

No se importa desde el paquete. Uso:
    .venv-fase-c2-fix/bin/python benchmarks/analyze_fase_d_run.py <log_file> [cue_log_file]

`cue_log_file` es opcional -- el archivo que guarda `fase_d_schedule.py` (desde la
corrección de 2026-10, antes no guardaba nada en disco, ver BITACORA.md "Fase D").
Cuando está presente, el tiempo de liberación se calcula desde la señal QUITA real
del guion (no desde la propia detección, que es lo único que se podía medir antes
sin esa señal) -- emparejado por orden (n-ésima liberación real <-> n-ésima señal
QUITA), avisando explícitamente si los conteos no coinciden en vez de alinear a
ciegas.
"""

import os
import re
import sys
from collections import deque
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)

from cva_gesture_bridge.vision.detector import GestureStabilizer  # noqa: E402

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")
DIAG_CANDIDATE_RE = re.compile(r"\[diag\] Gesto candidato: (\S+) \(confianza=([\d.]+)")
DIAG_NONE_RE = re.compile(r"\[diag\] Sin gesto reconocido")
DETECTADO_RE = re.compile(r"Gesto detectado: (\S+) \(confianza=([\d.]+)\)")
LIBERADO_RE = re.compile(r"Gesto liberado \(antes: (\S+)\)")
CUE_RE = re.compile(r"CUE: \[(\d+)/\d+\] (HAZ|QUITA)(?:: (\S+))?")


def _parse_ts(line):
    m = TS_RE.match(line)
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S,%f") if m else None


def _parse_cue_log(path):
    cues = []  # (ts, rep_num, kind, gesture_o_None)
    with open(path, encoding="utf-8") as f:
        for line in f:
            ts = _parse_ts(line)
            if ts is None:
                continue
            m = CUE_RE.search(line)
            if m:
                cues.append((ts, int(m.group(1)), m.group(2), m.group(3)))
    return cues


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "benchmarks/fase_d_run_sin_condicion.log"
    cue_path = sys.argv[2] if len(sys.argv) > 2 else None
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

    # --- 3) Tiempo de sistema por repetición: se REPRODUCE el GestureStabilizer
    # real sobre la secuencia cruda completa (diag_candidato/diag_none, en orden)
    # -- al momento en que confirma de verdad, se busca la coincidencia MÁS
    # ANTIGUA dentro de los últimos window_size observaciones (la ventana real
    # que usó el algoritmo), no una racha continua. ---
    raw_observations = [
        (ts, data[0] if kind == "diag_candidato" else None)
        for ts, kind, data in events
        if kind in ("diag_candidato", "diag_none")
    ]

    stabilizer = GestureStabilizer()  # window_size=7, min_matches=5 (defaults reales)
    # Se lee el atributo "privado" a propósito -- así esta ventana de análisis
    # queda sincronizada con el tamaño real del stabilizer aunque cambie el
    # default en el futuro, en vez de duplicar el número 7 a mano aparte.
    window = deque(maxlen=stabilizer._window_size)
    confirm_events = []  # (confirm_ts, gesture, t_sistema_ms) en cada CAMBIO de confirmado
    prev_confirmed = None
    for ts, gesture in raw_observations:
        window.append((ts, gesture))
        confirmed = stabilizer.observe(gesture)
        if confirmed is not None and confirmed != prev_confirmed:
            matching_ts = [t for (t, g) in window if g == confirmed]
            earliest_ts = min(matching_ts)  # siempre hay al menos 1 (la que acaba de confirmar)
            t_sistema_ms = (ts - earliest_ts).total_seconds() * 1000
            confirm_events.append((ts, confirmed, t_sistema_ms))
        prev_confirmed = confirmed

    print(f"Confirmaciones reproducidas con el GestureStabilizer real: {len(confirm_events)}")
    if len(confirm_events) != len(reps):
        print(
            f"AVISO: {len(confirm_events)} confirmaciones reproducidas vs {len(reps)} "
            f"líneas 'Gesto detectado' reales -- no coinciden en cantidad, revisar antes "
            f"de confiar en el emparejamiento por orden de abajo."
        )

    print("\n=== Detalle por repetición ===")
    clean_system_times = []

    for r, (confirm_ts, confirmed_gesture, t_sistema_ms) in zip(reps, confirm_events):
        gesture = r["gesture"]
        # Chequeo de consistencia: el gesto reproducido debe coincidir con el de
        # la línea real "Gesto detectado" -- si no, el emparejamiento por orden
        # se rompió en algún punto anterior.
        mismatch = " **DESAJUSTE vs log real**" if confirmed_gesture != gesture else ""

        if not r["contaminated"]:
            clean_system_times.append(t_sistema_ms)

        marca = "CONTAMINADA" if r["contaminated"] else ""
        print(
            f"  [{r['detect_idx']:5d}] {r['detect_ts'].strftime('%H:%M:%S,%f')[:-3]} "
            f"{gesture:14s} conf={r['confidence']:.2f} "
            f"t_sistema={t_sistema_ms:7.0f}ms "
            f"{marca}{mismatch}"
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

    # --- 4) Si hay CUE log: tiempo de liberación real (desde la señal QUITA, no
    # desde la propia detección) + tiempo total desde la señal HAZ hasta la
    # detección -- pedido en la revisión del plan, 2026-10. Emparejado por orden,
    # avisando si los conteos no coinciden en vez de alinear a ciegas. ---
    if cue_path:
        cues = _parse_cue_log(cue_path)
        haz_cues = [c for c in cues if c[2] == "HAZ"]
        quita_cues = [c for c in cues if c[2] == "QUITA"]
        print(f"\n=== CUE log: {cue_path} ({len(haz_cues)} HAZ, {len(quita_cues)} QUITA) ===")

        releases_only = [(ts, data) for ts, kind, data in events if kind == "liberado"]
        if len(releases_only) == len(quita_cues):
            release_from_cue = [
                (r_ts - q_ts).total_seconds() * 1000
                for (r_ts, _), (q_ts, _, _, _) in zip(releases_only, quita_cues)
            ]
            n2 = len(release_from_cue)
            print(
                f"Tiempo de liberación desde la señal QUITA real -- n={n2} "
                f"avg={sum(release_from_cue)/n2:.0f}ms min={min(release_from_cue):.0f}ms "
                f"max={max(release_from_cue):.0f}ms"
            )
        else:
            print(
                f"AVISO: {len(releases_only)} liberaciones reales vs {len(quita_cues)} "
                f"señales QUITA -- no coinciden, no se calcula (revisar repeticiones "
                f"contaminadas o perdidas antes de confiar en un emparejamiento por orden)"
            )

        detections_only = [(r["detect_ts"], r["gesture"]) for r in reps if not r["contaminated"]]
        if len(detections_only) == len(haz_cues):
            total_from_cue = [
                (d_ts - h_ts).total_seconds() * 1000
                for (d_ts, _), (h_ts, _, _, _) in zip(detections_only, haz_cues)
            ]
            n3 = len(total_from_cue)
            print(
                f"Tiempo TOTAL desde la señal HAZ (incluye reacción humana, NO es "
                f"tiempo de sistema, no se evalúa contra AC3) -- n={n3} "
                f"avg={sum(total_from_cue)/n3:.0f}ms min={min(total_from_cue):.0f}ms "
                f"max={max(total_from_cue):.0f}ms"
            )
        else:
            print(
                f"AVISO: {len(detections_only)} detecciones no contaminadas vs "
                f"{len(haz_cues)} señales HAZ -- no coinciden, no se calcula"
            )


if __name__ == "__main__":
    main()
