"""Cliente del robot EV3 — Fase 11, Etapa 1 (ver BITACORA.md "Fase 11, Etapa 1").

Etapa 1 = sin contacto con el robot real: modo ENSAYO por defecto (solo registra lo
que enviaría) y pruebas contra un actuador simulado (`tests/robot_sim.py`). No toca la
detección, el estabilizador ni el contrato Pi->cliente: recibe, cuadro a cuadro, el
gesto YA CONFIRMADO por `GestureStabilizer`, su confianza y una hora monotónica.

Seguridad, en orden de prioridad:
1. PARAR gana siempre: es inmediato, no necesita sostenimiento ni confianza mínima
   (una parada falsa es molesta, no peligrosa) y deja el estado BLOQUEADO.
2. Falla cerrado: cualquier error, timeout, respuesta inesperada, configuración
   inválida, gesto desconocido o dato ilegible NO mueve el robot. Ante un error de
   una orden de movimiento se consulta /estado_ejecucion, se manda la parada y queda
   BLOQUEADO; nunca se reenvía el paso.
3. Topes duros en el código (no solo en la configuración): se comprueban al cargar la
   configuración Y otra vez justo antes de enviar cada orden.
4. Sostenimiento medido en tiempo real (monotónico), no en cuadros: ver HoldTracker.
5. Movimiento solo con estado ARMADO; tras cualquier parada, error o timeout hace
   falta re-armar explícitamente. Desarme automático por inactividad.

PENDIENTE (no verificado): el formato exacto del cuerpo JSON de /ejecutar_ruta y de
las respuestas. El contrato de referencia (claude/CVA_robot-ev3_main_py_respaldo.md)
no está en esta Pi ni en el repositorio; los nombres de campo están aislados en
`build_route_payload()` para corregirlos en un solo lugar cuando se verifique.
"""

import http.client
import json
import logging
import math
import re
import socket
import threading
import time
import urllib.parse
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# --- Topes DUROS: no se pueden subir desde la configuración -------------------------
HARD_MAX_DEGREES_PER_STEP = 180
HARD_MAX_POWER = 30
# Piso del "mínimo entre pasos" configurable: la configuración puede pedir más, nunca menos.
HARD_MIN_STEP_INTERVAL_S = 1.0

# --- Acciones y gestos --------------------------------------------------------------
PARAR = "PARAR"
AVANZAR = "AVANZAR"
GIRAR_DERECHA = "GIRAR_DERECHA"
GIRAR_IZQUIERDA = "GIRAR_IZQUIERDA"
ACTIONS = (PARAR, AVANZAR, GIRAR_DERECHA, GIRAR_IZQUIERDA)

# Signo de (motor izquierdo, motor derecho) por acción de movimiento.
MOVEMENT_SIGNS = {
    AVANZAR: (+1, +1),
    GIRAR_DERECHA: (+1, -1),
    GIRAR_IZQUIERDA: (-1, +1),
}

# Gestos que hoy clasifica `vision/detector.py` (catálogo de Fase B/C2).
KNOWN_GESTURES = ("puño_cerrado", "palma_abierta", "dedo_pulgar", "dedo_menique")

MOTOR_RE = re.compile(r"^out[A-D]$")

PATH_STATUS = "/status"
PATH_EJECUTAR = "/ejecutar_ruta"
PATH_ESTADO = "/estado_ejecucion"
PATH_DETENER = "/detener_ejecucion"
PATH_STOP = "/stop"

# Estados del controlador.
DESARMADO = "DESARMADO"
ARMADO = "ARMADO"
BLOQUEADO = "BLOQUEADO"


class RobotConfigError(ValueError):
    """Configuración inválida: el actuador no arranca."""


class HardLimitError(ValueError):
    """Una orden supera un tope duro: no se envía."""


class RobotTransportError(Exception):
    """Timeout, conexión rechazada, respuesta perdida o ilegible."""


# --- Configuración ------------------------------------------------------------------

@dataclass(frozen=True)
class RobotConfig:
    mapeo: Dict[str, str]
    min_confianza: float = 0.75  # mismo valor que reference/config/robot.json
    grados_paso: int = 90
    potencia: int = 20
    motor_izq: str = "outC"
    motor_der: str = "outD"
    sostener_inicio_s: float = 3.0
    sostener_paso_s: float = 3.0
    hueco_max_s: float = 1.0
    min_entre_pasos_s: float = 3.0
    desarme_inactividad_s: float = 60.0
    timeout_http_s: float = 1.0
    robot_url: Optional[str] = None  # sin valor por defecto: el modo real lo exige


