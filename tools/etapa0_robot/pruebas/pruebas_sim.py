#!/usr/bin/env python3
"""Pruebas de las herramientas de la Etapa 0 (ev3-*.sh) contra el simulador, en 127.0.0.1.

Uso, desde la raíz del repositorio:  python3 tools/etapa0_robot/pruebas/pruebas_sim.py

Ejecuta cada script como lo haría una persona (bash, con su entrada y su entorno), contra
`SimEtapa0`: la clase de `tests/robot_sim.py` (sin modificarla) más velocidad y posición de
outC y outD en /status y las fallas que el original no tiene. Imprime la salida literal de
cada caso y si cumple lo esperado. Cada caso usa un HOME temporal vacío: sin ~/.ev3_url y
con los registros fuera de ~/robot_pruebas. Termina con código 0 solo si todo cumple.

No es parte de la suite de pytest (no se llama test_*): tarda ~1 min por las esperas reales.
"""

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import time

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(DIR))
sys.path.insert(0, REPO)
from tests.robot_sim import RobotSim  # noqa: E402

VEL = 300.0  # grados/s del simulador mientras una rueda gira
TEMPORALES = []
atexit.register(lambda: [shutil.rmtree(d, ignore_errors=True) for d in TEMPORALES])


def temporal(prefijo):
    d = tempfile.mkdtemp(prefix=prefijo)
    TEMPORALES.append(d)
    return d


class SimEtapa0(RobotSim):
    """RobotSim con ruedas que se mueven: cada ruta abre un tramo de movimiento y la parada lo
    corta. Fallas añadidas:
      forzar_409       POST /ejecutar_ruta responde 409 aunque /estado_ejecucion diga libre
      total_cero       POST /ejecutar_ruta responde {"ok": true, "totalPasos": 0} sin moverse
      ignora_detener   /detener_ejecucion responde ok pero nada se para
      parada_lenta_s   tras /detener_ejecucion, ruedas y ejecutando siguen ese tiempo
      reanuda_tras_s   tras parar, las ruedas vuelven a girar 3 s pasado ese tiempo
    """

    def __init__(self, route_duration_s=5.0, **kw):
        super().__init__(route_duration_s=route_duration_s, **kw)
        self.tramos = []  # [inicio, fin, dir_C, dir_D] en reloj monotónico
        self.forzar_409 = False
        self.total_cero = False
        self.ignora_detener = False
        self.parada_lenta_s = 0.0
        self.reanuda_tras_s = None

    def _motor(self, t, i):
        pos = vel = 0.0
        for ini, fin, *dirs in self.tramos:
            if t > ini:
                pos += dirs[i] * VEL * (min(t, fin) - ini)
            if ini <= t < fin:
                vel = dirs[i] * VEL
        return round(vel), round(pos)

    def _apply(self, method, path, body):
        now = time.monotonic()
        if method == "GET" and path == "/status":
            with self._lock:
                self.applied.append((method, path, now))
                motors = []
                for p in ("outA", "outB", "outC", "outD"):
                    vel, pos = self._motor(now, 0 if p == "outC" else 1) if p in ("outC", "outD") else (0, 0)
                    motors.append({"port": p, "speed": vel, "position": pos, "connected": p in self.connected_ports})
                return 200, {"motors": motors, "battery": 7.9, "sensors": [], "connected": True}
        if method == "POST" and path == "/ejecutar_ruta":
            with self._lock:
                if self.forzar_409:
                    self.applied.append((method, path, now))
                    return 409, {"ok": False, "error": "ya hay una ruta en ejecución"}
                if self.total_cero:
                    self.applied.append((method, path, now))
                    return 200, {"ok": True, "totalPasos": 0}
            status, obj = super()._apply(method, path, body)
            if status == 200 and obj["totalPasos"] > 0:
                p0 = body["pasos"][0]
                dirs = [1 if p0["gradosIzq"] > 0 else -1, 1 if p0["gradosDer"] > 0 else -1]
                with self._lock:
                    self.tramos.append([now, now + self.route_duration_s, *dirs])
            return status, obj
        if method == "POST" and path == "/detener_ejecucion":
            if self.ignora_detener:
                with self._lock:
                    self.applied.append((method, path, now))
                return 200, {"ok": True}
            status, obj = super()._apply(method, path, body)
            with self._lock:
                corte = now + self.parada_lenta_s
                for t in self.tramos:
                    t[1] = min(t[1], max(t[0], corte))
                if self.parada_lenta_s:
                    self._executing_until = corte
                if self.reanuda_tras_s is not None and self.tramos:
                    ini = now + self.reanuda_tras_s
                    self.tramos.append([ini, ini + 3.0, *self.tramos[-1][2:]])
                    self.reanuda_tras_s = None
            return status, obj
        return super()._apply(method, path, body)

    def posts(self, path):
        return [r for r in self.requests if r[0] == "POST" and r[1] == path]


