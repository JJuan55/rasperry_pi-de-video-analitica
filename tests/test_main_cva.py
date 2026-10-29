"""Pruebas de tools/robot_ev3/main_cva.py (Tarea 12), sin robot.

`ev3dev2` no existe fuera del ladrillo: se inyecta uno falso en sys.modules antes
de cargar main_cva (y se retira después). Sus motores "giran" durante
`SEG_POR_GRADO` segundos por grado y registran cada orden. sysfs (/status) se
simula con un directorio temporal. El servidor escucha en 127.0.0.1 con puerto
efímero; para probar IP no permitidas, el cliente sale de 127.0.0.2 / 127.0.0.3
(en Linux todo 127.0.0.0/8 es loopback).
"""
import ast
import http.client
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tokenize
import types
import unittest
from contextlib import redirect_stderr
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_CVA = os.path.join(REPO, "tools", "robot_ev3", "main_cva.py")

# Código del ev3dev2.motor falso: se usa tal cual en el proceso de prueba y, escrito
# como paquete, en la prueba que arranca main_cva.py como programa.
FAKE_MOTOR_SRC = '''
import time

OUTPUT_A, OUTPUT_B, OUTPUT_C, OUTPUT_D = "outA", "outB", "outC", "outD"
FALLAR = set()          # puertos cuyo constructor lanza excepción (motor no creado)
SEG_POR_GRADO = 0.0     # duración simulada del giro
CREADOS = []            # (clase, puerto) en orden de creación


class _Motor(object):
    def __init__(self, port):
        CREADOS.append((type(self).__name__, port))
        if port in FALLAR:
            raise Exception("motor no encontrado en " + port)
        self.port = port
        self.ordenes = []   # (speed, degrees, brake, block)
        self.apagados = []  # brake de cada off()
        self._hasta = 0.0

    def on_for_degrees(self, speed, degrees, brake=True, block=True):
        self.ordenes.append((speed, degrees, brake, block))
        self._hasta = time.monotonic() + abs(degrees) * SEG_POR_GRADO

    @property
    def is_running(self):
        return time.monotonic() < self._hasta

    def off(self, brake=True):
        self.apagados.append(brake)
        self._hasta = 0.0

    def on(self, *args, **kwargs):
        raise AssertionError("main_cva no debe usar on() (era de /move y ?cmd=)")


class LargeMotor(_Motor):
    pass


class MediumMotor(_Motor):
    pass
'''

PASO = {"motorIzq": "outC", "motorDer": "outD", "gradosIzq": 90, "gradosDer": 90, "potencia": 20}


def load_main_cva(fallar=(), seg_por_grado=0.0):
    """Carga una copia nueva de main_cva con un ev3dev2 falso. Devuelve (módulo, fake)."""
    fake_motor = types.ModuleType("ev3dev2.motor")
    exec(FAKE_MOTOR_SRC, fake_motor.__dict__)
    fake_motor.FALLAR = set(fallar)
    fake_motor.SEG_POR_GRADO = seg_por_grado
    fake_pkg = types.ModuleType("ev3dev2")
    fake_pkg.motor = fake_motor
    saved = {k: sys.modules.get(k) for k in ("ev3dev2", "ev3dev2.motor")}
    sys.modules["ev3dev2"], sys.modules["ev3dev2.motor"] = fake_pkg, fake_motor
    try:
        spec = importlib.util.spec_from_file_location("main_cva_prueba", MAIN_CVA)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return mod, fake_motor


