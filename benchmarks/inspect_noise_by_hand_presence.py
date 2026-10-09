"""Fase C2 — corrección antes de Fase D: dentro del ruido real medido en los 40
tramos de gesto sostenido (ver analyze_raw_stability.py), separa cuánto es "mano
realmente ausente" (gesture=None, extended_fingers==0) de "mano presente pero
ambigua/gesto en conflicto" (extended_fingers en {2,3}, o un gesto real distinto)
-- esto calibra GESTURE_RELEASE_AFTER_MISSES_NO_HAND con datos, no a ojo. Usa el
cache de cache_raw_sequences.py (ahora con extended_fingers), no repite inferencia.
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


def _hand_present(gesture, extended_fingers):
    return not (gesture is None and extended_fingers == 0)


def main():
    with open(CACHE_PATH) as f:
        data = json.load(f)

    no_hand_bursts = []  # rachas consecutivas de ruido con mano ausente
    hand_present_bursts = []  # rachas consecutivas de ruido con mano presente/ambigua
    no_hand_total = 0
    hand_present_total = 0

    for session, d in data.items():
        gestures = d["gestures"]
        extended = d["extended_fingers"]
        runs = _find_runs(gestures)
        for dominant, start, end in runs:
            seg_gestures = gestures[start:end]
            seg_extended = extended[start:end]

            cur_no_hand = 0
            cur_hand_present = 0
            for g, ext in zip(seg_gestures, seg_extended):
                is_noise = g != dominant
                if not is_noise:
                    if cur_no_hand:
                        no_hand_bursts.append(cur_no_hand)
                    if cur_hand_present:
                        hand_present_bursts.append(cur_hand_present)
                    cur_no_hand = 0
                    cur_hand_present = 0
                    continue

                if _hand_present(g, ext):
                    hand_present_total += 1
                    cur_hand_present += 1
                    if cur_no_hand:
                        no_hand_bursts.append(cur_no_hand)
                        cur_no_hand = 0
                else:
                    no_hand_total += 1
                    cur_no_hand += 1
                    if cur_hand_present:
                        hand_present_bursts.append(cur_hand_present)
                        cur_hand_present = 0

            if cur_no_hand:
                no_hand_bursts.append(cur_no_hand)
            if cur_hand_present:
                hand_present_bursts.append(cur_hand_present)

    total = no_hand_total + hand_present_total
    print("=== Composición del ruido real dentro de los 40 tramos sostenidos ===")
    print(f"Total de frames de ruido: {total}")
    if total:
        print(f"  -> mano AUSENTE (gesture=None, extended_fingers==0): {no_hand_total} ({no_hand_total/total*100:.1f}%)")
        print(f"  -> mano PRESENTE pero ambigua/conflicto: {hand_present_total} ({hand_present_total/total*100:.1f}%)")
    print()
    print(f"Rachas de ruido con mano AUSENTE: {len(no_hand_bursts)}, longitudes: {sorted(no_hand_bursts, reverse=True)[:10]}")
    print(f"  máxima racha mano ausente: {max(no_hand_bursts) if no_hand_bursts else 0} frames seguidos")
    print(f"Rachas de ruido con mano PRESENTE/ambigua: {len(hand_present_bursts)}, longitudes: {sorted(hand_present_bursts, reverse=True)[:10]}")
    print(f"  máxima racha mano presente/ambigua: {max(hand_present_bursts) if hand_present_bursts else 0} frames seguidos")


if __name__ == "__main__":
    main()
