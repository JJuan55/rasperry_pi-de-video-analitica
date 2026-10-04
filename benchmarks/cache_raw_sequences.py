"""Fase C2 -- corre el detector real UNA vez sobre todas las sesiones reales y
guarda la secuencia cruda de gestos (sin cooldown, sin estabilizador) en un JSON,
para que el resto del análisis de esta fase (ruido, simulación de confirmación) no
tenga que repetir ~4-5 min de inferencia cada vez que cambia una pregunta.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)

from cva_gesture_bridge import config  # noqa: E402
from cva_gesture_bridge.vision.detector import GestureDetector  # noqa: E402

SPIKE_DIR = os.path.join(REPO_ROOT, "..", "cva-pi-repo-spike", "spike_mediapipe")
SESSIONS = [
    "captured_frames_2026-09-25",
    "captured_frames_2026-09-25_v2",
    "captured_frames_2026-10-02_menique",
]
OUT_PATH = os.path.join(HERE, "raw_sequences_cache.json")


def _session_frame_paths(session):
    session_dir = os.path.join(SPIKE_DIR, session)
    names = sorted(
        f for f in os.listdir(session_dir)
        if f.endswith(".jpg") and f.split("_")[0].isdigit()
    )
    return names, [os.path.join(session_dir, n) for n in names]


def main():
    detector = GestureDetector(
        config.MEDIAPIPE_MODEL_PATH,
        config.MIN_CONFIDENCE,
        config.MEDIAPIPE_MIN_HAND_DETECTION_CONFIDENCE,
        config.MEDIAPIPE_MIN_HAND_PRESENCE_CONFIDENCE,
        config.MEDIAPIPE_MIN_TRACKING_CONFIDENCE,
    )

    data = {}
    for session in SESSIONS:
        names, paths = _session_frame_paths(session)
        print(f"=== {session} ({len(paths)} frames) ===", flush=True)
        gestures = []
        confidences = []
        for i, p in enumerate(paths):
            with open(p, "rb") as f:
                jpeg = f.read()
            r = detector.detect(jpeg)
            gestures.append(r.gesture)
            confidences.append(round(r.confidence, 4))
            if (i + 1) % 1000 == 0:
                print(f"  {i + 1}/{len(paths)}", flush=True)
        # timestamp_ms esta en el propio nombre de archivo -- lo extraemos para poder
        # reconstruir el dt real entre frames consecutivos en la simulacion.
        timestamps_ms = [int(n.split("__")[0]) for n in names]
        data[session] = {
            "filenames": names,
            "timestamps_ms": timestamps_ms,
            "gestures": gestures,
            "confidences": confidences,
        }

    with open(OUT_PATH, "w") as f:
        json.dump(data, f)
    print(f"\nGuardado: {OUT_PATH}")


if __name__ == "__main__":
    main()
