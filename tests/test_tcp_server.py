import asyncio
import struct

from cva_gesture_bridge.transport.tcp_server import TcpServer


class DummyWatchdog:
    """Watchdog falso para verificar que el servidor lo alimenta en cada frame."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.feeds = 0

    def start(self) -> None:
        self.started = True

    def feed(self) -> None:
        self.feeds += 1

    def stop(self) -> None:
        self.stopped = True


async def _running_server(**kwargs):
    server = TcpServer("127.0.0.1", 0, **kwargs)
    await server.start()
    task = asyncio.create_task(server.serve_forever())
    return server, task


async def test_health_check_connect_and_close_without_data_survives():
    server, task = await _running_server()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.05)
        # El servidor debe seguir vivo y aceptando conexiones nuevas tras el health check.
        reader2, writer2 = await asyncio.open_connection("127.0.0.1", server.port)
        writer2.close()
        await writer2.wait_closed()
    finally:
        task.cancel()
        await server.close()


async def test_receives_and_counts_a_single_frame():
    received = []

    def on_frame(peer, size, fps, latency_ms):
        received.append((size, fps, latency_ms))

    server, task = await _running_server(on_frame=on_frame)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        payload = b"\xff\xd8\xff" + (b"x" * 200)  # JPEG-like fake bytes
        writer.write(struct.pack(">I", len(payload)) + payload)
        await writer.drain()
        await asyncio.sleep(0.1)

        assert len(received) == 1
        size, fps, latency_ms = received[0]
        assert size == len(payload)
        assert fps > 0
        assert latency_ms >= 0

        writer.close()
        await writer.wait_closed()
    finally:
        task.cancel()
        await server.close()


async def test_receives_multiple_frames_in_sequence_on_same_connection():
    received = []

    def on_frame(peer, size, fps, latency_ms):
        received.append(size)

    server, task = await _running_server(on_frame=on_frame)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        for n in range(1, 4):
            payload = b"f" * (10 * n)
            writer.write(struct.pack(">I", len(payload)) + payload)
        await writer.drain()
        await asyncio.sleep(0.1)

        assert received == [10, 20, 30]

        writer.close()
        await writer.wait_closed()
    finally:
        task.cancel()
        await server.close()


async def test_latency_ms_measures_real_gap_between_consecutive_frames():
    latencies = []

    def on_frame(peer, size, fps, latency_ms):
        latencies.append(latency_ms)

    server, task = await _running_server(on_frame=on_frame)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        payload = b"x" * 5

        writer.write(struct.pack(">I", len(payload)) + payload)
        await writer.drain()
        await asyncio.sleep(0.1)  # gap real y medible antes del segundo frame

        writer.write(struct.pack(">I", len(payload)) + payload)
        await writer.drain()
        await asyncio.sleep(0.05)

        assert len(latencies) == 2
        # El segundo frame debe reflejar el ~0.1s de espera real entre ambos.
        assert latencies[1] >= 80.0

        writer.close()
        await writer.wait_closed()
    finally:
        task.cancel()
        await server.close()


async def test_watchdog_is_started_and_fed_per_frame_then_stopped_on_disconnect():
    watchdogs = []

    def watchdog_factory():
        wd = DummyWatchdog()
        watchdogs.append(wd)
        return wd

    server, task = await _running_server(watchdog_factory=watchdog_factory)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        payload = b"x" * 5
        writer.write(struct.pack(">I", len(payload)) + payload)
        writer.write(struct.pack(">I", len(payload)) + payload)
        await writer.drain()
        await asyncio.sleep(0.1)

        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.05)

        assert len(watchdogs) == 1
        assert watchdogs[0].started
        assert watchdogs[0].feeds == 2
        assert watchdogs[0].stopped
    finally:
        task.cancel()
        await server.close()


async def test_connection_closed_mid_frame_is_handled_without_crashing():
    server, task = await _running_server()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        # Anuncia un frame de 100 bytes pero solo manda 10 y cierra.
        writer.write(struct.pack(">I", 100) + b"x" * 10)
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.05)

        # El servidor debe seguir vivo tras el corte abrupto.
        reader2, writer2 = await asyncio.open_connection("127.0.0.1", server.port)
        writer2.close()
        await writer2.wait_closed()
    finally:
        task.cancel()
        await server.close()


async def test_on_jpeg_frame_receives_bytes_and_can_reply_with_send_line():
    # Fase 8: on_jpeg_frame es el enganche real de visión, pero este test no depende
    # de YOLO/OpenCV — usa un callback falso para probar solo el wiring de tcp_server.
    received_frames = []

    async def on_jpeg_frame(jpeg_bytes, writer):
        received_frames.append(jpeg_bytes)
        await TcpServer.send_line(writer, "gesto: dedo_anular, confianza: 0.90")

    server, task = await _running_server(on_jpeg_frame=on_jpeg_frame)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        payload = b"\xff\xd8\xff" + (b"x" * 50)
        writer.write(struct.pack(">I", len(payload)) + payload)
        await writer.drain()

        line = await asyncio.wait_for(reader.readline(), timeout=1.0)
        assert line == b"gesto: dedo_anular, confianza: 0.90\n"
        assert received_frames == [payload]

        writer.close()
        await writer.wait_closed()
    finally:
        task.cancel()
        await server.close()


async def test_on_jpeg_frame_exception_is_logged_and_connection_keeps_working():
    async def on_jpeg_frame(jpeg_bytes, writer):
        raise RuntimeError("fallo simulado del detector")

    server, task = await _running_server(on_jpeg_frame=on_jpeg_frame)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        payload = b"x" * 10
        writer.write(struct.pack(">I", len(payload)) + payload)
        writer.write(struct.pack(">I", len(payload)) + payload)
        await writer.drain()
        await asyncio.sleep(0.1)

        # La conexión debe seguir viva pese al fallo del callback de visión.
        assert not writer.is_closing()

        writer.close()
        await writer.wait_closed()
    finally:
        task.cancel()
        await server.close()


async def test_send_line_writes_utf8_terminated_in_newline():
    received = bytearray()

    async def collector(reader, writer):
        data = await reader.readline()
        received.extend(data)
        writer.close()

    listener = await asyncio.start_server(collector, "127.0.0.1", 0)
    port = listener.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await TcpServer.send_line(writer, "gesto: dedo anular, instruccion: mover adelante")
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.05)
    finally:
        listener.close()
        await listener.wait_closed()

    assert received == b"gesto: dedo anular, instruccion: mover adelante\n"
