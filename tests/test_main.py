"""Tests del wiring de main.py que sí es testeable sin cámara/YOLO real: el cooldown
entre gestos procesados (pedido por JD el 2026-09-17), la confirmación por
estabilidad temporal con histéresis (Fase C2), y el envío al cliente SOLO del gesto
ya CONFIRMADO, solo cuando cambia, con un evento explícito de liberación (fix
2026-10, ver BITACORA.md "Fase C2 — corrección antes de Fase D": `main.py` mandaba
el resultado CRUDO de cada frame, no el confirmado, y mandaba una línea por frame
mientras se sostenía un gesto en vez de una sola vez). Usa un detector falso, no el
GestureDetector real — no depende de pesos ni de inferencia.
"""

import asyncio

from cva_gesture_bridge import main as main_module
from cva_gesture_bridge.vision.detector import RELEASE_LINE, GestureResult


class _StubDetector:
    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    def detect(self, jpeg_bytes):
        self.calls += 1
        return self._results.pop(0)

    def format_line(self, gesture, confidence):
        # Firma real desde Fase C2: gesto/confianza sueltos (el gesto CONFIRMADO),
        # no un GestureResult crudo -- ver detector.py.
        if gesture is None:
            return None
        return f"gesto: {gesture}, confianza: {confidence:.2f}"


class _FakeWriter:
    def __init__(self):
        self.lines = []

    def write(self, data):
        self.lines.append(data)

    async def drain(self):
        pass


def _make_on_jpeg_frame(
    detector,
    cooldown_seconds=0.0,
    stability_window=3,
    stability_min_matches=2,
    stability_release_after_misses=10,
    stability_release_after_misses_no_hand=2,
):
    return main_module._make_on_jpeg_frame(
        detector,
        cooldown_seconds=cooldown_seconds,
        stability_window=stability_window,
        stability_min_matches=stability_min_matches,
        stability_release_after_misses=stability_release_after_misses,
        stability_release_after_misses_no_hand=stability_release_after_misses_no_hand,
    )


# --- Cooldown (stability_window=1, stability_min_matches=1: cualquier detección
# confirma de inmediato, para aislar el comportamiento del cooldown del de la
# estabilidad temporal) ---


async def test_first_frame_is_always_processed():
    detector = _StubDetector([GestureResult("puño_cerrado", 0.9, 0)])
    on_jpeg_frame = _make_on_jpeg_frame(detector, cooldown_seconds=5.0, stability_window=1, stability_min_matches=1)

    await on_jpeg_frame(b"frame", _FakeWriter())

    assert detector.calls == 1


async def test_frame_within_cooldown_window_is_skipped_without_calling_detector():
    detector = _StubDetector([GestureResult("puño_cerrado", 0.9, 0)])
    on_jpeg_frame = _make_on_jpeg_frame(detector, cooldown_seconds=5.0, stability_window=1, stability_min_matches=1)
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
    on_jpeg_frame = _make_on_jpeg_frame(detector, cooldown_seconds=0.15, stability_window=1, stability_min_matches=1)
    writer = _FakeWriter()

    await on_jpeg_frame(b"frame1", writer)
    await asyncio.sleep(0.2)  # pasa el cooldown
    await on_jpeg_frame(b"frame2", writer)

    assert detector.calls == 2
    assert len(writer.lines) == 2  # dos gestos DISTINTOS -> dos envíos


async def test_cooldown_counts_from_last_processed_attempt_even_without_gesture():
    # El cooldown se cuenta desde el último frame *procesado* (se haya reconocido un
    # gesto o no), no solo desde el último gesto mandado al cliente.
    detector = _StubDetector([
        GestureResult(None, 0.0, 2),  # sin gesto reconocido, pero sí "procesado"
        GestureResult("puño_cerrado", 0.9, 0),
    ])
    on_jpeg_frame = _make_on_jpeg_frame(detector, cooldown_seconds=5.0, stability_window=1, stability_min_matches=1)
    writer = _FakeWriter()

    await on_jpeg_frame(b"frame1", writer)
    await on_jpeg_frame(b"frame2", writer)  # dentro del cooldown -> se descarta

    assert detector.calls == 1
    assert writer.lines == []


# --- Confirmación (ventana por mayoría, cooldown_seconds=0: sin gate de cooldown,
# para aislar el comportamiento del stabilizer) ---


async def test_single_gesture_below_min_matches_is_not_sent():
    detector = _StubDetector([GestureResult("puño_cerrado", 0.9, 0)])
    on_jpeg_frame = _make_on_jpeg_frame(detector)
    writer = _FakeWriter()

    await on_jpeg_frame(b"frame1", writer)

    assert detector.calls == 1
    assert writer.lines == []  # todavía no aparece las veces necesarias en la ventana


async def test_gesture_seen_twice_in_window_gets_sent_once_confirmed():
    detector = _StubDetector([GestureResult("puño_cerrado", 0.9, 0)] * 2)
    on_jpeg_frame = _make_on_jpeg_frame(detector)
    writer = _FakeWriter()

    await on_jpeg_frame(b"frame1", writer)
    assert writer.lines == []  # 1 de 2 necesarias

    await on_jpeg_frame(b"frame2", writer)
    assert len(writer.lines) == 1  # 2 de 2, confirmado y mandado


