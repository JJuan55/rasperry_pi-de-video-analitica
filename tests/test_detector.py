"""Tests de la parte de vision/detector.py testeable sin cámara real: la geometría de
conteo de dedos (sobre contornos sintéticos dibujados a propósito, no fotos), la
clasificación, el filtro de plausibilidad (fix de falsos positivos sin mano
presente), el stabilizer de estabilidad temporal, y el formato/umbral de la línea
que sale por send_line().

Lo que depende de inferencia YOLO real o de fotos reales de una mano/cara (segment_hand
contra piel real, GestureDetector.detect end-to-end) queda fuera de este archivo — es
validación manual documentada en BITACORA.md "Fase 8", igual que el smoke test de
Fase 6.
"""

import math

import cv2
import numpy as np

from cva_gesture_bridge.vision.detector import (
    GESTURE_DEDO_MENIQUE,
    GESTURE_DEDO_PULGAR,
    GESTURE_PALMA_ABIERTA,
    GESTURE_PUÑO_CERRADO,
    GestureResult,
    GestureStabilizer,
    MotionGate,
    _is_plausible_hand_contour,
    classify_contour,
    classify_gesture,
    count_extended_fingers,
    format_line,
)


def _contour_from_mask(mask: np.ndarray) -> np.ndarray:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return max(contours, key=cv2.contourArea)


def _fist_contour() -> np.ndarray:
    """Blob compacto con textura leve (nudillos) — simula un puño cerrado real, no un
    círculo perfecto: un círculo perfecto es geométricamente indistinguible de una cara
    lisa, que es exactamente el bug real que motivó el filtro de plausibilidad (ver
    `_face_contour` y BITACORA.md "Fase 8")."""
    cx, cy, base_r, amplitude, lobes, n_points = 105, 105, 75, 0.12, 8, 40
    points = []
    for i in range(n_points):
        theta = 2 * math.pi * i / n_points
        r = base_r * (1 + amplitude * math.sin(lobes * theta))
        points.append((int(cx + r * math.cos(theta)), int(cy + r * math.sin(theta))))
    mask = np.zeros((220, 220), dtype=np.uint8)
    cv2.fillPoly(mask, [np.array(points, dtype=np.int32)], 255)
    return _contour_from_mask(mask)


def _face_contour() -> np.ndarray:
    """Óvalo liso de solidity muy alta — sin mano real presente, esto es justo lo que
    `segment_hand()` encontraba al segmentar una cara por color de piel dentro de la
    región "persona" de YOLO (bug real de producción, ver BITACORA.md "Fase 8")."""
    mask = np.zeros((220, 180), dtype=np.uint8)
    cv2.ellipse(mask, (90, 110), (60, 90), 0, 0, 360, 255, -1)
    return _contour_from_mask(mask)


def _one_finger_contour(finger_on_left: bool) -> np.ndarray:
    """Base ancha ("palma") + un solo rectángulo angosto hacia arriba ("dedo")."""
    mask = np.zeros((260, 200), dtype=np.uint8)
    cv2.rectangle(mask, (40, 160), (160, 240), 255, -1)
    finger_x = 65 if finger_on_left else 135
    cv2.rectangle(mask, (finger_x - 15, 20), (finger_x + 15, 165), 255, -1)
    return _contour_from_mask(mask)


def _open_palm_contour() -> np.ndarray:
    """Silueta en abanico con 5 puntas (dedos) y 4 valles entre ellas — simula una
    palma abierta. Las puntas están a alturas distintas a propósito: si quedaran
    alineadas, el convex hull las trata como colineales y solo reporta un defect para
    todo el borde superior en vez de uno por valle (esto se descubrió al escribir este
    test contra un rectángulo con 5 dedos parejos, que fallaba con solo 2-3 defects).
    `valley_y` bajado de 140 a 155 el 2026-09-18 (junto con subir
    `_MIN_DEFECT_DEPTH_RATIO` a 0.20 en detector.py): con 140 el defect más débil
    quedaba a 0.198, muy pegado al nuevo umbral — con 155 queda en ~0.23, con margen
    real."""
    tips_x = [50, 85, 120, 155, 190]
    tips_y = [60, 25, 15, 30, 70]
    valley_x = [68, 103, 138, 173]
    valley_y = 155

    points = [(20, 220), (30, 150)]
    for i, (tx, ty) in enumerate(zip(tips_x, tips_y)):
        points.append((tx, ty))
        if i < len(valley_x):
            points.append((valley_x[i], valley_y))
    points += [(210, 150), (220, 220)]

    mask = np.zeros((260, 260), dtype=np.uint8)
    cv2.fillPoly(mask, [np.array(points, dtype=np.int32)], 255)
    return _contour_from_mask(mask)


