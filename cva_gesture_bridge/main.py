"""Punto de entrada del bridge.

Fase 8: además de recibir/loguear frames (Fase 6) y medir latencia (Fase 7), corre el
detector de gestos real sobre cada frame y manda el resultado de vuelta al cliente.
"""

import asyncio
import logging
import time

import cv2
import numpy as np

from cva_gesture_bridge import config
from cva_gesture_bridge.logging_setup import configure_logging
from cva_gesture_bridge.transport.tcp_server import TcpServer
from cva_gesture_bridge.transport.watchdog import Watchdog
from cva_gesture_bridge.vision.detector import GestureDetector

logger = logging.getLogger(__name__)


def _on_watchdog_timeout() -> None:
    # Fase 8: todavía no hay actuadores que cortar (eso llega en Fase 9+); el watchdog
    # solo advierte que se dejó de recibir video.
    logger.warning("Watchdog disparado — sin actuadores que cortar todavía (Fase 8)")


def _make_watchdog() -> Watchdog:
    return Watchdog(config.WATCHDOG_TIMEOUT_SECONDS, _on_watchdog_timeout)


def _make_on_jpeg_frame(detector: GestureDetector, cooldown_seconds: float):
    # last_processed_at vive en este closure, creado una sola vez por arranque del
    # bridge — el cooldown es global al proceso, no por conexión (ver nota en
    # BITACORA.md "Fase 8": para el uso real de este proyecto, una sola conexión
    # persistente por sesión, equivale a un cooldown por sesión).
    last_processed_at = None

    async def on_jpeg_frame(jpeg_bytes: bytes, writer: asyncio.StreamWriter) -> None:
        nonlocal last_processed_at
        now = time.monotonic()
        if last_processed_at is not None and (now - last_processed_at) < cooldown_seconds:
            # Todavía en cooldown (pedido por JD el 2026-09-17, ver BITACORA.md "Fase
            # 8") — se descarta este frame para visión sin correr el detector. El
            # conteo de fps/bytes de Fase 6/7 (on_frame) no se ve afectado.
            return
        last_processed_at = now

        loop = asyncio.get_running_loop()
        # La inferencia YOLO+OpenCV es CPU-bound y no async — se corre en un thread
        # aparte para no bloquear el loop mientras dura (cientos de ms, ver benchmark
        # de Fase 8 en BITACORA.md).
        result = await loop.run_in_executor(None, detector.detect, jpeg_bytes)

        if result.gesture is not None:
            logger.info(
                "Gesto detectado: %s (confianza=%.2f, dedos_extendidos=%d)",
                result.gesture, result.confidence, result.extended_fingers,
            )
        else:
            logger.debug("Sin gesto reconocido en este frame (dedos_extendidos=%d)", result.extended_fingers)

        line = detector.format_line(result)
        if line is not None:
            await TcpServer.send_line(writer, line)

    return on_jpeg_frame


def _warm_up(detector: GestureDetector) -> None:
    # La primera inferencia real de YOLO es notablemente más lenta que las siguientes
    # (~1.8s vs. ~0.44s medido en esta Pi, ver BITACORA.md "Fase 8") — se paga ese costo
    # una vez al arrancar, en vez de en el primer gesto real de un cliente.
    blank_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    ok, jpeg_bytes = cv2.imencode(".jpg", blank_frame)
    if ok:
        detector.detect(jpeg_bytes.tobytes())
        logger.info("Detector de gestos precalentado (warmup de arranque)")


async def run() -> None:
    detector = GestureDetector(config.YOLO_MODEL, config.MIN_CONFIDENCE)
    _warm_up(detector)
    server = TcpServer(
        config.BRIDGE_HOST,
        config.BRIDGE_PORT,
        watchdog_factory=_make_watchdog,
        on_jpeg_frame=_make_on_jpeg_frame(detector, config.GESTURE_COOLDOWN_SECONDS),
    )
    await server.start()
    await server.serve_forever()


def main() -> None:
    configure_logging(config.LOG_LEVEL, config.LOG_FILE, config.LOG_RETENTION_DAYS)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("cva_gesture_bridge detenido por el usuario")


if __name__ == "__main__":
    main()