_FIELD_TYPES = {
    "mapeo": dict, "min_confianza": float, "grados_paso": int, "potencia": int,
    "motor_izq": str, "motor_der": str, "sostener_inicio_s": float,
    "sostener_paso_s": float, "hueco_max_s": float, "min_entre_pasos_s": float,
    "desarme_inactividad_s": float, "timeout_http_s": float, "robot_url": (str, type(None)),
}


def _check_type(name, value):
    expected = _FIELD_TYPES[name]
    if isinstance(value, bool):  # bool es int en Python: no se acepta como número
        raise RobotConfigError(f"'{name}': se esperaba {expected}, llegó booleano")
    if expected is float and isinstance(value, int):
        return float(value)
    if not isinstance(value, expected):
        raise RobotConfigError(f"'{name}': tipo inválido {type(value).__name__}")
    if isinstance(value, float) and not math.isfinite(value):
        raise RobotConfigError(f"'{name}': valor no finito")
    return value


def config_from_dict(data) -> RobotConfig:
    if not isinstance(data, dict):
        raise RobotConfigError("la configuración debe ser un objeto JSON")
    known = {f.name for f in fields(RobotConfig)}
    unknown = set(data) - known
    if unknown:
        raise RobotConfigError(f"claves desconocidas: {sorted(unknown)}")
    if "mapeo" not in data:
        raise RobotConfigError("falta 'mapeo'")
    kwargs = {k: _check_type(k, v) for k, v in data.items()}
    if kwargs.get("robot_url") == "":
        kwargs["robot_url"] = None
    cfg = RobotConfig(**kwargs)
    validate_config(cfg)
    return cfg


def load_robot_config(path) -> RobotConfig:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise RobotConfigError(f"no se pudo leer {path}: {e}") from e
    return config_from_dict(data)


def validate_config(cfg: RobotConfig) -> None:
    for g, a in cfg.mapeo.items():
        if g not in KNOWN_GESTURES:
            raise RobotConfigError(f"gesto desconocido en el mapeo: {g!r}")
        if a not in ACTIONS:
            raise RobotConfigError(f"acción desconocida en el mapeo: {a!r}")
    if PARAR not in cfg.mapeo.values():
        raise RobotConfigError("el mapeo debe tener un gesto para PARAR")
    if not (0.0 < cfg.min_confianza <= 1.0):
        raise RobotConfigError("min_confianza debe estar en (0, 1]")
    if not (1 <= cfg.grados_paso <= HARD_MAX_DEGREES_PER_STEP):
        raise RobotConfigError(f"grados_paso fuera del tope duro (1..{HARD_MAX_DEGREES_PER_STEP})")
    if not (1 <= cfg.potencia <= HARD_MAX_POWER):
        raise RobotConfigError(f"potencia fuera del tope duro (1..{HARD_MAX_POWER})")
    for name in ("motor_izq", "motor_der"):
        if not MOTOR_RE.match(getattr(cfg, name)):
            raise RobotConfigError(f"{name} inválido: {getattr(cfg, name)!r}")
    if cfg.motor_izq == cfg.motor_der:
        raise RobotConfigError("motor_izq y motor_der no pueden ser el mismo")
    for name in ("sostener_inicio_s", "sostener_paso_s", "hueco_max_s", "desarme_inactividad_s"):
        if getattr(cfg, name) <= 0:
            raise RobotConfigError(f"{name} debe ser > 0")
    if cfg.min_entre_pasos_s < HARD_MIN_STEP_INTERVAL_S:
        raise RobotConfigError(f"min_entre_pasos_s no puede ser menor que {HARD_MIN_STEP_INTERVAL_S}")
    if not (0.1 <= cfg.timeout_http_s <= 5.0):
        raise RobotConfigError("timeout_http_s debe estar entre 0.1 y 5.0")
    if cfg.robot_url is not None:
        parsed = urllib.parse.urlparse(cfg.robot_url)
        if parsed.scheme != "http" or not parsed.hostname or parsed.path not in ("", "/"):
            raise RobotConfigError(f"robot_url inválida (se espera http://host[:puerto]): {cfg.robot_url!r}")


# --- Órdenes -----------------------------------------------------------------------

