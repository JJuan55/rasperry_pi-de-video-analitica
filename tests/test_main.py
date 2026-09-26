"""Tests del wiring de main.py que sí es testeable sin cámara/YOLO real: el cooldown
entre gestos procesados (pedido por JD el 2026-09-17) y la confirmación por
estabilidad temporal antes de loguear/mandar un gesto (pedido por JD el 2026-09-18,
fix de falsos positivos sin mano presente — y su rediseño a ventana deslizante el
mismo día, tras evidencia real de que una racha exacta casi nunca se completaba). Ver
BITACORA.md "Fase 8". Usa un detector falso, no el GestureDetector real — no depende
de pesos ni de inferencia.
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


# --- Cooldown (stability_window=1, stability_min_matches=1: cualquier detección
# confirma de inmediato, para aislar el comportamiento del cooldown del de la
# estabilidad temporal) ---


async def test_first_frame_is_always_processed():
    detector = _StubDetector([GestureResult("puño_cerrado", 0.9, 0)])
    on_jpeg_frame = main_module._make_on_jpeg_frame(
        detector, cooldown_seconds=5.0, stability_window=1, stability_min_matches=1
    )

    await on_jpeg_frame(b"frame", _FakeWriter())

    assert detector.calls == 1


async def test_frame_within_cooldown_window_is_skipped_without_calling_detector():
    detector = _StubDetector([GestureResult("puño_cerrado", 0.9, 0)])
    on_jpeg_frame = main_module._make_on_jpeg_frame(
        detector, cooldown_seconds=5.0, stability_window=1, stability_min_matches=1
    )
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
    on_jpeg_frame = main_module._make_on_jpeg_frame(
        detector, cooldown_seconds=0.15, stability_window=1, stability_min_matches=1
    )
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
    on_jpeg_frame = main_module._make_on_jpeg_frame(
        detector, cooldown_seconds=5.0, stability_window=1, stability_min_matches=1
    )
    writer = _FakeWriter()

    await on_jpeg_frame(b"frame1", writer)
    await on_jpeg_frame(b"frame2", writer)  # dentro del cooldown -> se descarta

    assert detector.calls == 1
    assert writer.lines == []


# --- Estabilidad temporal, ventana deslizante (cooldown_seconds=0: sin gate de
# cooldown, para aislar el comportamiento del stabilizer) ---


async def test_single_gesture_below_min_matches_is_not_sent():
    detector = _StubDetector([GestureResult("puño_cerrado", 0.9, 0)])
    on_jpeg_frame = main_module._make_on_jpeg_frame(
        detector, cooldown_seconds=0.0, stability_window=3, stability_min_matches=2
    )
    writer = _FakeWriter()

    await on_jpeg_frame(b"frame1", writer)

    assert detector.calls == 1
    assert writer.lines == []  # todavía no aparece las veces necesarias en la ventana


async def test_gesture_seen_twice_in_window_gets_sent_once_confirmed():
    detector = _StubDetector([GestureResult("puño_cerrado", 0.9, 0)] * 2)
    on_jpeg_frame = main_module._make_on_jpeg_frame(
        detector, cooldown_seconds=0.0, stability_window=3, stability_min_matches=2
    )
    writer = _FakeWriter()

    await on_jpeg_frame(b"frame1", writer)
    assert writer.lines == []  # 1 de 2 necesarias

    await on_jpeg_frame(b"frame2", writer)
    assert len(writer.lines) == 1  # 2 de 2, confirmado


async def test_one_noisy_frame_in_between_does_not_prevent_confirmation():
    # Este es el patrón real que se vio en producción (ver BITACORA.md "Fase 8"): el
    # mismo puño sostenido, con un frame de por medio clasificado distinto por ruido.
    # Con la racha exacta anterior esto nunca confirmaba; con la ventana sí.
    detector = _StubDetector([
        GestureResult("puño_cerrado", 0.9, 0),
        GestureResult("dedo_pulgar", 0.6, 1),  # ruido de un frame
        GestureResult("puño_cerrado", 0.9, 0),
    ])
    on_jpeg_frame = main_module._make_on_jpeg_frame(
        detector, cooldown_seconds=0.0, stability_window=3, stability_min_matches=2
    )
    writer = _FakeWriter()

    for _ in range(3):
        await on_jpeg_frame(b"frame", writer)

    assert len(writer.lines) == 1
    assert "puño_cerrado".encode("utf-8") in writer.lines[0]


async def test_three_different_gestures_never_confirm():
    detector = _StubDetector([
        GestureResult("puño_cerrado", 0.9, 0),
        GestureResult("dedo_pulgar", 0.6, 1),
        GestureResult("palma_abierta", 0.5, 5),
    ])
    on_jpeg_frame = main_module._make_on_jpeg_frame(
        detector, cooldown_seconds=0.0, stability_window=3, stability_min_matches=2
    )
    writer = _FakeWriter()

    for _ in range(3):
        await on_jpeg_frame(b"frame", writer)

    assert writer.lines == []


async def test_frame_without_gesture_does_not_confirm_but_does_not_erase_the_window():
    detector = _StubDetector([
        GestureResult("puño_cerrado", 0.9, 0),
        GestureResult(None, 0.0, 2),  # ruido de un frame sin gesto
        GestureResult("puño_cerrado", 0.9, 0),
    ])
    on_jpeg_frame = main_module._make_on_jpeg_frame(
        detector, cooldown_seconds=0.0, stability_window=3, stability_min_matches=2
    )
    writer = _FakeWriter()

    for _ in range(3):
        await on_jpeg_frame(b"frame", writer)

    # El puño_cerrado del primer frame sigue en la ventana (tamaño 3) -> 2 de 2.
    assert len(writer.lines) == 1


# --- Capturador temporal de frames (2026-09-25, ver BITACORA.md "Fase 8" / spike
# MediaPipe) -- config.CAPTURE_FRAMES_DIR se parchea vía monkeypatch, nunca vía env
# var real, para no tocar el estado del proceso de test. ---


async def test_capture_disabled_by_default_writes_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module.config, "CAPTURE_FRAMES_DIR", None)
    detector = _StubDetector([GestureResult("puño_cerrado", 0.9, 0)])
    on_jpeg_frame = main_module._make_on_jpeg_frame(
        detector, cooldown_seconds=0.0, stability_window=1, stability_min_matches=1
    )

    await on_jpeg_frame(b"frame", _FakeWriter())

    assert list(tmp_path.iterdir()) == []


async def test_capture_saves_processed_frame_with_gesture_label(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module.config, "CAPTURE_FRAMES_DIR", str(tmp_path))
    detector = _StubDetector([GestureResult("puño_cerrado", 0.87, 0)])
    on_jpeg_frame = main_module._make_on_jpeg_frame(
        detector, cooldown_seconds=0.0, stability_window=1, stability_min_matches=1
    )

    await on_jpeg_frame(b"contenido-jpeg-falso", _FakeWriter())

    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].name.endswith("__puño_cerrado_0.87.jpg")
    assert files[0].read_bytes() == b"contenido-jpeg-falso"


async def test_capture_saves_processed_frame_without_gesture_as_sin_gesto(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module.config, "CAPTURE_FRAMES_DIR", str(tmp_path))
    detector = _StubDetector([GestureResult(None, 0.0, 2)])
    on_jpeg_frame = main_module._make_on_jpeg_frame(
        detector, cooldown_seconds=0.0, stability_window=1, stability_min_matches=1
    )

    await on_jpeg_frame(b"frame", _FakeWriter())

    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].name.endswith("__sin_gesto.jpg")


async def test_capture_saves_cooldown_skipped_frame_as_sin_evaluar_not_sin_gesto(tmp_path, monkeypatch):
    # El frame saltado por cooldown nunca pasó por el detector -- etiquetarlo
    # "sin_gesto" sería un dato falso (implicaría que sí se evaluó).
    monkeypatch.setattr(main_module.config, "CAPTURE_FRAMES_DIR", str(tmp_path))
    detector = _StubDetector([GestureResult("puño_cerrado", 0.9, 0)])
    on_jpeg_frame = main_module._make_on_jpeg_frame(
        detector, cooldown_seconds=5.0, stability_window=1, stability_min_matches=1
    )

    await on_jpeg_frame(b"frame1", _FakeWriter())  # se procesa, consume el único resultado
    await on_jpeg_frame(b"frame2", _FakeWriter())  # cae en cooldown

    files = [f.name for f in tmp_path.iterdir()]
    assert len(files) == 2
    assert any(f.endswith("__puño_cerrado_0.90.jpg") for f in files)
    assert any(f.endswith("__sin_evaluar.jpg") for f in files)
    assert detector.calls == 1  # el segundo frame nunca llamó a detect()
