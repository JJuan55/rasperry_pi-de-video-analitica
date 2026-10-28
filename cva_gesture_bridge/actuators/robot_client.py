"""Cliente del robot EV3 — Fase 11, Etapa 1 (ver BITACORA.md "Fase 11, Etapa 1").

Etapa 1 = sin contacto con el robot real: modo ENSAYO por defecto (solo registra lo
que enviaría) y pruebas contra un actuador simulado (`tests/robot_sim.py`). No toca la
detección, el estabilizador ni el contrato Pi->cliente. Recibe, cuadro a cuadro: el
gesto CONFIRMADO por `GestureStabilizer` (solo sirve para iniciar el conteo y para
PARAR), el gesto CRUDO del detector en ese cuadro (es la evidencia que se cuenta), sus
confianzas y la hora del cuadro.

Contrato del servicio del robot: docs/CONTRATO_ROBOT_EV3.md.

Seguridad, en orden de prioridad:
1. PARAR gana siempre: activa el cerrojo al instante (ninguna ruta nueva sale hasta
   re-armar) y sale por una conexión NUEVA en su propio hilo, sin esperar al paso en
   vuelo. No se da por cumplido al enviarlo: queda en PARANDO vigilando
   /estado_ejecucion hasta confirmar (ver StopSupervisor).
2. Falla cerrado: error, timeout, respuesta inesperada (incluido totalPasos distinto
   de los pasos enviados), configuración inválida, gesto desconocido o dato ilegible
   NO mueven el robot; un paso fallido dispara PARAR y nunca se reenvía.
3. Topes duros en el código, no solo en la configuración: se comprueban al cargar la
   configuración y otra vez justo antes de enviar cada orden.
4. Sostenimiento medido en tiempo real y solo con evidencia cruda (HoldTracker).
5. Movimiento solo en ARMADO; tras cualquier parada, error o timeout hace falta
   re-armar explícitamente. Desarme automático por inactividad.
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
from collections import deque
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# --- Topes DUROS: no se pueden subir desde la configuración -------------------------
HARD_MAX_DEGREES_PER_STEP = 180
HARD_MAX_POWER = 30  # el robot usa 40 si no se manda potencia: siempre se manda
HARD_MIN_STEP_INTERVAL_S = 1.0  # piso del mínimo entre pasos configurable
HARD_MAX_STEPS_PER_ORDER = 1  # una orden de ruta = un paso; nunca rutas largas ni pausaMs

# --- Acciones y gestos --------------------------------------------------------------
PARAR = "PARAR"
AVANZAR = "AVANZAR"
GIRAR_DERECHA = "GIRAR_DERECHA"
GIRAR_IZQUIERDA = "GIRAR_IZQUIERDA"
ACTIONS = (PARAR, AVANZAR, GIRAR_DERECHA, GIRAR_IZQUIERDA)

# Signo de (rueda izquierda, rueda derecha). Contrato: grados positivos = adelante.
MOVEMENT_SIGNS = {
    AVANZAR: (+1, +1),
    GIRAR_DERECHA: (+1, -1),
    GIRAR_IZQUIERDA: (-1, +1),
}

# Gestos que hoy clasifica `vision/detector.py` (catálogo de Fase B/C2).
KNOWN_GESTURES = ("puño_cerrado", "palma_abierta", "dedo_pulgar", "dedo_menique")

MOTOR_RE = re.compile(r"^out[A-D]$")
STEP_KEYS = {"motorIzq", "motorDer", "gradosIzq", "gradosDer", "potencia"}

PATH_STATUS = "/status"
PATH_EJECUTAR = "/ejecutar_ruta"
PATH_ESTADO = "/estado_ejecucion"
PATH_DETENER = "/detener_ejecucion"
PATH_STOP = "/stop"

# Estados del controlador.
DESARMADO = "DESARMADO"
ARMADO = "ARMADO"
PARANDO = "PARANDO"
BLOQUEADO = "BLOQUEADO"


class RobotConfigError(ValueError):
    """Configuración inválida: el actuador no arranca."""


class HardLimitError(ValueError):
    """Una orden supera un tope duro: no se envía."""


class RobotTransportError(Exception):
    """Timeout, conexión rechazada, respuesta perdida."""


# --- Configuración ------------------------------------------------------------------

@dataclass(frozen=True)
class RobotConfig:
    mapeo: dict
    min_confianza: float = 0.75  # mismo valor que reference/config/robot.json
    grados_paso: int = 90
    potencia: int = 20
    motor_izq: str = "outC"
    motor_der: str = "outD"
    sostener_inicio_s: float = 3.0
    sostener_paso_s: float = 3.0
    hueco_max_s: float = 1.0
    ventana_crudo: int = 4  # el crudo debe coincidir en `coincidencias_crudo` de los
    coincidencias_crudo: int = 3  # últimos `ventana_crudo` cuadros (tolera uno suelto)
    min_entre_pasos_s: float = 3.0
    desarme_inactividad_s: float = 60.0
    timeout_http_s: float = 1.0
    margen_parada_s: float = 6.0  # vigilancia tras el último envío de ruta
    sondeo_parada_s: float = 0.3
    alarma_parada_s: float = 10.0  # en PARANDO sin confirmar más que esto -> ERROR
    estado_cada_s: float = 30.0  # línea de estado en --seguir; 0 = desactivada
    robot_url: Optional[str] = None  # sin valor por defecto: el modo real lo exige


_FIELD_TYPES = {
    "mapeo": dict, "min_confianza": float, "grados_paso": int, "potencia": int,
    "motor_izq": str, "motor_der": str, "sostener_inicio_s": float,
    "sostener_paso_s": float, "hueco_max_s": float, "ventana_crudo": int,
    "coincidencias_crudo": int, "min_entre_pasos_s": float,
    "desarme_inactividad_s": float, "timeout_http_s": float, "margen_parada_s": float,
    "sondeo_parada_s": float, "alarma_parada_s": float, "estado_cada_s": float,
    "robot_url": (str, type(None)),
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
    unknown = set(data) - {f.name for f in fields(RobotConfig)}
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
    for name in ("sostener_inicio_s", "sostener_paso_s", "hueco_max_s", "desarme_inactividad_s",
                 "margen_parada_s", "alarma_parada_s"):
        if getattr(cfg, name) <= 0:
            raise RobotConfigError(f"{name} debe ser > 0")
    if not (1 <= cfg.coincidencias_crudo <= cfg.ventana_crudo <= 10):
        raise RobotConfigError("se requiere 1 <= coincidencias_crudo <= ventana_crudo <= 10")
    if cfg.min_entre_pasos_s < HARD_MIN_STEP_INTERVAL_S:
        raise RobotConfigError(f"min_entre_pasos_s no puede ser menor que {HARD_MIN_STEP_INTERVAL_S}")
    if not (0.1 <= cfg.timeout_http_s <= 5.0):
        raise RobotConfigError("timeout_http_s debe estar entre 0.1 y 5.0")
    if not (0.05 <= cfg.sondeo_parada_s <= 2.0):
        raise RobotConfigError("sondeo_parada_s debe estar entre 0.05 y 2.0")
    if cfg.estado_cada_s != 0 and not (5.0 <= cfg.estado_cada_s <= 600.0):
        raise RobotConfigError("estado_cada_s debe ser 0 (desactivada) o estar entre 5 y 600")
    if cfg.robot_url is not None:
        parsed = urllib.parse.urlparse(cfg.robot_url)
        if parsed.scheme != "http" or not parsed.hostname or parsed.path not in ("", "/"):
            raise RobotConfigError(f"robot_url inválida (se espera http://host[:puerto]): {cfg.robot_url!r}")


# --- Órdenes y respuestas -----------------------------------------------------------

def build_route_payload(action: str, cfg: RobotConfig) -> dict:
    """Cuerpo de POST /ejecutar_ruta con UN paso (docs/CONTRATO_ROBOT_EV3.md)."""
    if action not in MOVEMENT_SIGNS:
        raise ValueError(f"{action!r} no es una acción de movimiento")
    si, sd = MOVEMENT_SIGNS[action]
    payload = {"pasos": [{
        "motorIzq": cfg.motor_izq,
        "motorDer": cfg.motor_der,
        "gradosIzq": si * cfg.grados_paso,
        "gradosDer": sd * cfg.grados_paso,
        "potencia": cfg.potencia,
    }]}
    check_hard_limits(payload)
    return payload


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def check_hard_limits(payload: dict) -> None:
    """Se llama al construir la orden y otra vez justo antes de enviarla."""
    if not isinstance(payload, dict) or set(payload) != {"pasos"}:
        raise HardLimitError("la orden debe ser exactamente {'pasos': [...]}")
    pasos = payload["pasos"]
    if not isinstance(pasos, list) or not (1 <= len(pasos) <= HARD_MAX_STEPS_PER_ORDER):
        raise HardLimitError(f"se permite {HARD_MAX_STEPS_PER_ORDER} paso por orden")
    for paso in pasos:
        if not isinstance(paso, dict) or set(paso) != STEP_KEYS:
            raise HardLimitError(f"paso con campos inválidos: {paso!r}")  # incluye pausaMs
        for key in ("gradosIzq", "gradosDer"):
            v = paso[key]
            if not _is_int(v) or abs(v) > HARD_MAX_DEGREES_PER_STEP:
                raise HardLimitError(f"{key}={v!r} supera el tope duro de {HARD_MAX_DEGREES_PER_STEP} grados")
        p = paso["potencia"]
        if not _is_int(p) or not (0 < p <= HARD_MAX_POWER):
            raise HardLimitError(f"potencia={p!r} supera el tope duro de {HARD_MAX_POWER}")
        for key in ("motorIzq", "motorDer"):
            if not isinstance(paso[key], str) or not MOTOR_RE.match(paso[key]):
                raise HardLimitError(f"{key}={paso[key]!r} inválido")


def parse_json_object(text: str) -> Optional[dict]:
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def route_response_ok(status: int, body: str, sent_steps: int) -> bool:
    """El robot trata un JSON malformado como cuerpo vacío y responde ok:true con
    totalPasos 0: por eso se exige totalPasos == pasos enviados."""
    obj = parse_json_object(body)
    return (200 <= status < 300 and obj is not None and obj.get("ok") is True
            and _is_int(obj.get("totalPasos")) and obj["totalPasos"] == sent_steps)


def parse_ejecutando(status: int, body: str) -> Optional[bool]:
    """GET /estado_ejecucion -> True/False, o None si no se puede saber."""
    obj = parse_json_object(body)
    if not (200 <= status < 300) or obj is None or not isinstance(obj.get("ejecutando"), bool):
        return None
    return obj["ejecutando"]


def check_status_motors(status: int, body: str, cfg: RobotConfig) -> Optional[str]:
    """GET /status al arrancar el modo real. Un motor no conectado se ignora SIN avisar
    en el robot, así que se exige connected:true en los dos motores configurados.
    Devuelve None si está bien, o el motivo del rechazo."""
    obj = parse_json_object(body)
    if not (200 <= status < 300) or obj is None:
        return f"/status ilegible o con código {status}"
    motors = obj.get("motors")
    if not isinstance(motors, list):
        return "/status sin lista 'motors'"
    for wanted in (cfg.motor_izq, cfg.motor_der):
        found = [m for m in motors if isinstance(m, dict) and isinstance(m.get("port"), str)
                 and (m["port"] == wanted or m["port"].endswith(":" + wanted))]
        if not found:
            return f"/status no informa el motor {wanted}"
        if not all(m.get("connected") is True for m in found):
            return f"el motor {wanted} no está conectado"
    return None


# --- Sostenimiento en tiempo real con evidencia cruda --------------------------------

class HoldTracker:
    """Cuenta cuánto tiempo REAL lleva la mano mostrando el gesto a ejecutar.

    - El gesto CONFIRMADO solo elige el objetivo (`target`) e inicia el conteo.
    - Un intervalo entre dos cuadros consecutivos se suma solo si el gesto CRUDO del
      detector coincide con el objetivo (con confianza >= mínima) en al menos
      `min_matches` de los últimos `window` cuadros (por defecto 3 de 4: tolera un
      cuadro suelto) y el intervalo es < `max_gap_s`.
    - Si falla cualquiera de las dos cosas, la cuenta vuelve a cero. También si cambia
      el objetivo, si no hay objetivo o si el reloj retrocede.
    - Hay un paso al llegar a `start_s` y otro por cada `step_s` sostenidos después,
      contados desde la evidencia que había en el paso anterior.

    Por qué: el confirmado sigue vigente ~2,3-2,9 s después de que la mano se va
    (histéresis del estabilizador); contarlo daba pasos con 0,1-1 s de mano real
    (BITACORA.md, Fase 11 Etapa 1). Con evidencia cruda, al irse la mano la cuenta se
    corta en uno o dos cuadros."""

    def __init__(self, start_s: float, step_s: float, max_gap_s: float,
                 window: int = 4, min_matches: int = 3, min_confidence: float = 0.0) -> None:
        self.start_s = start_s
        self.step_s = step_s
        self.max_gap_s = max_gap_s
        self.min_matches = min_matches
        self.min_confidence = min_confidence
        self.raw = deque(maxlen=window)  # (gesto crudo, confianza suficiente)
        self.last_t: Optional[float] = None
        self.reset()

    def reset(self) -> None:
        """Pone la cuenta a cero; conserva el historial crudo (sigue siendo evidencia)."""
        self.target: Optional[str] = None
        self.accumulated_s = 0.0
        self.steps = 0
        self._next_step_at = self.start_s

    def observe(self, target: Optional[str], raw: Optional[str], raw_conf: float, t: float) -> bool:
        """Un cuadro. True si en este cuadro toca un paso."""
        dt = None if self.last_t is None else t - self.last_t
        if dt is not None and dt < 0:
            self.raw.clear()  # reloj hacia atrás: el historial deja de ser fiable
        self.raw.append((raw, isinstance(raw_conf, (int, float)) and raw_conf >= self.min_confidence))
        self.last_t = t
        if target is None:
            self.reset()
            return False
        if target != self.target:
            self.reset()
            self.target = target
            return False
        matches = sum(1 for g, ok in self.raw if g == target and ok)
        if dt is None or dt < 0 or dt >= self.max_gap_s or matches < self.min_matches:
            self.accumulated_s = 0.0
            self.steps = 0
            self._next_step_at = self.start_s
            return False
        self.accumulated_s += dt
        if self.accumulated_s >= self._next_step_at:
            self.steps += 1
            # El siguiente paso se cuenta desde la evidencia de ESTE paso, no desde una
            # marca fija (3, 6, 9 s): así dos pasos quedan separados por al menos
            # step_s de evidencia, y como la evidencia nunca avanza más rápido que el
            # reloj, el mínimo entre pasos (igual a step_s) no omite ninguno.
            self._next_step_at = self.accumulated_s + self.step_s
            return True
        return False


# --- Transporte HTTP ----------------------------------------------------------------

@dataclass
class RobotResponse:
    status: int
    body: str
    elapsed_ms: float


class DryRunTransport:
    """Modo ENSAYO: no abre ninguna conexión, solo registra lo que enviaría. `sent` es
    compartido entre todas las "conexiones" del ensayo para poder revisarlo entero."""

    dry = True

    def __init__(self, sent: Optional[list] = None) -> None:
        self.sent = sent if sent is not None else []

    def request(self, method: str, path: str, body: Optional[dict] = None) -> RobotResponse:
        self.sent.append((method, path, body))
        logger.info("ENSAYO: enviaría %s %s %s", method, path, json.dumps(body, ensure_ascii=False) if body else "")
        if path == PATH_EJECUTAR and isinstance(body, dict) and isinstance(body.get("pasos"), list):
            return RobotResponse(200, json.dumps({"ok": True, "totalPasos": len(body["pasos"])}), 0.0)
        if path == PATH_ESTADO:
            return RobotResponse(200, '{"ejecutando": false}', 0.0)
        return RobotResponse(200, '{"ok": true}', 0.0)

    def close(self) -> None:
        pass


class HttpRobotTransport:
    """HTTP con la biblioteca estándar. Reutiliza la conexión si se usó hace poco
    (`reuse_window_s`); si no, abre una nueva, para no estrenar una orden con una
    conexión que el servidor ya cerró por inactividad (falso error y parada
    innecesaria). Cualquier fallo cierra la conexión y lanza RobotTransportError."""

    dry = False

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


class OrderLog:
    """Registro común de órdenes (hora de envío y tiempo de respuesta de cada una)."""

    def __init__(self) -> None:
        self.records: List[OrderRecord] = []
        self._lock = threading.Lock()

    def send(self, transport, method: str, path: str, body: Optional[dict] = None) -> Optional[RobotResponse]:
        sent_wall = datetime.now().astimezone().isoformat(timespec="milliseconds")
        sent_mono = time.monotonic()
        try:
            resp = transport.request(method, path, body)
        except RobotTransportError as e:
            ms = (time.monotonic() - sent_mono) * 1000.0
            with self._lock:
                self.records.append(OrderRecord(method, path, body, sent_wall, sent_mono, None, ms, str(e)))
            logger.error("Robot: %s %s enviada %s -> ERROR tras %.0f ms: %s", method, path, sent_wall, ms, e)
            return None
        with self._lock:
            self.records.append(OrderRecord(method, path, body, sent_wall, sent_mono, resp.status, resp.elapsed_ms, None))
        if getattr(transport, "dry", False):
            # ENSAYO: no salió nada por la red; no se informa un "enviada -> 200" que no existió.
            logger.info("Robot: ENSAYO (NO enviada): %s %s", method, path)
        else:
            logger.info("Robot: %s %s enviada %s -> %d en %.0f ms", method, path, sent_wall, resp.status, resp.elapsed_ms)
        return resp


# --- Rutas: una en vuelo, sin bloquear la visión -------------------------------------

class RouteDispatcher:
    """Envía las órdenes de ruta en su propio hilo, por una conexión reutilizada.

    - Como mucho UNA en vuelo; una nueva con otra en vuelo se descarta (no se encola:
      un paso viejo enviado tarde es peor que un paso perdido).
    - `block()` (lo llama PARAR al instante) descarta lo pendiente y rechaza nuevas
      rutas hasta `unblock()` (re-armado). La ruta ya en vuelo no se puede retirar: de
      eso se encarga la vigilancia de StopSupervisor.
    - Fallo (sin respuesta, código inesperado, totalPasos distinto): on_failure ->
      PARAR. Nunca se reenvía. 409: no se reintenta, se registra.
    - Expone `in_flight` y `last_send_mono` para la vigilancia de la parada.

    Con threaded=False ejecuta en línea (pruebas deterministas de la lógica)."""

    def __init__(self, transport, order_log: OrderLog, on_failure: Callable[[str], None],
                 threaded: bool = True) -> None:
        self.transport = transport
        self.log = order_log
        self.on_failure = on_failure
        self.threaded = threaded
        self.last_send_mono: Optional[float] = None
        self._cv = threading.Condition()
        self._pending: Optional[dict] = None
        self._in_flight = False
        self._blocked = False
        self._closed = False
        self._thread = None
        if threaded:
            self._thread = threading.Thread(target=self._worker, name="robot-rutas", daemon=True)
            self._thread.start()

    @property
    def in_flight(self) -> bool:
        with self._cv:
            return self._in_flight or self._pending is not None

    def block(self) -> None:
        with self._cv:
            self._blocked = True
            self._pending = None

    def unblock(self) -> None:
        with self._cv:
            self._blocked = False

    def submit(self, payload: dict) -> bool:
        with self._cv:
            if self._closed or self._blocked or self._in_flight or self._pending is not None:
                return False
            if self.threaded:
                self._pending = payload
                self._cv.notify()
                return True
            self._in_flight = True
        try:
            self._send_route(payload)
        finally:
            with self._cv:
                self._in_flight = False
        return True

    def wait_idle(self, timeout_s: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout_s
        with self._cv:
            while self._in_flight or self._pending is not None:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._cv.wait(left)
        return True

    def close(self, timeout_s: float = 5.0) -> None:
        with self._cv:
            self._closed = True
            self._pending = None
            self._cv.notify_all()
        if self._thread is not None:
            self._thread.join(timeout_s)
            if self._thread.is_alive():
                logger.error("El hilo de rutas del robot no terminó en %.1fs", timeout_s)
        self.transport.close()

    def _worker(self) -> None:
        while True:
            with self._cv:
                while self._pending is None and not self._closed:
                    self._cv.wait()
                if self._closed:
                    return
                payload, self._pending = self._pending, None
                if self._blocked:  # PARAR llegó entre el submit y este punto
                    continue
                self._in_flight = True
            try:
                self._send_route(payload)
            except Exception:  # el hilo no puede morir en silencio
                logger.exception("Error inesperado enviando una ruta")
                self.on_failure("error inesperado en el hilo de rutas")
            finally:
                with self._cv:
                    self._in_flight = False
                    self._cv.notify_all()

    def _send_route(self, payload: dict) -> None:
        try:
            check_hard_limits(payload)  # segunda comprobación, justo antes de enviar
        except HardLimitError as e:
            logger.error("Robot: orden NO enviada, %s", e)
            self.on_failure(f"tope duro: {e}")
            return
        self.last_send_mono = time.monotonic()
        resp = self.log.send(self.transport, "POST", PATH_EJECUTAR, payload)
        if resp is not None and route_response_ok(resp.status, resp.body, len(payload["pasos"])):
            return
        if resp is not None and resp.status == 409:
            logger.warning("Robot: 409 (ya hay una ruta en ejecución) -- no se reintenta: %s", resp.body[:200])
            return
        if resp is None:
            reason = "sin respuesta"
        elif 200 <= resp.status < 300:
            reason = f"respuesta no válida (se exige ok:true y totalPasos={len(payload['pasos'])}): {resp.body[:200]}"
        else:
            reason = f"código inesperado {resp.status}"
        self.on_failure(f"paso fallido ({reason})")


# --- PARAR: hilo propio, conexión nueva, vigilancia hasta confirmar -----------------

class StopSupervisor:
    """Ejecuta PARAR sin esperar a nada más y vigila hasta confirmarlo.

    1. Conexión NUEVA (transport_factory) en su propio hilo: POST /detener_ejecucion y
       POST /stop (en una ruta en curso /stop solo no la cancela).
    2. Estado PARANDO: cada `sondeo_parada_s` consulta /estado_ejecucion. Si
       `ejecutando` es true (p. ej. una ruta retrasada llegó DESPUÉS del detener y lo
       anuló con su detener.clear()), vuelve a parar. Si no hay respuesta legible,
       repite /detener_ejecucion.
    3. Termina solo cuando (a) no hay ruta en vuelo (respondió o venció su plazo),
       (b) pasaron `margen_parada_s` desde el último envío de ruta y (c) hubo DOS
       lecturas seguidas con ejecutando:false.
    4. Si no lo logra en `alarma_parada_s`: ERROR visible (repetido cada 5 s) y sigue
       reintentando. Nunca reanuda solo.
    En ENSAYO registra el detener y el stop y termina (no hay robot que vigilar)."""

    def __init__(self, cfg: RobotConfig, transport_factory: Callable[[], object], order_log: OrderLog,
                 routes: RouteDispatcher, on_confirmed: Callable[[], None]) -> None:
        self.cfg = cfg
        self.transport_factory = transport_factory
        self.log = order_log
        self.routes = routes
        self.on_confirmed = on_confirmed
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._abandon = threading.Event()
        self.confirmed = threading.Event()
        self.starts = 0  # cuántos hilos de parada se crearon (para pruebas)

    @property
    def active(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self, reason: str) -> bool:
        """False si ya hay una parada en curso (no se duplican hilos)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                logger.warning("Robot: PARAR (%s) ignorado: ya hay una parada en curso", reason)
                return False
            self.confirmed.clear()
            self._abandon.clear()
            self.starts += 1
            self._thread = threading.Thread(target=self._run, args=(reason,), name="robot-parada", daemon=True)
            self._thread.start()
            return True

    def wait(self, timeout_s: float) -> bool:
        return self.confirmed.wait(timeout_s)

    def abandon(self) -> None:
        """Solo al cerrar el proceso: deja de vigilar (no cambia el estado: no reanuda)."""
        self._abandon.set()

    def _stop_pair(self, transport) -> None:
        self.log.send(transport, "POST", PATH_DETENER)
        self.log.send(transport, "POST", PATH_STOP)

    def _run(self, reason: str) -> None:
        transport = self.transport_factory()
        t0 = time.monotonic()
        logger.warning("Robot: PARAR (%s) -- conexión nueva, %s y %s", reason, PATH_DETENER, PATH_STOP)
        try:
            self._stop_pair(transport)
            if getattr(transport, "dry", False):
                self._finish()
                return
            false_streak = 0
            last_alarm = None
            while not self._abandon.wait(self.cfg.sondeo_parada_s):
                resp = self.log.send(transport, "GET", PATH_ESTADO)
                ejecutando = None if resp is None else parse_ejecutando(resp.status, resp.body)
                if ejecutando is False:
                    false_streak += 1
                elif ejecutando is True:
                    false_streak = 0
                    logger.warning("Robot: ejecutando:true durante la parada -- se vuelve a parar")
                    self._stop_pair(transport)
                else:
                    false_streak = 0
                    self.log.send(transport, "POST", PATH_DETENER)
                last = self.routes.last_send_mono
                margin_ok = last is None or time.monotonic() - last >= self.cfg.margen_parada_s
                if false_streak >= 2 and margin_ok and not self.routes.in_flight:
                    logger.warning("Robot: parada CONFIRMADA en %.1fs (%s)", time.monotonic() - t0, reason)
                    self._finish()
                    return
                waited = time.monotonic() - t0
                if waited >= self.cfg.alarma_parada_s and (last_alarm is None or time.monotonic() - last_alarm >= 5.0):
                    last_alarm = time.monotonic()
                    logger.error("Robot: PARADA NO CONFIRMADA tras %.0fs (%s) -- se sigue reintentando; "
                                 "revisar el robot en persona. No se reanuda sola.", waited, reason)
            logger.error("Robot: vigilancia de la parada abandonada al cerrar SIN confirmación (%s)", reason)
        except Exception:
            logger.exception("Robot: error inesperado en el hilo de parada")
        finally:
            transport.close()

    def _finish(self) -> None:
        self.confirmed.set()
        self.on_confirmed()


