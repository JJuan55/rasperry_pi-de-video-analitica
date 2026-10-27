"""Actuador simulado del robot EV3 — SOLO para pruebas (Fase 11, Etapa 1).

Servidor HTTP local (127.0.0.1, puerto efímero) con el mismo contrato que usa
`robot_client.py`: GET /status, POST /ejecutar_ruta (409 si hay una ruta en curso),
GET /estado_ejecucion, POST /detener_ejecucion (cancela la ruta), POST /stop (para
motores, pero en una ruta en curso NO la cancela: así lo indica el contrato).

Fallas inyectables: `delay_s` (retraso antes de responder), `lose_next` (las N
próximas peticiones se leen y la conexión se cierra sin responder) y `go_down()`
(caída total: el puerto deja de aceptar conexiones).
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class RobotSim:
    def __init__(self, route_duration_s: float = 0.0) -> None:
        self.route_duration_s = route_duration_s
        self.delay_s = 0.0
        self.lose_next = 0
        self.requests = []  # (método, ruta, cuerpo)
        self._lock = threading.Lock()
        self._executing_until = 0.0
        sim = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # keep-alive, como un servidor real

            def log_message(self, *args):
                pass

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    return json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    return {"_ilegible": raw.decode("utf-8", "replace")}

            def _reply(self, status, obj):
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self, method):
                body = self._body() if method == "POST" else None
                if sim._down:  # caída total: tampoco responden las conexiones ya abiertas
                    self.close_connection = True
                    return
                with sim._lock:
                    sim.requests.append((method, self.path, body))
                    lose = sim.lose_next > 0
                    if lose:
                        sim.lose_next -= 1
                    delay = sim.delay_s
                if delay:
                    time.sleep(delay)
                if lose:
                    self.close_connection = True
                    return  # respuesta perdida: se cierra sin responder
                status, obj = sim._route(method, self.path, body)
                self._reply(status, obj)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self._down = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def executing(self) -> bool:
        return time.monotonic() < self._executing_until

    def _route(self, method, path, body):
        with self._lock:
            if method == "GET" and path == "/status":
                return 200, {"ok": True}
            if method == "GET" and path == "/estado_ejecucion":
                return 200, {"ejecutando": self.executing}
            if method == "POST" and path == "/ejecutar_ruta":
                if self.executing:
                    return 409, {"error": "ruta en curso"}
                needed = ("motorIzq", "motorDer", "gradosIzq", "gradosDer", "potencia")
                if not isinstance(body, dict) or any(k not in body for k in needed):
                    return 400, {"error": "cuerpo inválido"}
                self._executing_until = time.monotonic() + self.route_duration_s
                return 200, {"aceptado": True}
            if method == "POST" and path == "/detener_ejecucion":
                self._executing_until = 0.0
                return 200, {"detenido": True}
            if method == "POST" and path == "/stop":
                return 200, {"motores": "parados"}  # no cancela una ruta en curso
            return 404, {"error": "ruta desconocida"}

    def paths(self):
        with self._lock:
            return [(m, p) for m, p, _ in self.requests]

    def go_down(self) -> None:
        if not self._down:
            self._down = True
            self.server.shutdown()
            self.server.server_close()

    def close(self) -> None:
        self.go_down()