def build_route_payload(action: str, cfg: RobotConfig) -> dict:
    """Cuerpo de POST /ejecutar_ruta para un paso.

    PENDIENTE: nombres de campo no verificados contra el contrato real del servicio
    del robot. Lo que sí viene de la especificación de la tarea: motorIzq=outC,
    motorDer=outD, ±grados por motor según la acción y la potencia."""
    if action not in MOVEMENT_SIGNS:
        raise ValueError(f"{action!r} no es una acción de movimiento")
    si, sd = MOVEMENT_SIGNS[action]
    payload = {
        "motorIzq": cfg.motor_izq,
        "motorDer": cfg.motor_der,
        "gradosIzq": si * cfg.grados_paso,
        "gradosDer": sd * cfg.grados_paso,
        "potencia": cfg.potencia,
    }
    check_hard_limits(payload)
    return payload


def check_hard_limits(payload: dict) -> None:
    """Se llama al construir la orden y otra vez justo antes de enviarla."""
    for key in ("gradosIzq", "gradosDer"):
        v = payload.get(key)
        if not isinstance(v, int) or isinstance(v, bool) or abs(v) > HARD_MAX_DEGREES_PER_STEP:
            raise HardLimitError(f"{key}={v!r} supera el tope duro de {HARD_MAX_DEGREES_PER_STEP} grados")
    p = payload.get("potencia")
    if not isinstance(p, int) or isinstance(p, bool) or not (0 < p <= HARD_MAX_POWER):
        raise HardLimitError(f"potencia={p!r} supera el tope duro de {HARD_MAX_POWER}")


# --- Sostenimiento en tiempo real ---------------------------------------------------

class HoldTracker:
    """Cuenta cuánto tiempo REAL lleva sostenido el mismo gesto confirmado.

    Solo suma el intervalo entre dos cuadros consecutivos si ambos traen el mismo
    gesto y están separados por menos de `max_gap_s`. Un hueco >= max_gap_s, un
    cambio de gesto, `None` o un reloj que retrocede reinician la cuenta. Hay un paso
    al llegar a `start_s` y otro por cada `step_s` sostenidos después.

    Por qué así: el estabilizador cuenta CUADROS, y en Fase D se vio que una ráfaga de
    cuadros atrasados (~30 ms) cubre en tiempo real mucho menos que lo que suponen sus
    cuadros, y que hay huecos de hasta 2,4 s sin cuadros. Aquí, una ráfaga solo suma
    lo que realmente duró, y un hueco no se cuenta como "seguía sostenido": se
    reinicia, porque durante el hueco no hay evidencia de que la mano siguiera ahí."""

    def __init__(self, start_s: float, step_s: float, max_gap_s: float) -> None:
        self.start_s = start_s
        self.step_s = step_s
        self.max_gap_s = max_gap_s
        self.reset()

    def reset(self) -> None:
        self.current: Optional[str] = None
        self.last_t: Optional[float] = None
        self.accumulated_s = 0.0
        self.steps = 0

    def observe(self, gesture: Optional[str], t: float) -> bool:
        """True si en este cuadro toca un paso."""
        if gesture is None:
            self.reset()
            return False
        dt = None if self.last_t is None else t - self.last_t
        if gesture == self.current and dt is not None and 0.0 <= dt < self.max_gap_s:
            self.accumulated_s += dt
        else:
            self.current = gesture
            self.accumulated_s = 0.0
            self.steps = 0
        self.last_t = t
        if self.accumulated_s >= self.start_s + self.steps * self.step_s:
            self.steps += 1
            return True
        return False


# --- Transporte HTTP ----------------------------------------------------------------

@dataclass
class RobotResponse:
    status: int
    body: str
    elapsed_ms: float


class DryRunTransport:
    """Modo ENSAYO: no abre ninguna conexión, solo registra lo que enviaría."""

    def __init__(self) -> None:
        self.sent: List[tuple] = []

    def request(self, method: str, path: str, body: Optional[dict] = None) -> RobotResponse:
        self.sent.append((method, path, body))
        logger.info("ENSAYO: enviaría %s %s %s", method, path, json.dumps(body, ensure_ascii=False) if body else "")
        return RobotResponse(200, '{"ensayo": true}', 0.0)

    def close(self) -> None:
        pass


