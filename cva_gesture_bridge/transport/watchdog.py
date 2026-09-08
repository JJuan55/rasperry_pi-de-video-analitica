"""Watchdog: dispara un callback si pasa un tiempo sin recibir `feed()`.

Fase 6: solo el mecanismo. El callback todavía no corta ninguna instrucción real (no
existen instrucciones hasta Fase 8+) — solo debe existir y ser testeable de forma aislada.
"""

import asyncio
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class Watchdog:
    def __init__(self, timeout_seconds: float, on_timeout: Callable[[], None]) -> None:
        self._timeout_seconds = timeout_seconds
        self._on_timeout = on_timeout
        self._handle: Optional[asyncio.TimerHandle] = None
        self._tripped = False

    def start(self) -> None:
        self._tripped = False
        self._reschedule()

    def feed(self) -> None:
        if self._handle is not None:
            self._tripped = False
            self._reschedule()

    def stop(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None

    @property
    def tripped(self) -> bool:
        return self._tripped

    def _reschedule(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
        loop = asyncio.get_running_loop()
        self._handle = loop.call_later(self._timeout_seconds, self._fire)

    def _fire(self) -> None:
        self._handle = None
        self._tripped = True
        logger.warning(
            "Watchdog: sin frames en %.1fs, dejando de emitir instrucciones",
            self._timeout_seconds,
        )
        self._on_timeout()
