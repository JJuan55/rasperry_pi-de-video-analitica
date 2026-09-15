import logging

from cva_gesture_bridge.logging_setup import configure_logging


def test_configure_logging_writes_to_rotating_file(tmp_path):
    log_file = tmp_path / "sub" / "cva_gesture_bridge.log"

    configure_logging("INFO", str(log_file), retention_days=3)
    try:
        logger = logging.getLogger("cva_gesture_bridge.test_logging_setup")
        logger.info("mensaje de prueba fase 7")
        for handler in logging.getLogger().handlers:
            handler.flush()

        assert log_file.exists(), "configure_logging debe crear el directorio y el archivo de log"
        content = log_file.read_text(encoding="utf-8")
        assert "mensaje de prueba fase 7" in content
    finally:
        logging.getLogger().handlers.clear()


def test_configure_logging_replaces_previous_handlers(tmp_path):
    log_file_a = tmp_path / "a.log"
    log_file_b = tmp_path / "b.log"

    configure_logging("INFO", str(log_file_a), retention_days=1)
    configure_logging("INFO", str(log_file_b), retention_days=1)
    try:
        logging.getLogger("cva_gesture_bridge.test_logging_setup").info("solo en b")
        for handler in logging.getLogger().handlers:
            handler.flush()

        assert "solo en b" in log_file_b.read_text(encoding="utf-8")
        # El primer archivo no debe recibir mensajes posteriores a la reconfiguración.
        assert not log_file_a.exists() or "solo en b" not in log_file_a.read_text(encoding="utf-8")
    finally:
        logging.getLogger().handlers.clear()
