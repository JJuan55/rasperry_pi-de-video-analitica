"""Fase D -- analiza un log real del bridge (de prueba o de producción, de solo
lectura) contra las reglas definidas en el plan (BITACORA.md "Fase D"): tiempo
de sistema, % de aciertos sobre repeticiones NO contaminadas, repeticiones
contaminadas (sin "Gesto liberado" inmediatamente antes, salvo la primera de
toda la batería), líneas por repetición, tiempo de liberación.

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
racha continua más reciente. El mismo mecanismo de reproducción, con
`hand_present` (ver abajo), se usa también para la liberación.

Ventana de tiempo y rotación de logs (Fase D, sesión 2): el log de producción
rota a medianoche (`TimedRotatingFileHandler`, ver logging_setup.py) -- un
archivo activo (`cva_gesture_bridge.log`) y archivos rotados con sufijo de
fecha (`cva_gesture_bridge.log.YYYY-MM-DD`). `--desde`/`--hasta` filtran por
ese rango ANTES de analizar; si el rango cruza medianoche, el programa
descubre y concatena automáticamente los archivos rotados vecinos (mismo
directorio, mismo nombre base) en orden cronológico real (por el primer
timestamp de cada archivo, no por el nombre a ciegas) -- nunca escribe nada,
solo lee.

Uso:
    .venv-fase-c2-fix/bin/python benchmarks/analyze_fase_d_run.py <log_file>
        [--cue CUE_LOG] [--desde "YYYY-MM-DD HH:MM:SS"] [--hasta "YYYY-MM-DD HH:MM:SS"]

`--cue` es opcional -- el archivo que guarda `fase_d_schedule.py`/
`fase_d_hard_cases.py`. Cuando está presente, el tiempo de liberación se
calcula desde la señal QUITA real del guion (no desde la propia detección, que
es lo único que se podía medir antes sin esa señal) -- emparejado por orden
(n-ésima liberación real <-> n-ésima señal QUITA), avisando explícitamente si
los conteos no coinciden en vez de alinear a ciegas.
"""

import argparse
import glob
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
DIAG_CANDIDATE_RE = re.compile(
    r"\[diag\] Gesto candidato: (\S+) \(confianza=([\d.]+), dedos_extendidos=(\d+)"
)
DIAG_NONE_RE = re.compile(r"\[diag\] Sin gesto reconocido en este frame \(dedos_extendidos=(\d+)\)")
DETECTADO_RE = re.compile(r"Gesto detectado: (\S+) \(confianza=([\d.]+)\)")
LIBERADO_RE = re.compile(r"Gesto liberado \(antes: (\S+)\)")
CUE_RE = re.compile(r"CUE: \[(\d+)/\d+\] (HAZ|QUITA)(?:: (\S+))?")

TS_INPUT_FORMATS = ("%Y-%m-%d %H:%M:%S,%f", "%Y-%m-%d %H:%M:%S")


def parse_ts(line):
    m = TS_RE.match(line)
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S,%f") if m else None


def parse_window_ts(s):
    """Parsea un timestamp de --desde/--hasta, con o sin milisegundos."""
    for fmt in TS_INPUT_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Timestamp inválido: {s!r} (formato esperado: 'YYYY-MM-DD HH:MM:SS[,mmm]')")


def discover_rotated_logs(path):
    """Encuentra los archivos rotados vecinos de `path` (mismo directorio,
    mismo nombre base -- `TimedRotatingFileHandler` los nombra
    `<base>` y `<base>.YYYY-MM-DD`) y los devuelve en orden CRONOLÓGICO REAL
    (por el primer timestamp de contenido de cada archivo, no por el nombre a
    ciegas) -- de solo lectura, nunca escribe ni borra nada."""
    candidates = sorted(glob.glob(glob.escape(path) + "*"))
    dated = []
    for candidate in candidates:
        try:
            with open(candidate, encoding="utf-8") as f:
                first_ts = None
                for line in f:
                    first_ts = parse_ts(line)
                    if first_ts is not None:
                        break
        except OSError:
            continue
        if first_ts is not None:
            dated.append((first_ts, candidate))
    dated.sort(key=lambda pair: pair[0])
    return [candidate for _, candidate in dated]


