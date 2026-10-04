"""Fase C2 -- tiempo de confirmación real con el GestureStabilizer nuevo (ventana por
mayoría + histéresis) y cooldown≈0, simulado sobre la secuencia cruda ya cacheada
(cache_raw_sequences.py) -- no repite inferencia.

"Simular la secuencia" (pedido de JD) significa: reproducir, para cada sesión, la
MISMA secuencia de gestos crudos que salió del detector real, frame por frame, en
orden, pasándola por el GestureStabilizer real (no un mock) con los timestamps
reales de captura (no un fps asumido) para medir cuánto tiempo de reloj real tarda
en confirmarse cada tramo de gesto sostenido desde que el gesto dominante empieza a
aparecer -- contra AC3 (<1.5s).

Nota honesta: los timestamps reales son los del CAPTURADOR corriendo contra el
sistema viejo (Fase 8) a su propio ritmo real de llegada de frames del cliente (no
el techo de MediaPipe medido en sustained_load.py) -- es la cadencia real con la que
el cliente manda frames, que es lo que de verdad limita cuándo puede confirmarse un
gesto en producción, cooldown aparte.
"""

import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)

from cva_gesture_bridge.vision.detector import GestureStabilizer  # noqa: E402

CACHE_PATH = os.path.join(HERE, "raw_sequences_cache.json")
MIN_RUN_LENGTH = 15


def _find_runs(gestures):
    runs = []
    i = 0
    n = len(gestures)
    while i < n:
        if gestures[i] is None:
            i += 1
            continue
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
    with open(CACHE_PATH) as f:
        data = json.load(f)

    all_confirm_times_ms = []
    ac3_ms = 1500

    for session, d in data.items():
        gestures = d["gestures"]
        timestamps_ms = d["timestamps_ms"]
        runs = _find_runs(gestures)
        print(f"\n=== {session} ===")

        # Un solo stabilizer para TODA la sesión, igual que en producción real (una
        # instancia por conexión) -- la histéresis entre tramos consecutivos importa.
        stabilizer = GestureStabilizer()  # defaults calibrados: 7/5/20

        run_idx = 0
        confirmed_this_run = False
        run_start_ts = None

        for i, (g, ts) in enumerate(zip(gestures, timestamps_ms)):
            confirmed = stabilizer.observe(g)

            if run_idx < len(runs):
                dominant, start, end = runs[run_idx]
                already_confirmed_from_before = False
                if i == start:
                    run_start_ts = ts
                    already_confirmed_from_before = (confirmed == dominant)
                    confirmed_this_run = already_confirmed_from_before
                    if already_confirmed_from_before:
                        # La histéresis mantuvo confirmado el mismo gesto desde un
                        # tramo anterior -- no hubo que reconfirmar nada, 0ms reales.
                        all_confirm_times_ms.append(0)
                        print(
                            f"  tramo {run_idx:2d} [{start:5d}:{end:5d}] gesto={dominant:14s} "
                            f"ya estaba confirmado por histéresis desde el tramo anterior (0ms)  OK"
                        )
                if start <= i < end and not confirmed_this_run and confirmed == dominant:
                    delta_ms = ts - run_start_ts
                    all_confirm_times_ms.append(delta_ms)
                    confirmed_this_run = True
                    mark = "OK" if delta_ms <= ac3_ms else "** SUPERA AC3 **"
                    print(
                        f"  tramo {run_idx:2d} [{start:5d}:{end:5d}] gesto={dominant:14s} "
                        f"confirmado en {delta_ms:5d}ms desde que empezó el tramo  {mark}"
                    )
                if i == end - 1:
                    if not confirmed_this_run:
                        print(
                            f"  tramo {run_idx:2d} [{start:5d}:{end:5d}] gesto={dominant:14s} "
                            f"NUNCA se confirmó dentro del tramo (histéresis de un gesto "
                            f"previo pudo haber tapado la ventana, o el tramo es corto)"
                        )
                    run_idx += 1

    print("\n=== Resumen ===")
    n = len(all_confirm_times_ms)
    print(f"Tramos confirmados y medidos: {n}")
    if n:
        all_confirm_times_ms.sort()
        avg = sum(all_confirm_times_ms) / n
        p50 = all_confirm_times_ms[n // 2]
        p95 = all_confirm_times_ms[int(0.95 * n) - 1] if n >= 20 else max(all_confirm_times_ms)
        print(f"Tiempo de confirmación -- avg={avg:.0f}ms p50={p50:.0f}ms p95={p95:.0f}ms max={max(all_confirm_times_ms)}ms min={min(all_confirm_times_ms)}ms")
        over_ac3 = sum(1 for t in all_confirm_times_ms if t > ac3_ms)
        print(f"Tramos que superaron AC3 (<{ac3_ms}ms): {over_ac3}/{n}")


if __name__ == "__main__":
    main()
