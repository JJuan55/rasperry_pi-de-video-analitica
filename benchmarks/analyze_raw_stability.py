"""Fase C2 -- ruido real de clasificación por frame (sin estabilizador), para
calibrar window_size/min_matches del GestureStabilizer con datos, no a ojo.

Corre el detector real (una sola instancia, orden cronológico dentro de cada sesión
-- igual criterio que sustained_load.py) sobre los frames reales ya capturados, sin
cooldown ni estabilizador, y mide: dentro de cada tramo donde la persona sostuvo un
gesto real, cuántos frames se desvían del gesto dominante de ese tramo (ruido) y cuál
es la racha más larga de frames de ruido seguidos (el caso peor que una ventana de
mayoría tiene que poder absorber sin perder la confirmación).

Los tramos se detectan automáticamente a partir de la secuencia cruda de gestos (no
de la etiqueta del nombre de archivo, que ya sabemos que no es confiable) -- un tramo
nuevo empieza cuando el gesto dominante en una ventana corta de adelanto cambia.
"""

import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)

from cva_gesture_bridge import config  # noqa: E402
from cva_gesture_bridge.vision.detector import GestureDetector  # noqa: E402
from sustained_load import _load_frame_paths, SESSIONS, SPIKE_DIR  # noqa: E402

MIN_RUN_LENGTH = 15  # frames minimos para contar un tramo como "gesto sostenido real"


def _session_frame_paths(session):
    session_dir = os.path.join(SPIKE_DIR, session)
    names = sorted(
        f for f in os.listdir(session_dir)
        if f.endswith(".jpg") and f.split("_")[0].isdigit()
    )
    return [os.path.join(session_dir, n) for n in names]


def _find_runs(gestures):
    """Tramos maximales de gestos no-None iguales o separados por ruido corto --
    agrupa por el valor crudo, tratando None como posible ruido dentro de un tramo
    (no como un tramo propio), ya que lo que nos interesa es "mientras la persona
    sostenía ESTE gesto, qué tan seguido el frame crudo decía otra cosa"."""
    runs = []
    i = 0
    n = len(gestures)
    while i < n:
        if gestures[i] is None:
            i += 1
            continue
        # ventana de adelanto: mientras la moda de los próximos 15 frames siga siendo
        # gestures[i], seguimos extendiendo el tramo.
        start = i
        dominant = gestures[i]
        j = i
        while j < n:
            lookahead = gestures[j:j + MIN_RUN_LENGTH]
            non_none = [g for g in lookahead if g is not None]
            if not non_none:
                break
            mode = Counter(non_none).most_common(1)[0][0]
            if mode != dominant:
                break
            j += 1
        if j - start >= MIN_RUN_LENGTH:
            runs.append((dominant, start, j))
            i = j
        else:
            i += 1
    return runs


def main():
    detector = GestureDetector(
        config.MEDIAPIPE_MODEL_PATH,
        config.MIN_CONFIDENCE,
        config.MEDIAPIPE_MIN_HAND_DETECTION_CONFIDENCE,
        config.MEDIAPIPE_MIN_HAND_PRESENCE_CONFIDENCE,
        config.MEDIAPIPE_MIN_TRACKING_CONFIDENCE,
    )

    all_noise_rates = []
    all_max_bursts = []
    total_runs = 0

    for session in SESSIONS:
        paths = _session_frame_paths(session)
        print(f"\n=== {session} ({len(paths)} frames) ===")
        gestures = []
        for p in paths:
            with open(p, "rb") as f:
                jpeg = f.read()
            r = detector.detect(jpeg)
            gestures.append(r.gesture)

        runs = _find_runs(gestures)
        print(f"  tramos sostenidos detectados (>= {MIN_RUN_LENGTH} frames): {len(runs)}")
        for dominant, start, end in runs:
            segment = gestures[start:end]
            length = len(segment)
            noise_count = sum(1 for g in segment if g != dominant)
            noise_rate = noise_count / length

            max_burst = 0
            cur_burst = 0
            for g in segment:
                if g != dominant:
                    cur_burst += 1
                    max_burst = max(max_burst, cur_burst)
                else:
                    cur_burst = 0

            all_noise_rates.append(noise_rate)
            all_max_bursts.append(max_burst)
            total_runs += 1
            print(
                f"    [{start:5d}:{end:5d}] gesto={dominant:14s} len={length:4d} "
                f"ruido={noise_count:3d}/{length} ({noise_rate*100:4.1f}%) "
                f"racha_max_de_ruido={max_burst}"
            )

    print("\n=== Resumen global ===")
    print(f"Tramos sostenidos analizados: {total_runs}")
    if all_noise_rates:
        print(f"Tasa de ruido por frame -- avg={sum(all_noise_rates)/len(all_noise_rates)*100:.2f}% max={max(all_noise_rates)*100:.2f}%")
        print(f"Racha de ruido más larga observada en cualquier tramo: {max(all_max_bursts)} frames seguidos")
        from collections import Counter as C
        burst_hist = C(all_max_bursts)
        print(f"Distribución de rachas máximas por tramo: {dict(sorted(burst_hist.items()))}")


if __name__ == "__main__":
    main()
