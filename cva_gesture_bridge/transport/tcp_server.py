"""Servidor TCP crudo del bridge — protocolo verificado contra el cliente Rust
(reference/session.rs, reference/bridge.rs). Ver CLAUDE.md sección 4.

Fase 6: solo acepta la conexión persistente, lee frames y loguea conteo/tamaño/fps.
Nada de visión ni de instrucciones todavía.
"""

import asyncio
import logging
import struct
import time
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

FRAME_LENGTH_BYTES = 4


class ConnectionStats:
    """Contadores de una conexión persistente: frames, bytes, fps y latencia real medidos."""

    def __init__(self) -> None:
        self.total_frames = 0
        self.total_bytes = 0
        self.last_latency_ms = 0.0
        self._start = time.monotonic()
        self._last_frame_at = self._start

    def record_frame(self, size: int) -> None:
        now = time.monotonic()
        # Latencia real entre frames consecutivos (Fase 7 — checkpoint end-to-end).
        # En el primer frame no hay frame anterior con qué comparar: se mide desde
        # el inicio de la conexión.
        self.last_latency_ms = (now - self._last_frame_at) * 1000.0
        self._last_frame_at = now
        self.total_frames += 1
        self.total_bytes += size

    @property
    def fps(self) -> float:
        elapsed = time.monotonic() - self._start
        return self.total_frames / elapsed if elapsed > 0 else 0.0


class TcpServer:
    """Servidor asyncio para el protocolo crudo de frames del cliente CVA."""

    def __init__(
        self,
        host: str,
        port: int,
        watchdog_factory: Optional[Callable[[], object]] = None,
        on_frame: Optional[Callable[[str, int, float, float], None]] = None,
        on_jpeg_frame: Optional[
            Callable[[bytes, asyncio.StreamWriter], Awaitable[None]]
        ] = None,
    ) -> None:
        self._host = host
        self._port = port
        self._watchdog_factory = watchdog_factory
        self._on_frame = on_frame
        # Fase 8: callback async separado (en vez de ampliar on_frame de nuevo) para no
        # tocar la firma ya usada por los tests de Fase 6/7 — recibe los bytes JPEG
        # crudos y el writer, para que quien lo use pueda llamar a send_line().
        self._on_jpeg_frame = on_jpeg_frame
        self._server: Optional[asyncio.base_events.Server] = None

    @property
    def port(self) -> int:
        assert self._server is not None, "El servidor no ha arrancado (llama a start() primero)"
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self._host, self._port)
        addrs = ", ".join(str(sock.getsockname()) for sock in self._server.sockets)
        logger.info("cva_gesture_bridge escuchando en %s", addrs)

    async def serve_forever(self) -> None:
        assert self._server is not None, "El servidor no ha arrancado (llama a start() primero)"
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        logger.info("Conexión aceptada desde %s", peer)

        watchdog = self._watchdog_factory() if self._watchdog_factory else None
        stats = ConnectionStats()

        try:
            if watchdog is not None:
                watchdog.start()
            while True:
                try:
                    len_bytes = await reader.readexactly(FRAME_LENGTH_BYTES)
                except asyncio.IncompleteReadError as exc:
                    if len(exc.partial) == 0:
                        # Health check (sección 4.2 de CLAUDE.md): conexión abierta y
                        # cerrada sin datos. Tráfico normal, no un error.
                        logger.info("Conexión de %s cerrada sin datos (health check)", peer)
                    else:
                        logger.warning(
                            "Conexión de %s cerrada a mitad de la cabecera de longitud (%d/%d bytes)",
                            peer, len(exc.partial), FRAME_LENGTH_BYTES,
                        )
                    return

                frame_len = struct.unpack(">I", len_bytes)[0]

                try:
                    jpeg_bytes = await reader.readexactly(frame_len)
                except asyncio.IncompleteReadError as exc:
                    logger.warning(
                        "Conexión de %s cerrada a mitad de un frame (%d/%d bytes)",
                        peer, len(exc.partial), frame_len,
                    )
                    return

                stats.record_frame(len(jpeg_bytes))
                if watchdog is not None:
                    watchdog.feed()
                if self._on_frame is not None:
                    self._on_frame(str(peer), len(jpeg_bytes), stats.fps, stats.last_latency_ms)
                if self._on_jpeg_frame is not None:
                    try:
                        await self._on_jpeg_frame(jpeg_bytes, writer)
                    except Exception:
                        logger.exception(
                            "on_jpeg_frame falló procesando un frame de %s — se sigue "
                            "leyendo la conexión, no se corta por un fallo de visión",
                            peer,
                        )
                logger.info(
                    "Frame #%d de %s: %d bytes, fps_real=%.2f, latencia_ms=%.1f",
                    stats.total_frames, peer, len(jpeg_bytes), stats.fps, stats.last_latency_ms,
                )
        finally:
            if watchdog is not None:
                watchdog.stop()
            writer.close()
            try:
                await writer.wait_closed()
            except OSError as exc:
                logger.warning("Error cerrando el socket de %s: %s", peer, exc)
            logger.info(
                "Conexión con %s finalizada. total_frames=%d, total_bytes=%d",
                peer, stats.total_frames, stats.total_bytes,
            )

    @staticmethod
    async def send_line(writer: asyncio.StreamWriter, message: str) -> None:
        """Manda una línea de texto UTF-8 terminada en \\n (sección 4.4). Sin uso en Fase 6."""
        writer.write((message.rstrip("\n") + "\n").encode("utf-8"))
        await writer.drain()
