"""Configuración del bridge, con overrides por variable de entorno."""

import os

# Puerto y host verificados contra el cliente Rust (reference/bridge.rs: DEFAULT_CVA_BRIDGE_PORT).
BRIDGE_HOST = os.environ.get("CVA_BRIDGE_HOST", "0.0.0.0")
BRIDGE_PORT = int(os.environ.get("CVA_BRIDGE_PORT", "8766"))

# Punto de partida sugerido en CLAUDE.md sección 5; ajustable con pruebas reales.
WATCHDOG_TIMEOUT_SECONDS = float(os.environ.get("CVA_WATCHDOG_TIMEOUT_SECONDS", "5"))

LOG_LEVEL = os.environ.get("CVA_LOG_LEVEL", "INFO")