def read_lines_with_rotation(path, desde=None, hasta=None):
    """Lee `path` y, si hace falta (la ventana --desde/--hasta cruza un
    archivo rotado), sus vecinos -- concatenados en orden cronológico real,
    filtrados por la ventana si se dio. Si no se pasa ventana, igual concatena
    todos los archivos relacionados (para no perder nada si `path` apunta a un
    rotado viejo y se pidió analizar todo lo disponible)."""
    files = discover_rotated_logs(path)
    if not files:
        files = [path]

    lines = []
    for file_path in files:
        with open(file_path, encoding="utf-8") as f:
            for line in f:
                ts = parse_ts(line)
                if ts is None:
                    lines.append(line)  # continuación de traceback, etc. -- se conserva
                    continue
                if desde is not None and ts < desde:
                    continue
                if hasta is not None and ts > hasta:
                    continue
                lines.append(line)
    return lines


def parse_cue_log(path):
    cues = []  # (ts, rep_num, kind, gesture_o_None)
    with open(path, encoding="utf-8") as f:
        for line in f:
            ts = parse_ts(line)
            if ts is None:
                continue
            m = CUE_RE.search(line)
            if m:
                cues.append((ts, int(m.group(1)), m.group(2), m.group(3)))
    return cues


def build_events(lines):
    """De líneas de texto a eventos (timestamp, tipo, datos) -- incluye
    `dedos_extendidos` en diag_candidato/diag_none para poder reproducir
    `hand_present` tal cual lo calcula main.py
    (`not (gesture is None and extended_fingers == 0)`)."""
    events = []
    for line in lines:
        ts = parse_ts(line)
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
            events.append((ts, "diag_candidato", (m.group(1), float(m.group(2)), int(m.group(3)))))
            continue
        m = DIAG_NONE_RE.search(line)
        if m:
            events.append((ts, "diag_none", int(m.group(1))))
    return events