async def test_one_noisy_frame_in_between_does_not_prevent_confirmation():
    # Patrón real (ver BITACORA.md "Fase 8"): el mismo puño sostenido, con un frame
    # de por medio clasificado distinto por ruido, antes de llegar a confirmarse.
    detector = _StubDetector([
        GestureResult("puño_cerrado", 0.9, 0),
        GestureResult("dedo_pulgar", 0.6, 1),  # ruido de un frame
        GestureResult("puño_cerrado", 0.9, 0),
    ])
    on_jpeg_frame = _make_on_jpeg_frame(detector)
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
    on_jpeg_frame = _make_on_jpeg_frame(detector)
    writer = _FakeWriter()

    for _ in range(3):
        await on_jpeg_frame(b"frame", writer)

    assert writer.lines == []


async def test_frame_without_gesture_does_not_confirm_but_does_not_erase_the_window():
    detector = _StubDetector([
        GestureResult("puño_cerrado", 0.9, 0),
        GestureResult(None, 0.0, 2),  # ruido de un frame sin gesto (mano ambigua, no ausente)
        GestureResult("puño_cerrado", 0.9, 0),
    ])
    on_jpeg_frame = _make_on_jpeg_frame(detector)
    writer = _FakeWriter()

    for _ in range(3):
        await on_jpeg_frame(b"frame", writer)

    # El puño_cerrado del primer frame sigue en la ventana (tamaño 3) -> 2 de 2.
    assert len(writer.lines) == 1


# --- Fix 2026-10 (ver BITACORA.md "Fase C2 — corrección antes de Fase D"): enviar
# el gesto CONFIRMADO, solo en el cambio, con evento explícito de liberación. ------


async def test_sustained_gesture_sends_exactly_one_message_not_one_per_frame():
    # Este es el bug real que motivó esta corrección: con cooldown≈0 se evaluaba
    # casi cada frame, y antes de este fix se mandaba una línea por cada uno de
    # ellos mientras el gesto seguía sostenido -- debe mandarse una sola vez.
    detector = _StubDetector([GestureResult("puño_cerrado", 0.9, 0)] * 10)
    on_jpeg_frame = _make_on_jpeg_frame(detector)
    writer = _FakeWriter()

    for _ in range(10):
        await on_jpeg_frame(b"frame", writer)

    assert detector.calls == 10  # se siguió evaluando cada frame (cooldown≈0)
    assert len(writer.lines) == 1  # pero se mandó un solo mensaje
    assert "puño_cerrado".encode("utf-8") in writer.lines[0]


async def test_a_single_noisy_frame_while_already_confirmed_does_not_resend():
    # Distinto del test de arriba sobre ruido DURANTE la confirmación: acá el ruido
    # llega DESPUÉS, con el gesto ya confirmado y sostenido -- la histéresis debe
    # mantenerlo sin que eso dispare un reenvío.
    detector = _StubDetector([
        GestureResult("puño_cerrado", 0.9, 0),
        GestureResult("puño_cerrado", 0.9, 0),  # confirma acá (2do de 2)
        GestureResult("dedo_pulgar", 0.6, 1),  # ruido de un solo frame, ya confirmado
        GestureResult("puño_cerrado", 0.9, 0),
    ])
    on_jpeg_frame = _make_on_jpeg_frame(detector)
    writer = _FakeWriter()

    for _ in range(4):
        await on_jpeg_frame(b"frame", writer)

    assert len(writer.lines) == 1  # el ruido no generó un segundo envío


async def test_gesture_switch_sends_exactly_once_and_only_after_reaching_majority():
    detector = _StubDetector([
        GestureResult("puño_cerrado", 0.9, 0),
        GestureResult("puño_cerrado", 0.9, 0),  # puño confirma acá (envío 1)
        GestureResult("palma_abierta", 0.8, 5),  # 1 de 2 para cambiar -- todavía no
        GestureResult("palma_abierta", 0.8, 5),  # 2 de 2 -- cambia acá (envío 2)
        GestureResult("palma_abierta", 0.8, 5),  # sigue sostenido -- sin envío nuevo
    ])
    on_jpeg_frame = _make_on_jpeg_frame(detector)
    writer = _FakeWriter()

    for i in range(5):
        await on_jpeg_frame(b"frame", writer)
        if i == 1:
            assert len(writer.lines) == 1  # puño ya confirmado y mandado
        if i == 2:
            assert len(writer.lines) == 1  # todavía no alcanzó mayoría para cambiar
        if i == 3:
            assert len(writer.lines) == 2  # cambió, se manda una sola vez

    assert len(writer.lines) == 2
    assert "palma_abierta".encode("utf-8") in writer.lines[1]


