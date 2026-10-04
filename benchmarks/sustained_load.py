"""Fase C2 -- costo real sostenido de MediaPipe sin cooldown artificial.

No es parte del paquete de producción (carpeta benchmarks/, no se importa desde
cva_gesture_bridge). Corre con .venv-fase-b/bin/python.

Alimenta al GestureDetector real, con UNA sola instancia de larga duración (igual que
en producción -- no una instancia nueva por frame), con miles de frames reales ya
capturados en Fase A/recaptura de meñique, en orden cronológico dentro de cada sesión,
espalda con espalda sin ningún sleep/cooldown artificial -- el objetivo es medir el
techo real de velocidad de la Pi, no simular el framerate de llegada del cliente (eso
se compara aparte, al final, contra el resultado medido aquí).

Mide con /proc directamente (sin psutil -- no es una dependencia real del proyecto,
no hace falta agregarla solo para este benchmark de una vez).
"""

import os
import sys
import time

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

CLOCK_TICKS = os.sysconf("SC_CLK_TCK")


def _cpu_times():
    with open("/proc/self/stat") as f:
        fields = f.read().split()
    utime = int(fields[13])
    stime = int(fields[14])
    return (utime + stime) / CLOCK_TICKS


def _rss_mb():
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    return 0.0


def _load_frame_paths():
    paths = []
    for session in SESSIONS:
        session_dir = os.path.join(SPIKE_DIR, session)
        if not os.path.isdir(session_dir):
            print(f"[aviso] no encontrado, se salta: {session_dir}")
            continue
        names = sorted(
            f for f in os.listdir(session_dir)
            if f.endswith(".jpg") and f.split("_")[0].isdigit()
        )
        paths.extend(os.path.join(session_dir, n) for n in names)
    return paths


def main(limit=None):
    frame_paths = _load_frame_paths()
    if limit is not None:
        frame_paths = frame_paths[:limit]
    total = len(frame_paths)
    print(f"Modelo: {config.MEDIAPIPE_MODEL_PATH}")
    print(f"Frames reales disponibles: {total}")
    if total == 0:
        print("No hay frames -- abortando.")
        return

    detector = GestureDetector(
        config.MEDIAPIPE_MODEL_PATH,
        config.MIN_CONFIDENCE,
        config.MEDIAPIPE_MIN_HAND_DETECTION_CONFIDENCE,
        config.MEDIAPIPE_MIN_HAND_PRESENCE_CONFIDENCE,
        config.MEDIAPIPE_MIN_TRACKING_CONFIDENCE,
    )

    # Warmup descartado (mismo criterio que el resto del proyecto).
    with open(frame_paths[0], "rb") as f:
        detector.detect(f.read())

    latencies_ms = []
    cpu_samples = []  # (wall_elapsed_s, cpu_pct_desde_la_ultima_muestra)
    rss_samples_mb = []

    sample_every = 100
    last_cpu_time = _cpu_times()
    last_wall = time.monotonic()

    wall_start = time.monotonic()
    for i, path in enumerate(frame_paths):
        with open(path, "rb") as f:
            jpeg_bytes = f.read()
        t0 = time.monotonic()
        detector.detect(jpeg_bytes)
        latencies_ms.append((time.monotonic() - t0) * 1000.0)

        if (i + 1) % sample_every == 0:
            now_wall = time.monotonic()
            now_cpu = _cpu_times()
            wall_delta = now_wall - last_wall
            cpu_delta = now_cpu - last_cpu_time
            cpu_pct = 100.0 * cpu_delta / wall_delta if wall_delta > 0 else 0.0
            cpu_samples.append((now_wall - wall_start, cpu_pct))
            rss_samples_mb.append(_rss_mb())
            last_wall, last_cpu_time = now_wall, now_cpu
            elapsed = now_wall - wall_start
            print(
                f"  [{i + 1}/{total}] {elapsed:6.1f}s corridos -- "
                f"cpu={cpu_pct:5.1f}% rss={rss_samples_mb[-1]:6.1f}MB "
                f"latencia_ultimos100_avg={sum(latencies_ms[-sample_every:]) / sample_every:5.1f}ms"
            )

    wall_total = time.monotonic() - wall_start

    latencies_ms.sort()
    n = len(latencies_ms)
    p50 = latencies_ms[n // 2]
    p95 = latencies_ms[int(0.95 * n) - 1]
    p99 = latencies_ms[int(0.99 * n) - 1]
    avg = sum(latencies_ms) / n
    fps_techo = n / wall_total

    print()
    print("=== Resumen ===")
    print(f"Frames procesados: {n}")
    print(f"Tiempo total: {wall_total:.1f}s ({wall_total / 60:.1f} min)")
    print(f"Latencia por frame -- avg={avg:.1f}ms p50={p50:.1f}ms p95={p95:.1f}ms p99={p99:.1f}ms max={max(latencies_ms):.1f}ms")
    print(f"FPS techo (sin cooldown, espalda con espalda): {fps_techo:.2f}")
    if cpu_samples:
        cpu_vals = [c for _, c in cpu_samples]
        print(f"CPU% -- avg={sum(cpu_vals) / len(cpu_vals):.1f}% max={max(cpu_vals):.1f}% (de {os.cpu_count()} núcleos, 100% = 1 núcleo saturado)")
    if rss_samples_mb:
        print(f"RSS -- inicio={rss_samples_mb[0]:.1f}MB fin={rss_samples_mb[-1]:.1f}MB (¿crece sostenido? {'SI -- revisar' if rss_samples_mb[-1] > rss_samples_mb[0] * 1.2 else 'no, estable'})")

    cliente_fps_real = 7.0  # ~6-8fps real, CLAUDE.md sección 2
    print()
    print(f"Referencia: el cliente real manda frames a ~{cliente_fps_real:.0f}fps "
          f"(~{1000 / cliente_fps_real:.0f}ms entre frames) -- margen de la Pi sobre "
          f"ese ritmo: {(1000 / cliente_fps_real) / avg:.1f}x (avg) / "
          f"{(1000 / cliente_fps_real) / p95:.1f}x (p95)")


if __name__ == "__main__":
    _limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    main(limit=_limit)