class HttpRobotTransport:
    """HTTP con la biblioteca estándar. Reutiliza la conexión si se usó hace poco
    (`reuse_window_s`); si no, abre una nueva, para no estrenar cada orden con una
    conexión que el servidor ya cerró por inactividad (eso daría un falso error y una
    parada innecesaria). Cualquier fallo cierra la conexión y lanza RobotTransportError."""

    def __init__(self, base_url: str, timeout_s: float, reuse_window_s: float = 5.0) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "http" or not parsed.hostname:
            raise RobotConfigError(f"robot_url inválida: {base_url!r}")
        self.host = parsed.hostname
        self.port = parsed.port or 80
        self.timeout_s = timeout_s
        self.reuse_window_s = reuse_window_s
        self._conn: Optional[http.client.HTTPConnection] = None
        self._last_used = 0.0

    def _connection(self) -> http.client.HTTPConnection:
        if self._conn is not None and time.monotonic() - self._last_used > self.reuse_window_s:
            self.close()
        if self._conn is None:
            self._conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout_s)
        return self._conn

    def request(self, method: str, path: str, body: Optional[dict] = None) -> RobotResponse:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        t0 = time.monotonic()
        try:
            conn = self._connection()
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            text = resp.read().decode("utf-8", errors="replace")
        except (OSError, http.client.HTTPException, socket.timeout) as e:
            self.close()
            raise RobotTransportError(f"{method} {path}: {type(e).__name__}: {e}") from e
        finally:
            self._last_used = time.monotonic()
        return RobotResponse(resp.status, text, (time.monotonic() - t0) * 1000.0)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError as e:
                logger.warning("Error cerrando la conexión con el robot: %s", e)
            self._conn = None


# --- Despachador: una orden en vuelo, PARAR con prioridad, sin bloquear la visión ----

@dataclass
class OrderRecord:
    method: str
    path: str
    body: Optional[dict]
    sent_wall: str
    sent_mono: float
    status: Optional[int]
    elapsed_ms: Optional[float]
    error: Optional[str]


