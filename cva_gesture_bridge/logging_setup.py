"""Configuración de logging del bridge: consola (para journald) + archivo de texto
rotado diariamente, para poder consultar el historial de días anteriores sin depender
de la retención de journald.
"""

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level: str, log_file: str, retention_days: int) -> None:
    """Configura el logger raíz con salida a consola y a un .txt rotado a medianoche.

    `retention_days` es cuántos archivos rotados viejos se conservan además del actual.
    """
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = TimedRotatingFileHandler(
        log_path, when="midnight", backupCount=retention_days, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(stream_handler)
    root.addHandler(file_handler)