def correr(script, args, entrada="", env_extra=None, home=None, path=None, timeout=40):
    home = home or temporal("etapa0_home_")
    env = {"PATH": path or os.environ["PATH"], "HOME": home, "LANG": os.environ.get("LANG", "C.UTF-8")}
    env.update(env_extra or {})
    t0 = time.monotonic()
    p = subprocess.run(["bash", os.path.join(DIR, script), *args], input=entrada, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True, env=env, timeout=timeout)
    return p.returncode, p.stdout, time.monotonic() - t0, home


RESULTADOS = []


def caso(nombre, script, args, comprobaciones, entrada="", sim=None, **kw):
    """comprobaciones(rc, salida, segundos, home) -> lista de (descripción, bool)."""
    rc, salida, seg, home = correr(script, args, entrada, **kw)
    mostrado = " ".join(args)
    if sim is not None:
        mostrado = mostrado.replace(sim.url, "$SIM")
    print(f"\n### {nombre}")
    print(f"$ {script} {mostrado}" + (f"   (entrada: {entrada.strip()!r})" if entrada else ""))
    print(salida.rstrip())
    print(f"[código de salida {rc}, {seg:.2f} s]")
    checks = list(comprobaciones(rc, salida, seg, home))
    checks.append(("la salida no contiene la IP ni la URL", "127.0.0.1" not in salida))
    for desc, ok in checks:
        print(f"  {'PASA ' if ok else 'FALLA'} {desc}")
    RESULTADOS.append((nombre, all(ok for _, ok in checks)))


def nuevo_sim(**kw):
    s = SimEtapa0(**kw)
    return s


def registro_de(home):
    d = os.path.join(home, "robot_pruebas")
    archivos = sorted(os.listdir(d)) if os.path.isdir(d) else []
    return open(os.path.join(d, archivos[-1]), encoding="utf-8").read() if archivos else ""


