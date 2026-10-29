"""Actuador simulado del robot EV3 — SOLO para pruebas (Fase 11, Etapa 1).

Servidor HTTP local (127.0.0.1, puerto efímero) que reproduce el contrato real
(docs/CONTRATO_ROBOT_EV3.md) con sus cuerpos exactos:

- POST /ejecutar_ruta {"pasos":[...]} -> {"ok": true, "totalPasos": N}; si hay una
  ruta en curso, 409 {"ok": false, "error": "ya hay una ruta en ejecución"}. Un JSON
  malformado se trata como cuerpo vacío: {"ok": true, "totalPasos": 0}. Al procesarla
  ejecuta detener.clear(): borra una parada anterior.
- GET /estado_ejecucion -> {"pasoActual", "totalPasos", "ejecutando", "error"}.
- POST /detener_ejecucion -> {"ok": true}: marca el paro y cancela la ruta en curso.
- POST /stop -> {"ok": true, "stopped": [...]}: para motores, pero en una ruta en
  curso NO la cancela.
- GET /status -> {"motors": [...], "battery": V, "sensors": [], "connected": true},
  con outC y outD conectados y outA/outB no (configurable). Cada motor lleva
  `position`: grados acumulados del encoder (entero, no se reinicia). Una ruta suma
  sus grados a cada rueda de forma lineal durante `route_duration_s` (al instante si
  es 0); /detener_ejecucion congela la posición donde esté.
- Una rueda NO conectada no se mueve, y tampoco una de `immobile_ports`: conectada,
  la ruta contesta ok:true y no se mueve (como outD, motor mediano, con el servicio
  real que crea todos los motores como LargeMotor).
- Cada petición en su propio hilo (ThreadingHTTPServer), como el servidor real.

Fallas inyectables:
- `process_delay[path]`: espera ANTES de aplicar la petición (simula una orden que
  llega tarde al robot; con eso una ruta puede aplicarse DESPUÉS de un detener).
- `response_delay[path]`: espera DESPUÉS de aplicarla y antes de responder (el robot
  ya se movió pero la respuesta tarda).
- `drop_paths`: esas rutas se leen y la conexión se cierra sin responder.
- `lose_next`: las N próximas peticiones (cualquier ruta) se pierden igual.
- `go_down()`: caída total (tampoco responden las conexiones ya abiertas).
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORTS = ("outA", "outB", "outC", "outD")


class RobotSim:
    def __init__(self, route_duration_s: float = 0.0, connected_ports=("outC", "outD")) -> None:
        self.route_duration_s = route_duration_s
        self.connected_ports = set(connected_ports)
        self.immobile_ports = set()
        self.positions = {p: 0 for p in PORTS}  # posición asentada (sin el movimiento en curso)
        self._move = None  # (inicio, duración, {puerto: grados}) de la ruta en curso
        self.process_delay = {}
        self.response_delay = {}
        self.drop_paths = set()
        self.lose_next = 0
        self.requests = []  # (método, ruta, cuerpo, hora_monotónica_de_llegada)
        self.applied = []  # (método, ruta, hora_monotónica_en_que_se_aplicó)
        self._lock = threading.Lock()
        self._executing_until = 0.0
        self._total = 0
        self._detener = False
        self._down = False
        sim = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # keep-alive, como un servidor real

            def log_message(self, *args):
                pass

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    obj = json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    obj = None  # el robot real trata el JSON malformado como cuerpo vacío
                return obj

            def _reply(self, status, obj):
                data = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self, method):
                if sim._down:
                    self.close_connection = True
                    return
                body = self._body() if method == "POST" else None
                path = self.path
                with sim._lock:
                    sim.requests.append((method, path, body, time.monotonic()))
                    lose = sim.lose_next > 0 or path in sim.drop_paths
                    if sim.lose_next > 0:
                        sim.lose_next -= 1
                    before = sim.process_delay.get(path, 0.0)
                    after = sim.response_delay.get(path, 0.0)
                if before:
                    time.sleep(before)
                if sim._down:
                    self.close_connection = True
                    return
                status, obj = sim._apply(method, path, body)
                if after:
                    time.sleep(after)
                if lose:
                    self.close_connection = True
                    return  # aplicada pero sin respuesta
                self._reply(status, obj)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def executing(self) -> bool:
        return time.monotonic() < self._executing_until

    def _current_positions(self, now):
        pos = dict(self.positions)
        if self._move is not None:
            start, duration, deltas = self._move
            frac = 1.0 if duration <= 0 else min(1.0, (now - start) / duration)
            for p, g in deltas.items():
                pos[p] += round(g * frac)
        return pos

    def _settle(self, now):
        self.positions = self._current_positions(now)
        self._move = None

    def current_positions(self):
        with self._lock:
            return self._current_positions(time.monotonic())

    def _apply(self, method, path, body):
        with self._lock:
            now = time.monotonic()
            self.applied.append((method, path, now))
            if method == "GET" and path == "/status":
                pos = self._current_positions(now)
                motors = [{"port": p, "speed": 0, "position": pos[p], "connected": p in self.connected_ports}
                          for p in PORTS]
                return 200, {"motors": motors, "battery": 7.9, "sensors": [], "connected": True}
            if method == "GET" and path == "/estado_ejecucion":
                ej = self.executing
                return 200, {"pasoActual": 1 if ej else 0, "totalPasos": self._total, "ejecutando": ej, "error": None}
            if method == "POST" and path == "/ejecutar_ruta":
                if self.executing:
                    return 409, {"ok": False, "error": "ya hay una ruta en ejecución"}
                pasos = body.get("pasos") if isinstance(body, dict) else None
                pasos = pasos if isinstance(pasos, list) else []
                self._detener = False  # detener.clear(): borra una parada anterior
                self._total = len(pasos)
                if pasos:
                    self._executing_until = now + self.route_duration_s
                    self._settle(now)
                    deltas = {}
                    for paso in pasos:
                        for mk, gk in (("motorIzq", "gradosIzq"), ("motorDer", "gradosDer")):
                            port, deg = (paso.get(mk), paso.get(gk)) if isinstance(paso, dict) else (None, None)
                            if (port in self.connected_ports and port not in self.immobile_ports
                                    and isinstance(deg, int) and not isinstance(deg, bool)):
                                deltas[port] = deltas.get(port, 0) + deg
                    self._move = (now, self.route_duration_s, deltas)
                return 200, {"ok": True, "totalPasos": len(pasos)}
            if method == "POST" and path == "/detener_ejecucion":
                self._detener = True
                self._executing_until = 0.0
                self._settle(now)
                return 200, {"ok": True}
            if method == "POST" and path == "/stop":
                return 200, {"ok": True, "stopped": sorted(self.connected_ports)}
            return 404, {"ok": False, "error": "ruta desconocida"}

    def paths(self):
        with self._lock:
            return [(r[0], r[1]) for r in self.requests]

    def go_down(self) -> None:
        if not self._down:
            self._down = True
            self.server.shutdown()
            self.server.server_close()

    def close(self) -> None:
        self.go_down()