# --- Controlador --------------------------------------------------------------------

@dataclass
class FrameInput:
    confirmed: Optional[str]
    confirmed_conf: float
    raw: Optional[str]
    raw_conf: float
    t: float


class RobotController:
    """Estados DESARMADO / ARMADO / PARANDO / BLOQUEADO. `observe()` se llama una vez por
    cuadro desde el lazo de visión y nunca bloquea (solo encola)."""

    def __init__(self, cfg: RobotConfig, transport_factory: Callable[[], object],
                 threaded_routes: bool = True) -> None:
        validate_config(cfg)
        self.cfg = cfg
        self._lock = threading.RLock()
        self.state = DESARMADO
        self.state_reason = "inicio"
        self._final_state = BLOQUEADO
        self.tracker = HoldTracker(cfg.sostener_inicio_s, cfg.sostener_paso_s, cfg.hueco_max_s,
                                   cfg.ventana_crudo, cfg.coincidencias_crudo, cfg.min_confianza)
        self._prev_parar = False
        self._last_step_t: Optional[float] = None
        self._last_activity_t: Optional[float] = None
        self.illegible = 0
        self.steps_enqueued = 0  # solo para la línea de estado; no interviene en el control
        self.order_log = OrderLog()
        self.routes = RouteDispatcher(transport_factory(), self.order_log, self._on_route_failure, threaded_routes)
        self.stopper = StopSupervisor(cfg, transport_factory, self.order_log, self.routes, self._on_stop_confirmed)

    # -- comandos locales --
    def arm(self, now: float) -> bool:
        with self._lock:
            if self.state == PARANDO:
                logger.warning("Robot: no se puede armar mientras la parada no está confirmada")
                return False
            self.state, self.state_reason = ARMADO, "armado explícito"
            self.tracker.reset()  # hay que sostener desde cero después de armar
            self._last_activity_t = now
            self._last_step_t = None
            self.routes.unblock()
        logger.warning("Robot: ARMADO")
        return True

    def disarm(self, reason: str) -> None:
        self.stop(reason, final_state=DESARMADO)

    def manual_stop(self, reason: str = "parada manual") -> None:
        self.stop(reason, final_state=BLOQUEADO)

    def stop(self, reason: str, final_state: str = BLOQUEADO) -> None:
        """PARAR: cerrojo al instante y parada por su propio hilo y conexión."""
        with self._lock:
            self.routes.block()  # ninguna ruta nueva desde este momento
            self.tracker.reset()
            if self.state == PARANDO:
                if final_state == DESARMADO:
                    self._final_state = DESARMADO
                logger.warning("Robot: PARAR (%s) durante PARANDO: no se duplica", reason)
                return
            self.state, self.state_reason = PARANDO, reason
            self._final_state = final_state
        logger.warning("Robot: PARANDO (%s) -- cerrojo activo, requiere re-armado", reason)
        self.stopper.start(reason)

    # -- lazo de visión --
    def observe(self, frame: FrameInput) -> None:
        self.tick(frame.t)
        confirmed_action = self._classify(frame.confirmed)
        if confirmed_action == PARAR:
            # PARAR por el confirmado, sin sostenimiento ni confianza mínima; una vez
            # por aparición (sostener la palma no repite la parada).
            first = not self._prev_parar
            self._prev_parar = True
            self.tracker.observe(None, frame.raw, frame.raw_conf, frame.t)
            if first:
                self.manual_stop(f"gesto {frame.confirmed} = PARAR")
            return
        self._prev_parar = False
        target = frame.confirmed if confirmed_action is not None else None
        raw_conf = frame.raw_conf
        if (not isinstance(raw_conf, (int, float)) or isinstance(raw_conf, bool)
                or not math.isfinite(raw_conf) or not (0.0 <= raw_conf <= 1.0)):
            if frame.raw is not None:
                self.illegible += 1
                logger.warning("Robot: confianza cruda ilegible ignorada: %r", raw_conf)
            raw_conf = 0.0
        raw = frame.raw if isinstance(frame.raw, str) else None
        with self._lock:
            if self.state != ARMADO:
                target = None
            if not self.tracker.observe(target, raw, raw_conf, frame.t):
                return
            if self._last_step_t is not None and frame.t - self._last_step_t < self.cfg.min_entre_pasos_s:
                logger.info("Robot: paso de %s omitido (mínimo entre pasos %.1fs)", confirmed_action, self.cfg.min_entre_pasos_s)
                return
            try:
                payload = build_route_payload(confirmed_action, self.cfg)
            except HardLimitError as e:
                logger.error("Robot: %s", e)
                self.routes.block()
                self.state, self.state_reason = BLOQUEADO, f"tope duro: {e}"
                return
            if not self.routes.submit(payload):
                logger.warning("Robot: paso de %s descartado (ruta en vuelo o bloqueado)", confirmed_action)
                return
            self._last_step_t = frame.t
            self.steps_enqueued += 1
            self._last_activity_t = frame.t
        logger.info("Robot: paso %s encolado (%.2fs con evidencia cruda)", confirmed_action, self.tracker.accumulated_s)

    def tick(self, now: float) -> None:
        with self._lock:
            idle = (self.state == ARMADO and self._last_activity_t is not None
                    and now - self._last_activity_t >= self.cfg.desarme_inactividad_s)
        if idle:
            self.disarm(f"inactividad >= {self.cfg.desarme_inactividad_s:.0f}s")

    def close(self, wait_stop_s: float = 10.0) -> bool:
        """PARAR antes de salir; espera la confirmación como mucho `wait_stop_s`."""
        self.stop("cierre del proceso", final_state=DESARMADO)
        ok = self.stopper.wait(wait_stop_s)
        if not ok:
            logger.error("Robot: se sale SIN parada confirmada tras %.0fs -- revisar el robot", wait_stop_s)
            self.stopper.abandon()
        self.routes.close()
        return ok

    # -- internos --
    def _classify(self, gesture) -> Optional[str]:
        if gesture is None or gesture == "ninguno":
            return None
        if not isinstance(gesture, str) or gesture not in self.cfg.mapeo:
            if not isinstance(gesture, str) or gesture not in KNOWN_GESTURES:
                self.illegible += 1
                logger.warning("Robot: gesto ilegible o desconocido ignorado: %r", gesture)
            return None
        return self.cfg.mapeo[gesture]

    def _on_route_failure(self, reason: str) -> None:
        self.manual_stop(reason)

    def _on_stop_confirmed(self) -> None:
        with self._lock:
            if self.state == PARANDO:
                self.state = self._final_state
        logger.warning("Robot: %s (%s)", self.state, self.state_reason)


def make_transport_factory(cfg: RobotConfig, real: bool) -> Callable[[], object]:
    """ENSAYO por defecto. El modo real exige la bandera explícita Y robot_url. Cada
    llamada a la fábrica es una conexión nueva (en ENSAYO comparten el registro)."""
    if not real:
        sent: list = []
        return lambda: DryRunTransport(sent)
    if not cfg.robot_url:
        raise RobotConfigError("modo real sin 'robot_url' en la configuración: no se arranca")
    return lambda: HttpRobotTransport(cfg.robot_url, cfg.timeout_http_s)
