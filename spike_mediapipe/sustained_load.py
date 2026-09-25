"""Spike Fase A — inferencia sostenida de HandLandmarker durante varios minutos, para
medir CPU/memoria reales desde afuera (con `ps` sobre este PID) mientras corre.
"""

import os
import sys
import time

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(HERE, "hand_landmarker.task")
FRAME_SIZE = (320, 240)
DURATION_SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 180.0

base_options = mp_python.BaseOptions(model_asset_path=MODEL_PATH)
options = mp_vision.HandLandmarkerOptions(
    base_options=base_options, running_mode=mp_vision.RunningMode.IMAGE, num_hands=1
)
landmarker = mp_vision.HandLandmarker.create_from_options(options)

# Copia local, no una ruta cruzada al repo de producción (ver BITACORA.md,
# "Corrección de aislamiento", 2026-09-25).
zidane = cv2.resize(cv2.imread(os.path.join(HERE, "zidane_test.jpg")), FRAME_SIZE)
frame_rgb = cv2.cvtColor(zidane, cv2.COLOR_BGR2RGB)

print(f"PID={os.getpid()}", flush=True)
print(f"Corriendo inferencia continua por {DURATION_SECONDS:.0f}s...", flush=True)

count = 0
t_start = time.monotonic()
while time.monotonic() - t_start < DURATION_SECONDS:
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
    landmarker.detect(mp_image)
    count += 1

elapsed = time.monotonic() - t_start
print(f"Listo: {count} inferencias en {elapsed:.1f}s ({count / elapsed:.2f} fps sostenido)", flush=True)