def pruebas_paso():
    print("\n================ ev3-paso.sh ================")
    s = nuevo_sim()
    caso("paso: normal (avanzar, valores por defecto)", "ev3-paso.sh", ["avanzar", "--url", s.url],
         lambda rc, out, seg, h: [
             ("código 0", rc == 0),
             ("un solo POST /ejecutar_ruta", len(s.posts("/ejecutar_ruta")) == 1),
             ("cuerpo C+90 D+90 potencia 20", s.posts("/ejecutar_ruta")[0][2] == {"pasos": [
                 {"motorIzq": "outC", "motorDer": "outD", "gradosIzq": 90, "gradosDer": 90, "potencia": 20}]}),
             ("antes del POST: /status y /estado_ejecucion", [r[1] for r in s.requests][:2] == ["/status", "/estado_ejecucion"]),
         ], entrada="SI\n", sim=s)
    s.close()

    for direc, gi, gd in (("derecha", 45, -45), ("izquierda", -45, 45)):
        s = nuevo_sim()
        caso(f"paso: {direc} --grados 45 --potencia 10", "ev3-paso.sh",
             [direc, "--grados", "45", "--potencia", "10", "--url", s.url],
             lambda rc, out, seg, h, s=s, gi=gi, gd=gd: [
                 ("código 0", rc == 0),
                 (f"cuerpo C{gi:+d} D{gd:+d} potencia 10", s.posts("/ejecutar_ruta")[0][2]["pasos"][0]
                  == {"motorIzq": "outC", "motorDer": "outD", "gradosIzq": gi, "gradosDer": gd, "potencia": 10}),
             ], entrada="SI\n", sim=s)
        s.close()

    s = nuevo_sim()
    for args in (["--grados", "181"], ["--grados", "0"], ["--potencia", "31"], ["--grados", "90.5"]):
        caso(f"paso: tope {' '.join(args)}", "ev3-paso.sh", ["avanzar", *args, "--url", s.url],
             lambda rc, out, seg, h: [("código 2", rc == 2), ("ninguna petición al robot", s.requests == [])],
             entrada="SI\n", sim=s)
    caso("paso: tope en --ensayo también (--grados 181)", "ev3-paso.sh", ["avanzar", "--grados", "181", "--ensayo"],
         lambda rc, out, seg, h: [("código 2", rc == 2)])
    s.close()

    caso("paso: sin URL configurada", "ev3-paso.sh", ["avanzar"],
         lambda rc, out, seg, h: [("código 2", rc == 2), ("mensaje claro", "no hay dirección del robot" in out)],
         entrada="SI\n")

    s = nuevo_sim()
    caso("paso: URL inválida (con ruta)", "ev3-paso.sh", ["avanzar", "--url", s.url + "/status"],
         lambda rc, out, seg, h: [("código 2", rc == 2), ("ninguna petición", s.requests == [])], entrada="SI\n", sim=s)
    caso("paso: --url manda sobre EV3_URL (EV3_URL apunta a un puerto cerrado)", "ev3-paso.sh",
         ["avanzar", "--url", s.url], lambda rc, out, seg, h: [("código 0", rc == 0), ("origen --url", "URL tomada de --url" in out)],
         entrada="SI\n", sim=s, env_extra={"EV3_URL": "http://127.0.0.1:9"})
    s.close()
    s = nuevo_sim()
    home = temporal("etapa0_home_")
    with open(os.path.join(home, ".ev3_url"), "w") as f:
        f.write(s.url + "\n")
    caso("paso: URL desde ~/.ev3_url", "ev3-paso.sh", ["avanzar"],
         lambda rc, out, seg, h: [("código 0", rc == 0), ("origen ~/.ev3_url", "URL tomada de ~/.ev3_url" in out)],
         entrada="SI\n", sim=s, home=home)
    s.close()

    s = nuevo_sim(route_duration_s=30.0)
    s._apply("POST", "/ejecutar_ruta", {"pasos": [{"motorIzq": "outC", "motorDer": "outD", "gradosIzq": 90, "gradosDer": 90, "potencia": 20}]})
    caso("paso: ruta en curso (estado ejecutando:true) -> no envía", "ev3-paso.sh", ["avanzar", "--url", s.url],
         lambda rc, out, seg, h: [("código 1", rc == 1), ("ningún POST", s.posts("/ejecutar_ruta") == [])],
         entrada="SI\n", sim=s)
    s.close()

    s = nuevo_sim()
    s.forzar_409 = True
    caso("paso: 409 (otra ruta empezó entre la comprobación y el envío)", "ev3-paso.sh", ["avanzar", "--url", s.url],
         lambda rc, out, seg, h: [("código 1", rc == 1), ("dice 409", "409" in out),
                                  ("un solo POST (no reintenta)", len(s.posts("/ejecutar_ruta")) == 1)],
         entrada="SI\n", sim=s)
    s.close()

    s = nuevo_sim()
    s.drop_paths.add("/ejecutar_ruta")
    caso("paso: la respuesta no llega (el robot cierra sin responder)", "ev3-paso.sh", ["avanzar", "--url", s.url],
         lambda rc, out, seg, h: [("código 1", rc == 1), ("un solo POST (no reintenta)", len(s.posts("/ejecutar_ruta")) == 1),
                                  ("consulta /estado_ejecucion después", s.requests[-1][1] == "/estado_ejecucion"),
                                  ("avisa que sin respuesta no es no ejecutado", "no significa" in out)],
         entrada="SI\n", sim=s)
    s.close()

    s = nuevo_sim()
    s.response_delay["/ejecutar_ruta"] = 6.0
    caso("paso: la respuesta tarda más de 5 s (tiempo agotado)", "ev3-paso.sh", ["avanzar", "--url", s.url],
         lambda rc, out, seg, h: [("código 1", rc == 1), ("un solo POST (no reintenta)", len(s.posts("/ejecutar_ruta")) == 1),
                                  ("tiempo agotado", "tiempo agotado" in out)],
         entrada="SI\n", sim=s)
    s.close()

    s = nuevo_sim()
    s.total_cero = True
    caso("paso: respuesta ok con totalPasos 0", "ev3-paso.sh", ["avanzar", "--url", s.url],
         lambda rc, out, seg, h: [("código 1", rc == 1), ("lo trata como fallo", "totalPasos=0" in out)],
         entrada="SI\n", sim=s)
    s.close()

    s = nuevo_sim()
    for entrada in ("si\n", "NO\n", "SI, claro\n", ""):
        caso(f"paso: confirmación {entrada.strip()!r} (no es SI)", "ev3-paso.sh", ["avanzar", "--url", s.url],
             lambda rc, out, seg, h: [("código 3", rc == 3), ("ningún POST", s.posts("/ejecutar_ruta") == [])],
             entrada=entrada, sim=s)
    s.close()

    s = nuevo_sim(connected_ports=("outC",))
    caso("paso: outD no conectado -> no envía", "ev3-paso.sh", ["avanzar", "--url", s.url],
         lambda rc, out, seg, h: [("código 1", rc == 1), ("ningún POST", s.posts("/ejecutar_ruta") == [])],
         entrada="SI\n", sim=s)
    s.close()

    s = nuevo_sim()
    falso = temporal("etapa0_curl_")
    marca = os.path.join(falso, "curl_llamado")
    with open(os.path.join(falso, "curl"), "w") as f:
        f.write(f"#!/bin/sh\necho llamado >> {marca}\nexit 7\n")
    os.chmod(os.path.join(falso, "curl"), 0o755)
    caso("paso: --ensayo no abre ninguna conexión (curl falso primero en PATH)", "ev3-paso.sh",
         ["derecha", "--grados", "180", "--potencia", "30", "--url", s.url, "--ensayo"],
         lambda rc, out, seg, h: [("código 0", rc == 0), ("el simulador no recibió nada", s.requests == []),
                                  ("curl no se ejecutó", not os.path.exists(marca)),
                                  ("muestra el cuerpo", '"gradosIzq": 180, "gradosDer": -180, "potencia": 30' in out)],
         sim=s, path=falso + ":" + os.environ["PATH"])
    caso("paso: --ensayo sin URL configurada", "ev3-paso.sh", ["izquierda", "--ensayo"],
         lambda rc, out, seg, h: [("código 0", rc == 0)], path=falso + ":" + os.environ["PATH"])
    s.close()