class ServerTestCase(unittest.TestCase):
    """Un main_cva nuevo por prueba, con sysfs simulado y servidor en un hilo."""

    FALLAR = ()
    SEG_POR_GRADO = 0.0
    PERMITIDAS = ("127.0.0.3",)

    def setUp(self):
        self.m, self.fake = load_main_cva(self.FALLAR, self.SEG_POR_GRADO)
        self.sysfs = tempfile.mkdtemp(prefix="sysfs_")
        self.addCleanup(shutil.rmtree, self.sysfs)
        tacho = os.path.join(self.sysfs, "tacho-motor")
        for node, port, pos in (("motor0", "outC", 1234), ("motor1", "outD", -56)):
            d = os.path.join(tacho, node)
            os.makedirs(d)
            for name, value in (("address", "ev3-ports:" + port), ("speed", "0"), ("position", str(pos))):
                with open(os.path.join(d, name), "w") as f:
                    f.write(value + "\n")
        battery = os.path.join(self.sysfs, "voltage_now")
        with open(battery, "w") as f:
            f.write("7912000\n")
        self.m.TACHO_DIR = tacho
        self.m.SENSOR_DIR = os.path.join(self.sysfs, "no-hay-sensores")
        self.m.BATTERY_FILE = battery

        self.server = self.m.ThreadedHTTPServer(("127.0.0.1", 0), self.m.Ev3RequestHandler, self.PERMITIDAS)
        self.port = self.server.server_address[1]
        hilo = threading.Thread(target=self.server.serve_forever, daemon=True)
        hilo.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.m.detener_ejecucion()
        self.server.shutdown()
        self.server.server_close()

    def req(self, method, path, body=None, raw=None, source="127.0.0.1", headers=None):
        """Devuelve (código, objeto JSON)."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3, source_address=(source, 0))
        try:
            data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
            hdrs = {"Content-Type": "application/json"} if data is not None else {}
            hdrs.update(headers or {})
            conn.request(method, path, body=data, headers=hdrs)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode())
        finally:
            conn.close()

    def motor(self, port):
        return self.m.motors[port]

    def wait_idle(self, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.m.estado_ejecucion()["ejecutando"]:
                return True
            time.sleep(0.01)
        return False

    def nothing_moved(self):
        return not self.motor("outC").ordenes and not self.motor("outD").ordenes


class MotorsAndStatusTests(ServerTestCase):
    def test_outc_large_outd_medium_and_medium_motor_moves(self):
        self.assertIsInstance(self.motor("outC"), self.fake.LargeMotor)
        self.assertIsInstance(self.motor("outD"), self.fake.MediumMotor)
        self.assertEqual(sorted(self.fake.CREADOS), [("LargeMotor", "outC"), ("MediumMotor", "outD")])
        self.assertIsNone(self.m.motors["outA"])
        self.assertIsNone(self.m.motors["outB"])
        st, obj = self.req("POST", "/ejecutar_ruta", {"pasos": [PASO]})
        self.assertEqual((st, obj), (200, {"ok": True, "totalPasos": 1}))
        self.assertTrue(self.wait_idle())
        self.assertEqual(self.motor("outC").ordenes, [(20.0, 90, True, False)])
        self.assertEqual(self.motor("outD").ordenes, [(20.0, 90, True, False)])  # la rueda D sí recibe la orden

    def test_turn_signs_and_default_power_is_20(self):
        paso = {"motorIzq": "outC", "motorDer": "outD", "gradosIzq": 90, "gradosDer": -90}  # sin potencia
        self.assertEqual(self.req("POST", "/ejecutar_ruta", {"pasos": [paso]})[0], 200)
        self.assertTrue(self.wait_idle())
        self.assertEqual(self.motor("outC").ordenes, [(20.0, 90, True, False)])
        self.assertEqual(self.motor("outD").ordenes, [(-20.0, 90, True, False)])

    def test_port_formats_like_normalize_port(self):
        for izq, der in (("C", "D"), ("ev3-ports:outC", "ev3-ports:outD"), ("outc", "outd"), ("D", "C")):
            with self.subTest(izq=izq, der=der):
                paso = dict(PASO, motorIzq=izq, motorDer=der)
                self.assertEqual(self.req("POST", "/ejecutar_ruta", {"pasos": [paso]})[0], 200)
                self.assertTrue(self.wait_idle())

    def test_status_keeps_format_and_connected_when_motor_created(self):
        st, obj = self.req("GET", "/status")
        self.assertEqual(st, 200)
        self.assertEqual(set(obj), {"connected", "battery", "motors", "sensors"})
        self.assertEqual(obj["battery"], 7.91)
        self.assertEqual(obj["sensors"], [])
        self.assertEqual([m["port"] for m in obj["motors"]], ["outA", "outB", "outC", "outD"])
        for m in obj["motors"]:
            self.assertEqual(set(m), {"port", "connected", "speed", "position"})
        by_port = {m["port"]: m for m in obj["motors"]}
        self.assertEqual(by_port["outC"], {"port": "outC", "connected": True, "speed": 0, "position": 1234})
        self.assertEqual(by_port["outD"], {"port": "outD", "connected": True, "speed": 0, "position": -56})
        self.assertFalse(by_port["outA"]["connected"])

    def test_estado_ejecucion_format(self):
        self.assertEqual(self.req("GET", "/estado_ejecucion"),
                         (200, {"ejecutando": False, "pasoActual": -1, "totalPasos": 0, "error": None}))


class MotorDNotCreatedTests(ServerTestCase):
    FALLAR = ("outD",)

    def test_route_using_d_is_400_and_status_says_disconnected(self):
        self.assertIsNone(self.m.motors["outD"])
        st, obj = self.req("POST", "/ejecutar_ruta", {"pasos": [PASO]})
        self.assertEqual((st, obj), (400, {"ok": False, "error": "motor no disponible: outD"}))
        self.assertEqual(self.motor("outC").ordenes, [])  # tampoco se movió la rueda C
        self.assertFalse(self.m.estado_ejecucion()["ejecutando"])
        by_port = {m["port"]: m for m in self.req("GET", "/status")[1]["motors"]}
        self.assertFalse(by_port["outD"]["connected"])  # sysfs lo ve, pero no se pudo crear
        self.assertEqual(by_port["outD"]["position"], -56)
        self.assertTrue(by_port["outC"]["connected"])


class ValidationTests(ServerTestCase):
    def test_empty_missing_or_unreadable_route_is_400(self):
        cases = [
            ("pasos vacío", {"raw": b'{"pasos": []}'}, "pasos debe ser una lista no vacía"),
            ("sin pasos", {"raw": b'{"otra": 1}'}, "falta pasos"),
            ("sin cuerpo", {}, "falta pasos"),
            ("pasos no es lista", {"raw": b'{"pasos": {"a": 1}}'}, "pasos debe ser una lista no vacía"),
            ("JSON ilegible", {"raw": b'{"pasos": [ '}, "cuerpo JSON ilegible"),
            ("JSON no es objeto", {"raw": b'[1, 2]'}, "cuerpo JSON ilegible"),
            ("no es UTF-8", {"raw": b'\xff\xfe'}, "cuerpo JSON ilegible"),
        ]
        for name, kw, msg in cases:
            with self.subTest(name):
                st, obj = self.req("POST", "/ejecutar_ruta", **kw)
                self.assertEqual((st, obj), (400, {"ok": False, "error": msg}))
        self.assertTrue(self.nothing_moved())

    def test_limits_and_types_are_400_and_nothing_moves(self):
        cases = {
            "potencia 31": dict(PASO, potencia=31),
            "potencia 0": dict(PASO, potencia=0),
            "potencia decimal": dict(PASO, potencia=20.0),
            "potencia booleana": dict(PASO, potencia=True),
            "potencia texto": dict(PASO, potencia="20"),
            "grados 181": dict(PASO, gradosIzq=181),
            "grados -181": dict(PASO, gradosDer=-181),
            "grados decimales": dict(PASO, gradosIzq=90.0),
            "grados texto": dict(PASO, gradosDer="90"),
            "grados booleanos": dict(PASO, gradosIzq=True),
            "grados nulos": dict(PASO, gradosDer=None),
            "sin gradosDer": {k: v for k, v in PASO.items() if k != "gradosDer"},
            "los dos grados en 0": dict(PASO, gradosIzq=0, gradosDer=0),
            "puerto outA": dict(PASO, motorIzq="outA"),
            "puerto outB": dict(PASO, motorDer="B"),
            "mismo puerto": dict(PASO, motorDer="outC"),
            "mismo puerto escrito distinto": dict(PASO, motorIzq="ev3-ports:outD"),
            "puerto no texto": dict(PASO, motorIzq=3),
            "sin motorDer": {k: v for k, v in PASO.items() if k != "motorDer"},
            "paso no es objeto": [90, 90],
            "pausa 5001": {"pausaMs": 5001},
            "pausa negativa": {"pausaMs": -1},
            "pausa decimal": {"pausaMs": 1.5},
            "pausa booleana": {"pausaMs": True},
            "pausa con más claves": {"pausaMs": 100, "gradosIzq": 90},
        }
        for name, paso in cases.items():
            with self.subTest(name):
                st, obj = self.req("POST", "/ejecutar_ruta", {"pasos": [paso]})
                self.assertEqual(st, 400, obj)
                self.assertIs(obj["ok"], False)
        st, obj = self.req("POST", "/ejecutar_ruta", {"pasos": [PASO] * 5})
        self.assertEqual((st, obj), (400, {"ok": False, "error": "como mucho 4 pasos"}))
        # un paso malo al final rechaza la ruta entera: tampoco se mueven los buenos
        st, _ = self.req("POST", "/ejecutar_ruta", {"pasos": [PASO, PASO, dict(PASO, potencia=31)]})
        self.assertEqual(st, 400)
        self.assertTrue(self.nothing_moved())
        self.assertFalse(self.m.estado_ejecucion()["ejecutando"])

    def test_error_messages_name_the_step_and_field(self):
        _, obj = self.req("POST", "/ejecutar_ruta", {"pasos": [PASO, dict(PASO, potencia=31)]})
        self.assertEqual(obj["error"], "paso 1: potencia debe ser entera entre 1 y 30")
        _, obj = self.req("POST", "/ejecutar_ruta", {"pasos": [dict(PASO, motorIzq="outA")]})
        self.assertEqual(obj["error"], "paso 0: motorIzq debe ser outC u outD")

    def test_valid_limits_and_pauses_are_accepted(self):
        for pasos in ([dict(PASO, gradosIzq=180, gradosDer=-180, potencia=30)],
                      [dict(PASO, gradosIzq=0, gradosDer=1, potencia=1)],
                      [{"pausaMs": 0}, PASO, {"pausaMs": 5000}]):
            with self.subTest(pasos=pasos):
                self.assertEqual(self.req("POST", "/ejecutar_ruta", {"pasos": pasos})[0], 200)
                self.m.detener_ejecucion()
                self.assertTrue(self.wait_idle())


class StopTestCase(ServerTestCase):
    SEG_POR_GRADO = 0.02  # 180° = 3,6 s: la ruta sigue en curso mientras se prueba


class RouteLifecycleTests(StopTestCase):
    FOUR_STEPS = {"pasos": [dict(PASO, gradosIzq=180, gradosDer=180, potencia=20)] * 4}

    def test_four_steps_of_180_at_power_20_are_accepted(self):
        st, obj = self.req("POST", "/ejecutar_ruta", self.FOUR_STEPS)
        self.assertEqual((st, obj), (200, {"ok": True, "totalPasos": 4}))
        estado = self.req("GET", "/estado_ejecucion")[1]
        self.assertEqual((estado["ejecutando"], estado["totalPasos"]), (True, 4))
        self.assertEqual(self.motor("outC").ordenes, [(20.0, 180, True, False)])

    def test_running_is_true_as_soon_as_the_route_is_accepted(self):
        # Antes, ejecutando:true lo ponía el hilo: un GET justo tras el 200 podía ver false.
        with mock.patch.object(self.m.threading.Thread, "start", lambda self: None):
            self.assertEqual(self.m.iniciar_ejecucion([PASO]), (True, None))
        self.assertTrue(self.m.estado_ejecucion()["ejecutando"])

    def test_409_while_a_route_is_running(self):
        self.assertEqual(self.req("POST", "/ejecutar_ruta", self.FOUR_STEPS)[0], 200)
        st, obj = self.req("POST", "/ejecutar_ruta", {"pasos": [PASO]})
        self.assertEqual((st, obj), (409, {"ok": False, "error": "ya hay una ruta en ejecución"}))
        self.assertEqual(len(self.motor("outC").ordenes), 1)  # la segunda no arrancó nada

    def test_simultaneous_starts_only_one_is_accepted(self):
        for ronda in range(5):
            with self.subTest(ronda=ronda):
                go = threading.Barrier(8)
                codes = []

                def post():
                    go.wait()
                    codes.append(self.req("POST", "/ejecutar_ruta", {"pasos": [PASO]})[0])
                hilos = [threading.Thread(target=post) for _ in range(8)]
                for h in hilos:
                    h.start()
                for h in hilos:
                    h.join(5)
                self.assertEqual(sorted(codes), [200] + [409] * 7)
                self.m.detener_ejecucion()
                self.assertTrue(self.wait_idle())
        self.assertEqual(len(self.motor("outC").ordenes), 5)  # una ruta por ronda

    def test_simultaneous_starts_direct_call(self):
        go = threading.Barrier(16)
        results = []

        def start():
            go.wait()
            results.append(self.m.iniciar_ejecucion([PASO])[0])
        hilos = [threading.Thread(target=start) for _ in range(16)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(5)
        self.assertEqual(results.count(True), 1)

    def _assert_stopped_by(self, path, body=None):
        self.assertEqual(self.req("POST", "/ejecutar_ruta", self.FOUR_STEPS)[0], 200)
        time.sleep(0.3)
        self.assertTrue(self.motor("outC").is_running)
        t0 = time.monotonic()
        self.assertEqual(self.req("POST", path, body), (200, {"ok": True}))
        self.assertTrue(self.wait_idle(1.0))
        self.assertLess(time.monotonic() - t0, 0.5)
        self.assertFalse(self.motor("outC").is_running)
        self.assertFalse(self.motor("outD").is_running)
        self.assertIn(True, self.motor("outC").apagados)  # off(brake=True)
        self.assertIn(True, self.motor("outD").apagados)
        time.sleep(0.3)
        self.assertEqual(len(self.motor("outC").ordenes), 1)  # los pasos 2-4 no arrancaron

    def test_detener_ejecucion_stops_the_route(self):
        self._assert_stopped_by("/detener_ejecucion")

    def test_stop_also_cancels_the_route_and_ignores_body(self):
        # A diferencia del /stop de main.py (solo apagaba motores; la ruta seguía).
        self._assert_stopped_by("/stop", {"motor": "outA"})

    def test_stop_without_body_and_without_route(self):
        self.assertEqual(self.req("POST", "/stop"), (200, {"ok": True}))
        self.assertTrue(self.m._ejecucion["detener"].is_set())


class RemovedRoutesTests(ServerTestCase):
    def test_removed_routes_are_404_and_do_nothing(self):
        with mock.patch.object(self.m.os, "kill") as kill:
            for method, path, body in (
                    ("POST", "/move", {"motor": "outC", "speed": 50}),
                    ("GET", "/?cmd=killpid&pid=99999", None),
                    ("GET", "/?cmd=motor&idx=1&speed=50", None),
                    ("GET", "/?cmd=stop&idx=1", None),
                    ("GET", "/", None),
                    ("POST", "/", None),
                    ("GET", "/ejecutar_ruta", None),
                    ("POST", "/status", None),
                    ("GET", "/stop", None),
                    ("POST", "/otra", {})):
                with self.subTest(method=method, path=path):
                    self.assertEqual(self.req(method, path, body),
                                     (404, {"ok": False, "error": "ruta desconocida"}))
        kill.assert_not_called()
        self.assertTrue(self.nothing_moved())
        for name in ("get_motor_by_index", "apply_speed", "_legacy_command"):
            self.assertFalse(hasattr(self.m, name) or hasattr(self.m.Ev3RequestHandler, name), name)
        self.assertNotIn("killpid", open(MAIN_CVA, encoding="utf-8").read())


class AllowedIpTests(ServerTestCase):
    PERMITIDAS = ("127.0.0.3", "127.0.0.4")

    def test_ip_not_allowed_is_403_for_every_route(self):
        for method, path in (("GET", "/status"), ("GET", "/estado_ejecucion"), ("POST", "/ejecutar_ruta"),
                             ("POST", "/detener_ejecucion"), ("POST", "/stop"), ("GET", "/otra")):
            with self.subTest(path=path):
                body = {"pasos": [PASO]} if path == "/ejecutar_ruta" else None
                st, obj = self.req(method, path, body, source="127.0.0.2")
                self.assertEqual((st, obj), (403, {"ok": False, "error": "prohibido"}))
        self.assertTrue(self.nothing_moved())
        self.assertFalse(self.m._ejecucion["detener"].is_set())  # ni el /stop ajeno llegó a ejecutarse

    def test_403_does_not_read_the_body(self):
        # Content-Length grande y ningún byte de cuerpo: si el servidor intentara leerlo,
        # se quedaría esperando y la petición vencería su timeout de 3 s.
        t0 = time.monotonic()
        st, _ = self.req("POST", "/ejecutar_ruta", raw=b"", source="127.0.0.2", headers={"Content-Length": "100000"})
        self.assertEqual(st, 403)
        self.assertLess(time.monotonic() - t0, 1.0)

    def test_localhost_and_every_allowed_ip_are_served(self):
        for source in ("127.0.0.1", "127.0.0.3", "127.0.0.4"):
            with self.subTest(source=source):
                self.assertEqual(self.req("GET", "/status", source=source)[0], 200)
        self.assertEqual(self.req("POST", "/ejecutar_ruta", {"pasos": [PASO]}, source="127.0.0.4")[0], 200)


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.m, _ = load_main_cva()

    def parse_error(self, argv):
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            self.m.parse_args(argv)
        self.assertEqual(cm.exception.code, 2)
        return err.getvalue()

    def test_permitir_is_required_and_repeatable(self):
        self.assertIn("falta --permitir", self.parse_error([]))
        self.assertIn("falta --permitir", self.parse_error(["--puerto", "8081"]))
        args = self.m.parse_args(["--permitir", "10.1.2.3", "--permitir", "10.1.2.4"])
        self.assertEqual(args.permitir, ["10.1.2.3", "10.1.2.4"])
        self.assertEqual(args.puerto, 8081)
        self.assertEqual(self.m.parse_args(["--permitir", "10.1.2.3", "--puerto", "9000"]).puerto, 9000)

    def test_invalid_values_without_echoing_them(self):
        for argv in (["--permitir", "10.1.2.999"], ["--permitir", "robot.local"], ["--permitir", "::1"]):
            with self.subTest(argv=argv):
                msg = self.parse_error(argv)
                self.assertIn("no es una IPv4 válida", msg)
                self.assertNotIn(argv[1], msg)
        for puerto in ("0", "70000", "x"):
            with self.subTest(puerto=puerto):
                self.parse_error(["--permitir", "10.1.2.3", "--puerto", puerto])


class ProgramTests(unittest.TestCase):
    """main_cva.py arrancado como programa (con el ev3dev2 falso en PYTHONPATH)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fake_ev3dev2_")
        self.addCleanup(shutil.rmtree, self.tmp)
        pkg = os.path.join(self.tmp, "ev3dev2")
        os.makedirs(pkg)
        open(os.path.join(pkg, "__init__.py"), "w").close()
        with open(os.path.join(pkg, "motor.py"), "w") as f:
            f.write(FAKE_MOTOR_SRC)
        self.env = dict(os.environ, PYTHONPATH=self.tmp, PYTHONDONTWRITEBYTECODE="1")

    def test_does_not_start_without_permitir(self):
        r = subprocess.run([sys.executable, MAIN_CVA], env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 2)
        self.assertIn("falta --permitir", r.stderr)

    def test_starts_serves_localhost_and_never_prints_the_ips(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        ips = ["10.9.8.7", "10.9.8.6"]
        p = subprocess.Popen([sys.executable, "-u", MAIN_CVA, "--permitir", ips[0], "--permitir", ips[1],
                              "--puerto", str(port)], env=self.env, cwd=self.tmp,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            line = p.stdout.readline()
            self.assertIn("puerto {}".format(port), line)
            self.assertIn("2 IP permitida(s)", line)
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            conn.request("GET", "/estado_ejecucion")
            self.assertEqual(conn.getresponse().status, 200)
            conn.close()
        finally:
            p.terminate()
            out, err = p.communicate(timeout=10)
        written = line + out + err
        for ip in ips:
            self.assertNotIn(ip, written)
        self.assertEqual(os.listdir(self.tmp), ["ev3dev2"])  # no escribió ningún archivo


class Python35SourceTests(unittest.TestCase):
    """El ladrillo corre Python 3.5: se comprueba la fuente, no se ejecuta en 3.5."""

    def setUp(self):
        with open(MAIN_CVA, encoding="utf-8") as f:
            self.src = f.read()

    def test_parses_as_python_3_5(self):
        ast.parse(self.src, filename=MAIN_CVA, feature_version=(3, 5))

    def test_no_fstrings_annotations_or_newer_syntax(self):
        tree = ast.parse(self.src)
        for node in ast.walk(tree):
            line = getattr(node, "lineno", "?")
            self.assertNotIsInstance(node, ast.JoinedStr, "f-string en la línea %s" % line)
            self.assertNotIsInstance(node, ast.AnnAssign, "anotación de variable en la línea %s" % line)
            self.assertNotIsInstance(node, ast.NamedExpr)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.assertFalse(node.returns, "anotación de retorno en %s" % node.name)
                self.assertFalse(any(a.annotation for a in node.args.args), "anotación en %s" % node.name)
        for tok in tokenize.generate_tokens(io.StringIO(self.src).readline):
            if tok.type == tokenize.NUMBER:
                self.assertNotIn("_", tok.string, "separador numérico (3.6+) en la línea %d" % tok.start[0])
            if tok.type == tokenize.STRING:
                prefix = tok.string[:tok.string.index(tok.string[-1])].lower()
                self.assertNotIn("f", prefix, "f-string en la línea %d" % tok.start[0])

    def test_only_stdlib_and_ev3dev2_imports(self):
        allowed = {"argparse", "ipaddress", "json", "os", "threading", "time", "http", "socketserver",
                   "urllib", "ev3dev2"}
        for node in ast.walk(ast.parse(self.src)):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module.split(".")[0]]
            else:
                continue
            for n in names:
                self.assertIn(n, allowed)


if __name__ == "__main__":
    unittest.main()
