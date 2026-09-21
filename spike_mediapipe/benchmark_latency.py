"""Spike Fase A — benchmark de latencia real de MediaPipe HandLandmarker en esta Pi 5.

No es parte del paquete de producción. Corre con .venv-mediapipe-spike/bin/python.
Mismo criterio de medición que el benchmark de YOLO de Fase 8 (BITACORA.md): warmup
descartado, luego N iteraciones medidas, sobre bytes JPEG decodificados con
cv2.imdecode (mismo camino real que usaría el bridge).
"""

import os
import time

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(HERE, "hand_landmarker.task")
FRAME_SIZE = (320, 240)  # resolución real del cliente, confirmada en Fase 8 (BITACORA.md)
N_WARMUP = 3
N_ITERS = 30


def make_jpeg_bytes(bgr_image) -> bytes:
    img = cv2.resize(bgr_image, FRAME_SIZE)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def make_landmarker(running_mode):
    base_options = mp_python.BaseOptions(model_asset_path=MODEL_PATH)
    options = mp_vision.HandLandmarkerOptions(
        base_options=base_options,
        running_mode=running_mode,
        num_hands=1,
    )
    return mp_vision.HandLandmarker.create_from_options(options)


def benchmark(name: str, jpeg_bytes: bytes, landmarker) -> None:
    def decode_and_infer():
        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        return landmarker.detect(mp_image)

    for _ in range(N_WARMUP):
        decode_and_infer()

    latencies_ms = []
    t_start = time.monotonic()
    for _ in range(N_ITERS):
        t0 = time.monotonic()
        result = decode_and_infer()
        latencies_ms.append((time.monotonic() - t0) * 1000.0)
    elapsed = time.monotonic() - t_start

    fps = N_ITERS / elapsed
    avg_ms = sum(latencies_ms) / len(latencies_ms)
    p95_ms = sorted(latencies_ms)[int(0.95 * len(latencies_ms)) - 1]
    n_hands = len(result.hand_landmarks) if result is not None else 0

    print(f"== {name} ==")
    print(f"  manos detectadas en la ultima corrida: {n_hands}")
    print(f"  fps promedio:        {fps:.2f}")
    print(f"  latencia promedio:   {avg_ms:.1f} ms")
    print(f"  latencia p95:        {p95_ms:.1f} ms")
    print(f"  latencia min/max:    {min(latencies_ms):.1f} / {max(latencies_ms):.1f} ms")


if __name__ == "__main__":
    ultralytics_assets = os.path.join(
        HERE, "..", ".venv", "lib", "python3.14", "site-packages", "ultralytics", "assets"
    )
    zidane = cv2.imread(os.path.join(ultralytics_assets, "zidane.jpg"))
    blank = np.zeros((480, 640, 3), dtype=np.uint8)

    print(f"Modelo: {MODEL_PATH} ({os.path.getsize(MODEL_PATH)} bytes)")
    print(f"Resolucion de prueba: {FRAME_SIZE[0]}x{FRAME_SIZE[1]}\n")

    landmarker = make_landmarker(mp_vision.RunningMode.IMAGE)
    benchmark("zidane.jpg (persona real, sin mano clara -- caso 'sin mano')", make_jpeg_bytes(zidane), landmarker)
    benchmark("frame en negro (caso 'sin mano', sintetico)", make_jpeg_bytes(blank), landmarker)