def pruebas_parar():
    print("\n================ ev3-parar.sh ================")
    ruta = {"pasos": [{"motorIzq": "outC", "motorDer": "outD", "gradosIzq": 180, "gradosDer": 180, "potencia": 20}]}

    s = nuevo_sim(route_duration_s=30.0)
    s._apply("POST", "/ejecutar_ruta", ruta)
    caso("parar: normal (ruta en curso)", "ev3-parar.sh", ["--url", s.url],
         lambda rc, out, seg, h: [("código 0", rc == 0), ("PARADA CONFIRMADA en ... ms", "PARADA CONFIRMADA en" in out),
                                  ("un solo POST /detener_ejecucion", len(s.posts("/detener_ejecucion")) == 1),
                                  ("sin confirmación previa ni /status", all(r[1] != "/status" for r in s.requests))],
         sim=s)
    s.close()

    caso("parar: sin URL configurada", "ev3-parar.sh", [],
         lambda rc, out, seg, h: [("código 2", rc == 2), ("mensaje claro", "no hay dirección del robot" in out)])

    s = nuevo_sim(route_duration_s=30.0)
    s._apply("POST", "/ejecutar_ruta", ruta)
    s.drop_paths.add("/status")
    caso("parar: /status no responde (no lo necesita)", "ev3-parar.sh", ["--url", s.url],
         lambda rc, out, seg, h: [("código 0", rc == 0)], sim=s)
    s.close()

    s = nuevo_sim(route_duration_s=30.0)
    s._apply("POST", "/ejecutar_ruta", ruta)
    s.drop_paths.add("/detener_ejecucion")
    caso("parar: /detener_ejecucion no responde (se aplica pero se pierde la respuesta)", "ev3-parar.sh", ["--url", s.url],
         lambda rc, out, seg, h: [("tres intentos", len(s.posts("/detener_ejecucion")) == 3),
                                  ("código 0 (estado confirma ejecutando:false)", rc == 0), ("aviso", "AVISO" in out)],
         sim=s)
    s.close()

    s = nuevo_sim(route_duration_s=30.0)
    s._apply("POST", "/ejecutar_ruta", ruta)
    s.parada_lenta_s = 1.5
    caso("parar: la parada tarda 1,5 s en confirmarse", "ev3-parar.sh", ["--url", s.url],
         lambda rc, out, seg, h: [("código 0", rc == 0), ("tarda >= 1500 ms", seg >= 1.5)], sim=s)
    s.close()

    s = nuevo_sim(route_duration_s=60.0)
    s._apply("POST", "/ejecutar_ruta", ruta)
    s.ignora_detener = True
    caso("parar: la parada nunca se confirma", "ev3-parar.sh", ["--url", s.url],
         lambda rc, out, seg, h: [("código distinto de 0", rc != 0), ("aviso en mayúsculas", "PARADA NO CONFIRMADA" in out),
                                  ("se rinde a los 10 s (± 0,5)", 9.5 <= seg <= 10.5)], sim=s)
    s.close()

    s = nuevo_sim()
    s.go_down()
    caso("parar: robot caído (nada responde)", "ev3-parar.sh", ["--url", s.url],
         lambda rc, out, seg, h: [("código distinto de 0", rc != 0), ("aviso en mayúsculas", "PARADA NO CONFIRMADA" in out),
                                  ("<= 10,5 s", seg <= 10.5)], sim=s)


