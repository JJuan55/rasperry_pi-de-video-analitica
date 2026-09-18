"""Tests del wiring de main.py que sí es testeable sin cámara/YOLO real: el cooldown
entre gestos procesados (pedido por JD el 2026-09-17) y la confirmación por
estabilidad temporal antes de loguear/mandar un gesto (pedido por JD el 2026-09-18,
fix de falsos positivos sin mano presente). Ver BITACORA.md "Fase 8". Usa un detector
falso, no el GestureDetector real — no depende de pesos ni de inferencia.
"""

import asyncio

from cva_gesture_bridge import main as main_module
from cva_gesture_bridge.vision.detector import GestureResult


class _StubDetector:
    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    def detect(self, jpeg_bytes):
        self.calls += 1
        return self._results.pop(0)

    def format_line(self, result):
        if result.gesture is None:
            return None
        return f"gesto: {result.gesture}, confianza: {result.confidence:.2f}"


class _FakeWriter:
    def __init__(self):
        self.lines = []

    def write(self, data):
        self.lines.append(data)

    async def drain(self):
        pass


# --- Cooldown (stability_streak=1: cualquier detección confirma de inmediato, para
# aislar el comportamiento del cooldown del de la estabilidad temporal) ---


async def test_first_frame_is_always_processed():
    detector = _StubDetector([GestureResult("puño_cerrado", 0.9, 0)])
    on_jpeg_frame = main_module._make_on_jpeg_frame(detector, cooldown_seconds=5.0, stability_streak=1)

    await on_jpeg_frame(b"frame", _FakeWriter())

    assert detector.calls == 1


async def test_frame_within_cooldown_window_is_skipped_without_calling_detector():
    detector = _StubDetector([GestureResult("puño_cerrado", 0.9, 0)])
    on_jpeg_frame = main_module._make_on_jpeg_frame(detector, cooldown_seconds=5.0, stability_streak=1)
    writer = _FakeWriter()

    await on_jpeg_frame(b"frame1", writer)
    await on_jpeg_frame(b"frame2", writer)  # llega casi de inmediato

    assert detector.calls == 1
    assert len(writer.lines) == 1


async def test_frame_after_cooldown_elapses_is_processed_again():
    detector = _StubDetector([
        GestureResult("puño_cerrado", 0.9, 0),
        GestureResult("palma_abierta", 0.9, 5),
    ])
    on_jpeg_frame = main_module._make_on_jpeg_frame(detector, cooldown_seconds=0.15, stability_streak=1)
    writer = _FakeWriter()

    await on_jpeg_frame(b"frame1", writer)
    await asyncio.sleep(0.2)  # pasa el cooldown
    await on_jpeg_frame(b"frame2", writer)

    assert detector.calls == 2
    assert len(writer.lines) == 2


async def test_cooldown_counts_from_last_processed_attempt_even_without_gesture():
    # El cooldown se cuenta desde el último frame *procesado* (se haya reconocido un
    # gesto o no), no solo desde el último gesto mandado al cliente.
    detector = _StubDetector([
        GestureResult(None, 0.0, 2),  # sin gesto reconocido, pero sí "procesado"
        GestureResult("puño_cerrado", 0.9, 0),
    ])
    on_jpeg_frame = main_module._make_on_jpeg_frame(detector, cooldown_seconds=5.0, stability_streak=1)
    writer = _FakeWriter()

    await on_jpeg_frame(b"frame1", writer)
    await on_jpeg_frame(b"frame2", writer)  # dentro del cooldown -> se descarta

    assert detector.calls == 1
    assert writer.lines == []


# --- Estabilidad temporal (cooldown_seconds=0: sin gate de cooldown, para aislar el
# comportamiento del stabilizer) ---


async def test_single_gesture_below_streak_is_not_sent():
    detector = _StubDetector([GestureResult("puño_cerrado", 0.9, 0)])
    on_jpeg_frame = main_module._make_on_jpeg_frame(detector, cooldown_seconds=0.0, stability_streak=3)
    writer = _FakeWriter()

    await on_jpeg_frame(b"frame1", writer)

    assert detector.calls == 1
    assert writer.lines == []  # todavía no se repitió las veces necesarias


async def test_gesture_repeated_enough_times_gets_sent_once_confirmed():
    detector = _StubDetector([GestureResult("puño_cerrado", 0.9, 0)] * 3)
    on_jpeg_frame = main_module._make_on_jpeg_frame(detector, cooldown_seconds=0.0, stability_streak=3)
    writer = _FakeWriter()

    await on_jpeg_frame(b"frame1", writer)
    await on_jpeg_frame(b"frame2", writer)
    assert writer.lines == []  # streak=2, todavía no confirma

    await on_jpeg_frame(b"frame3", writer)
    assert len(writer.lines) == 1  # streak=3, confirmado


async def test_gesture_that_changes_before_confirming_resets_the_streak():
    detector = _StubDetector([
        GestureResult("puño_cerrado", 0.9, 0),
        GestureResult("puño_cerrado", 0.9, 0),
        GestureResult("palma_abierta", 0.9, 5),  # cambia justo antes de confirmar
        GestureResult("palma_abierta", 0.9, 5),
    ])
    on_jpeg_frame = main_module._make_on_jpeg_frame(detector, cooldown_seconds=0.0, stability_streak=3)
    writer = _FakeWriter()

    for _ in range(4):
        await on_jpeg_frame(b"frame", writer)

    assert writer.lines == []  # ninguna racha llegó a 3 seguidas


async def test_frame_without_gesture_resets_the_streak():
    detector = _StubDetector([
        GestureResult("puño_cerrado", 0.9, 0),
        GestureResult("puño_cerrado", 0.9, 0),
        GestureResult(None, 0.0, 2),  # ruido de un frame -> corta la racha
        GestureResult("puño_cerrado", 0.9, 0),
        GestureResult("puño_cerrado", 0.9, 0),
    ])
    on_jpeg_frame = main_module._make_on_jpeg_frame(detector, cooldown_seconds=0.0, stability_streak=3)
    writer = _FakeWriter()

    for _ in range(5):
        await on_jpeg_frame(b"frame", writer)

    assert writer.lines == []  # la racha se cortó antes de llegar a 3
