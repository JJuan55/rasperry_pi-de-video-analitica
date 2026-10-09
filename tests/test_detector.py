"""Tests de vision/detector.py — Fase B (MediaPipe HandLandmarker).

Dos bloques, con criterios de "listo" distintos:

1. Geometría/filtros pura (OneEuroFilter, LandmarkSmoother, extended_fingers_pattern,
   classify_world_landmarks, format_line, GestureStabilizer) — sintéticos, no
   necesitan cámara ni mediapipe instalado, corren siempre y en cualquier máquina.

2. Fixtures reales (`tests/fixtures_real/`) — fotos reales de JD tomadas en Fase A
   (spike MediaPipe), usadas como pedido explícitamente para esta fase: cubren los 4
   gestos del catálogo más los 3 tipos históricos de falso positivo (cara, torso x2,
   frame vacío). Estas fotos NUNCA se comitean (ver .gitignore) — si la carpeta no
   está presente (clon limpio, otra máquina) o mediapipe no está instalado, este
   bloque se salta entero (skip, no fail) en vez de romper el resto del suite.

   Cada test de este segundo bloque crea una instancia NUEVA de `GestureDetector`
   (fixture `fresh_detector`, function-scoped) en vez de reusar una compartida. Esto
   no es cosmético: `HandLandmarker` en `RunningMode.VIDEO` mantiene tracking interno
   entre llamadas de una misma instancia, pensado para frames de una secuencia
   temporal real — al alimentarle fotos sueltas sin relación (como estos fixtures)
   una instancia compartida arrastra estado de la foto anterior y da resultados
   distintos según el orden en que corran los tests (confirmado real durante la
   investigación de esta fase, ver BITACORA.md "Fase B": el mismo frame de
   palma_abierta pasó de "0 manos detectadas" a clasificar correctamente solo por
   usar una instancia fresca). Una instancia por test es lo único determinista aquí.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from cva_gesture_bridge.vision.detector import (
    FINGER_MCP_TIP,
    GESTURE_DEDO_MENIQUE,
    GESTURE_DEDO_PULGAR,
    GESTURE_PALMA_ABIERTA,
    GESTURE_PUÑO_CERRADO,
    THUMB_MCP,
    THUMB_TIP,
    WRIST,
    GestureResult,
    GestureStabilizer,
    LandmarkSmoother,
    OneEuroFilter,
    classify_world_landmarks,
    extended_fingers_pattern,
    format_line,
    _stub_sounddevice_if_unavailable,
)

# --- Landmarks de mundo sintéticos ------------------------------------------------
#
# Un dedo "extendido" es, para el código real, solo un ratio distancia(punta,muñeca) /
# distancia(nudillo,muñeca) por encima de un umbral (ver detector.py) — no hace falta
# una mano 3D realista para probar esa lógica, alcanza con puntos colineales desde la
# muñeca a la distancia que corresponda. Los umbrales reales (_THUMB_EXTENDED_RATIO=
# 1.7, _FINGER_EXTENDED_RATIO=1.15) se recalibraron en esta fase contra datos reales
# (ver BITACORA.md) — estos multiplicadores sintéticos quedan con margen amplio a
# ambos lados para no quedar pegados al límite exacto.
_MCP_DIST = 0.03
_EXTENDED_MULT = 2.0
_FOLDED_MULT = 0.5
_THUMB_EXTENDED_MULT = 2.5
_THUMB_FOLDED_MULT = 0.6

_FINGER_DIRECTIONS = {
    "pulgar": (1.0, 0.0, 0.0),
    "indice": (0.0, 1.0, 0.0),
    "medio": (0.0, 0.0, 1.0),
    "anular": (0.577, 0.577, 0.577),
    "menique": (-1.0, 0.0, 0.0),
}


def _point(direction, dist):
    return tuple(d * dist for d in direction)


def _synthetic_hand(*, pulgar: bool, indice: bool, medio: bool, anular: bool, menique: bool):
    lm = [(0.0, 0.0, 0.0)] * 21
    lm[WRIST] = (0.0, 0.0, 0.0)

    direction = _FINGER_DIRECTIONS["pulgar"]
    tip_dist = _MCP_DIST * (_THUMB_EXTENDED_MULT if pulgar else _THUMB_FOLDED_MULT)
    lm[THUMB_MCP] = _point(direction, _MCP_DIST)
    lm[THUMB_TIP] = _point(direction, tip_dist)

    extended_by_name = {"indice": indice, "medio": medio, "anular": anular, "menique": menique}
    for name, (mcp_idx, tip_idx) in FINGER_MCP_TIP.items():
        direction = _FINGER_DIRECTIONS[name]
        tip_dist = _MCP_DIST * (_EXTENDED_MULT if extended_by_name[name] else _FOLDED_MULT)
        lm[mcp_idx] = _point(direction, _MCP_DIST)
        lm[tip_idx] = _point(direction, tip_dist)

    return lm


_PUÑO = dict(pulgar=False, indice=False, medio=False, anular=False, menique=False)
_PALMA = dict(pulgar=True, indice=True, medio=True, anular=True, menique=True)
_PULGAR = dict(pulgar=True, indice=False, medio=False, anular=False, menique=False)
_MENIQUE = dict(pulgar=False, indice=False, medio=False, anular=False, menique=True)


def test_extended_fingers_pattern_fist_is_all_false():
    assert extended_fingers_pattern(_synthetic_hand(**_PUÑO)) == (False, False, False, False, False)


def test_extended_fingers_pattern_open_palm_is_all_true():
    assert extended_fingers_pattern(_synthetic_hand(**_PALMA)) == (True, True, True, True, True)


def test_extended_fingers_pattern_thumb_only():
    assert extended_fingers_pattern(_synthetic_hand(**_PULGAR)) == (True, False, False, False, False)


def test_extended_fingers_pattern_pinky_only():
    assert extended_fingers_pattern(_synthetic_hand(**_MENIQUE)) == (False, False, False, False, True)


def test_classify_world_landmarks_fist_is_puno_cerrado():
    gesture, extended = classify_world_landmarks(_synthetic_hand(**_PUÑO))
    assert gesture == GESTURE_PUÑO_CERRADO
    assert extended == 0


def test_classify_world_landmarks_open_palm_is_palma_abierta():
    gesture, extended = classify_world_landmarks(_synthetic_hand(**_PALMA))
    assert gesture == GESTURE_PALMA_ABIERTA
    assert extended == 5


def test_classify_world_landmarks_thumb_only_is_dedo_pulgar():
    gesture, extended = classify_world_landmarks(_synthetic_hand(**_PULGAR))
    assert gesture == GESTURE_DEDO_PULGAR
    assert extended == 1


def test_classify_world_landmarks_pinky_only_is_dedo_menique():
    gesture, extended = classify_world_landmarks(_synthetic_hand(**_MENIQUE))
    assert gesture == GESTURE_DEDO_MENIQUE
    assert extended == 1


def test_classify_world_landmarks_four_fingers_without_thumb_is_still_palma_abierta():
    # Mismo criterio que Fase 8 (ver detector.py): >=4 extendidos cuenta como palma
    # abierta aunque el pulgar en particular no cruce el umbral — validado contra un
    # fixture real en Fase A donde esto pasaba con una palma genuinamente abierta.
    hand = _synthetic_hand(pulgar=False, indice=True, medio=True, anular=True, menique=True)
    gesture, extended = classify_world_landmarks(hand)
    assert gesture == GESTURE_PALMA_ABIERTA
    assert extended == 4


def test_classify_world_landmarks_two_or_three_fingers_is_outside_catalog():
    hand = _synthetic_hand(pulgar=False, indice=True, medio=True, anular=False, menique=False)
    gesture, extended = classify_world_landmarks(hand)
    assert gesture is None
    assert extended == 2


def test_classify_world_landmarks_index_only_is_outside_catalog():
    # El catálogo de esta fase (GESTOS.md, decisión de JD) solo tiene 4 gestos —
    # índice solo no es ninguno de ellos, a diferencia de pulgar/meñique que sí tienen
    # su propio caso especial en classify_world_landmarks.
    hand = _synthetic_hand(pulgar=False, indice=True, medio=False, anular=False, menique=False)
    gesture, extended = classify_world_landmarks(hand)
    assert gesture is None
    assert extended == 1


def test_format_line_returns_none_without_gesture():
    # Fase C2 (corrección antes de Fase D): format_line ahora toma gesto/confianza
    # sueltos, no un GestureResult -- lo que se formatea es el gesto ya CONFIRMADO
    # por GestureStabilizer, no el resultado crudo de un solo frame.
    assert format_line(None, confidence=0.0, min_confidence=0.5) is None


def test_format_line_returns_none_below_confidence_threshold():
    assert format_line(GESTURE_DEDO_MENIQUE, confidence=0.3, min_confidence=0.5) is None


def test_format_line_formats_gesture_and_confidence_above_threshold():
    line = format_line(GESTURE_DEDO_MENIQUE, confidence=0.876, min_confidence=0.5)
    assert line == "gesto: dedo_menique, confianza: 0.88"


# --- OneEuroFilter / LandmarkSmoother (suavizado, nuevo en Fase B) ----------------


def test_one_euro_filter_first_sample_passes_through_unchanged():
    f = OneEuroFilter()
    assert f(5.0, t=0.0) == 5.0


def test_one_euro_filter_smooths_a_sudden_jump():
    f = OneEuroFilter(min_cutoff=1.0, beta=0.0)
    f(0.0, t=0.0)
    smoothed = f(10.0, t=0.033)  # ~30fps
    # Ni se queda en el valor viejo ni salta de golpe al nuevo -- suaviza.
    assert 0.0 < smoothed < 10.0


def test_one_euro_filter_converges_towards_a_sustained_value():
    f = OneEuroFilter(min_cutoff=1.0, beta=0.0)
    t = 0.0
    f(0.0, t=t)
    last = 0.0
    for _ in range(60):  # ~2s sostenidos a 30fps
        t += 0.033
        last = f(10.0, t=t)
    assert last > 9.0  # converge, no se queda pegado al valor inicial


def test_one_euro_filter_reset_forgets_previous_state():
    f = OneEuroFilter()
    f(0.0, t=0.0)
    f(10.0, t=0.033)
    f.reset()
    assert f(5.0, t=1.0) == 5.0  # como si fuera la primera muestra otra vez


def test_one_euro_filter_repeated_timestamp_does_not_crash():
    f = OneEuroFilter()
    f(0.0, t=1.0)
    f(1.0, t=1.0)  # mismo t -- dt se fuerza a un mínimo, no división por cero


def test_landmark_smoother_first_call_passes_through_unchanged():
    smoother = LandmarkSmoother()
    raw = [(float(i), float(i) * 2, float(i) * 3) for i in range(21)]
    assert smoother.smooth(raw, t=0.0) == raw


def test_landmark_smoother_reset_makes_next_call_pass_through_again():
    smoother = LandmarkSmoother()
    raw = [(float(i), 0.0, 0.0) for i in range(21)]
    smoother.smooth(raw, t=0.0)
    smoother.smooth([(x + 5.0, y, z) for x, y, z in raw], t=0.033)
    smoother.reset()
    assert smoother.smooth(raw, t=1.0) == raw


# --- GestureStabilizer (Fase C2 -- ventana por mayoría CON histéresis, ver
# docstring de la clase en detector.py y BITACORA.md "Fase C2" para la calibración
# con datos reales que respalda estos defaults: window_size=7, min_matches=5,
# release_after_misses=20) -----------------------------------------------------


def test_stabilizer_does_not_confirm_with_insufficient_matches():
    stabilizer = GestureStabilizer(window_size=7, min_matches=5)
    result = None
    for _ in range(4):
        result = stabilizer.observe(GESTURE_PUÑO_CERRADO)
    assert result is None


def test_stabilizer_confirms_once_min_matches_reached_within_window():
    stabilizer = GestureStabilizer(window_size=7, min_matches=5)
    result = None
    for _ in range(5):
        result = stabilizer.observe(GESTURE_PUÑO_CERRADO)
    assert result == GESTURE_PUÑO_CERRADO


def test_stabilizer_confirms_even_with_noise_interleaved_in_the_window():
    # 5 de 7 no exige que sean consecutivas -- ruido intercalado no rompe la
    # confirmación, siempre que la mayoría se alcance dentro de la ventana.
    stabilizer = GestureStabilizer(window_size=7, min_matches=5)
    sequence = [
        GESTURE_PUÑO_CERRADO, None, GESTURE_PUÑO_CERRADO,
        GESTURE_PUÑO_CERRADO, None, GESTURE_PUÑO_CERRADO, GESTURE_PUÑO_CERRADO,
    ]
    result = None
    for g in sequence:
        result = stabilizer.observe(g)
    assert result == GESTURE_PUÑO_CERRADO


def test_stabilizer_hysteresis_survives_a_noise_burst_shorter_than_release_threshold():
    # 14 frames de ruido seguidos es la racha más larga medida contra datos reales
    # en Fase C2 (BITACORA.md) -- con release_after_misses=20 de margen, no se suelta.
    stabilizer = GestureStabilizer(window_size=7, min_matches=5, release_after_misses=20)
    for _ in range(5):
        stabilizer.observe(GESTURE_PUÑO_CERRADO)
    result = None
    for _ in range(14):
        result = stabilizer.observe(None)
    assert result == GESTURE_PUÑO_CERRADO


def test_stabilizer_releases_after_sustained_misses():
    stabilizer = GestureStabilizer(window_size=7, min_matches=5, release_after_misses=20)
    for _ in range(5):
        stabilizer.observe(GESTURE_PUÑO_CERRADO)
    result = None
    for _ in range(20):
        result = stabilizer.observe(None)
    assert result is None


def test_stabilizer_switches_to_a_new_gesture_once_it_reaches_its_own_majority():
    stabilizer = GestureStabilizer(window_size=7, min_matches=5)
    for _ in range(5):
        stabilizer.observe(GESTURE_PUÑO_CERRADO)
    result = None
    for _ in range(5):
        result = stabilizer.observe(GESTURE_PALMA_ABIERTA)
    assert result == GESTURE_PALMA_ABIERTA


def test_stabilizer_does_not_switch_on_a_single_frame_of_a_different_gesture():
    stabilizer = GestureStabilizer(window_size=7, min_matches=5)
    for _ in range(5):
        stabilizer.observe(GESTURE_PUÑO_CERRADO)
    result = stabilizer.observe(GESTURE_PALMA_ABIERTA)
    assert result == GESTURE_PUÑO_CERRADO


def test_stabilizer_default_parameters_match_the_calibrated_values():
    # Si esto falla, alguien cambió los defaults de la clase sin actualizar
    # config.py (o viceversa) -- deben moverse juntos, ver BITACORA.md "Fase C2".
    stabilizer = GestureStabilizer()
    result = None
    for _ in range(5):
        result = stabilizer.observe(GESTURE_PUÑO_CERRADO)
    assert result == GESTURE_PUÑO_CERRADO  # confirma con el default de min_matches=5
    # hand_present=True por default -- usa release_after_misses (20), no el umbral
    # rápido de mano ausente.
    for _ in range(19):
        result = stabilizer.observe(None)
    assert result == GESTURE_PUÑO_CERRADO  # todavía no llega a release_after_misses=20
    result = stabilizer.observe(None)
    assert result is None  # el miss número 20 sí suelta


# --- Fase C2, corrección antes de Fase D: confirmed_confidence + histéresis con
# dos umbrales según mano presente/ausente (ver BITACORA.md) ----------------------


def test_stabilizer_confirmed_confidence_tracks_the_last_matching_observation():
    stabilizer = GestureStabilizer(window_size=3, min_matches=2)
    stabilizer.observe(GESTURE_PUÑO_CERRADO, confidence=0.80)
    stabilizer.observe(GESTURE_PUÑO_CERRADO, confidence=0.95)  # confirma acá
    assert stabilizer.confirmed_confidence == 0.95

    # Un frame de ruido (gesto distinto) NO debe pisar la confianza confirmada --
    # sigue siendo la del último frame que sí coincidió con puño_cerrado.
    stabilizer.observe(GESTURE_DEDO_PULGAR, confidence=0.99)
    assert stabilizer.confirmed_confidence == 0.95

    stabilizer.observe(GESTURE_PUÑO_CERRADO, confidence=0.88)
    assert stabilizer.confirmed_confidence == 0.88


def test_stabilizer_confirmed_confidence_resets_to_zero_after_release():
    stabilizer = GestureStabilizer(window_size=3, min_matches=2, release_after_misses_no_hand=2)
    stabilizer.observe(GESTURE_PUÑO_CERRADO, confidence=0.9)
    stabilizer.observe(GESTURE_PUÑO_CERRADO, confidence=0.9)
    assert stabilizer.confirmed_confidence == 0.9

    for _ in range(2):
        stabilizer.observe(None, hand_present=False)
    assert stabilizer.confirmed_confidence == 0.0


def test_stabilizer_hand_absent_releases_faster_than_hand_present_ambiguous():
    # Mismo ruido (14 observaciones de None), la única diferencia es hand_present --
    # confirma que los dos umbrales realmente se usan por separado.
    release_after_misses = 20
    release_after_misses_no_hand = 10

    stabilizer_no_hand = GestureStabilizer(
        window_size=7, min_matches=5,
        release_after_misses=release_after_misses,
        release_after_misses_no_hand=release_after_misses_no_hand,
    )
    stabilizer_hand_present = GestureStabilizer(
        window_size=7, min_matches=5,
        release_after_misses=release_after_misses,
        release_after_misses_no_hand=release_after_misses_no_hand,
    )
    for _ in range(5):
        stabilizer_no_hand.observe(GESTURE_PUÑO_CERRADO)
        stabilizer_hand_present.observe(GESTURE_PUÑO_CERRADO)

    result_no_hand = None
    result_hand_present = None
    for _ in range(14):
        result_no_hand = stabilizer_no_hand.observe(None, hand_present=False)
        result_hand_present = stabilizer_hand_present.observe(None, hand_present=True)

    assert result_no_hand is None  # se soltó (14 >= release_after_misses_no_hand=10)
    assert result_hand_present == GESTURE_PUÑO_CERRADO  # se mantiene (14 < 20)


def test_stabilizer_no_hand_default_survives_the_worst_real_burst_measured():
    # Hallazgo real de Fase C2 (benchmarks/inspect_noise_by_hand_presence.py): la
    # racha de ruido de mano ausente más larga dentro de un gesto genuinamente
    # sostenido fue 13 frames -- con el default (16) no debe soltarse.
    stabilizer = GestureStabilizer()  # release_after_misses_no_hand=16 por default
    for _ in range(5):
        stabilizer.observe(GESTURE_PUÑO_CERRADO)
    result = None
    for _ in range(13):
        result = stabilizer.observe(None, hand_present=False)
    assert result == GESTURE_PUÑO_CERRADO


# --- Fixtures reales (Fase A -> Fase B) -------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures_real"


def _mediapipe_available() -> bool:
    try:
        import mediapipe  # noqa: F401
    except ImportError:
        return False
    return True


requires_real_fixtures = pytest.mark.skipif(
    not (FIXTURES_DIR.is_dir() and _mediapipe_available()),
    reason=(
        "tests/fixtures_real/ no está presente o mediapipe no está instalado -- son "
        "fotos reales de JD (Fase A), nunca se comitean (ver .gitignore); estos tests "
        "solo corren en la máquina donde se capturaron, con el .venv de Fase B activo"
    ),
)


@pytest.fixture
def fresh_detector():
    # Instancia nueva por test -- ver docstring del módulo, evita contaminación de
    # tracking VIDEO-mode entre fixtures sin relación temporal real.
    from cva_gesture_bridge import config
    from cva_gesture_bridge.vision.detector import GestureDetector

    return GestureDetector(
        config.MEDIAPIPE_MODEL_PATH,
        config.MIN_CONFIDENCE,
        config.MEDIAPIPE_MIN_HAND_DETECTION_CONFIDENCE,
        config.MEDIAPIPE_MIN_HAND_PRESENCE_CONFIDENCE,
        config.MEDIAPIPE_MIN_TRACKING_CONFIDENCE,
    )


def _read_fixture(name: str) -> bytes:
    with open(FIXTURES_DIR / name, "rb") as f:
        return f.read()


@requires_real_fixtures
def test_real_fixture_closed_fist_is_puno_cerrado(fresh_detector):
    result = fresh_detector.detect(_read_fixture("fixture_puno_cerrado.jpg"))
    assert result.gesture == GESTURE_PUÑO_CERRADO
    assert result.confidence >= 0.9


@requires_real_fixtures
def test_real_fixture_open_palm_is_palma_abierta(fresh_detector):
    result = fresh_detector.detect(_read_fixture("fixture_palma_abierta.jpg"))
    assert result.gesture == GESTURE_PALMA_ABIERTA
    assert result.confidence >= 0.9


@requires_real_fixtures
def test_real_fixture_thumb_up_is_dedo_pulgar(fresh_detector):
    result = fresh_detector.detect(_read_fixture("fixture_dedo_pulgar.jpg"))
    assert result.gesture == GESTURE_DEDO_PULGAR
    assert result.confidence >= 0.9


@requires_real_fixtures
def test_real_fixture_pinky_up_is_dedo_menique(fresh_detector):
    # Fixture reemplazado 2026-10-02: el original (Fase B inicial) quedó con una
    # salvedad documentada -- la rotación de la mano no permitía confirmar a ojo si
    # era índice o meñique. Este nuevo fixture se grabó con una secuencia completa
    # (palma abierta -> ir doblando dedos uno por uno, palma siempre de frente a la
    # cámara) que sirve de referencia posicional: el dedo que queda al final se
    # verificó comparando su posición contra el frame de palma abierta de la misma
    # sesión -- cae exactamente donde estaba el meñique, el dedo más alejado del
    # pulgar. Sin ambigüedad esta vez. Ver BITACORA.md "Fase B".
    result = fresh_detector.detect(_read_fixture("fixture_dedo_menique.jpg"))
    assert result.gesture == GESTURE_DEDO_MENIQUE
    assert result.confidence >= 0.9


@requires_real_fixtures
def test_real_fixture_face_is_never_classified_as_a_catalog_gesture(fresh_detector):
    # Caso histórico (Fase A, BITACORA.md): a resolución real del cliente (320x240)
    # el detector CRUDO de MediaPipe sí "ve" una mano en esta foto de cara/torso real
    # (handedness score 0.80-0.91 medido en la investigación de esta fase, alto) --
    # NO es un falso negativo del modelo, es un verdadero landmark detectado sobre una
    # cara. Lo que evita que esto llegue como gesto real al cliente es la capa de
    # arriba: el patrón geométrico de esos landmarks (clasificado por
    # classify_world_landmarks) no coincide con ninguno de los 4 gestos del catálogo,
    # así que gesture queda en None y confidence en 0.0 antes de llegar a
    # GestureStabilizer/format_line. Resuelto y entendido, no ignorado.
    result = fresh_detector.detect(_read_fixture("persona_cara_sin_mano.jpg"))
    assert result.gesture is None
    assert result.confidence == 0.0


@requires_real_fixtures
def test_real_fixture_torso_without_hand_1_is_never_classified_as_a_catalog_gesture(fresh_detector):
    result = fresh_detector.detect(_read_fixture("persona_torso_sin_mano_1.jpg"))
    assert result.gesture is None
    assert result.confidence == 0.0


@requires_real_fixtures
def test_real_fixture_torso_without_hand_2_is_never_classified_as_a_catalog_gesture(fresh_detector):
    result = fresh_detector.detect(_read_fixture("persona_torso_sin_mano_2.jpg"))
    assert result.gesture is None
    assert result.confidence == 0.0


@requires_real_fixtures
def test_empty_black_frame_detects_nothing(fresh_detector):
    blank = np.zeros((240, 320, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", blank)
    assert ok
    result = fresh_detector.detect(buf.tobytes())
    assert result.gesture is None
    assert result.confidence == 0.0
    assert result.extended_fingers == 0


# --- Fase C2: detector + GestureStabilizer (histéresis) juntos, contra los mismos
# fixtures reales -- no alcanza con probar el detector crudo solo (ya se hizo
# arriba), la histéresis es justamente lo que podría hacer que un falso positivo
# aislado se "pegara" más tiempo si no estuviera bien diseñada. Simula una vista
# sostenida (no un solo frame) alimentando el mismo fixture repetidas veces al
# detector real + un GestureStabilizer con los defaults calibrados en BITACORA.md
# "Fase C2". Meta de JD: cero falsos positivos de los 3 tipos históricos contra el
# esquema de confirmación nuevo, no solo contra el detector crudo. ---------------


@requires_real_fixtures
def test_sustained_face_view_never_confirms_a_gesture(fresh_detector):
    stabilizer = GestureStabilizer()
    jpeg = _read_fixture("persona_cara_sin_mano.jpg")
    results = [stabilizer.observe(fresh_detector.detect(jpeg).gesture) for _ in range(30)]
    assert all(r is None for r in results)


@requires_real_fixtures
def test_sustained_torso_view_never_confirms_a_gesture(fresh_detector):
    stabilizer = GestureStabilizer()
    jpeg = _read_fixture("persona_torso_sin_mano_1.jpg")
    results = [stabilizer.observe(fresh_detector.detect(jpeg).gesture) for _ in range(30)]
    assert all(r is None for r in results)


@requires_real_fixtures
def test_sustained_empty_frame_never_confirms_a_gesture(fresh_detector):
    stabilizer = GestureStabilizer()
    blank = np.zeros((240, 320, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", blank)
    assert ok
    jpeg = buf.tobytes()
    results = [stabilizer.observe(fresh_detector.detect(jpeg).gesture) for _ in range(30)]
    assert all(r is None for r in results)


@requires_real_fixtures
def test_sustained_real_gesture_confirms_through_the_full_pipeline(fresh_detector):
    # Control positivo: el mismo esquema que arriba, pero con un gesto real del
    # catálogo -- confirma que detector+stabilizer juntos SÍ reconocen el caso
    # válido, no solo que rechazan los falsos positivos.
    stabilizer = GestureStabilizer()
    jpeg = _read_fixture("fixture_puno_cerrado.jpg")
    results = [stabilizer.observe(fresh_detector.detect(jpeg).gesture) for _ in range(10)]
    assert results[-1] == GESTURE_PUÑO_CERRADO


# --- Incidente real de Fase D: mediapipe importa su submódulo de audio sin que
# este proyecto lo use nunca, y ese submódulo intenta inicializar PortAudio en el
# momento del import -- revienta en el servicio real (unidad de sistema de
# systemd, sin sesión de audio) aunque funcionara en pruebas interactivas por SSH.
# Ver BITACORA.md "Fase D" y el docstring de _stub_sounddevice_if_unavailable. ---


def test_stub_sounddevice_installed_when_real_import_fails(monkeypatch):
    import builtins
    import sys

    # Guarda el valor real (si lo hay) para restaurarlo al final -- la función
    # muta sys.modules directamente, no a través de monkeypatch, así que
    # monkeypatch no deshace eso solo (revisión externa, 2026-10: el primer
    # intento dejaba el stub en blanco contaminando sys.modules para el resto
    # de la sesión de tests).
    original = sys.modules.get("sounddevice")
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sounddevice":
            raise OSError("simulado: sin PulseAudio/PortAudio disponible")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    try:
        _stub_sounddevice_if_unavailable()
        assert "sounddevice" in sys.modules
        # Un stub en blanco (types.ModuleType) nunca tiene __file__ -- el paquete real sí.
        assert not hasattr(sys.modules["sounddevice"], "__file__")
    finally:
        if original is not None:
            sys.modules["sounddevice"] = original
        else:
            sys.modules.pop("sounddevice", None)


def test_stub_not_installed_when_real_sounddevice_import_succeeds(monkeypatch):
    import builtins
    import sys
    import types as types_module

    # No depende de que ESTA máquina/entorno tenga audio disponible de verdad
    # (revisión externa, 2026-10: el intento anterior sí dependía de eso, y el
    # incidente real de Fase D demostró que la disponibilidad de audio varía
    # según el contexto -- interactivo por SSH vs. unidad de systemd) -- se
    # simula un import real exitoso con un módulo falso que sí tiene __file__,
    # igual que tendría el paquete real instalado.
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    fake_real_module = types_module.ModuleType("sounddevice")
    fake_real_module.__file__ = "/fake/site-packages/sounddevice.py"
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sounddevice":
            sys.modules["sounddevice"] = fake_real_module
            return fake_real_module
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    _stub_sounddevice_if_unavailable()

    # La función no debe pisar el import real exitoso con un stub -- eso
    # ocultaría un fallo real de audio en los entornos donde sí hay sesión
    # disponible.
    assert sys.modules["sounddevice"] is fake_real_module
    assert hasattr(sys.modules["sounddevice"], "__file__")


def test_stub_does_not_touch_an_already_imported_sounddevice(monkeypatch):
    import sys

    sentinel = object()
    monkeypatch.setitem(sys.modules, "sounddevice", sentinel)

    _stub_sounddevice_if_unavailable()

    assert sys.modules["sounddevice"] is sentinel
