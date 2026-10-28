"""Proceso en primer plano del actuador del robot — Fase 11, Etapa 1.

No se importa desde main.py ni cambia el servicio: lee el registro del bridge (solo
lectura) y reconstruye, cuadro a cuadro, el gesto CONFIRMADO, el gesto CRUDO del
detector, sus confianzas y la hora del cuadro, para alimentar `RobotController`.
Armado por teclado (líneas en la terminal):

    a  + Enter  -> armar (o re-armar tras una parada/bloqueo)
    d  + Enter  -> desarmar (ejecuta PARAR)
    p  + Enter  -> PARAR (queda BLOQUEADO)
    q  + Enter  -> PARAR y salir (también Ctrl+C, SIGTERM y SIGHUP)

Fuentes de gestos (una obligatoria):
    --seguir RUTA       sigue el archivo en vivo (como tail -F)
    --reproducir RUTA   reproduce una copia (solo en ENSAYO)

Base de tiempo, en los dos modos: la HORA DE LA LÍNEA `[diag]` del registro, es decir,
cuándo el bridge terminó de procesar ese cuadro (lectura + inferencia, ~35-65 ms
después de leerlo; la hora exacta de lectura no se registra). NO es la hora a la que
este proceso lee la línea: así el sondeo del archivo (~50 ms) y cualquier retraso al
leerlo no alteran los intervalos entre cuadros. El desarme por inactividad usa el reloj
de pared (time.time()), que es el mismo reloj con que el bridge escribe esas horas. Un
salto del reloj hacia atrás reinicia la cuenta de sostenimiento; uno hacia adelante
solo puede adelantar el desarme: los dos fallan hacia el lado seguro.

Modo: ENSAYO por defecto (no abre ninguna conexión). El modo real exige --real Y
`robot_url` en la configuración, y al arrancar exige que GET /status responda con
outC y outD connected:true (el robot ignora sin avisar un motor no conectado).

Uso:
    python -m cva_gesture_bridge.actuators.robot_runner --config config/robot_actuador.json \\
        --seguir logs/cva_gesture_bridge.log
"""

import argparse
import hashlib
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import List, Optional

from cva_gesture_bridge.actuators.robot_client import (
    PATH_STATUS,
    FrameInput,
    OrderLog,
    RobotConfigError,
    RobotController,
    check_status_motors,
    load_robot_config,
    make_transport_factory,
)

