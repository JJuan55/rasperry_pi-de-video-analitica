"""Reconocimiento de gestos — Fase 8: YOLO para localizar la región de la mano/persona,
OpenCV clásico (contorno + convex hull + convexity defects) para clasificar el gesto.

Decisión de arquitectura (JD, ver BITACORA.md "Fase 8"): no existe ningún modelo/dataset
ya entrenado para los 4 gestos de esta fase. En vez de entrenar uno nuevo o bajar pesos
de terceros no verificados, YOLO (pesos oficiales de Ultralytics, sin fine-tuning) solo
localiza la región de la persona en el frame para acotar la búsqueda; la clasificación
real del gesto es 100% OpenCV (segmentación por color de piel + conteo de dedos vía
convexity defects). Es un prototipo heurístico, no un clasificador entrenado — su
precisión real contra AC3 (≥70% aciertos) depende de la validación manual con cámara
real documentada en BITACORA.md, no de este código en aislamiento.

Catálogo de esta fase (subconjunto de GESTOS.md, decisión de JD): puño_cerrado,
palma_abierta, dedo_indice, dedo_anular. `dedo_menique`, `dedo_medio` y sus mapeos
completos quedan para fases futuras.
"""

import logging
import math
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

GESTURE_PUÑO_CERRADO = "puño_cerrado"
GESTURE_PALMA_ABIERTA = "palma_abierta"
GESTURE_DEDO_INDICE = "dedo_indice"
GESTURE_DEDO_ANULAR = "dedo_anular"

# COCO class 0 = "person" — no existe clase de "mano" en los pesos oficiales sin
# fine-tuning (ver nota de arquitectura arriba).
YOLO_PERSON_CLASS_ID = 0

# Rango HSV de piel usado para segmentar la mano dentro de la región de interés.
# Heurística clásica de OpenCV — su precisión varía con el tono de piel y la
# iluminación; ver limitación documentada en el reporte de cierre de Fase 8.
_SKIN_HSV_LOW = np.array([0, 30, 60], dtype=np.uint8)
_SKIN_HSV_HIGH = np.array([20, 150, 255], dtype=np.uint8)

_MIN_CONTOUR_AREA = 1500  # px² — descarta ruido pequeño en la máscara de piel
_MIN_DEFECT_DEPTH_RATIO = 0.15  # proporción de la diagonal del bounding box
_SINGLE_FINGER_ASPECT_THRESHOLD = 1.4  # alto/ancho mínimo para distinguir 1 dedo de puño
_SINGLE_FINGER_ASPECT_CONFIDENT = 2.2  # alto/ancho a partir del cual la confianza satura


@dataclass
class GestureResult:
    gesture: Optional[str]
    confidence: float
    extended_fingers: int


