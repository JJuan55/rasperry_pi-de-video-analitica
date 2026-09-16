"""Tests de la parte de vision/detector.py testeable sin cámara real: la geometría de
conteo de dedos (sobre contornos sintéticos dibujados a propósito, no fotos), la
clasificación y el formato/umbral de la línea que sale por send_line().

Lo que depende de inferencia YOLO real o de fotos reales de una mano (segment_hand
contra piel real, GestureDetector.detect end-to-end) queda fuera de este archivo — es
validación manual documentada en BITACORA.md "Fase 8", igual que el smoke test de
Fase 6.
"""

import cv2
import numpy as np

from cva_gesture_bridge.vision.detector import (
    GESTURE_DEDO_ANULAR,
    GESTURE_DEDO_INDICE,
    GESTURE_PALMA_ABIERTA,
    GESTURE_PUÑO_CERRADO,
    GestureResult,
    classify_gesture,
    count_extended_fingers,
    format_line,
)


def _contour_from_mask(mask: np.ndarray) -> np.ndarray:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return max(contours, key=cv2.contourArea)


def _fist_contour() -> np.ndarray:
    """Blob compacto y convexo, sin dedos — simula un puño cerrado."""
    mask = np.zeros((200, 200), dtype=np.uint8)
    cv2.circle(mask, (100, 100), 70, 255, -1)
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
    test contra un rectángulo con 5 dedos parejos, que fallaba con solo 2-3 defects)."""
    tips_x = [50, 85, 120, 155, 190]
    tips_y = [60, 25, 15, 30, 70]
    valley_x = [68, 103, 138, 173]
    valley_y = 140

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


def test_classify_gesture_single_finger_on_left_is_indice():
    contour = _one_finger_contour(finger_on_left=True)
    gesture, confidence = classify_gesture(contour, extended_fingers=1)
    assert gesture == GESTURE_DEDO_INDICE
    assert 0.0 < confidence <= 1.0


def test_classify_gesture_single_finger_on_right_is_anular():
    contour = _one_finger_contour(finger_on_left=False)
    gesture, confidence = classify_gesture(contour, extended_fingers=1)
    assert gesture == GESTURE_DEDO_ANULAR
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
    result = GestureResult(gesture=GESTURE_DEDO_ANULAR, confidence=0.3, extended_fingers=1)
    assert format_line(result, min_confidence=0.5) is None


def test_format_line_formats_gesture_and_confidence_above_threshold():
    result = GestureResult(gesture=GESTURE_DEDO_ANULAR, confidence=0.876, extended_fingers=1)
    line = format_line(result, min_confidence=0.5)
    assert line == "gesto: dedo_anular, confianza: 0.88"
