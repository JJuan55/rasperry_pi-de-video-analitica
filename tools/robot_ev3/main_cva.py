"""Servidor HTTP que corre DENTRO del ladrillo EV3 — copia acotada para CVA.

Derivado de main.py (Tarea 12, ver tools/robot_ev3/README.md). Solo usa la
biblioteca estándar de Python (más `ev3dev2` para los motores, que ya viene con
ev3dev). ev3dev-stretch trae Python 3.5: sin f-strings ni anotaciones de
variables.

    cd /home/robot/cva && timeout 1800 python3 main_cva.py --permitir IP [--permitir IP2] [--puerto 8081]

Solo atiende a las IP de --permitir (obligatorio, repetible) y a 127.0.0.1; a
cualquier otra le responde 403 sin leer el cuerpo. Las IP no se escriben en el
registro.

Contrato HTTP:

    GET  /status        -> {connected, battery, motors[], sensors[]}
                            cada motor: {port, connected, speed, position}
                            (position = grados de encoder acumulados); outC y
                            outD solo salen connected:true si sysfs los ve Y el
                            motor se pudo crear
    POST /ejecutar_ruta <- {pasos: [...]}      validado antes de mover nada
                                               (ver validar_pasos()); 400 si no
    GET  /estado_ejecucion -> {ejecutando, pasoActual, totalPasos, error}
    POST /detener_ejecucion -> paro de emergencia de una ruta en curso
    POST /stop          -> igual que /detener_ejecucion (cancela la ruta en
                           curso, a diferencia del /stop de main.py); ignora el
                           cuerpo
"""
import argparse
import ipaddress
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlparse

try:
    from ev3dev2.motor import LargeMotor, MediumMotor, OUTPUT_C, OUTPUT_D  # type: ignore
except ImportError:  # pragma: no cover - solo ocurre fuera del ladrillo
    LargeMotor = None
    MediumMotor = None
    OUTPUT_C, OUTPUT_D = "outC", "outD"

MOTOR_PORTS = ["outA", "outB", "outC", "outD"]
SENSOR_PORTS = ["in1", "in2", "in3", "in4"]

# ─── Motores físicos ──────────────────────────────────────────────────────────
# outC es el motor GRANDE (oruga izquierda) y outD el MEDIANO (oruga derecha).
# outA y outB no se usan. Cada motor se crea con try-except para que el
# servidor arranque aunque falte un cable; el que falla queda en None y
# /ejecutar_ruta lo rechaza con 400 en vez de ignorarlo.

_PORT_CLASSES = {
    "outC": (LargeMotor, OUTPUT_C),
    "outD": (MediumMotor, OUTPUT_D),
}

motors = {"outA": None, "outB": None}
for _port, (_clase, _const) in _PORT_CLASSES.items():
    try:
        motors[_port] = _clase(_const) if _clase else None
    except Exception:
        motors[_port] = None

PUERTOS_RUTA = ("outC", "outD")
MAX_PASOS = 4
MAX_GRADOS = 180
MAX_POTENCIA = 30
POTENCIA_POR_DEFECTO = 20
MAX_PAUSA_MS = 5000
PUERTO_POR_DEFECTO = 8081


def normalize_port(port):
    """Acepta "A", "outA" o "ev3-ports:outA" y devuelve siempre "outA"."""
    p = str(port).strip().split(":")[-1]
    if not p.startswith("out"):
        p = "out" + p.upper()
    return p[:3] + p[3:].upper()


def get_motor(port):
    return motors.get(normalize_port(port))


# ─── Validación de rutas ──────────────────────────────────────────────────────
# Todo se comprueba ANTES de arrancar nada: una ruta con un solo paso inválido
# se rechaza entera y no se mueve ningún motor.

def _es_entero(valor):
    return isinstance(valor, int) and not isinstance(valor, bool)


