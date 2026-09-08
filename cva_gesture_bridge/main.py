"""Punto de entrada del bridge. Fase 6: arranca el servidor TCP y solo loguea."""

import asyncio
import logging

from cva_gesture_bridge import config
from cva_gesture_bridge.transport.tcp_server import TcpServer
from cva_gesture_bridge.transport.watchdog import Watchdog

logger = logging.getLogger(__name__)


def _on_watchdog_timeout() -> None:
    # Fase 6: todavía no hay instrucciones que cortar (eso llega en Fase 8+).
    logger.warning("Watchdog disparado — sin instrucciones que cortar todavía (Fase 6)")


def _make_watchdog() -> Watchdog:
    return Watchdog(config.WATCHDOG_TIMEOUT_SECONDS, _on_watchdog_timeout)


async def run() -> None:
    server = TcpServer(config.BRIDGE_HOST, config.BRIDGE_PORT, watchdog_factory=_make_watchdog)
    await server.start()
    await server.serve_forever()


def main() -> None:
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("cva_gesture_bridge detenido por el usuario")


if __name__ == "__main__":
    main()
