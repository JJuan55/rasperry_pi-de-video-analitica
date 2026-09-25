"""Spike Fase A -- análisis real de los 2132 frames capturados en la sesión de JD del
2026-09-25 (ver BITACORA.md). Corre HandLandmarker sobre cada frame (orden
cronológico real), guarda los datos crudos a CSV, agrupa en "segmentos" de mano
presente (para identificar cada gesto sostenido), mide estabilidad frame a frame
dentro de cada segmento, y guarda una imagen anotada de ejemplo por segmento.

No es parte del paquete de producción. Corre con .venv-mediapipe-spike/bin/python.
"""

import csv
import os
import re
import sys

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(HERE, "hand_landmarker.task")
# Uso: python analyze_captured_frames.py [sufijo_de_sesion]
SESSION_SUFFIX = sys.argv[1] if len(sys.argv) > 1 else "2026-09-25"
FRAMES_DIR = os.path.join(HERE, f"captured_frames_{SESSION_SUFFIX}")
RESULTS_CSV = os.path.join(HERE, f"captured_frames_analysis_{SESSION_SUFFIX}.csv")
ANNOTATED_DIR = os.path.join(HERE, f"captured_frames_annotated_{SESSION_SUFFIX}")

# Máximo hueco (en frames consecutivos SIN mano detectada) que todavía se considera
# parte del mismo segmento -- tolera 1-2 misses puntuales de MediaPipe sin partir un
# gesto sostenido en varios segmentos.
MAX_GAP_FRAMES_WITHIN_SEGMENT = 3
MIN_SEGMENT_LENGTH = 5  # descarta detecciones aisladas de 1-4 frames (ruido, no un gesto real sostenido)

# mp.solutions no existe en este build de mediapipe (1.0.1, solo API de Tasks) --
# topología fija y pública de los 21 landmarks de una mano (documentación oficial de
# MediaPipe Hands), a mano.
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),          # pulgar
    (0, 5), (5, 6), (6, 7), (7, 8),          # índice
    (5, 9), (9, 10), (10, 11), (11, 12),     # medio
    (9, 13), (13, 14), (14, 15), (15, 16),   # anular
    (13, 17), (17, 18), (18, 19), (19, 20),  # meñique
    (0, 17),                                 # base de la palma
]


def make_landmarker():
    base_options = mp_python.BaseOptions(model_asset_path=MODEL_PATH)
    options = mp_vision.HandLandmarkerOptions(
        base_options=base_options, running_mode=mp_vision.RunningMode.IMAGE, num_hands=1
    )
    return mp_vision.HandLandmarker.create_from_options(options)


def list_frames_chronological():
    pattern = re.compile(r"^(\d+)__(.+)\.jpg$")
    entries = []
    for name in os.listdir(FRAMES_DIR):
        m = pattern.match(name)
        if not m:
            continue
        entries.append((int(m.group(1)), m.group(2), name))
    entries.sort(key=lambda e: e[0])
    return entries


def draw_landmarks(frame_bgr, landmarks_px):
    out = frame_bgr.copy()
    for a, b in HAND_CONNECTIONS:
        cv2.line(out, landmarks_px[a], landmarks_px[b], (0, 255, 0), 1)
    for x, y in landmarks_px:
        cv2.circle(out, (x, y), 2, (0, 0, 255), -1)
    return out