def _validar_paso(i, paso):
    """Devuelve None si el paso es válido, o el motivo del rechazo."""
    if not isinstance(paso, dict):
        return "paso {}: debe ser un objeto".format(i)
    if "pausaMs" in paso:
        if set(paso) != {"pausaMs"}:
            return "paso {}: una pausa solo lleva pausaMs".format(i)
        n = paso["pausaMs"]
        if not _es_entero(n) or not 0 <= n <= MAX_PAUSA_MS:
            return "paso {}: pausaMs debe ser entero entre 0 y {}".format(i, MAX_PAUSA_MS)
        return None
    puertos = []
    for clave in ("motorIzq", "motorDer"):
        valor = paso.get(clave)
        if not isinstance(valor, str) or normalize_port(valor) not in PUERTOS_RUTA:
            return "paso {}: {} debe ser outC u outD".format(i, clave)
        puertos.append(normalize_port(valor))
    if puertos[0] == puertos[1]:
        return "paso {}: motorIzq y motorDer deben ser distintos".format(i)
    for clave in ("gradosIzq", "gradosDer"):
        valor = paso.get(clave)
        if not _es_entero(valor) or not -MAX_GRADOS <= valor <= MAX_GRADOS:
            return "paso {}: {} debe ser entero entre -{} y {}".format(i, clave, MAX_GRADOS, MAX_GRADOS)
    if paso["gradosIzq"] == 0 and paso["gradosDer"] == 0:
        return "paso {}: al menos un grado debe ser distinto de 0".format(i)
    if "potencia" in paso:
        valor = paso["potencia"]
        if not _es_entero(valor) or not 1 <= valor <= MAX_POTENCIA:
            return "paso {}: potencia debe ser entera entre 1 y {}".format(i, MAX_POTENCIA)
    for puerto in puertos:
        if motors.get(puerto) is None:
            return "motor no disponible: {}".format(puerto)
    return None


def validar_pasos(pasos):
    """Devuelve None si la ruta es válida, o el motivo del rechazo (HTTP 400)."""
    if not isinstance(pasos, list) or not pasos:
        return "pasos debe ser una lista no vacía"
    if len(pasos) > MAX_PASOS:
        return "como mucho {} pasos".format(MAX_PASOS)
    for i, paso in enumerate(pasos):
        error = _validar_paso(i, paso)
        if error:
            return error
    return None


# ─── Ejecución real de una ruta planeada ──────────────────────────────────────
# La secuencia corre en un hilo aparte, DENTRO del ladrillo: así no depende de
# que el navegador mantenga una conexión abierta por cada paso (con la Wi-Fi
# del laboratorio, una sola petición larga es mucho más frágil que muchas
# cortas). El frontend solo arranca la ruta, consulta en qué paso va, y puede
# frenarla en cualquier momento — igual que "subir un programa y mirarlo
# correr" en vez de controlar cada movimiento por separado.

_ejecucion = {
    "detener": threading.Event(),
    "ejecutando": False,
    "paso_actual": -1,
    "total_pasos": 0,
    "error": None,
}
# Hace atómico "comprobar que no hay ruta y marcarla en ejecución": sin él, dos
# POST /ejecutar_ruta simultáneos podían arrancar dos rutas a la vez.
_inicio_lock = threading.Lock()


def _dormir_interrumpible(segundos):
    """time.sleep(segundos), pero revisando el paro de emergencia cada 20ms
    en vez de dormir de un tirón — para que una pausa larga no bloquee un
    "detener" pedido a mitad de camino."""
    fin = time.time() + segundos
    while time.time() < fin:
        if _ejecucion["detener"].is_set():
            return
        time.sleep(min(0.02, fin - time.time()))


def _mover_dos_motores(motor_izq, motor_der, grados_izq, grados_der, potencia_maxima):
    """Mueve las dos ruedas a la vez, cada una la cantidad de grados que le
    corresponde, usando el encoder de cada motor (on_for_degrees) en vez de
    "prender y esperar" — así la distancia real no depende de la batería ni
    de la fricción, es la misma cuenta de cinematica.js.

    Un arco no gira las dos ruedas la misma cantidad. Si las arrancáramos a
    la misma velocidad, la que tiene menos por recorrer terminaría antes y
    se quedaría esperando a la otra — el robot pivotaría de más al final en
    vez de trazar una curva pareja. Por eso la velocidad de cada rueda es
    proporcional a cuánto le toca girar: la que más recorre usa la potencia
    máxima, la otra una fracción, y así las dos terminan al mismo tiempo.
    """
    maximo = max(abs(grados_izq), abs(grados_der))
    if maximo == 0:
        return

    def velocidad_para(grados):
        signo = 1 if grados > 0 else -1
        return signo * potencia_maxima * (abs(grados) / maximo)

    def iniciar(motor, grados):
        if motor is None or grados == 0:
            return
        motor.on_for_degrees(speed=velocidad_para(grados), degrees=abs(grados), brake=True, block=False)

    iniciar(motor_izq, grados_izq)
    iniciar(motor_der, grados_der)

    while True:
        corriendo_izq = motor_izq is not None and motor_izq.is_running
        corriendo_der = motor_der is not None and motor_der.is_running
        if not corriendo_izq and not corriendo_der:
            return
        if _ejecucion["detener"].is_set():
            if motor_izq is not None:
                motor_izq.off(brake=True)
            if motor_der is not None:
                motor_der.off(brake=True)
            return
        time.sleep(0.02)