def _distance(a: Tuple[int, int], b: Tuple[int, int]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def count_finger_gaps(contour: np.ndarray) -> int:
    """Cuenta los "huecos" entre dedos extendidos vía convexity defects.

    Cada hueco cualificado (ángulo <= 90° en el punto más profundo, profundidad
    relevante respecto al tamaño de la mano) corresponde a un espacio entre dos dedos
    extendidos consecutivos.
    """
    if contour is None or len(contour) < 5:
        return 0

    try:
        hull_indices = cv2.convexHull(contour, returnPoints=False)
    except cv2.error as exc:
        logger.warning("convexHull falló sobre un contorno degenerado: %s", exc)
        return 0

    if hull_indices is None or len(hull_indices) < 3:
        return 0

    try:
        defects = cv2.convexityDefects(contour, hull_indices)
    except cv2.error as exc:
        logger.warning("convexityDefects falló sobre un contorno degenerado: %s", exc)
        return 0

    if defects is None:
        return 0

    x, y, w, h = cv2.boundingRect(contour)
    scale = math.hypot(w, h)
    if scale == 0:
        return 0

    # cv2.convexityDefects devuelve shape (N, 1, 4) en versiones clásicas de OpenCV y
    # (N, 4) en OpenCV 5 — se normaliza para no depender de cuál sea.
    defects = defects.reshape(-1, 4)

    gaps = 0
    for i in range(defects.shape[0]):
        s, e, f, d = defects[i]
        depth = d / 256.0
        if depth / scale < _MIN_DEFECT_DEPTH_RATIO:
            continue

        start = tuple(contour[s][0])
        end = tuple(contour[e][0])
        far = tuple(contour[f][0])
        a_side = _distance(end, start)
        b_side = _distance(far, start)
        c_side = _distance(far, end)
        if b_side == 0 or c_side == 0:
            continue

        cos_angle = (b_side**2 + c_side**2 - a_side**2) / (2 * b_side * c_side)
        angle = math.acos(max(-1.0, min(1.0, cos_angle)))
        if angle <= math.pi / 2:
            gaps += 1

    return gaps


def count_extended_fingers(contour: np.ndarray) -> int:
    """Estima cuántos dedos están extendidos.

    Con 0 huecos cualificados no se puede distinguir puño cerrado (0 dedos) de un solo
    dedo extendido (1 dedo, sin hueco porque no hay un dedo vecino) solo con defects —
    se usa como segunda señal el aspect ratio del bounding box (una mano con un dedo
    extendido es notablemente más alta que ancha; un puño es compacto).
    """
    gaps = count_finger_gaps(contour)
    if gaps >= 1:
        return min(gaps + 1, 5)

    x, y, w, h = cv2.boundingRect(contour)
    aspect = (h / w) if w > 0 else 0.0
    return 1 if aspect >= _SINGLE_FINGER_ASPECT_THRESHOLD else 0


def classify_gesture(contour: np.ndarray, extended_fingers: int) -> Tuple[Optional[str], float]:
    """Clasifica el gesto entre los 4 del catálogo de esta fase, con una confianza
    heurística (no es una probabilidad aprendida — ver nota de módulo)."""
    area = cv2.contourArea(contour)
    hull = cv2.convexHull(contour)
    hull_area = cv2.contourArea(hull)
    solidity = (area / hull_area) if hull_area > 0 else 0.0

    if extended_fingers == 0:
        return GESTURE_PUÑO_CERRADO, min(1.0, solidity)

    if extended_fingers >= 4:
        confidence = min(1.0, (extended_fingers / 5.0) * max(solidity, 0.5))
        return GESTURE_PALMA_ABIERTA, confidence

    if extended_fingers == 1:
        x, y, w, h = cv2.boundingRect(contour)
        aspect = (h / w) if w > 0 else 0.0
        confidence = min(1.0, aspect / _SINGLE_FINGER_ASPECT_CONFIDENT)

        # Desambiguar índice vs. anular por la posición horizontal de la punta del
        # dedo respecto al centro del bounding box. Asume una orientación de mano
        # consistente (dorso o palma de frente a la cámara, dedos hacia arriba) — es
        # el supuesto más frágil de esta fase, ver reporte de cierre de Fase 8.
        fingertip = min(contour.reshape(-1, 2).tolist(), key=lambda p: p[1])
        center_x = x + w / 2.0
        if fingertip[0] < center_x:
            return GESTURE_DEDO_INDICE, confidence
        return GESTURE_DEDO_ANULAR, confidence

    # 2 o 3 dedos extendidos: fuera del catálogo de 4 gestos de esta fase.
    return None, 0.0


def segment_hand(frame_bgr: np.ndarray) -> Optional[np.ndarray]:
    """Encuentra el contorno más grande que parece piel dentro del frame (o ROI)."""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, _SKIN_HSV_LOW, _SKIN_HSV_HIGH)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < _MIN_CONTOUR_AREA:
        return None
    return largest


def format_line(result: GestureResult, min_confidence: float) -> Optional[str]:
    """Línea a mandar por `TcpServer.send_line` (sección 4.4 de CLAUDE.md) — solo
    gesto + confianza en esta fase, nunca una instrucción de actuador (Fase 9).

    Función libre (no depende de un modelo YOLO cargado) para poder testear la lógica
    de umbral sin necesitar cámara ni pesos reales.
    """
    if result.gesture is None or result.confidence < min_confidence:
        return None
    return f"gesto: {result.gesture}, confianza: {result.confidence:.2f}"


class GestureDetector:
    """Detector real de gestos sobre frames JPEG crudos (Fase 8)."""

    def __init__(self, model_path: str, min_confidence: float) -> None:
        from ultralytics import YOLO  # import perezoso: pesado, no hace falta en tests

        self._model = YOLO(model_path)
        self._min_confidence = min_confidence

    def _find_person_roi(self, frame_bgr: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
        results = self._model.predict(
            frame_bgr, classes=[YOLO_PERSON_CLASS_ID], verbose=False
        )
        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return None

        best_idx = int(boxes.conf.argmax())
        x1, y1, x2, y2 = boxes.xyxy[best_idx].tolist()
        h, w = frame_bgr.shape[:2]
        x1, y1 = max(0, int(x1)), max(0, int(y1))
        x2, y2 = min(w, int(x2)), min(h, int(y2))
        if x2 <= x1 or y2 <= y1:
            return None
        return x1, y1, x2, y2

    def detect(self, jpeg_bytes: bytes) -> GestureResult:
        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            logger.warning("No se pudo decodificar un frame JPEG recibido (%d bytes)", len(jpeg_bytes))
            return GestureResult(None, 0.0, 0)

        roi_box = self._find_person_roi(frame)
        roi = frame[roi_box[1] : roi_box[3], roi_box[0] : roi_box[2]] if roi_box else frame

        contour = segment_hand(roi)
        if contour is None:
            return GestureResult(None, 0.0, 0)

        extended = count_extended_fingers(contour)
        gesture, confidence = classify_gesture(contour, extended)
        return GestureResult(gesture, confidence, extended)

    def format_line(self, result: GestureResult) -> Optional[str]:
        return format_line(result, self._min_confidence)