def hand_present(gesture, extended_fingers):
    # Misma regla que main.py -- el único caso donde gesture=None y
    # extended_fingers==0 juntos es "no hay mano" (ver classify_world_landmarks:
    # 0 dedos extendidos CON mano real siempre clasifica puño_cerrado).
    return not (gesture is None and extended_fingers == 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log_path", nargs="?", default="benchmarks/fase_d_run_sin_condicion.log")
    parser.add_argument("--cue", dest="cue_path", default=None, help="Archivo de CUE opcional (fase_d_schedule.py/fase_d_hard_cases.py).")
    parser.add_argument("--desde", default=None, help="Timestamp inicial ('YYYY-MM-DD HH:MM:SS[,mmm]'), inclusive.")
    parser.add_argument("--hasta", default=None, help="Timestamp final ('YYYY-MM-DD HH:MM:SS[,mmm]'), inclusive.")
    args = parser.parse_args()

    desde = parse_window_ts(args.desde) if args.desde else None
    hasta = parse_window_ts(args.hasta) if args.hasta else None

    files_used = discover_rotated_logs(args.log_path) or [args.log_path]
    lines = read_lines_with_rotation(args.log_path, desde, hasta)
    events = build_events(lines)

    print(f"Archivo(s): {', '.join(files_used)}")
    if desde or hasta:
        print(f"Ventana: desde={desde or '(sin límite)'} hasta={hasta or '(sin límite)'}")
    detections_raw = [e for e in events if e[1] == "detectado"]
    releases_raw = [e for e in events if e[1] == "liberado"]
    print(f"Eventos: {len(events)} totales -- {len(detections_raw)} 'Gesto detectado', {len(releases_raw)} 'Gesto liberado'")
    print()

    # --- 2) Agrupar en "repeticiones" ---
    reps = []
    last_release_idx = None
    first_detection_seen = False
    for idx, (ts, kind, data) in enumerate(events):
        if kind == "liberado":
            last_release_idx = idx
        elif kind == "detectado":
            gesture, confidence = data
            contaminated = (last_release_idx is None) and first_detection_seen
            reps.append({
                "detect_idx": idx,
                "detect_ts": ts,
                "gesture": gesture,
                "confidence": confidence,
                "contaminated": contaminated,
            })
            first_detection_seen = True
            last_release_idx = None

    n_contaminated = sum(1 for r in reps if r["contaminated"])
    print(f"Repeticiones (= 'Gesto detectado'): {len(reps)}")
    print(f"Contaminadas (sin liberacion previa, salvo la primera): {n_contaminated}")
    print()

    # --- 3) Reproducir el GestureStabilizer real sobre TODAS las observaciones
    # crudas (con hand_present real) -- da, en cada confirmación, el tiempo de
    # sistema; y en cada liberación, el timestamp del último frame que SÍ
    # coincidió con el gesto confirmado (para el tiempo de liberación "del
    # sistema", sin reacción humana). ---
    raw_observations = []  # (ts, gesture, extended_fingers)
    for ts, kind, data in events:
        if kind == "diag_candidato":
            raw_observations.append((ts, data[0], data[2]))
        elif kind == "diag_none":
            raw_observations.append((ts, None, data))

    stabilizer = GestureStabilizer()
    window = deque(maxlen=stabilizer._window_size)
    confirm_events = []  # (confirm_ts, gesture, t_sistema_ms)
    release_events = []  # (release_ts, gesture_liberado, last_match_ts)
    prev_confirmed = None
    last_match_ts = None  # timestamp de la última observación == confirmado actual
    for ts, gesture, extended in raw_observations:
        window.append((ts, gesture))
        was_confirmed = stabilizer._confirmed
        confirmed = stabilizer.observe(gesture, hand_present=hand_present(gesture, extended))
        if gesture is not None and gesture == was_confirmed:
            last_match_ts = ts
        if confirmed is not None and confirmed != prev_confirmed:
            matching_ts = [t for (t, g) in window if g == confirmed]
            earliest_ts = min(matching_ts)
            t_sistema_ms = (ts - earliest_ts).total_seconds() * 1000
            confirm_events.append((ts, confirmed, t_sistema_ms))
            last_match_ts = ts
        if confirmed is None and was_confirmed is not None:
            release_events.append((ts, was_confirmed, last_match_ts))
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

    # Tiempo de liberacion, dos formas:
    # (a) detectado->liberado (incluye todo el tiempo sostenido, no es "liberación").
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
        print(f"\nTiempo detectado->liberado (sostenido + liberación) -- n={len(release_times)} avg={sum(release_times)/len(release_times):.0f}ms min={min(release_times):.0f}ms max={max(release_times):.0f}ms")

    # (b) última evidencia de mano -> liberado: la parte de SISTEMA de la
    # liberación (sin reacción humana), derivada del log crudo solo (no
    # necesita CUE). Si last_match_ts es None para algún evento (no debería
    # pasar salvo log truncado), se excluye y se avisa.
    system_release_times = []
    skipped_no_last_match = 0
    for release_ts, _, last_match_ts in release_events:
        if last_match_ts is None:
            skipped_no_last_match += 1
            continue
        system_release_times.append((release_ts - last_match_ts).total_seconds() * 1000)
    if system_release_times:
        n4 = len(system_release_times)
        avg4 = sum(system_release_times) / n4
        system_release_times.sort()
        print(
            f"Tiempo de liberación DEL SISTEMA (desde el último frame con el gesto "
            f"confirmado hasta 'Gesto liberado', sin reacción humana) -- n={n4} "
            f"avg={avg4:.0f}ms p50={system_release_times[n4//2]:.0f}ms "
            f"max={max(system_release_times):.0f}ms min={min(system_release_times):.0f}ms"
        )
    if skipped_no_last_match:
        print(f"AVISO: {skipped_no_last_match} liberaciones sin último frame identificable -- excluidas (no derivable del log disponible)")

    from collections import Counter
    gcount = Counter(r["gesture"] for r in reps if not r["contaminated"])
    print(f"\nRepeticiones NO contaminadas por gesto: {dict(gcount)}")

    # --- 4) Si hay CUE log: tiempo de liberación desde la señal QUITA real +
    # tiempo total desde la señal HAZ hasta la detección. ---
    if args.cue_path:
        cues = parse_cue_log(args.cue_path)
        haz_cues = [c for c in cues if c[2] == "HAZ"]
        quita_cues = [c for c in cues if c[2] == "QUITA"]
        print(f"\n=== CUE log: {args.cue_path} ({len(haz_cues)} HAZ, {len(quita_cues)} QUITA) ===")

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
                f"señales QUITA -- no coinciden, no se calcula"
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
                f"tiempo de sistema) -- n={n3} avg={sum(total_from_cue)/n3:.0f}ms "
                f"min={min(total_from_cue):.0f}ms max={max(total_from_cue):.0f}ms"
            )
        else:
            print(
                f"AVISO: {len(detections_only)} detecciones no contaminadas vs "
                f"{len(haz_cues)} señales HAZ -- no coinciden, no se calcula"
            )


if __name__ == "__main__":
    main()
