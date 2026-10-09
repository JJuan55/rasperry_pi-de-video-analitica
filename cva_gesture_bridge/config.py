"""Configuración del bridge, con overrides por variable de entorno."""

import os

# Puerto y host verificados contra el cliente Rust (reference/bridge.rs: DEFAULT_CVA_BRIDGE_PORT).
BRIDGE_HOST = os.environ.get("CVA_BRIDGE_HOST", "0.0.0.0")
BRIDGE_PORT = int(os.environ.get("CVA_BRIDGE_PORT", "8766"))

# Punto de partida sugerido en CLAUDE.md sección 5; ajustable con pruebas reales.
WATCHDOG_TIMEOUT_SECONDS = float(os.environ.get("CVA_WATCHDOG_TIMEOUT_SECONDS", "5"))

LOG_LEVEL = os.environ.get("CVA_LOG_LEVEL", "INFO")

# Archivo de texto con el historial de logs, rotado a medianoche, para poder
# consultarlo días después aunque journald ya haya descartado esas líneas.
LOG_FILE = os.environ.get("CVA_LOG_FILE", "logs/cva_gesture_bridge.log")
LOG_RETENTION_DAYS = int(os.environ.get("CVA_LOG_RETENTION_DAYS", "7"))

# Fase B — visión con MediaPipe HandLandmarker (reemplaza YOLO+OpenCV de Fase 8, ver
# BITACORA.md "Fase B" para el porqué). Modelo oficial de Google, empaquetado
# localmente igual que antes yolov8n.pt (no se descarga en producción).
MEDIAPIPE_MODEL_PATH = os.environ.get("CVA_MEDIAPIPE_MODEL_PATH", "models/hand_landmarker.task")

# Umbrales propios del modelo (HandLandmarkerOptions) — distintos de MIN_CONFIDENCE de
# abajo: estos controlan si MediaPipe reporta una mano en absoluto; MIN_CONFIDENCE
# controla si, ya con una mano reportada, se manda la línea al cliente. Puntos de
# partida razonables (valores por defecto de MediaPipe), a ajustar con datos reales.
MEDIAPIPE_MIN_HAND_DETECTION_CONFIDENCE = float(
    os.environ.get("CVA_MEDIAPIPE_MIN_HAND_DETECTION_CONFIDENCE", "0.5")
)
MEDIAPIPE_MIN_HAND_PRESENCE_CONFIDENCE = float(
    os.environ.get("CVA_MEDIAPIPE_MIN_HAND_PRESENCE_CONFIDENCE", "0.5")
)
MEDIAPIPE_MIN_TRACKING_CONFIDENCE = float(
    os.environ.get("CVA_MEDIAPIPE_MIN_TRACKING_CONFIDENCE", "0.5")
)

# Umbral mínimo de confianza (handedness score del modelo) para traducir una detección
# en una línea real hacia el cliente (sección 4.4 de CLAUDE.md). Punto de partida, se
# ajusta con datos reales de la validación manual documentada en BITACORA.md.
MIN_CONFIDENCE = float(os.environ.get("CVA_MIN_CONFIDENCE", "0.5"))

# Tiempo mínimo entre que se procesa un frame y se procesa el siguiente -- fijado en
# 5s en la Fase 8 original (YOLO+OpenCV, ~440ms/frame, para no saturar la Pi
# evaluando casi todo). Con MediaPipe (Fase B) esa razón ya no aplica igual: medido
# con carga sostenida real en esta Pi (Fase C2, `benchmarks/sustained_load.py`,
# 7805 frames reales de las 3 sesiones de captura, 4.5 min seguidos, un solo
# proceso, sin cooldown artificial) -- latencia avg=34.5ms p95=46.0ms p99=68.7ms
# max=111.9ms, CPU ~100% de 1 solo núcleo de 4 (sostenido, sin degradarse), RSS
# estable (209->214MB, sin fuga). El cliente real manda frames a ~7fps (~143ms entre
# frames) -- incluso el peor frame medido (111.9ms) deja margen real (~1.3x), y el
# caso típico deja 3-4x de margen. La Pi aguanta holgado evaluar cada frame que
# llega -- cooldown eliminado (0.0). Ver BITACORA.md "Fase C2" para la corrida
# completa.
GESTURE_COOLDOWN_SECONDS = float(os.environ.get("CVA_GESTURE_COOLDOWN_SECONDS", "0.0"))