def test_fist_contour_counts_zero_extended_fingers():
    assert count_extended_fingers(_fist_contour()) == 0


def test_single_finger_contour_counts_one_extended_finger():
    assert count_extended_fingers(_one_finger_contour(finger_on_left=True)) == 1
    assert count_extended_fingers(_one_finger_contour(finger_on_left=False)) == 1


def test_open_palm_contour_counts_at_least_four_extended_fingers():
    # 5 rectángulos separados producen 4 huecos cualificados -> 5 dedos, pero basta con
    # que el conteo cruce el umbral de "palma_abierta" (>=4) de forma robusta.
    assert count_extended_fingers(_open_palm_contour()) >= 4


def test_classify_gesture_fist_is_puno_cerrado():
    gesture, confidence = classify_gesture(_fist_contour(), extended_fingers=0)
    assert gesture == GESTURE_PUÑO_CERRADO
    assert 0.0 < confidence <= 1.0


def test_classify_gesture_open_palm_is_palma_abierta():
    contour = _open_palm_contour()
    extended = count_extended_fingers(contour)
    gesture, confidence = classify_gesture(contour, extended)
    assert gesture == GESTURE_PALMA_ABIERTA
    assert 0.0 < confidence <= 1.0


def test_classify_gesture_single_finger_on_left_is_pulgar():
    contour = _one_finger_contour(finger_on_left=True)
    gesture, confidence = classify_gesture(contour, extended_fingers=1)
    assert gesture == GESTURE_DEDO_PULGAR
    assert 0.0 < confidence <= 1.0


def test_classify_gesture_single_finger_on_right_is_menique():
    contour = _one_finger_contour(finger_on_left=False)
    gesture, confidence = classify_gesture(contour, extended_fingers=1)
    assert gesture == GESTURE_DEDO_MENIQUE
    assert 0.0 < confidence <= 1.0


def test_classify_gesture_two_or_three_fingers_is_outside_this_phase_catalog():
    # No hay contorno sintético de 2/3 dedos porque no son parte del catálogo de esta
    # fase — se prueba directamente que classify_gesture no inventa un gesto para ellos.
    gesture, confidence = classify_gesture(_fist_contour(), extended_fingers=2)
    assert gesture is None
    assert confidence == 0.0


def test_format_line_returns_none_without_gesture():
    result = GestureResult(gesture=None, confidence=0.0, extended_fingers=2)
    assert format_line(result, min_confidence=0.5) is None


def test_format_line_returns_none_below_confidence_threshold():
    result = GestureResult(gesture=GESTURE_DEDO_MENIQUE, confidence=0.3, extended_fingers=1)
    assert format_line(result, min_confidence=0.5) is None


def test_format_line_formats_gesture_and_confidence_above_threshold():
    result = GestureResult(gesture=GESTURE_DEDO_MENIQUE, confidence=0.876, extended_fingers=1)
    line = format_line(result, min_confidence=0.5)
    assert line == "gesto: dedo_menique, confianza: 0.88"


# --- Filtro de plausibilidad (fix de falsos positivos sin mano presente) ---


def test_face_like_contour_is_rejected_as_implausible():
    assert _is_plausible_hand_contour(_face_contour()) is False


def test_all_four_catalog_gesture_fixtures_are_plausible_hand_contours():
    assert _is_plausible_hand_contour(_fist_contour())
    assert _is_plausible_hand_contour(_open_palm_contour())
    assert _is_plausible_hand_contour(_one_finger_contour(finger_on_left=True))
    assert _is_plausible_hand_contour(_one_finger_contour(finger_on_left=False))


def test_classify_contour_face_like_contour_is_not_any_of_the_4_gestures():
    result = classify_contour(_face_contour())
    assert result.gesture is None
    assert result.confidence == 0.0


def test_classify_contour_still_recognizes_legitimate_fist():
    result = classify_contour(_fist_contour())
    assert result.gesture == GESTURE_PUÑO_CERRADO


def test_classify_contour_still_recognizes_legitimate_open_palm():
    result = classify_contour(_open_palm_contour())
    assert result.gesture == GESTURE_PALMA_ABIERTA


# --- GestureStabilizer (ventana deslizante, rediseñado el 2026-09-18 tras evidencia
# real de que una racha exacta casi nunca se completaba con una mano real sostenida —
# ver BITACORA.md "Fase 8") ---


def test_stabilizer_does_not_confirm_with_a_single_observation():
    stabilizer = GestureStabilizer(window_size=3, min_matches=2)
    assert stabilizer.observe(GESTURE_PUÑO_CERRADO) is None


