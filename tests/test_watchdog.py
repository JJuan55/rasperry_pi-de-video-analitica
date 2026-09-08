import asyncio

from cva_gesture_bridge.transport.watchdog import Watchdog


async def test_watchdog_fires_after_timeout_without_feed():
    fired = asyncio.Event()
    watchdog = Watchdog(0.05, fired.set)

    watchdog.start()

    await asyncio.wait_for(fired.wait(), timeout=1.0)
    assert watchdog.tripped


async def test_watchdog_does_not_fire_while_fed_in_time():
    fired = asyncio.Event()
    watchdog = Watchdog(0.15, fired.set)
    watchdog.start()

    for _ in range(5):
        await asyncio.sleep(0.05)
        watchdog.feed()

    assert not fired.is_set()
    assert not watchdog.tripped
    watchdog.stop()


async def test_watchdog_stop_prevents_further_firing():
    fired = asyncio.Event()
    watchdog = Watchdog(0.05, fired.set)
    watchdog.start()
    watchdog.stop()

    await asyncio.sleep(0.15)

    assert not fired.is_set()
    assert not watchdog.tripped


async def test_watchdog_can_restart_after_stop():
    fired = asyncio.Event()
    watchdog = Watchdog(0.05, fired.set)
    watchdog.start()
    watchdog.stop()

    watchdog.start()
    await asyncio.wait_for(fired.wait(), timeout=1.0)
    assert watchdog.tripped