def _ejecutar_pasos(pasos):
    _ejecucion["ejecutando"] = True
    _ejecucion["error"] = None
    try:
        for i, paso in enumerate(pasos):
            if _ejecucion["detener"].is_set():
                break
            _ejecucion["paso_actual"] = i

            if "pausaMs" in paso:
                _dormir_interrumpible(paso["pausaMs"] / 1000.0)
                continue

            motor_izq = get_motor(paso.get("motorIzq", ""))
            motor_der = get_motor(paso.get("motorDer", ""))
            _mover_dos_motores(
                motor_izq,
                motor_der,
                paso.get("gradosIzq", 0),
                paso.get("gradosDer", 0),
                paso.get("potencia", POTENCIA_POR_DEFECTO),
            )
    except Exception as error:  # pragma: no cover - defensivo, no debería pasar
        _ejecucion["error"] = str(error)
    finally:
        _ejecucion["ejecutando"] = False
        _ejecucion["paso_actual"] = -1


def iniciar_ejecucion(pasos):
    with _inicio_lock:
        if _ejecucion["ejecutando"]:
            return False, "ya hay una ruta en ejecución"
        # Se marca aquí, antes de responder: así /estado_ejecucion no puede
        # contestar ejecutando:false entre el 200 y el arranque del hilo.
        _ejecucion["ejecutando"] = True
        _ejecucion["detener"].clear()
        _ejecucion["total_pasos"] = len(pasos)
        hilo = threading.Thread(target=_ejecutar_pasos, args=(pasos,), daemon=True)
        try:
            hilo.start()
        except Exception:
            _ejecucion["ejecutando"] = False
            raise
    return True, None


def detener_ejecucion():
    """Paro de emergencia: no confía solo en que el hilo note la señal — acá
    mismo apaga todos los motores para que el frenado sea inmediato."""
    _ejecucion["detener"].set()
    for motor in motors.values():
        if motor is not None:
            try:
                motor.off(brake=True)
            except Exception:
                pass


def estado_ejecucion():
    return {
        "ejecutando": _ejecucion["ejecutando"],
        "pasoActual": _ejecucion["paso_actual"],
        "totalPasos": _ejecucion["total_pasos"],
        "error": _ejecucion["error"],
    }


# ─── Lectura del estado real desde sysfs ──────────────────────────────────────
# Leemos directamente de /sys en lugar de usar ev3dev2 para no depender de que
# cada sensor esté instanciado con la clase correcta.

TACHO_DIR = "/sys/class/tacho-motor"
SENSOR_DIR = "/sys/class/lego-sensor"
BATTERY_FILE = "/sys/class/power_supply/lego-ev3-battery/voltage_now"

DRIVER_TO_TYPE = {
    "lego-ev3-touch": "Touch",
    "lego-ev3-color": "Color",
    "lego-ev3-us": "Ultrasonic",
    "lego-ev3-gyro": "Gyro",
    "lego-ev3-ir": "Infrared",
}


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def _read_number(path, default=0.0):
    raw = _read(path)
    try:
        return float(raw)
    except ValueError:
        return default


def _listdir(path):
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def read_motors():
    """Devuelve los 4 puertos de salida, marcando cuáles tienen motor conectado.
    En outC y outD, connected exige además que el objeto motor se haya creado:
    sysfs ve el cable aunque este servidor no pueda mover ese motor."""
    found = {}
    for node in _listdir(TACHO_DIR):
        base = "{}/{}".format(TACHO_DIR, node)
        port = _read("{}/address".format(base)).split(":")[-1]
        if not port:
            continue
        found[port] = {
            "port": port,
            "connected": port not in PUERTOS_RUTA or motors.get(port) is not None,
            "speed": int(_read_number("{}/speed".format(base))),
            # Grados acumulados del encoder (cuenta desde que arrancó el motor,
            # no se resetea sola). La usa el editor de trayectorias para
            # reconstruir por odometría la ruta que el robot recorrió de verdad.
            "position": int(_read_number("{}/position".format(base))),
        }

    return [
        found.get(p, {"port": p, "connected": False, "speed": 0, "position": 0})
        for p in MOTOR_PORTS
    ]


def read_sensors():
    """Devuelve solo los sensores realmente conectados."""
    sensors = []
    for node in _listdir(SENSOR_DIR):
        base = "{}/{}".format(SENSOR_DIR, node)
        port = _read("{}/address".format(base)).split(":")[-1]
        if not port:
            continue
        driver = _read("{}/driver_name".format(base))
        sensors.append({
            "port": port,
            "sensor_type": DRIVER_TO_TYPE.get(driver, driver or "Unknown"),
            "value": _read_number("{}/value0".format(base)),
        })
    return sensors


def read_battery():
    """Voltaje en voltios (sysfs lo expone en microvoltios)."""
    # Sin separadores de millar en el literal: este archivo corre en el ladrillo,
    # donde el Python puede ser antiguo (los "_" numéricos requieren 3.6+)
    return round(_read_number(BATTERY_FILE) / 1000000.0, 2)


