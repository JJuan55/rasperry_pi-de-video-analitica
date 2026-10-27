"""Proceso en primer plano del actuador del robot — Fase 11, Etapa 1.

No se importa desde main.py ni cambia el servicio: lee el registro del bridge (solo
lectura) y reconstruye, cuadro a cuadro, el gesto CONFIRMADO, su confianza y la hora,
para alimentar `RobotController`. Armado por teclado (líneas en la terminal):

    a  + Enter  -> armar (o re-armar tras una parada/bloqueo)
    d  + Enter  -> desarmar (manda parada)
    p  + Enter  -> parada manual (queda BLOQUEADO)
    q  + Enter  -> manda parada y sale (también Ctrl+C / SIGTERM)

Fuentes de gestos (una obligatoria):
    --seguir RUTA       sigue el archivo en vivo (como tail -F), hora = monotónica de lectura
    --reproducir RUTA   reproduce una copia, hora = la del registro (solo en ENSAYO)

Modo: ENSAYO por defecto (no abre ninguna conexión). El modo real exige --real Y
`robot_url` en la configuración, y hace GET /status al arrancar; si falla, no arranca.

Uso:
    python -m cva_gesture_bridge.actuators.robot_runner --config config/robot_actuador.json \\
        --seguir logs/cva_gesture_bridge.log
"""

