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

# Fase 8 — visión. Tamaño de modelo elegido en BITACORA.md ("Fase 8") a partir del
# benchmark real en esta Pi 5, no de suposición.
YOLO_MODEL = os.environ.get("CVA_YOLO_MODEL", "yolov8n.pt")

# Umbral mínimo de confianza para traducir una detección en una línea real hacia el
# cliente (sección 4.4 de CLAUDE.md). Punto de partida, se ajusta con datos reales
# de la validación manual documentada en BITACORA.md.
MIN_CONFIDENCE = float(os.environ.get("CVA_MIN_CONFIDENCE", "0.5"))

# Tiempo mínimo entre que se procesa un gesto (se corre YOLO+OpenCV sobre un frame) y
# se procesa el siguiente — pedido por JD el 2026-09-17 tras la primera prueba manual,
# para no correr el detector en cada frame (~2.5 fps real). Ver BITACORA.md "Fase 8".
GESTURE_COOLDOWN_SECONDS = float(os.environ.get("CVA_GESTURE_COOLDOWN_SECONDS", "5"))

# Cuántas detecciones *procesadas* consecutivas deben coincidir en el mismo gesto antes
# de confirmarlo (loguear "Gesto detectado" y mandar send_line) — fix de falsos
# positivos sin mano presente (2026-09-18, ver BITACORA.md "Fase 8"). Combinado con el
# cooldown de arriba, cada detección procesada está ~GESTURE_COOLDOWN_SECONDS aparte,
# así que confirmar toma aproximadamente GESTURE_STABILITY_STREAK *
# GESTURE_COOLDOWN_SECONDS segundos de gesto sostenido — si eso resulta demasiado
# lento en la validación real de AC3 (<1.5s), el ajuste es bajar uno de los dos, no
# ambos a la vez sin medir.
GESTURE_STABILITY_STREAK = int(os.environ.get("CVA_GESTURE_STABILITY_STREAK", "3"))