def read_status():
    """Telemetría completa del robot: motores, sensores y batería."""
    return {
        "connected": True,
        "battery": read_battery(),
        "motors": read_motors(),
        "sensors": read_sensors(),
    }


# ─── Servidor HTTP ────────────────────────────────────────────────────────────

class Ev3RequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # permite reutilizar la conexión TCP

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            # El puente ya añade CORS, pero esto permite depurar contra el robot
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # El puente hace polling cada 400ms con un timeout corto (1s): si
            # tarda más que eso en contestar, el cliente ya cerró la conexión
            # cuando este hilo termina de armar la respuesta. No es un error
            # real ni hay nada que reintentar - solo ruido en la consola.
            pass

    def _read_json_body(self):
        """{} sin cuerpo; None si el cuerpo no se puede leer como JSON."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.close_connection = True  # no se sabe dónde termina el cuerpo
            return None
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def _permitido(self):
        """127.0.0.1 y las IP de --permitir. A las demás, 403 sin leer el cuerpo
        (y se cierra la conexión, porque el cuerpo queda sin leer)."""
        ip = self.client_address[0]
        if ip == "127.0.0.1" or ip in self.server.permitidas:
            return True
        self.close_connection = True
        self._send_json({"ok": False, "error": "prohibido"}, status=403)
        return False

    def do_GET(self):
        if not self._permitido():
            return
        parsed = urlparse(self.path)

        if parsed.path == "/status":
            self._send_json(read_status())
            return

        if parsed.path == "/estado_ejecucion":
            self._send_json(estado_ejecucion())
            return

        self._send_json({"ok": False, "error": "ruta desconocida"}, status=404)

    def do_POST(self):
        if not self._permitido():
            return
        parsed = urlparse(self.path)
        body = self._read_json_body()

        if parsed.path == "/ejecutar_ruta":
            if not isinstance(body, dict):
                self._send_json({"ok": False, "error": "cuerpo JSON ilegible"}, status=400)
                return
            if "pasos" not in body:
                self._send_json({"ok": False, "error": "falta pasos"}, status=400)
                return
            pasos = body["pasos"]
            error = validar_pasos(pasos)
            if error:
                self._send_json({"ok": False, "error": error}, status=400)
                return
            ok, error = iniciar_ejecucion(pasos)
            if not ok:
                self._send_json({"ok": False, "error": error}, status=409)
            else:
                self._send_json({"ok": True, "totalPasos": len(pasos)})
            return

        if parsed.path in ("/detener_ejecucion", "/stop"):
            # /stop hace lo mismo que /detener_ejecucion: el actuador de la Pi5
            # manda los dos al PARAR. El cuerpo de /stop se ignora.
            detener_ejecucion()
            self._send_json({"ok": True})
            return

        self._send_json({"ok": False, "error": "ruta desconocida"}, status=404)

    def log_message(self, fmt, *args):
        # El log por defecto va a stderr en cada peticion; con polling a 400 ms
        # eso inunda la consola del ladrillo. Silenciado a proposito (y así
        # tampoco quedan las IP de los clientes en el registro).
        pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Un hilo por petición: un comando de motor no espera al polling."""
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, permitidas):
        self.permitidas = frozenset(permitidas)
        HTTPServer.__init__(self, address, handler)


def _ip_v4(texto):
    try:
        return str(ipaddress.IPv4Address(texto))
    except ValueError:
        # Sin repetir el valor: la IP no se escribe en el registro.
        raise argparse.ArgumentTypeError("no es una IPv4 válida")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Servidor del robot EV3 acotado para CVA.")
    parser.add_argument("--permitir", action="append", type=_ip_v4, default=[], metavar="IP",
                        help="IP que puede usar el servidor (obligatoria, repetible). 127.0.0.1 siempre puede.")
    parser.add_argument("--puerto", type=int, default=PUERTO_POR_DEFECTO,
                        help="puerto TCP (por defecto {})".format(PUERTO_POR_DEFECTO))
    args = parser.parse_args(argv)
    if not args.permitir:
        parser.error("falta --permitir IP (al menos una)")
    if not 1 <= args.puerto <= 65535:
        parser.error("--puerto debe estar entre 1 y 65535")
    return args


def serve(host="0.0.0.0", port=PUERTO_POR_DEFECTO, permitidas=()):
    server = ThreadedHTTPServer((host, port), Ev3RequestHandler, permitidas)
    print("EV3 (CVA) escuchando en el puerto {}; {} IP permitida(s) más 127.0.0.1".format(
        port, len(server.permitidas)))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    _args = parse_args()
    serve(port=_args.puerto, permitidas=_args.permitir)