class OrderDispatcher:
    """Ejecuta las órdenes fuera del lazo de visión.

    - Movimiento: como mucho UNA orden en vuelo; si hay una, la nueva se descarta (no
      se encola: un paso viejo enviado tarde es peor que un paso perdido).
    - PARAR: prioridad. Anula cualquier paso pendiente aún no enviado. Si hay un paso
      en vuelo, la parada sale en cuanto ese termine o venza su timeout (peor caso:
      timeout_http_s + tiempo de la parada), por la misma conexión.
    - Error o timeout de un paso: avisa (on_failure -> BLOQUEADO), consulta
      /estado_ejecucion, manda la parada. Nunca reenvía el paso.
    - 409: no se reintenta; se registra.

    Con threaded=False ejecuta en línea (para pruebas deterministas de la lógica)."""

    def __init__(self, transport, on_failure: Callable[[str], None], threaded: bool = True) -> None:
        self.transport = transport
        self.on_failure = on_failure
        self.threaded = threaded
        self.records: List[OrderRecord] = []
        self._cv = threading.Condition()
        self._pending_step: Optional[dict] = None
        self._pending_stop: Optional[str] = None
        self._in_flight = False
        self._closed = False
        self._thread = None
        if threaded:
            self._thread = threading.Thread(target=self._worker, name="robot-dispatcher", daemon=True)
            self._thread.start()

    @property
    def busy(self) -> bool:
        with self._cv:
            return self._in_flight or self._pending_step is not None

    def submit_step(self, payload: dict) -> bool:
        with self._cv:
            if self._closed or self._in_flight or self._pending_step is not None or self._pending_stop:
                return False
            if not self.threaded:
                self._in_flight = True
            else:
                self._pending_step = payload
                self._cv.notify()
                return True
        try:
            self._do_step(payload)
        finally:
            with self._cv:
                self._in_flight = False
        return True

    def request_stop(self, reason: str) -> None:
        with self._cv:
            self._pending_step = None  # un paso no enviado ya no se envía
            if not self.threaded:
                pass
            else:
                self._pending_stop = reason
                self._cv.notify()
                return
        self._do_stop(reason)

    def close(self, timeout_s: float = 5.0) -> None:
        """Termina el hilo después de enviar cualquier parada pendiente."""
        with self._cv:
            self._closed = True
            self._cv.notify()
        if self._thread is not None:
            self._thread.join(timeout_s)
            if self._thread.is_alive():
                logger.error("El despachador del robot no terminó en %.1fs", timeout_s)
        self.transport.close()

    def wait_idle(self, timeout_s: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout_s
        with self._cv:
            while self._in_flight or self._pending_step is not None or self._pending_stop:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._cv.wait(left)
        return True

    def _worker(self) -> None:
        while True:
            with self._cv:
                while not (self._pending_stop or self._pending_step is not None or self._closed):
                    self._cv.wait()
                if self._pending_stop:
                    reason, self._pending_stop, kind = self._pending_stop, None, "stop"
                elif self._pending_step is not None:
                    payload, self._pending_step, kind = self._pending_step, None, "step"
                else:  # cerrado y sin nada pendiente
                    return
                self._in_flight = True
            try:
                if kind == "stop":
                    self._do_stop(reason)
                else:
                    self._do_step(payload)
            except Exception:  # el hilo no puede morir en silencio: se registra y se bloquea
                logger.exception("Error inesperado en el despachador del robot")
                self.on_failure("error inesperado en el despachador")
            finally:
                with self._cv:
                    self._in_flight = False
                    self._cv.notify_all()

    def _send(self, method: str, path: str, body: Optional[dict] = None) -> Optional[RobotResponse]:
        sent_wall = datetime.now().astimezone().isoformat(timespec="milliseconds")
        sent_mono = time.monotonic()
        try:
            resp = self.transport.request(method, path, body)
        except RobotTransportError as e:
            self.records.append(OrderRecord(method, path, body, sent_wall, sent_mono, None,
                                            (time.monotonic() - sent_mono) * 1000.0, str(e)))
            logger.error("Robot: %s %s enviada %s -> ERROR tras %.0f ms: %s",
                         method, path, sent_wall, (time.monotonic() - sent_mono) * 1000.0, e)
            return None
        self.records.append(OrderRecord(method, path, body, sent_wall, sent_mono, resp.status, resp.elapsed_ms, None))
        logger.info("Robot: %s %s enviada %s -> %d en %.0f ms", method, path, sent_wall, resp.status, resp.elapsed_ms)
        return resp

    def _do_step(self, payload: dict) -> None:
        try:
            check_hard_limits(payload)  # segunda comprobación, justo antes de enviar
        except HardLimitError as e:
            logger.error("Robot: orden NO enviada, %s", e)
            self.on_failure(f"tope duro: {e}")
            return
        resp = self._send("POST", PATH_EJECUTAR, payload)
        if resp is not None and 200 <= resp.status < 300:
            return
        if resp is not None and resp.status == 409:
            logger.warning("Robot: 409 (ocupado) en %s -- no se reintenta", PATH_EJECUTAR)
            return
        reason = "sin respuesta" if resp is None else f"respuesta inesperada {resp.status}"
        self.on_failure(f"paso fallido ({reason})")
        estado = self._send("GET", PATH_ESTADO)
        if estado is not None:
            logger.warning("Robot: estado tras el fallo: %d %s", estado.status, estado.body[:200])
        self._do_stop(f"tras paso fallido ({reason})")

    def _do_stop(self, reason: str) -> None:
        logger.warning("Robot: PARADA (%s): %s y luego %s", reason, PATH_DETENER, PATH_STOP)
        r1 = self._send("POST", PATH_DETENER)
        r2 = self._send("POST", PATH_STOP)
        ok = all(r is not None and 200 <= r.status < 300 for r in (r1, r2))
        if not ok:
            logger.error("Robot: PARADA NO CONFIRMADA (%s) -- revisar el robot en persona", reason)


# --- Controlador --------------------------------------------------------------------

class RobotController:
    """Máquina de estados DESARMADO / ARMADO / BLOQUEADO. `observe()` se llama una vez
    por cuadro desde el lazo de visión y nunca bloquea (solo encola en el despachador)."""

    def __init__(self, cfg: RobotConfig, dispatcher_factory: Callable[[Callable[[str], None]], OrderDispatcher]) -> None:
        validate_config(cfg)
        self.cfg = cfg
        self._lock = threading.RLock()
        self.state = DESARMADO
        self.state_reason = "inicio"
        self.tracker = HoldTracker(cfg.sostener_inicio_s, cfg.sostener_paso_s, cfg.hueco_max_s)
        self._prev_action: Optional[str] = None
        self._last_step_t: Optional[float] = None
        self._last_activity_t: Optional[float] = None
        self.illegible = 0
        self.dispatcher = dispatcher_factory(self._on_dispatch_failure)

    # -- comandos locales --
    def arm(self, now: float) -> None:
        with self._lock:
            self.state, self.state_reason = ARMADO, "armado explícito"
            self.tracker.reset()  # hay que sostener desde cero después de armar
            self._last_activity_t = now
            self._last_step_t = None
        logger.warning("Robot: ARMADO")

    def disarm(self, reason: str) -> None:
        with self._lock:
            self.state, self.state_reason = DESARMADO, reason
            self.tracker.reset()
        logger.warning("Robot: DESARMADO (%s)", reason)
        self.dispatcher.request_stop(f"desarme: {reason}")

    def manual_stop(self, reason: str = "parada manual") -> None:
        self._latch(reason)
        self.dispatcher.request_stop(reason)

    # -- lazo de visión --
    def observe(self, gesture, confidence, now: float) -> None:
        self.tick(now)
        action = self._classify(gesture, confidence)
        with self._lock:
            if action == PARAR:
                if self._prev_action != PARAR:
                    self._prev_action = PARAR
                    self.tracker.reset()
                    stop = True
                else:
                    stop = False
            else:
                stop = False
                self._prev_action = action
        if action == PARAR:
            if stop:
                self.manual_stop(f"gesto {gesture} = PARAR")
            return
        with self._lock:
            if action is None or self.state != ARMADO:
                self.tracker.reset()
                return
            if not self.tracker.observe(gesture, now):
                return
            if self._last_step_t is not None and now - self._last_step_t < self.cfg.min_entre_pasos_s:
                logger.info("Robot: paso de %s omitido (mínimo entre pasos %.1fs)", action, self.cfg.min_entre_pasos_s)
                return
            try:
                payload = build_route_payload(action, self.cfg)
            except HardLimitError as e:
                logger.error("Robot: %s", e)
                self._latch_locked(f"tope duro: {e}")
                return
            if not self.dispatcher.submit_step(payload):
                logger.warning("Robot: paso de %s descartado (hay una orden en vuelo)", action)
                return
            self._last_step_t = now
            self._last_activity_t = now
        logger.info("Robot: paso %s encolado (%.1fs sostenido)", action, self.tracker.accumulated_s)

    def tick(self, now: float) -> None:
        with self._lock:
            idle = (self.state == ARMADO and self._last_activity_t is not None
                    and now - self._last_activity_t >= self.cfg.desarme_inactividad_s)
        if idle:
            self.disarm(f"inactividad >= {self.cfg.desarme_inactividad_s:.0f}s")

    def close(self) -> None:
        self.disarm("cierre del proceso")
        self.dispatcher.close()

    # -- internos --
    def _classify(self, gesture, confidence) -> Optional[str]:
        if gesture is None or gesture == "ninguno":
            return None
        if not isinstance(gesture, str) or gesture not in self.cfg.mapeo:
            if not isinstance(gesture, str) or gesture not in KNOWN_GESTURES:
                self.illegible += 1
                logger.warning("Robot: gesto ilegible o desconocido ignorado: %r", gesture)
            return None
        action = self.cfg.mapeo[gesture]
        if action == PARAR:
            return PARAR  # PARAR no exige confianza mínima: parar de más es seguro
        if (not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
                or not math.isfinite(confidence) or not (0.0 <= confidence <= 1.0)):
            self.illegible += 1
            logger.warning("Robot: confianza ilegible ignorada: %r", confidence)
            return None
        if confidence < self.cfg.min_confianza:
            return None
        return action

    def _latch(self, reason: str) -> None:
        with self._lock:
            self._latch_locked(reason)

    def _latch_locked(self, reason: str) -> None:
        self.state, self.state_reason = BLOQUEADO, reason
        self.tracker.reset()
        logger.warning("Robot: BLOQUEADO (%s) -- requiere re-armado explícito", reason)

    def _on_dispatch_failure(self, reason: str) -> None:
        self._latch(reason)


def make_transport(cfg: RobotConfig, real: bool):
    """ENSAYO por defecto. El modo real exige la bandera explícita Y robot_url."""
    if not real:
        return DryRunTransport()
    if not cfg.robot_url:
        raise RobotConfigError("modo real sin 'robot_url' en la configuración: no se arranca")
    return HttpRobotTransport(cfg.robot_url, cfg.timeout_http_s)