logger = logging.getLogger(__name__)

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) ")
DIAG_CAND_RE = re.compile(
    r"\[diag\] Gesto candidato: (\S+) \(confianza=([\d.]+), dedos_extendidos=(\d+), confirmado=(True|False)\)$"
)
DIAG_NONE_RE = re.compile(r"\[diag\] Sin gesto reconocido en este frame \(dedos_extendidos=(\d+)\)$")
DETECTADO_RE = re.compile(r"Gesto detectado: (\S+) \(confianza=([\d.]+)\)$")
LIBERADO_RE = re.compile(r"Gesto liberado \(antes: (\S+)\)$")
# Fin de conexión o watchdog: no hay gesto vigente desde ahí.
CORTE_RE = re.compile(r"Conexión con .* finalizada|Watchdog: sin frames")
RELEVANT = ("[diag]", "Gesto detectado", "Gesto liberado")
SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def log_time_to_epoch(line: str) -> Optional[float]:
    m = TS_RE.match(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S,%f").timestamp()
    except ValueError:
        return None


class DiagFrameParser:
    """Reconstruye cada cuadro a partir del registro del bridge.

    Cada cuadro tiene una línea `[diag]` (gesto CRUDO y su confianza, y la hora del
    cuadro). El gesto CONFIRMADO sale de `Gesto detectado`/`Gesto liberado`, que
    main.py escribe DESPUÉS del `[diag]` del mismo cuadro: por eso el cuadro queda
    pendiente hasta la siguiente línea `[diag]`, salvo que llegue un cambio, que se
    aplica a ese mismo cuadro y lo emite al instante (un PARAR no espera al siguiente
    cuadro). La confianza del confirmado es la del último crudo que coincidió con él
    (igual que confirmed_confidence del estabilizador).

    La hora de cada cuadro es SIEMPRE la de su línea `[diag]`. Una línea relevante que
    no cumple el formato es ilegible: se cuenta y se ignora (no produce ningún cuadro,
    así que nunca mueve nada)."""

    def __init__(self) -> None:
        self.confirmed: Optional[str] = None
        self.confirmed_conf = 0.0
        self.illegible = 0
        self.frames_read = 0  # cuadros reales (líneas [diag]) emitidos
        self.last_frame_t: Optional[float] = None  # hora de registro del último cuadro
        self._pending = None  # [t, crudo, conf_crudo, emitido]

    def feed(self, line: str) -> List[FrameInput]:
        line = line.rstrip("\n")
        out: List[FrameInput] = []
        if CORTE_RE.search(line):
            t = log_time_to_epoch(line)
            self._flush(out)
            self.confirmed, self.confirmed_conf = None, 0.0
            if t is not None:
                out.append(FrameInput(None, 0.0, None, 0.0, t))
            return out
        if not any(k in line for k in RELEVANT):
            return out
        t = log_time_to_epoch(line)
        if t is None:
            self.illegible += 1
            return out
        try:
            if "[diag]" in line:
                m = DIAG_CAND_RE.search(line)
                if m:
                    raw, conf = m.group(1), float(m.group(2))
                elif DIAG_NONE_RE.search(line):
                    raw, conf = None, 0.0
                else:
                    self.illegible += 1
                    return out
                self._flush(out)
                self._pending = [t, raw, conf, False]
                return out
            m = DETECTADO_RE.search(line)
            if m:
                self.confirmed, self.confirmed_conf = m.group(1), float(m.group(2))
            elif LIBERADO_RE.search(line):
                self.confirmed, self.confirmed_conf = None, 0.0
            else:
                self.illegible += 1
                return out
        except ValueError:
            self.illegible += 1
            return out
        self._flush(out)
        return out

    def flush(self) -> List[FrameInput]:
        out: List[FrameInput] = []
        self._flush(out)
        return out

    def _flush(self, out: List[FrameInput]) -> None:
        p = self._pending
        if p is None or p[3]:
            return
        t, raw, conf, _ = p
        if self.confirmed is not None and raw == self.confirmed:
            self.confirmed_conf = conf
        out.append(FrameInput(self.confirmed, self.confirmed_conf, raw, conf, t))
        p[3] = True
        self.frames_read += 1
        self.last_frame_t = t


class StatusReporter:
    """Línea de estado periódica en --seguir (`estado_cada_s`; 0 = desactivada). Solo
    lee contadores: no toca el control. `now` es la hora de pared (time.time()), la
    misma base que la hora de las líneas del registro, y se pasa desde fuera para
    poder probarlo con un reloj inyectado."""

    STALE_S = 5.0  # edad del último cuadro a partir de la cual se avisa

    def __init__(self, every_s: float, mode: str, start: float) -> None:
        self.every_s = every_s
        self.mode = mode
        self._next = start + every_s

    def maybe_report(self, now: float, controller: RobotController, frames: "DiagFrameParser") -> bool:
        """Emite la línea si toca. Devuelve True si emitió."""
        if not self.every_s or now < self._next:
            return False
        self._next = now + self.every_s
        last = frames.last_frame_t
        if last is None:
            last_txt, age = "ninguno", None
        else:
            last_txt = datetime.fromtimestamp(last).strftime("%H:%M:%S,%f")[:-3]
            age = now - last
        logger.info(
            "Estado: modo=%s estado=%s cuadros=%d último=%s edad=%s ilegibles=%d gestos_ilegibles=%d "
            "pasos_encolados=%d órdenes=%d",
            self.mode, controller.state, frames.frames_read, last_txt,
            "-" if age is None else f"{age:.1f}s", frames.illegible, controller.illegible,
            controller.steps_enqueued, len(controller.order_log.records),
        )
        if age is None:
            logger.warning("SIN CUADROS desde el arranque")
        elif age > self.STALE_S:
            logger.warning("SIN CUADROS hace %.1f s", age)
        return True


def follow_lines(path: str, stop: threading.Event, poll_s: float = 0.05):
    """Como `tail -F`, solo lectura: empieza al final y reabre si el archivo rota.
    Rinde cada línea completa, o None en cada vuelta sin datos (para tick())."""
    f = open(path, encoding="utf-8", errors="replace")
    f.seek(0, os.SEEK_END)
    inode = os.fstat(f.fileno()).st_ino
    buf = ""
    try:
        while not stop.is_set():
            chunk = f.readline()
            if chunk:
                buf += chunk
                if buf.endswith("\n"):
                    yield buf
                    buf = ""
                continue
            try:
                st = os.stat(path)
                if st.st_ino != inode or st.st_size < f.tell():
                    f.close()
                    f = open(path, encoding="utf-8", errors="replace")
                    inode = os.fstat(f.fileno()).st_ino
                    buf = ""
                    logger.info("Registro rotado: reabierto %s", path)
            except FileNotFoundError:
                logger.info("Registro %s no está (rotación en curso): se reintenta", path)
            yield None
            stop.wait(poll_s)
    finally:
        f.close()


def keyboard_loop(controller: RobotController, stop: threading.Event, stream=None) -> None:
    stream = stream or sys.stdin
    for raw in stream:
        cmd = raw.strip().lower()
        if cmd == "a":
            controller.arm(time.time())
        elif cmd == "d":
            controller.disarm("comando local")
        elif cmd == "p":
            controller.manual_stop("PARAR manual (teclado)")
        elif cmd == "q":
            stop.set()
            return
        elif cmd:
            print("Comandos: a=armar  d=desarmar  p=parar  q=salir", flush=True)
        print(f"Estado: {controller.state} ({controller.state_reason})", flush=True)
    stop.set()  # fin de la entrada: se sale (con PARAR)


def check_robot_ready(factory, cfg) -> Optional[str]:
    """Modo real: /status debe responder y mostrar outC y outD conectados."""
    transport = factory()
    try:
        resp = OrderLog().send(transport, "GET", PATH_STATUS)
    finally:
        transport.close()
    if resp is None:
        return f"el robot no responde a {PATH_STATUS}"
    return check_status_motors(resp.status, resp.body, cfg)


def git_blob_id(path: str) -> str:
    """Mismo id que `git hash-object`: se compara directo con `git rev-parse <commit>:<ruta>`
    y no necesita git ni un repositorio (sirve también para una copia exportada)."""
    with open(path, "rb") as f:
        data = f.read()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _run_git(args: List[str], cwd: str) -> Optional[str]:
    """Solo lectura (GIT_OPTIONAL_LOCKS=0). None si git falla o no hay repositorio."""
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5,
                             env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("Versión: git no disponible (%s)", e)
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def code_version_line(config_path: str, git=_run_git) -> str:
    """Qué código y qué configuración corren: commit de HEAD, si los archivos del actuador
    difieren de ese commit, e id de blob de cada archivo tal como está en disco (los
    módulos ya se importaron de esos mismos archivos). El ensayo del 28/10 15:18 no lo
    registraba y hubo que reconstruir la versión después (ver BITACORA.md)."""
    pkg = os.path.dirname(os.path.abspath(__file__))
    files = [os.path.join(pkg, "robot_client.py"), os.path.abspath(__file__), os.path.abspath(config_path)]
    head = git(["rev-parse", "--short=7", "HEAD"], pkg)
    if head is None:
        commit = "commit=desconocido (sin repositorio git)"
    else:
        dirty = git(["status", "--porcelain", "--", *files], pkg)
        if dirty is None:
            commit = f"commit={head} (estado del árbol desconocido)"
        elif dirty:
            commit = f"commit={head} CON CAMBIOS SIN COMMIT"
        else:
            commit = f"commit={head} sin cambios"
    blobs = " ".join(f"{os.path.basename(p)}={git_blob_id(p)[:7]}" for p in files)
    return f"Código: {commit}; blobs en disco: {blobs}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--seguir")
    src.add_argument("--reproducir")
    parser.add_argument("--real", action="store_true", help="Modo real (exige robot_url). Sin esto: ENSAYO.")
    parser.add_argument("--armar-al-inicio", action="store_true",
                        help="Solo con --reproducir en ENSAYO: arma sin teclado para ensayar una copia.")
    parser.add_argument("--espera-parada-s", type=float, default=10.0,
                        help="Al salir, cuánto esperar la confirmación de PARAR (default 10).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        cfg = load_robot_config(args.config)
        if args.reproducir and args.real:
            raise RobotConfigError("--reproducir solo se permite en ENSAYO")
        if args.armar_al_inicio and (args.real or not args.reproducir):
            raise RobotConfigError("--armar-al-inicio solo con --reproducir en ENSAYO")
        factory = make_transport_factory(cfg, args.real)
    except RobotConfigError as e:
        logger.error("No se arranca: %s", e)
        return 2

    if args.real:
        problem = check_robot_ready(factory, cfg)
        if problem:
            logger.error("No se arranca: %s", problem)
            return 3

    # En --reproducir (solo ENSAYO) las rutas se envían en línea: la copia se recorre en
    # milisegundos y, con hilo, el PARAR de salida podía descartar un paso todavía no
    # tomado por el hilo (resultado no determinista). En --seguir van en su hilo para no
    # bloquear la lectura.
    controller = RobotController(cfg, factory, threaded_routes=not args.reproducir)
    logger.warning("Actuador del robot en modo %s. Estado inicial: %s", "REAL" if args.real else "ENSAYO", controller.state)
    logger.warning("%s", code_version_line(args.config))
    stop = threading.Event()
    frames = DiagFrameParser()

    def on_signal(signum, _frame):
        logger.warning("Señal %s: PARAR y salida", signal.Signals(signum).name)
        stop.set()

    previous = {s: signal.signal(s, on_signal) for s in SIGNALS}
    confirmed = False
    try:
        if args.reproducir:
            armed = not args.armar_al_inicio
            with open(args.reproducir, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if stop.is_set():
                        break
                    for fr in frames.feed(line):
                        if not armed:  # se arma con la hora del primer cuadro
                            controller.arm(fr.t)
                            armed = True
                        controller.observe(fr)
                        controller.tick(fr.t)
            for fr in frames.flush():
                controller.observe(fr)
        else:
            threading.Thread(target=keyboard_loop, args=(controller, stop), daemon=True).start()
            print("Comandos: a=armar  d=desarmar  p=parar  q=salir", flush=True)
            status = StatusReporter(cfg.estado_cada_s, "REAL" if args.real else "ENSAYO", time.time())
            for line in follow_lines(args.seguir, stop):
                if line is not None:
                    for fr in frames.feed(line):
                        controller.observe(fr)
                now = time.time()
                controller.tick(now)
                status.maybe_report(now, controller, frames)
    finally:
        confirmed = controller.close(args.espera_parada_s)  # PARAR antes de salir
        for s, h in previous.items():
            signal.signal(s, h)
        logger.warning("Actuador cerrado (parada %s). Líneas ilegibles: %d; gestos ilegibles: %d; órdenes: %d",
                       "confirmada" if confirmed else "NO confirmada", frames.illegible,
                       controller.illegible, len(controller.order_log.records))
    return 0 if confirmed else 4


if __name__ == "__main__":
    sys.exit(main())