def pruebas_prueba_parada():
    print("\n================ ev3-prueba-parada.sh ================")

    def base(rc, out, seg, h, s, rc_esperado, texto):
        log = registro_de(h)
        return [(f"código {rc_esperado}", rc == rc_esperado), (f"dice {texto!r}", texto in out),
                ("registro guardado en ~/robot_pruebas", "RESULTADO" in log),
                ("el registro no contiene la IP", log != "" and "127.0.0.1" not in log),
                ("se envió /detener_ejecucion", len(s.posts("/detener_ejecucion")) >= 1)]

    s = nuevo_sim()
    caso("prueba-parada: normal (4 pasos)", "ev3-prueba-parada.sh", ["--url", s.url],
         lambda rc, out, seg, h: base(rc, out, seg, h, s, 0, "RESULTADO: PARÓ en") + [
             ("una ruta de 4 pasos de 180° a potencia 20", [p["gradosIzq"] for p in s.posts("/ejecutar_ruta")[0][2]["pasos"]] == [180] * 4
              and all(p["potencia"] == 20 and p["gradosDer"] == 180 for p in s.posts("/ejecutar_ruta")[0][2]["pasos"])),
             ("~1,0 s entre la ruta y la parada", 0.95 <= s.posts("/detener_ejecucion")[0][3] - s.posts("/ejecutar_ruta")[0][3] <= 1.5),
             ("no reanuda", "no reanuda." in out)],
         entrada="SI\n", sim=s)
    s.close()

    s = nuevo_sim()
    caso("prueba-parada: --pasos 2", "ev3-prueba-parada.sh", ["--pasos", "2", "--url", s.url],
         lambda rc, out, seg, h: [("código 0", rc == 0), ("ruta de 2 pasos", len(s.posts("/ejecutar_ruta")[0][2]["pasos"]) == 2)],
         entrada="SI\n", sim=s)
    for n in ("0", "5"):
        caso(f"prueba-parada: tope --pasos {n}", "ev3-prueba-parada.sh", ["--pasos", n, "--url", s.url],
             lambda rc, out, seg, h: [("código 2", rc == 2), ("ninguna petición nueva", len(s.posts("/ejecutar_ruta")) == 1)],
             entrada="SI\n", sim=s)
    s.close()

    caso("prueba-parada: sin URL configurada", "ev3-prueba-parada.sh", [],
         lambda rc, out, seg, h: [("código 2", rc == 2), ("mensaje claro", "no hay dirección del robot" in out)], entrada="SI\n")

    s = nuevo_sim()
    caso("prueba-parada: confirmación 'no' (no es SI)", "ev3-prueba-parada.sh", ["--url", s.url],
         lambda rc, out, seg, h: [("código 3", rc == 3), ("ningún POST", s.posts("/ejecutar_ruta") == [] and s.posts("/detener_ejecucion") == [])],
         entrada="no\n", sim=s)
    s.close()

    s = nuevo_sim()
    s.forzar_409 = True
    caso("prueba-parada: 409", "ev3-prueba-parada.sh", ["--url", s.url],
         lambda rc, out, seg, h: base(rc, out, seg, h, s, 1, "PRUEBA NO VÁLIDA: 409"), entrada="SI\n", sim=s)
    s.close()

    s = nuevo_sim()
    s.drop_paths.add("/ejecutar_ruta")
    caso("prueba-parada: la respuesta a la ruta no llega (pero se mueve) -> para igual", "ev3-prueba-parada.sh", ["--url", s.url],
         lambda rc, out, seg, h: base(rc, out, seg, h, s, 1, "PRUEBA NO VÁLIDA: la ruta no recibió respuesta") + [
             ("un solo POST de ruta (no reintenta)", len(s.posts("/ejecutar_ruta")) == 1)],
         entrada="SI\n", sim=s)
    s.close()

    s = nuevo_sim()
    s.total_cero = True
    caso("prueba-parada: respuesta ok con totalPasos 0", "ev3-prueba-parada.sh", ["--url", s.url],
         lambda rc, out, seg, h: base(rc, out, seg, h, s, 1, "totalPasos=0"), entrada="SI\n", sim=s)
    s.close()

    s = nuevo_sim()
    s.parada_lenta_s = 1.5
    caso("prueba-parada: la parada tarda 1,5 s en confirmarse", "ev3-prueba-parada.sh", ["--url", s.url],
         lambda rc, out, seg, h: base(rc, out, seg, h, s, 0, "RESULTADO: PARÓ en") + [
             ("marca que no cumple < 1000 ms", "NO cumple el objetivo" in out)], entrada="SI\n", sim=s)
    s.close()

    s = nuevo_sim(route_duration_s=60.0)
    s.ignora_detener = True
    caso("prueba-parada: no para", "ev3-prueba-parada.sh", ["--url", s.url],
         lambda rc, out, seg, h: base(rc, out, seg, h, s, 1, "RESULTADO: NO PARÓ") + [
             ("aviso de corte de energía", "USA EL CORTE DE ENERGÍA" in out)], entrada="SI\n", sim=s)
    s.close()

    s = nuevo_sim()
    s.reanuda_tras_s = 1.0
    caso("prueba-parada: para y luego reanuda", "ev3-prueba-parada.sh", ["--url", s.url],
         lambda rc, out, seg, h: base(rc, out, seg, h, s, 1, "REANUDÓ"), entrada="SI\n", sim=s)
    s.close()


if __name__ == "__main__":
    pruebas_paso()
    pruebas_parar()
    pruebas_prueba_parada()
    malos = [n for n, ok in RESULTADOS if not ok]
    print(f"\n================ {len(RESULTADOS) - len(malos)} de {len(RESULTADOS)} casos cumplen ================")
    for n in malos:
        print(f"  FALLA: {n}")
    sys.exit(1 if malos else 0)
