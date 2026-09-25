"""Spike Fase A -- segunda pasada sobre los frames capturados: los segmentos de "mano
presente" de analyze_captured_frames.py resultaron demasiado largos (JD nunca sacó la
mano de cuadro entre un gesto y el siguiente). Esta pasada clasifica cada frame por
patrón de dedos extendidos usando los landmarks reales de MediaPipe (heurística simple
de geometría -- distancia de la punta al centro de la mano vs. la base del dedo, no es
la clasificación final de la Fase B, solo sirve para segmentar este dataset y dar una
primera señal de qué tan bien separa MediaPipe los 4 gestos), y agrupa corridas
consecutivas del mismo patrón que duran varios segundos -- eso sí debería alinear con
los tramos reales que JD sostuvo cada gesto.
"""

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
# Uso: python segment_by_gesture.py [sufijo_de_sesion]
# Sin argumento, usa la primera sesión (2026-09-25) por compatibilidad.
SESSION_SUFFIX = sys.argv[1] if len(sys.argv) > 1 else "2026-09-25"
FRAMES_DIR = os.path.join(HERE, f"captured_frames_{SESSION_SUFFIX}")
ANNOTATED_DIR = os.path.join(HERE, f"captured_frames_annotated_{SESSION_SUFFIX}")

WRIST, THUMB_MCP, THUMB_TIP = 0, 2, 4
FINGER_MCP_TIP = {
    "indice": (5, 8),
    "medio": (9, 12),
    "anular": (13, 16),
    "menique": (17, 20),
}
INDEX_MCP = 5

MIN_RUN_SECONDS = 3.0  # una corrida mas corta que esto no cuenta como "gesto sostenido"
MAX_GAP_FRAMES = 4  # tolerancia a parpadeos puntuales de clasificacion dentro de una corrida

HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
]

CATALOG_PATTERNS = {
    (False, False, False, False, False): "puño_cerrado",
    (True, False, False, False, False): "dedo_pulgar",
    (False, False, False, False, True): "dedo_menique",
}


def dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def classify_pattern(landmarks_norm):
    """landmarks_norm: lista de 21 (x,y) normalizados. Devuelve una tupla de 5
    booleanos (pulgar, indice, medio, anular, menique) -- extendido o no."""
    wrist = landmarks_norm[WRIST]

    thumb_extended = dist(landmarks_norm[INDEX_MCP], landmarks_norm[THUMB_TIP]) > \
        dist(landmarks_norm[INDEX_MCP], landmarks_norm[THUMB_MCP]) * 1.3

    fingers = []
    for name, (mcp, tip) in FINGER_MCP_TIP.items():
        extended = dist(wrist, landmarks_norm[tip]) > dist(wrist, landmarks_norm[mcp]) * 1.15
        fingers.append(extended)

    return (thumb_extended, *fingers)


def label_for_pattern(pattern):
    if pattern in CATALOG_PATTERNS:
        return CATALOG_PATTERNS[pattern]
    n_extended = sum(pattern)
    # Mismo criterio que classify_gesture() de producción (detector.py):
    # >=4 dedos extendidos -> palma_abierta, aunque no se detecten los 5 exactos
    # (el umbral rápido del pulgar de este script es más estricto que la geometría
    # real -- ver corrección tras revisar las imágenes anotadas).
    if n_extended >= 4:
        return "palma_abierta"
    return f"otro({n_extended}_dedos)"


def list_frames_chronological():
    pattern = re.compile(r"^(\d+)__(.+)\.jpg$")
    entries = []
    for name in os.listdir(FRAMES_DIR):
        m = pattern.match(name)
        if m:
            entries.append((int(m.group(1)), name))
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

    base_options = mp_python.BaseOptions(model_asset_path=MODEL_PATH)
    options = mp_vision.HandLandmarkerOptions(
        base_options=base_options, running_mode=mp_vision.RunningMode.IMAGE, num_hands=1
    )
    landmarker = mp_vision.HandLandmarker.create_from_options(options)

    frames_data = []
    for ts_ms, filename in entries:
        frame = cv2.imread(os.path.join(FRAMES_DIR, filename))
        if frame is None:
            continue
        h, w = frame.shape[:2]
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        result = landmarker.detect(mp_image)
        if not result.hand_landmarks:
            frames_data.append({"ts": ts_ms, "filename": filename, "pattern": None})
            continue
        landmarks_norm = [(lm.x, lm.y) for lm in result.hand_landmarks[0]]
        pattern = classify_pattern(landmarks_norm)
        frames_data.append({
            "ts": ts_ms, "filename": filename, "pattern": pattern,
            "landmarks_norm": landmarks_norm, "wh": (w, h),
            "confidence": float(result.handedness[0][0].score),
        })

    print(f"Total frames analizados: {len(frames_data)}")

    # --- agrupar corridas consecutivas del mismo patron (con tolerancia a huecos) ---
    runs = []
    current = []
    current_pattern = None
    gap = 0
    for fd in frames_data:
        if fd["pattern"] is None:
            if current:
                gap += 1
                if gap > MAX_GAP_FRAMES:
                    runs.append((current_pattern, current))
                    current, current_pattern, gap = [], None, 0
            continue
        if current_pattern is None or fd["pattern"] == current_pattern:
            current.append(fd)
            current_pattern = fd["pattern"]
            gap = 0
        else:
            runs.append((current_pattern, current))
            current = [fd]
            current_pattern = fd["pattern"]
            gap = 0
    if current:
        runs.append((current_pattern, current))

    # filtrar corridas muy cortas (probable transicion, no un gesto sostenido)
    real_runs = []
    for pattern, frames in runs:
        duration_s = (frames[-1]["ts"] - frames[0]["ts"]) / 1000.0
        if duration_s >= MIN_RUN_SECONDS and len(frames) >= 10:
            real_runs.append((pattern, frames, duration_s))

    print(f"\n=== Corridas de gesto sostenido detectadas (>= {MIN_RUN_SECONDS}s): {len(real_runs)} ===")

    for i, (pattern, frames, duration_s) in enumerate(real_runs):
        label = label_for_pattern(pattern)
        confs = [f["confidence"] for f in frames]
        avg_conf = sum(confs) / len(confs)

        w, h = frames[0]["wh"]
        movements = []
        for a, b in zip(frames, frames[1:]):
            dists = [dist((ax * w, ay * h), (bx * w, by * h))
                     for (ax, ay), (bx, by) in zip(a["landmarks_norm"], b["landmarks_norm"])]
            movements.append(sum(dists) / len(dists))
        mean_mov = sum(movements) / len(movements) if movements else 0.0
        max_mov = max(movements) if movements else 0.0

        print(f"\n--- Corrida {i}: patrón={label} ({len(frames)} frames, {duration_s:.1f}s) ---")
        print(f"  Confianza media MediaPipe: {avg_conf:.3f}")
        print(f"  Movimiento medio landmarks entre frames: {mean_mov:.2f} px (max={max_mov:.2f} px)")
        print(f"  Frames: {frames[0]['filename']} .. {frames[-1]['filename']}")

        mid = frames[len(frames) // 2]
        frame = cv2.imread(os.path.join(FRAMES_DIR, mid["filename"]))
        landmarks_px = [(int(x * w), int(y * h)) for x, y in mid["landmarks_norm"]]
        annotated = draw_landmarks(frame, landmarks_px)
        out_path = os.path.join(ANNOTATED_DIR, f"corrida_{i}_{label}_{mid['filename']}")
        cv2.imwrite(out_path, annotated)
        print(f"  Imagen anotada: {out_path}")


if __name__ == "__main__":
    main()