def test_stabilizer_confirms_when_min_matches_reached_even_without_exact_streak():
    # 2 de las últimas 3 alcanzan, aunque la del medio sea otro gesto — este es
    # exactamente el patrón real que antes nunca confirmaba.
    stabilizer = GestureStabilizer(window_size=3, min_matches=2)
    stabilizer.observe(GESTURE_PUÑO_CERRADO)
    stabilizer.observe(GESTURE_DEDO_PULGAR)  # ruido de un frame, ya no rompe todo
    assert stabilizer.observe(GESTURE_PUÑO_CERRADO) == GESTURE_PUÑO_CERRADO


def test_stabilizer_confirms_on_two_consecutive_matches():
    stabilizer = GestureStabilizer(window_size=3, min_matches=2)
    stabilizer.observe(GESTURE_PUÑO_CERRADO)
    assert stabilizer.observe(GESTURE_PUÑO_CERRADO) == GESTURE_PUÑO_CERRADO


def test_stabilizer_does_not_confirm_a_gesture_seen_only_once_in_the_window():
    stabilizer = GestureStabilizer(window_size=3, min_matches=2)
    stabilizer.observe(GESTURE_PUÑO_CERRADO)
    stabilizer.observe(GESTURE_DEDO_PULGAR)
    assert stabilizer.observe(GESTURE_PALMA_ABIERTA) is None  # los 3 son distintos


def test_stabilizer_none_observation_does_not_confirm_but_stays_in_the_window():
    stabilizer = GestureStabilizer(window_size=3, min_matches=2)
    stabilizer.observe(GESTURE_PUÑO_CERRADO)
    assert stabilizer.observe(None) is None  # ruido de un frame sin gesto
    # el puño_cerrado anterior sigue en la ventana (tamaño 3) -> esta es la 2da
    assert stabilizer.observe(GESTURE_PUÑO_CERRADO) == GESTURE_PUÑO_CERRADO


def test_stabilizer_window_slides_and_forgets_old_observations():
    stabilizer = GestureStabilizer(window_size=3, min_matches=2)
    stabilizer.observe(GESTURE_PUÑO_CERRADO)
    stabilizer.observe(GESTURE_PALMA_ABIERTA)
    stabilizer.observe(GESTURE_PALMA_ABIERTA)  # ventana: [puño, palma, palma]
    # el puño_cerrado original ya salió de la ventana (maxlen=3) -> no cuenta
    assert stabilizer.observe(GESTURE_PUÑO_CERRADO) is None


# --- MotionGate (segundo fix de falsos positivos: torso confundido con puño,
# 2026-09-18, ver BITACORA.md "Fase 8") — frames sintéticos de color sólido, no fotos ---


def _solid_frame(color_bgr, shape=(100, 100)):
    frame = np.zeros((*shape, 3), dtype=np.uint8)
    frame[:] = color_bgr
    return frame


def test_motion_gate_first_frame_establishes_background_and_returns_none():
    gate = MotionGate()
    frame = _solid_frame((120, 120, 120))
    assert gate.update_and_get_motion_mask(frame) is None


def test_motion_gate_unchanged_scene_reports_no_motion():
    gate = MotionGate()
    frame = _solid_frame((120, 120, 120))
    gate.update_and_get_motion_mask(frame)  # establece el fondo

    motion_mask = gate.update_and_get_motion_mask(frame)  # mismo frame otra vez

    assert motion_mask is not None
    assert np.count_nonzero(motion_mask) == 0


def test_motion_gate_flags_a_region_that_changed():
    gate = MotionGate()
    background = _solid_frame((120, 120, 120))
    gate.update_and_get_motion_mask(background)  # establece el fondo

    changed = background.copy()
    changed[30:70, 30:70] = (0, 200, 0)  # un parche que "aparece" — mano nueva, no fondo
    motion_mask = gate.update_and_get_motion_mask(changed)

    assert motion_mask is not None
    assert motion_mask[50, 50] == 255  # el centro del parche sí se marca
    assert motion_mask[5, 5] == 0  # una esquina sin cambios no se marca


def test_motion_gate_reset_forgets_the_learned_background():
    gate = MotionGate()
    gate.update_and_get_motion_mask(_solid_frame((120, 120, 120)))  # establece el fondo
    gate.reset()

    # Tras el reset, el siguiente frame vuelve a comportarse como el primero: solo
    # establece el fondo, no hay máscara todavía.
    assert gate.update_and_get_motion_mask(_solid_frame((200, 50, 50))) is None
