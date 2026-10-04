"""Fase C2 -- de los tramos con ruido que encontró analyze_raw_stability.py, ¿el
ruido es mayormente `None` (la mano se perdió un instante) o un gesto distinto en
conflicto? Esto decide si la histéresis (quedarse en el último gesto confirmado
mientras no aparezca OTRO gesto con su propia mayoría) alcanza para protegernos, o si
hace falta algo más. Usa el cache de cache_raw_sequences.py, no repite inferencia.
"""

import json
import os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
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

    none_total = 0
    conflict_total = 0
    conflict_examples = []

    for session, d in data.items():
        gestures = d["gestures"]
        runs = _find_runs(gestures)
        for dominant, start, end in runs:
            segment = gestures[start:end]
            noise = [g for g in segment if g != dominant]
            if not noise:
                continue
            none_count = sum(1 for g in noise if g is None)
            conflict_count = len(noise) - none_count
            none_total += none_count
            conflict_total += conflict_count
            if conflict_count:
                conflicting_values = Counter(g for g in noise if g is not None)
                conflict_examples.append(
                    (session, dominant, start, end, dict(conflicting_values))
                )
                print(
                    f"{session} [{start}:{end}] dominante={dominant} "
                    f"ruido_None={none_count} ruido_otro_gesto={conflict_count} "
                    f"valores_en_conflicto={dict(conflicting_values)}"
                )

    print()
    print("=== Resumen composición del ruido ===")
    total = none_total + conflict_total
    if total:
        print(f"Frames de ruido totales: {total}")
        print(f"  -> None (mano no detectada/perdida un instante): {none_total} ({none_total/total*100:.1f}%)")
        print(f"  -> otro gesto real en conflicto: {conflict_total} ({conflict_total/total*100:.1f}%)")
    else:
        print("No hay ruido en ningún tramo.")


if __name__ == "__main__":
    main()
