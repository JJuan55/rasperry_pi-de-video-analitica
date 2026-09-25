"""Reconocimiento de gestos — Fase B: MediaPipe HandLandmarker reemplaza por completo
el diseño de Fase 8 (YOLO para localizar "persona" + OpenCV para segmentar piel/contar
dedos + MotionGate + recorte de franja superior). Ver BITACORA.md "Fase B".

Por qué se retiró el diseño anterior — no lo reintroduzcas "por las dudas" sin releer
esto primero, el conflicto está documentado con datos reales, no es una corazonada:

- La segmentación por color de piel (HSV) confundía cara, cuello y torso con una mano
  real — geométricamente casi indistinguibles a la resolución real del cliente
  (320x240). Ver BITACORA.md "Fase 8", los dos fixes de falsos positivos.
- El recorte de franja superior (para sacar la cara) y `MotionGate` (para sacar
  torso/cara estáticos) fueron parches sobre ese problema de raíz, no una solución de
  fondo — y `MotionGate` en particular introdujo un conflicto estructural nuevo: el
  fondo se actualiza con cada frame procesado, incluso mientras hay una mano real en
  cuadro, así que una mano que cambia de gesto sin salir de cuadro se va fundiendo con
  su propio fondo reciente y el conteo de dedos se rompe. Confirmado con datos reales
  (ver BITACORA.md "Fase 8", "Segundo fix de falsos positivos").
- YOLO solo localizaba una caja "persona" gruesa (no existe clase "mano" en los pesos
  oficiales sin fine-tuning) — pagaba ~440ms de costo real sin aportar nada a la
  clasificación en sí, que era 100% heurística de OpenCV sobre esa caja.

MediaPipe HandLandmarker detecta la mano directamente (no hace falta acotar la región
con otro modelo antes) y da 21 landmarks 3D reales por mano, con su propio score de
confianza — reemplaza tanto la localización (antes YOLO) como la clasificación
geométrica (antes OpenCV) de una sola vez. Verificado con datos reales de las dos
sesiones de captura de Fase A (rama `spike/fase8-mediapipe-viabilidad`): confianza
consistentemente >0.92 en 46 gestos sostenidos reales, contra 0 reconocimientos de
`palma_abierta` del sistema viejo sobre la misma persona en la misma sesión.

Catálogo de gestos: igual que Fase 8 (subconjunto de GESTOS.md, decisión de JD) —
puño_cerrado, palma_abierta, dedo_pulgar, dedo_menique.
"""

import logging
import math
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

GESTURE_PUÑO_CERRADO = "puño_cerrado"
GESTURE_PALMA_ABIERTA = "palma_abierta"
GESTURE_DEDO_PULGAR = "dedo_pulgar"
GESTURE_DEDO_MENIQUE = "dedo_menique"

# Índices de los 21 landmarks de MediaPipe Hands (topología pública y fija).
WRIST = 0
THUMB_MCP, THUMB_TIP = 2, 4
FINGER_MCP_TIP = {
    "indice": (5, 8),
    "medio": (9, 12),
    "anular": (13, 16),
    "menique": (17, 20),
}

# Umbrales de "dedo extendido" sobre coordenadas de MUNDO (hand_world_landmarks, 3D en
# metros, normalizadas por el tamaño real de la mano — no píxeles de imagen, así no
# dependen de qué tan cerca esté la mano de la cámara).
#
# RECALIBRADOS con datos reales de Fase B (no se dieron por buenos los valores
# heredados del spike, que estaban tuneados sobre landmarks normalizados de imagen en
# 2D, no sobre mundo 3D) — ver BITACORA.md "Fase B", investigación de fixtures reales:
# con el umbral original del pulgar (1.3, medido contra el nudillo base del índice)
# un pulgar-arriba
# real medía ratio=1.253 y quedaba clasificado como puño_cerrado (falso negativo real,
# confirmado con imagen real inspeccionada visualmente, no solo con la etiqueta del
# nombre de archivo — esas etiquetas vienen del sistema viejo YOLO+OpenCV y resultaron
# no confiables como ground truth). Medir el pulgar contra la MUÑECA, igual que los
# otros 4 dedos, separa mucho mejor los dos casos reales: puño real=1.424,
# pulgar-arriba real=2.184 — margen amplio, umbral 1.7 a mitad de camino.
_THUMB_EXTENDED_RATIO = 1.7
_FINGER_EXTENDED_RATIO = 1.15


@dataclass
class GestureResult:
    gesture: Optional[str]
    confidence: float
    extended_fingers: int