import argparse
import logging
import os
import re
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from cva_gesture_bridge.actuators.robot_client import (
    PATH_STATUS,
    OrderDispatcher,
    RobotConfigError,
    RobotController,
    RobotTransportError,
    load_robot_config,
    make_transport,
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


@dataclass
class FrameEvent:
    gesture: Optional[str]
    confidence: float
    t: float


class DiagFrameParser:
    """Reconstruye el gesto confirmado por cuadro a partir del registro del bridge.

    Cada cuadro tiene una línea `[diag]` (una por frame). El gesto confirmado sale de
    `Gesto detectado`/`Gesto liberado`, que main.py escribe DESPUÉS del `[diag]` del
    mismo cuadro: por eso el cuadro queda pendiente hasta la siguiente línea `[diag]`,
    salvo que llegue un cambio, que se aplica a ese mismo cuadro y lo emite al
    instante (un PARAR no espera al siguiente cuadro). La confianza del confirmado es
    la del último candidato que coincidió con él (igual que confirmed_confidence).

    Línea relevante que no cumple el formato = ilegible: se cuenta y se ignora (no
    produce ningún cuadro, así que nunca mueve nada)."""

    def __init__(self) -> None:
        self.confirmed: Optional[str] = None
        self.confirmed_conf = 0.0
        self.illegible = 0
        self._pending = None  # [t, candidato, conf_candidato, emitido]

    def feed(self, line: str, t: float) -> List[FrameEvent]:
        line = line.rstrip("\n")
        out: List[FrameEvent] = []
        if CORTE_RE.search(line):
            self._flush(out)
            self.confirmed, self.confirmed_conf = None, 0.0
            out.append(FrameEvent(None, 0.0, t))
            return out
        if not any(k in line for k in RELEVANT):
            return out
        if not TS_RE.match(line):
            self.illegible += 1
            return out
        try:
            if "[diag]" in line:
                m = DIAG_CAND_RE.search(line)
                if m:
                    cand, conf = m.group(1), float(m.group(2))
                elif DIAG_NONE_RE.search(line):
                    cand, conf = None, 0.0
                else:
                    self.illegible += 1
                    return out
                self._flush(out)
                self._pending = [t, cand, conf, False]
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

    def flush(self) -> List[FrameEvent]:
        out: List[FrameEvent] = []
        self._flush(out)
        return out

    def _flush(self, out: List[FrameEvent]) -> None:
        p = self._pending
        if p is None or p[3]:
            return
        t, cand, conf, _ = p
        if self.confirmed is not None and cand == self.confirmed:
            self.confirmed_conf = conf
        out.append(FrameEvent(self.confirmed, self.confirmed_conf, t))
        p[3] = True


def log_time_to_epoch(line: str) -> Optional[float]:
    m = TS_RE.match(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S,%f").timestamp()
    except ValueError:
        return None


def follow_lines(path: str, stop: threading.Event, poll_s: float = 0.05):
    """Como `tail -F`, solo lectura: empieza al final y reabre si el archivo rota.
    Rinde (línea, None) o (None, None) en cada vuelta sin datos, para que el llamador
    pueda ejecutar tick()."""
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
                pass  # rotación en curso: se reintenta en la siguiente vuelta
            yield None
            stop.wait(poll_s)
    finally:
        f.close()


def keyboard_loop(controller: RobotController, stop: threading.Event, stream=None) -> None:
    stream = stream or sys.stdin
    for raw in stream:
        cmd = raw.strip().lower()
        now = time.monotonic()
        if cmd == "a":
            controller.arm(now)
        elif cmd == "d":
            controller.disarm("comando local")
        elif cmd == "p":
            controller.manual_stop("parada manual (teclado)")
        elif cmd == "q":
            stop.set()
            return
        elif cmd:
            print("Comandos: a=armar  d=desarmar  p=parar  q=salir", flush=True)
        print(f"Estado: {controller.state} ({controller.state_reason})", flush=True)
    stop.set()  # fin de la entrada: se sale (y se manda parada)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--seguir")
    src.add_argument("--reproducir")
    parser.add_argument("--real", action="store_true", help="Modo real (exige robot_url). Sin esto: ENSAYO.")
    parser.add_argument("--armar-al-inicio", action="store_true",
                        help="Solo con --reproducir en ENSAYO: arma sin teclado para ensayar una copia.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        cfg = load_robot_config(args.config)
        if args.reproducir and args.real:
            raise RobotConfigError("--reproducir solo se permite en ENSAYO")
        if args.armar_al_inicio and (args.real or not args.reproducir):
            raise RobotConfigError("--armar-al-inicio solo con --reproducir en ENSAYO")
        transport = make_transport(cfg, args.real)
    except RobotConfigError as e:
        logger.error("No se arranca: %s", e)
        return 2

    if args.real:
        try:
            r = transport.request("GET", PATH_STATUS)
        except RobotTransportError as e:
            logger.error("No se arranca: el robot no responde a %s: %s", PATH_STATUS, e)
            return 3
        if not 200 <= r.status < 300:
            logger.error("No se arranca: %s respondió %d", PATH_STATUS, r.status)
            return 3

    controller = RobotController(cfg, lambda on_fail: OrderDispatcher(transport, on_fail, threaded=True))
    logger.warning("Actuador del robot en modo %s. Estado inicial: %s", "REAL" if args.real else "ENSAYO", controller.state)
    stop = threading.Event()
    parser_ = DiagFrameParser()

    def on_signal(signum, _frame):
        logger.warning("Señal %s: parada y salida", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    try:
        if args.reproducir:
            armed_at_first_frame = args.armar_al_inicio
            with open(args.reproducir, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if stop.is_set():
                        break
                    t = log_time_to_epoch(line)
                    if t is None:
                        continue
                    if armed_at_first_frame:  # se arma con la hora de la primera línea con hora
                        controller.arm(t)
                        armed_at_first_frame = False
                    for ev in parser_.feed(line, t):
                        controller.observe(ev.gesture, ev.confidence, ev.t)
                    controller.tick(t)
            for ev in parser_.flush():
                controller.observe(ev.gesture, ev.confidence, ev.t)
        else:
            threading.Thread(target=keyboard_loop, args=(controller, stop), daemon=True).start()
            print("Comandos: a=armar  d=desarmar  p=parar  q=salir", flush=True)
            for line in follow_lines(args.seguir, stop):
                now = time.monotonic()
                if line is not None:
                    for ev in parser_.feed(line, now):
                        controller.observe(ev.gesture, ev.confidence, ev.t)
                controller.tick(now)
    finally:
        controller.close()
        logger.warning("Actuador cerrado. Líneas ilegibles: %d; gestos ilegibles: %d; órdenes: %d",
                       parser_.illegible, controller.illegible, len(controller.dispatcher.records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