# GestureStabilizer (ver detector.py) -- ventana deslizante por mayoría CON
# histéresis, rediseñado en Fase C2 sobre el esquema de Fase 8 (ventana de 3, 2
# coincidencias, sin histéresis -- un solo frame sin gesto ya tiraba la confirmación
# a None, lo que con cooldown=5s tardaba ~15s en confirmar un gesto real sostenido:
# 3 detecciones procesadas × 5s).
#
# Calibrado con datos reales, no a ojo (Fase C2, `benchmarks/analyze_raw_stability.py`
# + `inspect_noise_composition.py`, sobre 40 tramos de gesto realmente sostenido en
# las 3 sesiones de captura): el 100% del ruido crudo por frame dentro de un gesto
# sostenido real fue `None` (mano perdida un instante), nunca otro gesto en
# conflicto -- la racha de ruido más larga medida fue 14 frames seguidos.
#
# GESTURE_STABILITY_WINDOW/MIN_MATCHES (5 de 7, ~71%) controlan CONFIRMAR un gesto
# nuevo -- con cooldown≈0 y ~143ms real entre frames del cliente, eso es ~715ms en
# el caso típico, dentro de AC3 (<1.5s) con margen real medido (ver "Simulación de
# tiempo de confirmación" en BITACORA.md).
#
# SOLTAR un gesto ya confirmado (histéresis) usa DOS umbrales distintos desde la
# corrección de 2026-10 (ver BITACORA.md "Fase C2 — corrección antes de Fase D"):
# con un solo umbral largo, retirar la mano de cuadro dejaba un "gesto fantasma"
# confirmado ~2.7s de más (20 misses × ~143ms), un problema real si esto llega a
# controlar un actuador.
#
# La intuición inicial ("sin mano no hay ambigüedad, se puede soltar casi de
# inmediato") NO se sostuvo contra los datos reales (benchmarks/
# inspect_noise_by_hand_presence.py, los mismos 40 tramos sostenidos): dentro de un
# gesto genuinamente sostenido, el ruido de "mano ausente" (MediaPipe pierde el
# tracking un instante, p.ej. por un micro-ajuste de la mano) también tiene rachas
# largas -- la máxima medida fue 13 frames seguidos, casi igual que la máxima de
# "mano presente pero ambigua" (14). Un umbral corto (ej. 3) para
# GESTURE_RELEASE_AFTER_MISSES_NO_HAND habría soltado gestos reales sostenidos por
# error. Los dos umbrales quedaron con margen real sobre su propio peor caso
# medido, no con una asimetría grande: GESTURE_RELEASE_AFTER_MISSES=20 (margen
# sobre 14) y GESTURE_RELEASE_AFTER_MISSES_NO_HAND=16 (margen sobre 13) -- una
# mejora real pero modesta (2.86s -> 2.29s en el caso de mano retirada de verdad),
# no la mejora grande que la intuición inicial sugería. Documentado como trade-off
# explícito, no resuelto a fondo: una liberación más rápida todavía podría valer la
# pena para Fase D si se mide con una señal mejor que "cuadros seguidos sin mano".
GESTURE_STABILITY_WINDOW = int(os.environ.get("CVA_GESTURE_STABILITY_WINDOW", "7"))
GESTURE_STABILITY_MIN_MATCHES = int(os.environ.get("CVA_GESTURE_STABILITY_MIN_MATCHES", "5"))
GESTURE_RELEASE_AFTER_MISSES = int(os.environ.get("CVA_GESTURE_RELEASE_AFTER_MISSES", "20"))
GESTURE_RELEASE_AFTER_MISSES_NO_HAND = int(os.environ.get("CVA_GESTURE_RELEASE_AFTER_MISSES_NO_HAND", "16"))

# CAPTURADOR TEMPORAL (2026-09-25, ver BITACORA.md "Fase 8" / spike MediaPipe) — banco
# de frames reales para el spike, autorizado por JD explícitamente, con fecha de
# vencimiento: se usa una sola sesión de prueba y se apaga. Sin setear (caso normal),
# CERO cambio de comportamiento — ningún archivo nuevo importa ni ninguna rama de
# código nueva se ejecuta si esto queda en None.
CAPTURE_FRAMES_DIR = os.environ.get("CVA_CAPTURE_FRAMES_DIR")
