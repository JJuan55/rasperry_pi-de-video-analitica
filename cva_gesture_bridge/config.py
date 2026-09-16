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
