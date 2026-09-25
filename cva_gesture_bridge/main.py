"""Punto de entrada del bridge.

Fase 8: además de recibir/loguear frames (Fase 6) y medir latencia (Fase 7), corre el
detector de gestos real sobre cada frame y manda el resultado de vuelta al cliente.
"""

import asyncio
import logging
import os
import time

import cv2
import numpy as np

from cva_gesture_bridge import config
from cva_gesture_bridge.logging_setup import configure_logging
from cva_gesture_bridge.transport.tcp_server import TcpServer
from cva_gesture_bridge.transport.watchdog import Watchdog
from cva_gesture_bridge.vision.detector import GestureDetector, GestureStabilizer

logger = logging.getLogger(__name__)


def _on_watchdog_timeout() -> None:
    # Fase 8: todavía no hay actuadores que cortar (eso llega en Fase 9+); el watchdog
    # solo advierte que se dejó de recibir video.
    logger.warning("Watchdog disparado — sin actuadores que cortar todavía (Fase 8)")


def _make_watchdog() -> Watchdog:
    return Watchdog(config.WATCHDOG_TIMEOUT_SECONDS, _on_watchdog_timeout)


def _capture_frame_to_disk(capture_dir: str, jpeg_bytes: bytes, label: str) -> None:
    # CAPTURADOR TEMPORAL — ver nota en config.py y BITACORA.md. Nombre de archivo con
    # milisegundos epoch (único a ~7fps real) + etiqueta del resultado de ESE frame.
    safe_label = label.replace("/", "_").replace(" ", "_")
    filename = f"{int(time.time() * 1000)}__{safe_label}.jpg"
    path = os.path.join(capture_dir, filename)
    try:
        with open(path, "wb") as f:
            f.write(jpeg_bytes)
    except OSError as exc:
        # Errores explícitos, nunca silenciosos (CLAUDE.md sección 6.5) — pero un fallo
        # de captura (disco lleno, permisos) no debe tumbar la sesión de prueba real.
        logger.error("[captura] no se pudo guardar %s: %s", path, exc)


def _make_on_jpeg_frame(
    detector: GestureDetector,
    cooldown_seconds: float,
    stability_window: int,
    stability_min_matches: int,
):
    # last_processed_at y stabilizer viven en este closure, creado una sola vez por
    # arranque del bridge — tanto el cooldown como la ventana de estabilidad son
    # globales al proceso, no por conexión (ver nota en BITACORA.md "Fase 8": para el
    # uso real de este proyecto, una sola conexión persistente por sesión, equivale a
    # cooldown/estabilidad por sesión).
    last_processed_at = None
    stabilizer = GestureStabilizer(window_size=stability_window, min_matches=stability_min_matches)
    capture_dir = config.CAPTURE_FRAMES_DIR  # leído una vez al armar el callback

    async def on_jpeg_frame(jpeg_bytes: bytes, writer: asyncio.StreamWriter) -> None:
        nonlocal last_processed_at
        now = time.monotonic()
        in_cooldown = last_processed_at is not None and (now - last_processed_at) < cooldown_seconds

        if in_cooldown:
            if capture_dir:
                # CAPTURADOR TEMPORAL: este frame no pasa por el detector (cooldown),
                # pero igual se guarda para el banco de MediaPipe — etiquetado
                # "sin_evaluar" (no "sin_gesto") porque la detección real nunca corrió
                # sobre él; mezclar esas dos etiquetas sería un dato falso, no solo
                # impreciso. No cambia nada de la lógica de cooldown existente.
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _capture_frame_to_disk, capture_dir, jpeg_bytes, "sin_evaluar")
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

        if capture_dir:
            # CAPTURADOR TEMPORAL: este frame sí se evaluó -- se guarda con el
            # resultado real de la detección (gesto+confianza, o "sin_gesto" si no se
            # reconoció nada). No altera el resultado ni lo que se manda al cliente.
            label = f"{result.gesture}_{result.confidence:.2f}" if result.gesture is not None else "sin_gesto"
            await loop.run_in_executor(None, _capture_frame_to_disk, capture_dir, jpeg_bytes, label)

        # Fix de falsos positivos sin mano presente (2026-09-18, ver BITACORA.md "Fase
        # 8"): un gesto crudo de un solo frame no se loguea como "Gesto detectado" ni
        # se manda al cliente hasta que aparece al menos `stability_min_matches` veces
        # dentro de las últimas `stability_window` detecciones (ventana deslizante,
        # no una racha exacta — ver GestureStabilizer).
        confirmed_gesture = stabilizer.observe(result.gesture)

        # DIAGNÓSTICO TEMPORAL (2026-09-18, ver BITACORA.md "Fase 8"): a propósito en
        # INFO, no DEBUG, mismo motivo que en detector.py. Revertir a logger.debug una
        # vez resuelta la investigación de por qué dejó de detectar un puño real.
        if result.gesture is not None:
            logger.info(
                "[diag] Gesto candidato: %s (confianza=%.2f, dedos_extendidos=%d, confirmado=%s)",
                result.gesture, result.confidence, result.extended_fingers,
                confirmed_gesture is not None,
            )
        else:
            logger.info("[diag] Sin gesto reconocido en este frame (dedos_extendidos=%d)", result.extended_fingers)

        if confirmed_gesture is None:
            return

        logger.info(
            "Gesto detectado: %s (confianza=%.2f, dedos_extendidos=%d)",
            result.gesture, result.confidence, result.extended_fingers,
        )
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
        # El frame en negro del warmup no es la escena real — sin este reset,
        # MotionGate lo aprendería como "fondo" y el primer frame real completo
        # aparecería como "movimiento" en todas partes (ver BITACORA.md "Fase 8",
        # segundo fix de falsos positivos).
        detector.reset_motion_background()
        logger.info("Detector de gestos precalentado (warmup de arranque)")


def _prepare_capture_dir_if_configured() -> None:
    # CAPTURADOR TEMPORAL (ver config.py) -- se crea la carpeta al arrancar, no de
    # forma perezosa en el primer frame, para poder confirmarla con `ls`/systemctl
    # status inmediatamente después del reinicio (ver BITACORA.md).
    if config.CAPTURE_FRAMES_DIR:
        os.makedirs(config.CAPTURE_FRAMES_DIR, exist_ok=True)
        logger.warning(
            "[CAPTURA TEMPORAL ACTIVA] Guardando copia de cada frame en %s -- "
            "desactivar (unset CVA_CAPTURE_FRAMES_DIR + reinicio) apenas termine la "
            "sesión de prueba. Ver BITACORA.md, spike MediaPipe.",
            config.CAPTURE_FRAMES_DIR,
        )


async def run() -> None:
    _prepare_capture_dir_if_configured()
    detector = GestureDetector(config.YOLO_MODEL, config.MIN_CONFIDENCE)
    _warm_up(detector)
    server = TcpServer(
        config.BRIDGE_HOST,
        config.BRIDGE_PORT,
        watchdog_factory=_make_watchdog,
        on_jpeg_frame=_make_on_jpeg_frame(
            detector,
            config.GESTURE_COOLDOWN_SECONDS,
            config.GESTURE_STABILITY_WINDOW,
            config.GESTURE_STABILITY_MIN_MATCHES,
        ),
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