async def test_removing_the_hand_emits_release_event_after_the_configured_misses():
    # release_after_misses_no_hand=2 (default de _make_on_jpeg_frame de este
    # archivo) -- mano ausente (gesture=None, extended_fingers=0) usa el umbral
    # RÁPIDO, no el largo (20 en producción) que protege contra ruido con mano
    # presente. Ver BITACORA.md "Fase C2 — corrección antes de Fase D".
    detector = _StubDetector([
        GestureResult("puño_cerrado", 0.9, 0),
        GestureResult("puño_cerrado", 0.9, 0),  # confirma (envío 1)
        GestureResult(None, 0.0, 0),  # mano ausente, miss 1 de 2
        GestureResult(None, 0.0, 0),  # miss 2 de 2 -> se suelta (envío 2: liberación)
    ])
    on_jpeg_frame = _make_on_jpeg_frame(detector)
    writer = _FakeWriter()

    for i in range(4):
        await on_jpeg_frame(b"frame", writer)
        if i == 2:
            assert len(writer.lines) == 1  # todavía no se soltó (1er miss)

    assert len(writer.lines) == 2
    assert writer.lines[1] == (RELEASE_LINE + "\n").encode("utf-8")


async def test_hand_present_but_ambiguous_noise_uses_the_long_release_threshold_not_the_fast_one():
    # gesture=None con extended_fingers=2 (mano presente, forma ambigua, NO
    # ausente) debe usar release_after_misses (largo), no
    # release_after_misses_no_hand (rápido) -- si usara el rápido (2, en este
    # archivo) se soltaría antes de la 2da miss; con el largo (10) no.
    detector = _StubDetector(
        [GestureResult("puño_cerrado", 0.9, 0)] * 2  # confirma (envío 1)
        + [GestureResult(None, 0.0, 2)] * 3  # mano presente, ambigua -- 3 misses
    )
    on_jpeg_frame = _make_on_jpeg_frame(detector)
    writer = _FakeWriter()

    for _ in range(5):
        await on_jpeg_frame(b"frame", writer)

    # 3 misses < release_after_misses=10 -> todavía NO se soltó (sigue en 1 envío).
    assert len(writer.lines) == 1


# --- Capturador temporal de frames (2026-09-25, ver BITACORA.md "Fase 8" / spike
# MediaPipe) -- config.CAPTURE_FRAMES_DIR se parchea vía monkeypatch, nunca vía env
# var real, para no tocar el estado del proceso de test. Usa el resultado CRUDO de
# cada frame (no el confirmado) a propósito -- ver nota en main.py. ---


async def test_capture_disabled_by_default_writes_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module.config, "CAPTURE_FRAMES_DIR", None)
    detector = _StubDetector([GestureResult("puño_cerrado", 0.9, 0)])
    on_jpeg_frame = _make_on_jpeg_frame(detector, stability_window=1, stability_min_matches=1)

    await on_jpeg_frame(b"frame", _FakeWriter())

    assert list(tmp_path.iterdir()) == []


async def test_capture_saves_processed_frame_with_gesture_label(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module.config, "CAPTURE_FRAMES_DIR", str(tmp_path))
    detector = _StubDetector([GestureResult("puño_cerrado", 0.87, 0)])
    on_jpeg_frame = _make_on_jpeg_frame(detector, stability_window=1, stability_min_matches=1)

    await on_jpeg_frame(b"contenido-jpeg-falso", _FakeWriter())

    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].name.endswith("__puño_cerrado_0.87.jpg")
    assert files[0].read_bytes() == b"contenido-jpeg-falso"


async def test_capture_saves_processed_frame_without_gesture_as_sin_gesto(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module.config, "CAPTURE_FRAMES_DIR", str(tmp_path))
    detector = _StubDetector([GestureResult(None, 0.0, 2)])
    on_jpeg_frame = _make_on_jpeg_frame(detector, stability_window=1, stability_min_matches=1)

    await on_jpeg_frame(b"frame", _FakeWriter())

    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].name.endswith("__sin_gesto.jpg")


async def test_capture_saves_cooldown_skipped_frame_as_sin_evaluar_not_sin_gesto(tmp_path, monkeypatch):
    # El frame saltado por cooldown nunca pasó por el detector -- etiquetarlo
    # "sin_gesto" sería un dato falso (implicaría que sí se evaluó).
    monkeypatch.setattr(main_module.config, "CAPTURE_FRAMES_DIR", str(tmp_path))
    detector = _StubDetector([GestureResult("puño_cerrado", 0.9, 0)])
    on_jpeg_frame = _make_on_jpeg_frame(detector, cooldown_seconds=5.0, stability_window=1, stability_min_matches=1)

    await on_jpeg_frame(b"frame1", _FakeWriter())  # se procesa, consume el único resultado
    await on_jpeg_frame(b"frame2", _FakeWriter())  # cae en cooldown

    files = [f.name for f in tmp_path.iterdir()]
    assert len(files) == 2
    assert any(f.endswith("__puño_cerrado_0.90.jpg") for f in files)
    assert any(f.endswith("__sin_evaluar.jpg") for f in files)
    assert detector.calls == 1  # el segundo frame nunca llamó a detect()
