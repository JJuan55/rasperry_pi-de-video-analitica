"""Fase D, sesión 2 -- detalle por repetición de la batería de liberación
(`fase_d_schedule.py`), sobre un log del bridge y su CUE. Solo lee. No se
importa desde el paquete.

Calcula, con definiciones explícitas (ver BITACORA.md "Fase D, sesión 2 --
detalle de la batería"):

(b) Aciertos AC3 por repetición. Intervalo de la repetición i =
    [HAZ_i, HAZ_{i+1}); la última termina en la línea `FIN` del CUE.
    - acierto limpio: el PRIMER `Gesto detectado` del intervalo es el gesto
      pedido (se confirma sin enviar antes otro gesto).
    - acierto tras error: el gesto pedido se envía, pero después de otro.
    - sin envío del pedido: el gesto pedido no se envía en el intervalo.
    Aparte se marca si DESPUÉS del pedido se envió otro gesto distinto (no
    cambia la categoría, que sigue la definición pedida).

(c) Por cada liberación reproducida con el `GestureStabilizer` real:
    cuadros entre el último cuadro coincidente y la liberación ("contador de
    cuadros", con cuántos tenían mano y cuántos no), y los huecos de tiempo
    entre líneas `Frame` consecutivas en ese tramo ("hueco sin cuadros").
    Además, los cuadros crudos de los N segundos previos a un envío.

OJO con el log (verificado en tcp_server.py): `fps_real` es el promedio
ACUMULADO desde que se abrió la conexión, no instantáneo; `latencia_ms` es el
tiempo entre dos lecturas de frame consecutivas. Por eso los huecos se miden
aquí directamente sobre los timestamps de las líneas `Frame`.

Uso:
    .venv-fase-c2-fix/bin/python benchmarks/fase_d_battery_detail.py <log> <cue>
        [--desde ...] [--hasta ...] [--reps 3,7,15] [--antes-de-envio 3]
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_fase_d_run import (  # noqa: E402
    build_events,
    hand_present,
    parse_cue_log,
    parse_ts,
    parse_window_ts,
    raw_observations_from_events,
    read_lines_with_rotation,
    replay_stabilizer,
)

FRAME_RE = re.compile(r"Frame #(\d+) de .*latencia_ms=([\d.]+)")
FIN_RE = re.compile(r"CUE: FIN\b")

# Hueco = separación entre dos líneas `Frame` mayor que 2 periodos nominales
# a 7 fps (2 x 143ms). Umbral de reporte, no un parámetro del sistema.
GAP_THRESHOLD_MS = 286.0

ACIERTO_LIMPIO = "acierto limpio"
ACIERTO_TRAS_ERROR = "acierto tras error"
SIN_ENVIO = "sin envío del pedido"


def parse_frame_lines(lines):
    """[(ts, numero_frame)] por cada línea `Frame #N`, en orden."""
    frames = []
    for line in lines:
        m = FRAME_RE.search(line)
        if m:
            ts = parse_ts(line)
            if ts is not None:
                frames.append((ts, int(m.group(1))))
    return frames


def parse_fin_ts(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            if FIN_RE.search(line):
                return parse_ts(line)
    return None


def classify_battery_hits(cues, events, end_ts):
    """Función pura. `cues` como parse_cue_log; `events` como build_events;
    `end_ts` = fin de la última repetición (línea FIN), o None.

    Devuelve una fila por HAZ: dict(rep, haz_ts, pedido, envios=[(ts, gesto)],
    categoria, ms_hasta_pedido, otro_despues)."""
    haz = sorted((c for c in cues if c[2] == "HAZ"), key=lambda c: c[0])
    detections = [(ts, data[0]) for ts, kind, data in events if kind == "detectado"]
    rows = []
    for i, (h_ts, rep, _, pedido) in enumerate(haz):
        upper = haz[i + 1][0] if i + 1 < len(haz) else end_ts
        sends = [(ts, g) for ts, g in detections if ts >= h_ts and (upper is None or ts < upper)]
        idx = next((k for k, (_, g) in enumerate(sends) if g == pedido), None)
        if idx is None:
            categoria = SIN_ENVIO
            ms = None
            otro_despues = False
        else:
            categoria = ACIERTO_LIMPIO if idx == 0 else ACIERTO_TRAS_ERROR
            ms = (sends[idx][0] - h_ts).total_seconds() * 1000
            otro_despues = any(g != pedido for _, g in sends[idx + 1:])
        rows.append({
            "rep": rep, "haz_ts": h_ts, "pedido": pedido, "envios": sends,
            "categoria": categoria, "ms_hasta_pedido": ms, "otro_despues": otro_despues,
        })
    return rows


def release_span_detail(raw_observations, frames, last_match_ts, release_ts):
    """Función pura. Tramo (last_match_ts, release_ts]:
    - cuadros: observaciones crudas en el tramo, con/sin mano (misma regla
      hand_present de main.py);
    - huecos: separaciones entre líneas `Frame` consecutivas (incluyendo la
      del último cuadro coincidente) mayores que GAP_THRESHOLD_MS.
    Devuelve dict(ms, cuadros, con_mano, sin_mano, max_sep_ms, huecos=[(ts_inicio, ms)],
    ms_en_huecos)."""
    obs = [(ts, g, e) for ts, g, e in raw_observations if last_match_ts < ts <= release_ts]
    con_mano = sum(1 for _, g, e in obs if hand_present(g, e))
    frame_ts = [ts for ts, _ in frames if last_match_ts <= ts <= release_ts]
    seps = [((b - a).total_seconds() * 1000, a) for a, b in zip(frame_ts, frame_ts[1:])]
    huecos = [(a, ms) for ms, a in seps if ms > GAP_THRESHOLD_MS]
    return {
        "ms": (release_ts - last_match_ts).total_seconds() * 1000,
        "cuadros": len(obs),
        "con_mano": con_mano,
        "sin_mano": len(obs) - con_mano,
        "max_sep_ms": max((ms for ms, _ in seps), default=0.0),
        "huecos": huecos,
        "ms_en_huecos": sum(ms for _, ms in huecos),
    }


def frames_before(raw_observations, ts, seconds):
    """Observaciones crudas en [ts - seconds, ts]."""
    from datetime import timedelta
    start = ts - timedelta(seconds=seconds)
    return [(t, g, e) for t, g, e in raw_observations if start <= t <= ts]


def _hms(ts):
    return ts.strftime("%H:%M:%S,%f")[:-3]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log_path")
    parser.add_argument("cue_path")
    parser.add_argument("--desde", default=None)
    parser.add_argument("--hasta", default=None)
    parser.add_argument("--reps", default="", help="Repeticiones (por QUITA) a detallar en la liberación, ej. 3,7,15")
    parser.add_argument("--antes-de-envio", type=float, default=3.0, dest="antes")
    args = parser.parse_args()

    desde = parse_window_ts(args.desde) if args.desde else None
    hasta = parse_window_ts(args.hasta) if args.hasta else None
    lines = read_lines_with_rotation(args.log_path, desde, hasta)
    events = build_events(lines)
    frames = parse_frame_lines(lines)
    cues = parse_cue_log(args.cue_path)
    fin_ts = parse_fin_ts(args.cue_path)

    # --- (b) aciertos ---
    rows = classify_battery_hits(cues, events, fin_ts)
    print("=== (b) Aciertos por repetición (intervalo [HAZ_i, HAZ_i+1); la última hasta FIN) ===")
    print("Acierto limpio = el primer 'Gesto detectado' del intervalo es el pedido.")
    for r in rows:
        envios = ", ".join(f"{g}@+{(ts - r['haz_ts']).total_seconds() * 1000:.0f}ms" for ts, g in r["envios"]) or "(ninguno)"
        extra = "  ** después se envió otro gesto" if r["otro_despues"] else ""
        print(f"  [{r['rep']:2d}] HAZ {_hms(r['haz_ts'])} {r['pedido']:14s} -> {r['categoria']:20s} envíos: {envios}{extra}")
    n = len(rows)
    limpios = sum(1 for r in rows if r["categoria"] == ACIERTO_LIMPIO)
    tras_error = sum(1 for r in rows if r["categoria"] == ACIERTO_TRAS_ERROR)
    sin = sum(1 for r in rows if r["categoria"] == SIN_ENVIO)
    otros = sum(1 for r in rows if r["otro_despues"])
    print(f"Aciertos limpios: {limpios}/{n} = {100 * limpios / n:.0f}%  |  tras error: {tras_error}  |  sin envío: {sin}  |  con otro gesto enviado después del pedido: {otros}")

    # --- (c1) cuadros previos a cada envío equivocado ---
    raw = raw_observations_from_events(events)
    print(f"\n=== (c1) Cuadros crudos de los {args.antes:.0f}s previos a cada envío distinto del pedido ===")
    for r in rows:
        for ts, g in r["envios"]:
            if g == r["pedido"]:
                continue
            print(f"  rep [{r['rep']}] pedido={r['pedido']} envío equivocado {_hms(ts)} {g}:")
            for t, gg, e in frames_before(raw, ts, args.antes):
                print(f"    {_hms(t)}  {gg or '-':14s} dedos={e}  mano={'sí' if hand_present(gg, e) else 'no'}")

    # --- (c2) liberaciones: contador de cuadros vs hueco sin cuadros ---
    _, release_events = replay_stabilizer(raw)
    quitas = sorted((c for c in cues if c[2] == "QUITA"), key=lambda c: c[0])
    wanted = {int(x) for x in args.reps.split(",") if x.strip()}
    details = []
    for release_ts, gesture, last_match_ts in release_events:
        if last_match_ts is None:
            continue
        q = [c for c in quitas if c[0] <= release_ts]
        rep = q[-1][1] if q else None
        details.append((rep, release_ts, gesture, last_match_ts, release_span_detail(raw, frames, last_match_ts, release_ts)))
    if details:
        max_item = max(details, key=lambda d: d[4]["ms"])
    print(f"\n=== (c2) Liberaciones: cuadros entre el último coincidente y la liberación; huecos > {GAP_THRESHOLD_MS:.0f}ms entre líneas Frame ===")
    for item in details:
        rep, release_ts, gesture, last_match_ts, d = item
        marks = []
        if rep in wanted:
            marks.append("PEDIDA")
        if item is max_item:
            marks.append("MÁXIMO")
        if not marks:
            continue
        print(
            f"  [{'/'.join(marks)}] QUITA previa rep {rep}: {gesture} último coincidente {_hms(last_match_ts)} -> liberado {_hms(release_ts)} "
            f"= {d['ms']:.0f}ms | cuadros={d['cuadros']} (con mano {d['con_mano']}, sin mano {d['sin_mano']}) | "
            f"separación máx entre Frame={d['max_sep_ms']:.0f}ms | huecos={len(d['huecos'])} ({d['ms_en_huecos']:.0f}ms en huecos)"
        )
        for a, ms in d["huecos"]:
            print(f"      hueco desde {_hms(a)}: {ms:.0f}ms sin cuadros")
    all_ms = [d[4]["ms"] for d in details]
    all_frames = [d[4]["cuadros"] for d in details]
    print(f"  Todas las liberaciones reproducidas: n={len(details)}, cuadros min={min(all_frames)} max={max(all_frames)}, ms min={min(all_ms):.0f} max={max(all_ms):.0f}")


if __name__ == "__main__":
    main()