class GestureStabilizer:
    """Confirma un gesto si aparece al menos `min_matches` veces dentro de las
    últimas `window_size` detecciones crudas — filtra ruido de un frame aislado sin
    exigir que sea el mismo gesto en TODAS las muestras seguidas. Sin cambios desde
    Fase 8 (ver BITACORA.md) — es independiente de qué backend de visión se use."""

    def __init__(self, window_size: int = 3, min_matches: int = 2) -> None:
        from collections import deque

        self._window_size = window_size
        self._min_matches = min_matches
        self._history: deque = deque(maxlen=window_size)

    def observe(self, gesture: Optional[str]) -> Optional[str]:
        self._history.append(gesture)
        if gesture is None:
            return None
        matches = sum(1 for g in self._history if g == gesture)
        return gesture if matches >= self._min_matches else None


class OneEuroFilter:
    """Filtro adaptativo de suavizado de un valor escalar en el tiempo (Casiez et al.
    2012, "1€ Filter") — corta más ruido cuando el movimiento es lento y menos cuando
    es rápido, para no introducir lag perceptible en movimientos reales de la mano.
    Una instancia filtra UNA dimensión escalar; `LandmarkSmoother` de abajo maneja las
    63 (21 landmarks x,y,z) que hacen falta para una mano completa."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.0, d_cutoff: float = 1.0) -> None:
        self._min_cutoff = min_cutoff
        self._beta = beta
        self._d_cutoff = d_cutoff
        self._x_prev: Optional[float] = None
        self._dx_prev = 0.0
        self._t_prev: Optional[float] = None

    def reset(self) -> None:
        self._x_prev = None
        self._dx_prev = 0.0
        self._t_prev = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: float, t: float) -> float:
        if self._t_prev is None:
            self._x_prev, self._dx_prev, self._t_prev = x, 0.0, t
            return x

        dt = max(t - self._t_prev, 1e-6)  # nunca dt<=0 -- evita división por cero si
        # dos frames llegan con el mismo timestamp (resolución de reloj limitada)
        dx = (x - self._x_prev) / dt
        a_d = self._alpha(self._d_cutoff, dt)
        dx_hat = a_d * dx + (1 - a_d) * self._dx_prev

        cutoff = self._min_cutoff + self._beta * abs(dx_hat)
        a = self._alpha(cutoff, dt)
        x_hat = a * x + (1 - a) * self._x_prev

        self._x_prev, self._dx_prev, self._t_prev = x_hat, dx_hat, t
        return x_hat


class LandmarkSmoother:
    """Aplica un `OneEuroFilter` independiente a cada una de las 63 coordenadas (21
    landmarks x,y,z) de la mano, a través de la secuencia temporal de frames de una
    misma conexión. `reset()` cuando la mano desaparece de cuadro — si no, el primer
    landmark real tras un hueco se suavizaría contra una posición vieja y arrastraría
    un salto falso en vez de aparecer limpio."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.0) -> None:
        self._min_cutoff = min_cutoff
        self._beta = beta
        self._filters: Optional[List[OneEuroFilter]] = None

    def reset(self) -> None:
        self._filters = None

    def smooth(self, landmarks_xyz: List[Tuple[float, float, float]], t: float) -> List[Tuple[float, float, float]]:
        if self._filters is None:
            self._filters = [OneEuroFilter(self._min_cutoff, self._beta) for _ in range(len(landmarks_xyz) * 3)]

        out = []
        for i, (x, y, z) in enumerate(landmarks_xyz):
            fx, fy, fz = self._filters[i * 3], self._filters[i * 3 + 1], self._filters[i * 3 + 2]
            out.append((fx(x, t), fy(y, t), fz(z, t)))
        return out


