"""Watchdog: re-armado después del primer disparo (rama fix-watchdog-rearm).

Defecto: `_fire()` dejaba `self._handle = None` y `feed()` solo reprogramaba si
`_handle` no era None, así que después del primer disparo el watchdog no volvía a
armarse en esa conexión (visto en producción el 23/10: cinco huecos de más de 5 s y un
solo aviso). Tiempos cortos: timeout 0,05 s, esperas de 0,15 s como máximo.
"""

import asyncio
import logging
import struct

from cva_gesture_bridge.transport.tcp_server import TcpServer
from cva_gesture_bridge.transport.watchdog import Watchdog

TIMEOUT = 0.05
SILENCE = 0.15  # 3 veces el timeout


def _counter():
    calls = []
    return calls, (lambda: calls.append(1))


async def test_i_fires_once_after_silence():
    calls, cb = _counter()
    wd = Watchdog(TIMEOUT, cb)
    wd.start()
    await asyncio.sleep(SILENCE)
    assert len(calls) == 1
    wd.stop()


async def test_ii_feed_after_firing_rearms_and_second_silence_fires_again():
    calls, cb = _counter()
    wd = Watchdog(TIMEOUT, cb)
    wd.start()
    await asyncio.sleep(SILENCE)
    assert len(calls) == 1
    wd.feed()  # llega un cuadro después del hueco
    await asyncio.sleep(SILENCE)
    assert len(calls) == 2
    wd.stop()


async def test_iii_without_feed_it_does_not_fire_repeatedly():
    calls, cb = _counter()
    wd = Watchdog(TIMEOUT, cb)
    wd.start()
    await asyncio.sleep(SILENCE * 2)  # 6 timeouts de silencio
    assert len(calls) == 1
    wd.stop()


async def test_iv_feed_before_start_does_not_arm():
    calls, cb = _counter()
    wd = Watchdog(TIMEOUT, cb)
    wd.feed()
    await asyncio.sleep(SILENCE)
    assert calls == []
    assert not wd.tripped


async def test_v_feed_after_stop_does_not_arm():
    calls, cb = _counter()
    wd = Watchdog(TIMEOUT, cb)
    wd.start()
    wd.stop()
    wd.feed()
    await asyncio.sleep(SILENCE)
    assert calls == []
    # también después de haber disparado: stop() es definitivo hasta el próximo start()
    wd.start()
    await asyncio.sleep(SILENCE)
    assert len(calls) == 1
    wd.stop()
    wd.feed()
    await asyncio.sleep(SILENCE)
    assert len(calls) == 1


async def test_vi_tripped_true_on_fire_and_false_on_next_feed():
    calls, cb = _counter()
    wd = Watchdog(TIMEOUT, cb)
    wd.start()
    assert not wd.tripped
    await asyncio.sleep(SILENCE)
    assert wd.tripped
    wd.feed()
    assert not wd.tripped
    await asyncio.sleep(SILENCE)
    assert wd.tripped  # el segundo silencio lo vuelve a marcar
    wd.stop()


async def test_log_message_unchanged_on_each_firing(caplog):
    calls, cb = _counter()
    wd = Watchdog(TIMEOUT, cb)
    with caplog.at_level(logging.WARNING, logger="cva_gesture_bridge.transport.watchdog"):
        wd.start()
        await asyncio.sleep(SILENCE)
        wd.feed()
        await asyncio.sleep(SILENCE)
    wd.stop()
    msgs = [r.getMessage() for r in caplog.records]
    assert msgs == ["Watchdog: sin frames en 0.1s, dejando de emitir instrucciones"] * 2



async def test_a_start_twice_leaves_a_single_timer_and_fires_once():
    calls, cb = _counter()
    wd = Watchdog(TIMEOUT, cb)
    wd.start()
    first = wd._handle
    wd.start()
    assert first.cancelled()          # el segundo start() reemplaza el temporizador
    assert wd._handle is not first
    await asyncio.sleep(SILENCE)
    assert len(calls) == 1
    wd.stop()


async def test_b_start_after_stop_arms_again_and_fires_once():
    calls, cb = _counter()
    wd = Watchdog(TIMEOUT, cb)
    wd.start()
    wd.stop()
    wd.start()
    await asyncio.sleep(SILENCE)
    assert len(calls) == 1
    assert wd.tripped
    wd.stop()


async def test_c_feed_from_inside_on_timeout_rearms_and_fires_again_each_timeout():
    """Con asyncio (un solo hilo), un feed() "mientras on_timeout no terminó" solo
    puede venir del propio on_timeout. Con este arreglo ese feed() cuenta como un
    feed() posterior al disparo: re-arma y pone tripped=False antes de que el callback
    termine, y si el callback alimenta en cada disparo, dispara en cada timeout. En el
    código original ese feed() no hacía nada. Hoy ningún llamador lo hace
    (main._on_watchdog_timeout solo registra un aviso; tcp_server alimenta desde el
    bucle, nunca dentro del callback). Esta prueba fija el comportamiento."""
    calls, seen = [], {}

    def on_timeout():
        calls.append(1)
        seen["tripped_dentro"] = wd.tripped
        wd.feed()
        seen["tripped_tras_feed"] = wd.tripped

    wd = Watchdog(TIMEOUT, on_timeout)
    wd.start()
    await asyncio.sleep(SILENCE)
    assert seen == {"tripped_dentro": True, "tripped_tras_feed": False}
    assert len(calls) >= 2            # cada disparo re-arma: vuelve a disparar
    wd.stop()
    n = len(calls)
    await asyncio.sleep(SILENCE)
    assert len(calls) == n            # stop() lo corta

async def test_connection_with_long_gap_in_the_middle_is_watched_again():
    """Parte C: tcp_server.py arranca el watchdog por conexión, lo alimenta en cada
    cuadro y lo detiene al cerrar. Una conexión con un hueco largo en medio tiene que
    volver a quedar vigilada después del hueco."""
    calls, cb = _counter()
    watchdogs = []

    def factory():
        wd = Watchdog(TIMEOUT, cb)
        watchdogs.append(wd)
        return wd

    server = TcpServer("127.0.0.1", 0, watchdog_factory=factory)
    await server.start()
    task = asyncio.create_task(server.serve_forever())
    writer = None
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        frame = struct.pack(">I", 5) + b"x" * 5
        writer.write(frame)
        await writer.drain()
        await asyncio.sleep(SILENCE)          # hueco largo en medio de la conexión
        assert len(calls) == 1
        writer.write(frame)                   # vuelven los cuadros
        await writer.drain()
        await asyncio.sleep(0.02)
        assert not watchdogs[0].tripped       # re-armado por el cuadro
        await asyncio.sleep(SILENCE)          # otro hueco: debe volver a avisar
        assert len(calls) == 2
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(SILENCE)          # cerrada: stop() en el finally, sin más avisos
        assert len(calls) == 2
        assert len(watchdogs) == 1
    finally:
        # Cerrar el cliente siempre: si una aserción falla antes, server.close() esperaría
        # para siempre a que se cierre esa conexión (Server.wait_closed en Python 3.12+).
        if writer is not None and not writer.is_closing():
            writer.close()
            await writer.wait_closed()
        task.cancel()
        await server.close()