def main():
    os.makedirs(ANNOTATED_DIR, exist_ok=True)
    entries = list_frames_chronological()
    print(f"Total de frames a analizar: {len(entries)}")

    landmarker = make_landmarker()

    rows = []
    for ts_ms, old_label, filename in entries:
        frame = cv2.imread(os.path.join(FRAMES_DIR, filename))
        if frame is None:
            print(f"  [!] no se pudo leer {filename}")
            continue
        h, w = frame.shape[:2]
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        result = landmarker.detect(mp_image)

        num_hands = len(result.hand_landmarks)
        confidence = None
        landmarks_norm = None
        if num_hands > 0:
            confidence = float(result.handedness[0][0].score)
            landmarks_norm = [(lm.x, lm.y) for lm in result.hand_landmarks[0]]

        rows.append({
            "timestamp_ms": ts_ms,
            "filename": filename,
            "old_label": old_label,
            "num_hands": num_hands,
            "confidence": confidence,
            "landmarks_norm": landmarks_norm,
            "frame_wh": (w, h),
        })

    # --- guardar CSV crudo (sin los landmarks completos, serían demasiadas columnas --
    # esos se usan en memoria para las métricas de estabilidad de abajo) ---
    with open(RESULTS_CSV, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp_ms", "filename", "old_label", "num_hands", "confidence"])
        for r in rows:
            writer.writerow([r["timestamp_ms"], r["filename"], r["old_label"], r["num_hands"], r["confidence"]])
    print(f"CSV crudo guardado en {RESULTS_CSV}")

    # --- estadisticas generales ---
    total = len(rows)
    with_hand = [r for r in rows if r["num_hands"] > 0]
    without_hand = [r for r in rows if r["num_hands"] == 0]
    print(f"\n=== Estadísticas generales ===")
    print(f"Total frames: {total}")
    print(f"Con mano detectada: {len(with_hand)} ({100*len(with_hand)/total:.1f}%)")
    print(f"Sin mano detectada: {len(without_hand)} ({100*len(without_hand)/total:.1f}%)")
    if with_hand:
        confs = [r["confidence"] for r in with_hand]
        print(f"Confianza (handedness score) con mano -- min={min(confs):.3f} "
              f"max={max(confs):.3f} media={sum(confs)/len(confs):.3f}")

    # --- segmentar en tramos de mano presente ---
    segments = []
    current = []
    gap = 0
    for r in rows:
        if r["num_hands"] > 0:
            current.append(r)
            gap = 0
        else:
            if current:
                gap += 1
                if gap > MAX_GAP_FRAMES_WITHIN_SEGMENT:
                    if len(current) >= MIN_SEGMENT_LENGTH:
                        segments.append(current)
                    current = []
                    gap = 0
    if current and len(current) >= MIN_SEGMENT_LENGTH:
        segments.append(current)

    print(f"\n=== Segmentos de mano presente detectados: {len(segments)} ===")

    for i, seg in enumerate(segments):
        duration_s = (seg[-1]["timestamp_ms"] - seg[0]["timestamp_ms"]) / 1000.0
        confs = [r["confidence"] for r in seg if r["confidence"] is not None]
        avg_conf = sum(confs) / len(confs) if confs else 0.0

        # Estabilidad frame a frame: distancia euclidiana media (en pixeles reales,
        # 320x240) de cada uno de los 21 landmarks entre frames consecutivos DENTRO
        # del segmento (ignora los frames sin mano intercalados, si los hay).
        frames_with_lm = [r for r in seg if r["landmarks_norm"] is not None]
        w, h = frames_with_lm[0]["frame_wh"]
        per_frame_movement_px = []
        for a, b in zip(frames_with_lm, frames_with_lm[1:]):
            dists = []
            for (xa, ya), (xb, yb) in zip(a["landmarks_norm"], b["landmarks_norm"]):
                dx = (xa - xb) * w
                dy = (ya - yb) * h
                dists.append((dx**2 + dy**2) ** 0.5)
            per_frame_movement_px.append(sum(dists) / len(dists))

        if per_frame_movement_px:
            mean_mov = sum(per_frame_movement_px) / len(per_frame_movement_px)
            max_mov = max(per_frame_movement_px)
        else:
            mean_mov, max_mov = 0.0, 0.0

        print(f"\n--- Segmento {i}: {len(seg)} frames, {duration_s:.1f}s, "
              f"confianza media={avg_conf:.3f} ---")
        print(f"  Movimiento medio de landmarks entre frames consecutivos: {mean_mov:.2f} px "
              f"(max={max_mov:.2f} px) -- sobre imagen de {w}x{h}")
        print(f"  Primer frame: {seg[0]['filename']}, último: {seg[-1]['filename']}")

        # --- imagen anotada de ejemplo (frame del medio del segmento) ---
        mid = frames_with_lm[len(frames_with_lm) // 2]
        frame = cv2.imread(os.path.join(FRAMES_DIR, mid["filename"]))
        landmarks_px = [(int(x * w), int(y * h)) for x, y in mid["landmarks_norm"]]
        annotated = draw_landmarks(frame, landmarks_px)
        out_path = os.path.join(ANNOTATED_DIR, f"segmento_{i}_{mid['filename']}")
        cv2.imwrite(out_path, annotated)
        print(f"  Imagen anotada: {out_path}")


if __name__ == "__main__":
    main()