def _dist3(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def extended_fingers_pattern(landmarks_xyz: List[Tuple[float, float, float]]) -> Tuple[bool, bool, bool, bool, bool]:
    """`landmarks_xyz`: 21 tuplas (x,y,z) en metros (hand_world_landmarks, ya
    suavizadas o crudas — la función no lo asume). Devuelve una tupla de 5 booleanos
    (pulgar, índice, medio, anular, meñique) — extendido o no. Un dedo se considera
    extendido si la punta está más lejos de la muñeca que su nudillo base, con margen
    (`_FINGER_EXTENDED_RATIO`/`_THUMB_EXTENDED_RATIO` — ver comentario de esas
    constantes sobre por qué el pulgar también se mide contra la muñeca, no contra
    `INDEX_MCP` como en la primera versión de este archivo)."""
    wrist = landmarks_xyz[WRIST]

    thumb_extended = _dist3(wrist, landmarks_xyz[THUMB_TIP]) > (
        _dist3(wrist, landmarks_xyz[THUMB_MCP]) * _THUMB_EXTENDED_RATIO
    )

    fingers = []
    for _name, (mcp, tip) in FINGER_MCP_TIP.items():
        extended = _dist3(wrist, landmarks_xyz[tip]) > (_dist3(wrist, landmarks_xyz[mcp]) * _FINGER_EXTENDED_RATIO)
        fingers.append(extended)

    return (thumb_extended, *fingers)


def classify_world_landmarks(landmarks_xyz: List[Tuple[float, float, float]]) -> Tuple[Optional[str], int]:
    """De 21 landmarks de mundo a la decisión final de gesto — punto de entrada
    testeable sin cámara/MediaPipe real (basta con pasar landmarks sintéticos o de un
    fixture guardado). Devuelve (gesto_o_None, cantidad_de_dedos_extendidos)."""
    pattern = extended_fingers_pattern(landmarks_xyz)
    n_extended = sum(pattern)

    if pattern == (False, False, False, False, False):
        return GESTURE_PUÑO_CERRADO, n_extended
    if pattern == (True, False, False, False, False):
        return GESTURE_DEDO_PULGAR, n_extended
    if pattern == (False, False, False, False, True):
        return GESTURE_DEDO_MENIQUE, n_extended
    # Mismo criterio que Fase 8: >=4 dedos extendidos (aunque no sean exactamente los
    # 5) cuenta como palma abierta — validado contra los fixtures reales de Fase A,
    # ver BITACORA.md "Fase A": el pulgar a veces no cruza el umbral aunque la palma
    # esté genuinamente abierta.
    if n_extended >= 4:
        return GESTURE_PALMA_ABIERTA, n_extended

    # 2 o 3 dedos extendidos: fuera del catálogo de 4 gestos de esta fase.
    return None, n_extended


def format_line(result: GestureResult, min_confidence: float) -> Optional[str]:
    """Línea a mandar por `TcpServer.send_line` (sección 4.4 de CLAUDE.md) — solo
    gesto + confianza, nunca una instrucción de actuador (Fase 9). Sin cambios de
    formato desde Fase 8."""
    if result.gesture is None or result.confidence < min_confidence:
        return None
    return f"gesto: {result.gesture}, confianza: {result.confidence:.2f}"


class GestureDetector:
    """Detector real de gestos sobre frames JPEG crudos — Fase B (MediaPipe)."""

    def __init__(
        self,
        model_path: str,
        min_confidence: float,
        min_hand_detection_confidence: float = 0.5,
        min_hand_presence_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ) -> None:
        # Import perezoso: mediapipe es pesado, no hace falta para importar el módulo
        # (los tests de geometría pura no lo necesitan).
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        self._mp = mp
        base_options = mp_python.BaseOptions(model_asset_path=model_path)
        options = mp_vision.HandLandmarkerOptions(
            base_options=base_options,
            # VIDEO, no IMAGE: habilita el tracking interno de MediaPipe entre frames
            # de una misma conexión (usa min_tracking_confidence) en vez de re-detectar
            # desde cero cada vez -- coherente con que los frames llegan en una
            # secuencia temporal real, no como fotos sueltas sin relación.
            running_mode=mp_vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=min_hand_detection_confidence,
            min_hand_presence_confidence=min_hand_presence_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self._landmarker = mp_vision.HandLandmarker.create_from_options(options)
        self._min_confidence = min_confidence
        self._smoother = LandmarkSmoother()
        self._last_timestamp_ms = 0

    def _next_timestamp_ms(self) -> int:
        # VIDEO mode exige timestamps estrictamente crecientes -- el reloj de pared
        # puede repetirse entre dos llamadas muy seguidas (resolución de milisegundo),
        # así que se fuerza el incremento mínimo de 1ms cuando eso pasa.
        now_ms = int(time.time() * 1000)
        self._last_timestamp_ms = max(now_ms, self._last_timestamp_ms + 1)
        return self._last_timestamp_ms

    def detect(self, jpeg_bytes: bytes) -> GestureResult:
        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            logger.warning("No se pudo decodificar un frame JPEG recibido (%d bytes)", len(jpeg_bytes))
            return GestureResult(None, 0.0, 0)

        mp_image = self._mp.Image(
            image_format=self._mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        )
        timestamp_ms = self._next_timestamp_ms()
        result = self._landmarker.detect_for_video(mp_image, timestamp_ms)

        if not result.hand_world_landmarks:
            # Sin mano en este frame -- resetear el suavizado para que la próxima
            # mano real que aparezca no arrastre un salto falso contra esta posición
            # vieja (ver docstring de LandmarkSmoother).
            self._smoother.reset()
            return GestureResult(None, 0.0, 0)

        raw_xyz = [(lm.x, lm.y, lm.z) for lm in result.hand_world_landmarks[0]]
        smoothed_xyz = self._smoother.smooth(raw_xyz, timestamp_ms / 1000.0)

        confidence = float(result.handedness[0][0].score)
        gesture, extended = classify_world_landmarks(smoothed_xyz)
        return GestureResult(gesture, confidence if gesture is not None else 0.0, extended)

    def format_line(self, result: GestureResult) -> Optional[str]:
        return format_line(result, self._min_confidence)
