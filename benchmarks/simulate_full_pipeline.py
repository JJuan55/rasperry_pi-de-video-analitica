"""Fase C2 — corrección antes de Fase D: simula el pipeline completo de main.py
(observe con confidence/hand_present, envío solo en el cambio, evento de
liberación) sobre las secuencias reales ya cacheadas -- valida de punta a punta que
el fix no generó comportamiento raro (demasiados envíos, liberaciones que nunca
llegan, etc.) contra datos reales, no solo contra tests sintéticos.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)

from cva_gesture_bridge.vision.detector import GestureStabilizer, format_line, RELEASE_LINE  # noqa: E402

CACHE_PATH = os.path.join(HERE, "raw_sequences_cache.json")
MIN_CONFIDENCE = 0.5


def main():
    with open(CACHE_PATH) as f:
        data = json.load(f)

    for session, d in data.items():
        gestures = d["gestures"]
        confidences = d["confidences"]
        extended = d["extended_fingers"]
        timestamps_ms = d["timestamps_ms"]

        stabilizer = GestureStabilizer()  # defaults de producción
        last_sent = None
        sends = []  # (timestamp_ms, line)

        for g, conf, ext, ts in zip(gestures, confidences, extended, timestamps_ms):
            hand_present = not (g is None and ext == 0)
            confirmed = stabilizer.observe(g, conf, hand_present)

            if confirmed == last_sent:
                continue

            if confirmed is None:
                sends.append((ts, RELEASE_LINE))
                last_sent = None
                continue

            line = format_line(confirmed, stabilizer.confirmed_confidence, MIN_CONFIDENCE)
            if line is not None:
                sends.append((ts, line))
                last_sent = confirmed

        print(f"\n=== {session} ({len(gestures)} frames reales) ===")
        print(f"Total de líneas que se habrían mandado: {len(sends)}")
        n_release = sum(1 for _, line in sends if line == RELEASE_LINE)
        n_gesture = len(sends) - n_release
        print(f"  -> gestos confirmados: {n_gesture}, liberaciones: {n_release}")
        for ts, line in sends[:20]:
            print(f"  t={ts}: {line}")
        if len(sends) > 20:
            print(f"  ... ({len(sends) - 20} más)")


if __name__ == "__main__":
    main()
