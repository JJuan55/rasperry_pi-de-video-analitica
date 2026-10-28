"""Pruebas del actuador del robot (Fase 11, Etapa 1). unittest, sin red real: la
lógica se prueba con DryRunTransport y el HTTP contra `tests/robot_sim.py`
(127.0.0.1, puerto efímero), que reproduce el contrato de docs/CONTRATO_ROBOT_EV3.md.
Corren igual con pytest y con `python -m unittest`. Los márgenes de la parada se
acortan (p. ej. 0,5 s en vez de 6 s) para que las pruebas sean rápidas: la lógica es
la misma."""

import copy
import json
import logging
import os
import signal
import tempfile
import threading
import time
import unittest
from datetime import datetime
from unittest import mock

from cva_gesture_bridge.actuators import robot_client as rc
from cva_gesture_bridge.actuators import robot_runner as rr
from tests.robot_sim import RobotSim

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF_FILE = os.path.join(REPO, "config", "robot_actuador.json")
BASE = {
    "mapeo": {"palma_abierta": "PARAR", "puño_cerrado": "AVANZAR",
              "dedo_pulgar": "GIRAR_DERECHA", "dedo_menique": "GIRAR_IZQUIERDA"},
}
FAST = {"margen_parada_s": 0.5, "sondeo_parada_s": 0.05, "alarma_parada_s": 0.5}
STOP_PAIR = [("POST", rc.PATH_DETENER), ("POST", rc.PATH_STOP)]
LOGGER = "cva_gesture_bridge.actuators.robot_client"


def cfg(**over):
    d = copy.deepcopy(BASE)
    d.update(over)
    return rc.config_from_dict(d)


def fr(confirmed, t, raw="=", conf=0.95, raw_conf=None):
    """Un cuadro. raw="=" significa crudo igual al confirmado."""
    raw = confirmed if raw == "=" else raw
    return rc.FrameInput(confirmed, conf, raw, conf if raw_conf is None else raw_conf, t)


def hold(gesture, t0, seconds, period=0.143, raw="=", conf=0.95):
    n = int(round(seconds / period)) + 1
    return [fr(gesture, t0 + i * period, raw=raw, conf=conf) for i in range(n)]


def feed(ctrl, frames):
    for f in frames:
        ctrl.observe(f)


def dry_controller(config=None):
    sent = []
    ctrl = rc.RobotController(config or cfg(**FAST), lambda: rc.DryRunTransport(sent), threaded_routes=False)
    return ctrl, sent


def paths(sent):
    return [(m, p) for m, p, _ in sent]


def wait_until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


class ConfigTests(unittest.TestCase):
    def test_repo_config_file_is_valid_and_has_no_robot_address(self):
        c = rc.load_robot_config(CONF_FILE)
        self.assertEqual(c.mapeo, BASE["mapeo"])
        self.assertEqual((c.grados_paso, c.potencia, c.motor_izq, c.motor_der), (90, 20, "outC", "outD"))
        self.assertEqual((c.ventana_crudo, c.coincidencias_crudo, c.margen_parada_s, c.sondeo_parada_s),
                         (4, 3, 6.0, 0.3))
        self.assertIsNone(c.robot_url)

    def test_rejects_unknown_keys_gestures_actions_and_missing_parar(self):
        for over in ({"clave_rara": 1},
                     {"mapeo": {"palma_abierta": "PARAR", "dedo_indice": "AVANZAR"}},
                     {"mapeo": {"palma_abierta": "PARAR", "puño_cerrado": "SALTAR"}},
                     {"mapeo": {"puño_cerrado": "AVANZAR"}}):
            with self.assertRaises(rc.RobotConfigError, msg=str(over)):
                cfg(**over)

    def test_hard_limits_cannot_be_raised_from_config(self):
        for over in ({"grados_paso": 181}, {"potencia": 31}, {"potencia": 40}, {"potencia": 0},
                     {"min_entre_pasos_s": 0.5}, {"timeout_http_s": 30.0},
                     {"coincidencias_crudo": 5}, {"ventana_crudo": 0}, {"margen_parada_s": 0}):
            with self.assertRaises(rc.RobotConfigError, msg=str(over)):
                cfg(**over)
        cfg(grados_paso=180, potencia=30, min_entre_pasos_s=1.0)  # justo en el tope: válido

    def test_rejects_bad_types_and_urls(self):
        for over in ({"potencia": True}, {"potencia": "20"}, {"hueco_max_s": float("nan")},
                     {"robot_url": "https://x"}, {"robot_url": "http://h/ruta"},
                     {"motor_izq": "outZ"}, {"motor_izq": "outD"}):
            with self.assertRaises(rc.RobotConfigError, msg=str(over)):
                cfg(**over)

    def test_unreadable_file_fails_explicitly(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{no es json")
        self.addCleanup(os.unlink, f.name)
        with self.assertRaises(rc.RobotConfigError):
            rc.load_robot_config(f.name)

    def test_real_mode_requires_robot_url_no_default(self):
        with self.assertRaises(rc.RobotConfigError):
            rc.make_transport_factory(cfg(), real=True)
        self.assertIsInstance(rc.make_transport_factory(cfg(), real=False)(), rc.DryRunTransport)
        self.assertIsInstance(rc.make_transport_factory(cfg(robot_url="http://robot.local:5000"), real=False)(),
                              rc.DryRunTransport)  # con URL pero sin --real: sigue en ENSAYO


class ContractTests(unittest.TestCase):
    def test_exact_route_body_per_action(self):
        c = cfg()
        exp = {rc.AVANZAR: (90, 90), rc.GIRAR_DERECHA: (90, -90), rc.GIRAR_IZQUIERDA: (-90, 90)}
        for action, (izq, der) in exp.items():
            self.assertEqual(rc.build_route_payload(action, c), {"pasos": [
                {"motorIzq": "outC", "motorDer": "outD", "gradosIzq": izq, "gradosDer": der, "potencia": 20}]})
        with self.assertRaises(ValueError):
            rc.build_route_payload(rc.PARAR, c)

    def test_hard_limits_in_code(self):
        c = cfg()
        object.__setattr__(c, "potencia", 80)  # config alterada tras validar
        with self.assertRaises(rc.HardLimitError):
            rc.build_route_payload(rc.AVANZAR, c)
        ok = {"motorIzq": "outC", "motorDer": "outD", "gradosIzq": 90, "gradosDer": 90, "potencia": 20}
        bad = [
            {"pasos": [dict(ok, gradosIzq=181)]},
            {"pasos": [dict(ok, potencia=31)]},
            {"pasos": [dict(ok, gradosDer=90.0)]},
            {"pasos": [ok, ok]},                      # más de un paso por orden
            {"pasos": [{"pausaMs": 500}]},            # pausas: fuera de lo permitido
            {"pasos": [dict(ok, extra=1)]},
            {"pasos": []},
            {"pasos": [ok], "otra": 1},
            dict(ok),                                 # sin envoltorio "pasos"
        ]
        for b in bad:
            with self.assertRaises(rc.HardLimitError, msg=json.dumps(b)):
                rc.check_hard_limits(b)

    def test_route_response_requires_ok_and_exact_total(self):
        self.assertTrue(rc.route_response_ok(200, '{"ok": true, "totalPasos": 1}', 1))
        self.assertFalse(rc.route_response_ok(200, '{"ok": true, "totalPasos": 0}', 1))  # JSON malformado en el robot
        self.assertFalse(rc.route_response_ok(200, '{"ok": false, "totalPasos": 1}', 1))
        self.assertFalse(rc.route_response_ok(200, '{"ok": true, "totalPasos": true}', 1))
        self.assertFalse(rc.route_response_ok(200, 'no es json', 1))
        self.assertFalse(rc.route_response_ok(500, '{"ok": true, "totalPasos": 1}', 1))

    def test_parse_estado(self):
        self.assertIs(rc.parse_ejecutando(200, '{"pasoActual": 1, "totalPasos": 1, "ejecutando": true, "error": null}'), True)
        self.assertIs(rc.parse_ejecutando(200, '{"pasoActual": 0, "totalPasos": 0, "ejecutando": false, "error": null}'), False)
        self.assertIsNone(rc.parse_ejecutando(200, '{"ejecutando": "no"}'))
        self.assertIsNone(rc.parse_ejecutando(500, '{"ejecutando": false}'))
        self.assertIsNone(rc.parse_ejecutando(200, "<html>"))

    def test_status_requires_both_motors_connected(self):
        c = cfg()
        good = {"motors": [{"port": "outA", "connected": False}, {"port": "outC", "connected": True},
                           {"port": "outD", "connected": True}], "battery": 7.9, "sensors": [], "connected": True}
        self.assertIsNone(rc.check_status_motors(200, json.dumps(good), c))
        off = copy.deepcopy(good)
        off["motors"][2]["connected"] = False
        self.assertIn("outD", rc.check_status_motors(200, json.dumps(off), c))
        missing = {"motors": [{"port": "outC", "connected": True}]}
        self.assertIn("outD", rc.check_status_motors(200, json.dumps(missing), c))
        self.assertIsNotNone(rc.check_status_motors(200, "basura", c))
        self.assertIsNotNone(rc.check_status_motors(503, json.dumps(good), c))


class HoldTrackerTests(unittest.TestCase):
    def _steps(self, frames, max_gap=1.0, window=4, matches=3, min_conf=0.75):
        h = rc.HoldTracker(3.0, 3.0, max_gap, window, matches, min_conf)
        return [f.t for f in frames if h.observe(f.confirmed, f.raw, f.raw_conf, f.t)]

    def test_first_step_at_3s_of_raw_evidence_then_every_3s(self):
        steps = self._steps(hold("puño_cerrado", 100.0, 9.5))
        self.assertEqual(len(steps), 3)
        self.assertTrue(103.0 <= steps[0] < 103.3)

    def test_confirmed_without_raw_evidence_never_steps(self):
        # El caso real del 13/10: dedo_pulgar confirmado 4,5 s con 0,14 s de crudo.
        frames = hold("dedo_pulgar", 0.0, 0.14) + hold("dedo_pulgar", 0.286, 4.3, raw=None)
        self.assertEqual(self._steps(frames), [])

    def test_hand_leaving_stops_the_count_within_two_frames(self):
        # 2,5 s de mano + 2,5 s de histéresis (confirmado sin crudo): no llega a 3 s.
        frames = hold("puño_cerrado", 0.0, 2.5) + hold("puño_cerrado", 2.643, 2.5, raw=None)
        self.assertEqual(self._steps(frames), [])

    def test_one_stray_frame_in_four_is_tolerated(self):
        frames = []
        for i in range(30):  # 4,3 s con 1 de cada 4 cuadros sin crudo
            frames.append(fr("puño_cerrado", i * 0.143, raw=None if i % 4 == 3 else "="))
        self.assertEqual(len(self._steps(frames)), 1)

    def test_two_misses_in_four_reset(self):
        frames = []
        for i in range(40):  # patrón M M x x: nunca 3 de 4
            frames.append(fr("puño_cerrado", i * 0.143, raw=None if i % 4 in (2, 3) else "="))
        self.assertEqual(self._steps(frames), [])

    def test_window_is_configurable(self):
        frames = [fr("puño_cerrado", i * 0.143, raw=None if i % 4 in (2, 3) else "=") for i in range(40)]
        self.assertEqual(len(self._steps(frames, window=4, matches=2)), 1)

    def test_low_raw_confidence_counts_as_miss(self):
        frames = [fr("puño_cerrado", i * 0.143, raw_conf=0.5) for i in range(30)]
        self.assertEqual(self._steps(frames), [])

    def test_counts_real_time_not_frames(self):
        self.assertEqual(self._steps(hold("puño_cerrado", 0.0, 1.2, period=0.03)), [])
        steps = self._steps(hold("puño_cerrado", 0.0, 3.75, period=0.25))
        self.assertEqual(len(steps), 1)
        # En frío la ventana necesita 3 cuadros crudos antes de sumar: el paso llega a
        # 3,0 s de evidencia + como mucho 2 periodos (en una sesión real la ventana ya
        # está llena al confirmar, porque confirmar exige 5 de 7 crudos).
        self.assertTrue(3.0 <= steps[0] <= 3.0 + 2 * 0.25, steps)

    def test_gaps(self):
        a = hold("puño_cerrado", 0.0, 2.0)
        self.assertEqual(self._steps(a + hold("puño_cerrado", a[-1].t + 1.0, 2.0)), [])
        self.assertEqual(len(self._steps(a + hold("puño_cerrado", a[-1].t + 0.9, 2.0))), 1)
        b = hold("puño_cerrado", a[-1].t + 1.2, 2.0)
        self.assertEqual(len(self._steps(a + b, max_gap=1.5)), 1)

    def test_next_step_counts_from_evidence_at_previous_step(self):
        # A ~3,4 fps el primer paso sale con 3,19 s de evidencia: el siguiente toca a
        # 3,19 + 3 = 6,19 s (no en la marca fija de 6,0), así que cada par de pasos
        # queda separado por al menos 3 s de evidencia.
        h = rc.HoldTracker(3.0, 3.0, 1.0, 4, 3, 0.75)
        at = []
        for f in hold("puño_cerrado", 0.0, 10.5, period=0.29):
            if h.observe(f.confirmed, f.raw, f.raw_conf, f.t):
                at.append(round(h.accumulated_s, 2))
        self.assertEqual(len(at), 3)
        self.assertTrue(all(b - a >= 3.0 - 1e-9 for a, b in zip(at, at[1:])), at)

    def test_three_of_four_with_gap_0_9_keeps_and_gap_1_1_resets(self):
        # 2,0 s con un cuadro suelto sin crudo (3 de 4 se cumple), luego un hueco y 1,5 s más.
        def tramo(t0, seconds):
            n = int(round(seconds / 0.143)) + 1
            return [fr("puño_cerrado", t0 + i * 0.143, raw=None if i == 6 else "=") for i in range(n)]
        a = tramo(0.0, 2.0)
        # Hueco de 0,9 s: NO reinicia (y, como todo intervalo < 1 s con evidencia, suma). El
        # paso solo puede salir si se conservó lo del primer tramo: 1,5 s solos no llegan a 3 s.
        self.assertEqual(len(self._steps(a + tramo(a[-1].t + 0.9, 1.5))), 1)
        # Hueco de 1,1 s: reinicia; después solo hay 1,5 s, no llega a 3 s.
        self.assertEqual(self._steps(a + tramo(a[-1].t + 1.1, 1.5)), [])

    def test_change_none_and_clock_back_reset(self):
        for breaker in (fr("dedo_pulgar", 2.1), fr(None, 2.1, raw=None)):
            frames = hold("puño_cerrado", 0.0, 2.0) + [breaker] + hold("puño_cerrado", 2.2, 2.0)
            self.assertEqual(self._steps(frames), [])
        self.assertEqual(self._steps(hold("puño_cerrado", 10.0, 2.0) + hold("puño_cerrado", 9.0, 2.5)), [])


class ControllerLogicTests(unittest.TestCase):
    def test_disarmed_never_moves(self):
        ctrl, sent = dry_controller()
        feed(ctrl, hold("puño_cerrado", 0.0, 10.0))
        self.assertEqual(sent, [])

    def test_armed_hold_sends_one_step_per_3s(self):
        ctrl, sent = dry_controller()
        ctrl.arm(0.0)
        feed(ctrl, hold("dedo_pulgar", 0.0, 6.5))
        self.assertEqual(paths(sent), [("POST", rc.PATH_EJECUTAR)] * 2)
        self.assertEqual(sent[0][2]["pasos"][0]["gradosDer"], -90)

    def test_parar_latches_immediately_and_sends_pair(self):
        ctrl, sent = dry_controller()
        ctrl.arm(0.0)
        ctrl.observe(fr("palma_abierta", 0.0, conf=0.1))  # una vez, confianza baja
        self.assertIn(ctrl.state, (rc.PARANDO, rc.BLOQUEADO))
        self.assertTrue(ctrl.stopper.wait(2.0))
        self.assertEqual(paths(sent), STOP_PAIR)
        self.assertEqual(ctrl.state, rc.BLOQUEADO)

    def test_parar_takes_precedence_and_held_parar_does_not_repeat(self):
        ctrl, sent = dry_controller()
        ctrl.arm(0.0)
        feed(ctrl, hold("puño_cerrado", 0.0, 2.9))
        feed(ctrl, hold("palma_abierta", 3.0, 5.0))
        self.assertTrue(ctrl.stopper.wait(2.0))
        self.assertEqual(paths(sent), STOP_PAIR)
        self.assertEqual(ctrl.stopper.starts, 1)

    def test_latch_requires_explicit_rearm_and_fresh_hold(self):
        ctrl, sent = dry_controller()
        ctrl.arm(0.0)
        ctrl.observe(fr("palma_abierta", 0.0))
        self.assertTrue(ctrl.stopper.wait(2.0))
        feed(ctrl, hold("puño_cerrado", 1.0, 5.0))
        self.assertEqual(paths(sent), STOP_PAIR)
        self.assertTrue(ctrl.arm(6.2))
        feed(ctrl, hold("puño_cerrado", 6.2, 2.5))
        self.assertEqual(paths(sent), STOP_PAIR)
        feed(ctrl, hold("puño_cerrado", 8.843, 1.0))
        self.assertEqual(paths(sent)[-1], ("POST", rc.PATH_EJECUTAR))

    def test_auto_disarm_after_inactivity_runs_parar(self):
        ctrl, sent = dry_controller(cfg(desarme_inactividad_s=10.0, **FAST))
        ctrl.arm(0.0)
        ctrl.tick(9.9)
        self.assertEqual(ctrl.state, rc.ARMADO)
        ctrl.tick(10.0)
        self.assertTrue(ctrl.stopper.wait(2.0))
        self.assertEqual(ctrl.state, rc.DESARMADO)
        self.assertEqual(paths(sent), STOP_PAIR)

    def test_no_step_is_omitted_by_min_interval_at_low_fps(self):
        # Caso real del 28/10 15:05: a baja tasa de cuadros el segundo paso quedaba a
        # 2,86 s del primero, el mínimo de 3 s lo omitía y el contador lo consumía:
        # pasos cada 6 s en vez de cada 3.
        ctrl, sent = dry_controller()
        ctrl.arm(0.0)
        with self.assertLogs(LOGGER, level="INFO") as logs:
            feed(ctrl, hold("puño_cerrado", 0.0, 10.5, period=0.29))
        self.assertFalse(any("omitido" in m for m in logs.output))
        self.assertEqual(paths(sent), [("POST", rc.PATH_EJECUTAR)] * 3)

    def test_min_interval_between_steps(self):
        ctrl, sent = dry_controller(cfg(min_entre_pasos_s=4.0, **FAST))
        ctrl.arm(0.0)
        feed(ctrl, hold("puño_cerrado", 0.0, 9.5))
        self.assertEqual(paths(sent), [("POST", rc.PATH_EJECUTAR)] * 2)

    def test_corrupt_inputs_do_not_move(self):
        ctrl, sent = dry_controller()
        ctrl.arm(0.0)
        bad = [("dedo_indice", "dedo_indice", 0.9), ("PUÑO", "PUÑO", 0.9), (42, 42, 0.9),
               ("puño_cerrado", "puño_cerrado", float("nan")), ("puño_cerrado", "puño_cerrado", "0.9"),
               ("puño_cerrado", "puño_cerrado", 1.7), ("puño_cerrado", "puño_cerrado", True),
               ("ninguno", "ninguno", 0.9), ("puño_cerrado", 7, 0.9)]
        t = 0.0
        for _ in range(10):
            for c, r, k in bad:
                ctrl.observe(rc.FrameInput(c, 0.9, r, k, t))
                t += 0.143
        self.assertEqual(sent, [])
        self.assertGreater(ctrl.illegible, 0)

    def test_dry_run_sends_nothing_on_the_network(self):
        sim = RobotSim()
        self.addCleanup(sim.close)
        ctrl, sent = dry_controller(cfg(robot_url=sim.url, **FAST))
        ctrl.arm(0.0)
        feed(ctrl, hold("puño_cerrado", 0.0, 4.0))
        ctrl.observe(fr("palma_abierta", 4.2))
        self.assertTrue(ctrl.stopper.wait(2.0))
        self.assertEqual(len(sent), 3)
        self.assertEqual(sim.requests, [])


class SimTestCase(unittest.TestCase):
    def sim(self, **kw):
        s = RobotSim(**kw)
        self.addCleanup(s.close)
        return s

    def controller(self, sim, timeout=1.0, **over):
        conf = dict(FAST)
        conf.update(over)
        c = cfg(robot_url=sim.url, timeout_http_s=timeout, **conf)
        ctrl = rc.RobotController(c, rc.make_transport_factory(c, real=True))
        self.addCleanup(lambda: (ctrl.stopper.abandon(), ctrl.routes.close(1.0)))
        return ctrl

    def step(self, ctrl, t0=0.0):
        ctrl.arm(t0)
        feed(ctrl, hold("puño_cerrado", t0, 3.2))


class RouteWithSimulatorTests(SimTestCase):
    def test_step_reaches_robot_and_is_recorded(self):
        sim = self.sim()
        ctrl = self.controller(sim)
        self.step(ctrl)
        self.assertTrue(ctrl.routes.wait_idle(3.0))
        self.assertEqual(sim.paths(), [("POST", rc.PATH_EJECUTAR)])
        self.assertEqual(sim.requests[0][2]["pasos"][0]["gradosIzq"], 90)
        rec = ctrl.order_log.records[0]
        self.assertEqual(rec.status, 200)
        self.assertIsNotNone(rec.sent_wall)
        self.assertGreaterEqual(rec.elapsed_ms, 0.0)
        self.assertEqual(ctrl.state, rc.ARMADO)

    def test_total_pasos_mismatch_is_failure_and_parar(self):
        sim = self.sim()
        original = sim._apply

        def zero_total(method, path, body):  # como si el robot hubiera leído el cuerpo como vacío
            st, obj = original(method, path, body)
            if path == rc.PATH_EJECUTAR:
                obj = {"ok": True, "totalPasos": 0}
            return st, obj
        sim._apply = zero_total
        ctrl = self.controller(sim)
        self.step(ctrl)
        self.assertTrue(wait_until(lambda: ctrl.stopper.confirmed.is_set(), 5.0))
        self.assertEqual(ctrl.state, rc.BLOQUEADO)
        self.assertEqual(sim.paths().count(("POST", rc.PATH_EJECUTAR)), 1)
        self.assertIn(("POST", rc.PATH_DETENER), sim.paths())

    def test_409_is_not_retried(self):
        sim = self.sim(route_duration_s=30.0)
        ctrl = self.controller(sim)
        self.step(ctrl)
        self.assertTrue(ctrl.routes.wait_idle(3.0))
        feed(ctrl, hold("puño_cerrado", 3.343, 3.0))
        self.assertTrue(ctrl.routes.wait_idle(3.0))
        self.assertEqual(sim.paths(), [("POST", rc.PATH_EJECUTAR)] * 2)
        self.assertEqual([r.status for r in ctrl.order_log.records], [200, 409])
        self.assertEqual(ctrl.state, rc.ARMADO)

    def test_timeout_parar_never_resends(self):
        sim = self.sim()
        sim.response_delay[rc.PATH_EJECUTAR] = 0.5
        ctrl = self.controller(sim, timeout=0.2)
        self.step(ctrl)
        self.assertTrue(wait_until(lambda: ctrl.stopper.confirmed.is_set(), 5.0))
        self.assertEqual(sim.paths().count(("POST", rc.PATH_EJECUTAR)), 1)
        self.assertIn(("GET", rc.PATH_ESTADO), sim.paths())
        self.assertEqual(ctrl.state, rc.BLOQUEADO)
        feed(ctrl, hold("puño_cerrado", 4.0, 4.0))
        self.assertEqual(sim.paths().count(("POST", rc.PATH_EJECUTAR)), 1)

    def test_lost_response_is_handled_like_timeout(self):
        sim = self.sim()
        sim.drop_paths.add(rc.PATH_EJECUTAR)
        ctrl = self.controller(sim)
        self.step(ctrl)
        self.assertTrue(wait_until(lambda: ctrl.stopper.confirmed.is_set(), 5.0))
        self.assertEqual(ctrl.state, rc.BLOQUEADO)
        self.assertEqual(sim.paths().count(("POST", rc.PATH_EJECUTAR)), 1)

    def test_vision_loop_never_blocks_and_one_route_in_flight(self):
        sim = self.sim()
        sim.response_delay[rc.PATH_EJECUTAR] = 0.5
        ctrl = self.controller(sim, timeout=1.0, min_entre_pasos_s=1.0, sostener_paso_s=0.2)
        ctrl.arm(0.0)
        worst = 0.0
        for f in hold("puño_cerrado", 0.0, 4.0):
            t0 = time.monotonic()
            ctrl.observe(f)
            worst = max(worst, time.monotonic() - t0)
        self.assertLess(worst, 0.05)
        self.assertTrue(ctrl.routes.wait_idle(3.0))
        self.assertEqual(sim.paths(), [("POST", rc.PATH_EJECUTAR)])

    def test_startup_check_of_motors(self):
        good = self.sim()
        self.assertIsNone(rr.check_robot_ready(rc.make_transport_factory(cfg(robot_url=good.url), True),
                                               cfg(robot_url=good.url)))
        bad = self.sim(connected_ports=("outC",))
        problem = rr.check_robot_ready(rc.make_transport_factory(cfg(robot_url=bad.url), True), cfg(robot_url=bad.url))
        self.assertIn("outD", problem)


class StopWithSimulatorTests(SimTestCase):
    def test_i_slow_route_response_parar_goes_out_before_it(self):
        sim = self.sim(route_duration_s=30.0)
        sim.response_delay[rc.PATH_EJECUTAR] = 2.0
        ctrl = self.controller(sim, timeout=3.0)
        self.step(ctrl)
        self.assertTrue(wait_until(lambda: sim.paths()[:1] == [("POST", rc.PATH_EJECUTAR)], 2.0))
        t_parar = time.monotonic()
        ctrl.observe(fr("palma_abierta", 3.3))
        self.assertEqual(ctrl.state, rc.PARANDO)
        self.assertLess(time.monotonic() - t_parar, 0.05)  # no espera a la red
        self.assertTrue(wait_until(lambda: ("POST", rc.PATH_DETENER) in sim.paths(), 1.0))
        route_reply_at = sim.requests[0][3] + 2.0
        detener_at = next(r[3] for r in sim.requests if r[1] == rc.PATH_DETENER)
        self.assertLess(detener_at, route_reply_at - 1.0)  # salió mucho antes de que respondiera la ruta
        self.assertTrue(ctrl.routes.in_flight)  # la ruta seguía en vuelo
        self.assertTrue(wait_until(lambda: ctrl.stopper.confirmed.is_set(), 6.0))
        self.assertFalse(ctrl.routes.in_flight)  # no confirma hasta que la ruta terminó
        self.assertFalse(sim.executing)
        self.assertEqual(ctrl.state, rc.BLOQUEADO)

    def test_ii_route_applied_after_parar_is_detected_and_stopped_again(self):
        sim = self.sim(route_duration_s=30.0)
        sim.process_delay[rc.PATH_EJECUTAR] = 0.6  # la orden llega tarde al robot
        ctrl = self.controller(sim, timeout=2.0)
        self.step(ctrl)
        self.assertTrue(wait_until(lambda: sim.paths()[:1] == [("POST", rc.PATH_EJECUTAR)], 2.0))
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            ctrl.observe(fr("palma_abierta", 3.3))
            self.assertTrue(wait_until(lambda: ctrl.stopper.confirmed.is_set(), 6.0))
        applied = [p for _, p, _ in sim.applied]
        first_detener = applied.index(rc.PATH_DETENER)
        self.assertGreater(applied.index(rc.PATH_EJECUTAR), first_detener)  # la ruta anuló el detener
        self.assertGreaterEqual(applied.count(rc.PATH_DETENER), 2)  # y se volvió a parar
        self.assertTrue(any("ejecutando:true durante la parada" in m for m in logs.output))
        self.assertFalse(sim.executing)
        self.assertEqual(ctrl.state, rc.BLOQUEADO)

    def test_iii_robot_not_answering_parar_keeps_retrying_with_alarm_never_resumes(self):
        sim = self.sim()
        sim.drop_paths.update({rc.PATH_DETENER, rc.PATH_STOP, rc.PATH_ESTADO})
        ctrl = self.controller(sim, timeout=0.2)
        ctrl.arm(0.0)
        with self.assertLogs(LOGGER, level="ERROR") as logs:
            ctrl.observe(fr("palma_abierta", 0.0))
            time.sleep(1.5)
        self.assertTrue(any("PARADA NO CONFIRMADA" in m for m in logs.output))
        self.assertEqual(ctrl.state, rc.PARANDO)
        self.assertFalse(ctrl.stopper.confirmed.is_set())
        self.assertGreater(sim.paths().count(("POST", rc.PATH_DETENER)), 2)  # sigue reintentando
        self.assertFalse(ctrl.arm(2.0))  # no se puede re-armar sin parada confirmada
        feed(ctrl, hold("puño_cerrado", 2.0, 4.0))
        self.assertNotIn(("POST", rc.PATH_EJECUTAR), sim.paths())

    def test_iv_second_parar_during_parando_does_not_duplicate_threads(self):
        sim = self.sim()
        sim.response_delay[rc.PATH_ESTADO] = 0.1
        ctrl = self.controller(sim, margen_parada_s=1.0)
        ctrl.arm(0.0)
        ctrl.observe(fr("palma_abierta", 0.0))
        ctrl.observe(fr(None, 0.143, raw=None))
        ctrl.observe(fr("palma_abierta", 0.286))  # nuevo PARAR mientras se está parando
        ctrl.manual_stop("tecla p")
        self.assertEqual(ctrl.stopper.starts, 1)
        self.assertEqual(sum(1 for t in threading.enumerate() if t.name == "robot-parada"), 1)
        self.assertTrue(wait_until(lambda: ctrl.stopper.confirmed.is_set(), 5.0))

    def test_robot_down_alarm_and_stays_parando(self):
        sim = self.sim()
        ctrl = self.controller(sim, timeout=0.2)
        sim.go_down()
        with self.assertLogs(LOGGER, level="ERROR") as logs:
            self.step(ctrl)
            time.sleep(1.2)
        self.assertEqual(ctrl.state, rc.PARANDO)
        self.assertTrue(any("PARADA NO CONFIRMADA" in m for m in logs.output))


class DiagParserTests(unittest.TestCase):
    L = [
        "2026-10-23 10:55:49,061 INFO __main__: [diag] Gesto candidato: palma_abierta (confianza=0.97, dedos_extendidos=5, confirmado=False)",
        "2026-10-23 10:55:49,192 INFO __main__: [diag] Gesto candidato: palma_abierta (confianza=1.00, dedos_extendidos=5, confirmado=True)",
        "2026-10-23 10:55:49,192 INFO __main__: Gesto detectado: palma_abierta (confianza=1.00)",
        "2026-10-23 10:55:49,300 INFO cva_gesture_bridge.transport.tcp_server: Frame #12 de ('127.0.0.1', 1): 1 bytes, fps_real=7.00, latencia_ms=1.0",
        "2026-10-23 10:55:49,330 INFO __main__: [diag] Sin gesto reconocido en este frame (dedos_extendidos=0)",
        "2026-10-23 10:55:53,946 INFO __main__: Gesto liberado (antes: palma_abierta)",
    ]

    @staticmethod
    def epoch(s):
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S,%f").timestamp()

    def test_time_base_is_the_log_line_not_reading_time(self):
        p = rr.DiagFrameParser()
        time.sleep(0.05)  # leer "tarde" no cambia la hora del cuadro
        evs = p.feed(self.L[0]) + p.feed(self.L[1])
        self.assertEqual(evs[0].t, self.epoch("2026-10-23 10:55:49,061"))

    def test_confirmed_raw_and_association(self):
        p = rr.DiagFrameParser()
        evs = [e for line in self.L for e in p.feed(line)]
        self.assertEqual([(e.confirmed, e.raw) for e in evs],
                         [(None, "palma_abierta"), ("palma_abierta", "palma_abierta"), (None, None)])
        self.assertEqual(evs[1].raw_conf, 1.0)
        self.assertEqual(p.illegible, 0)

    def test_frame_without_change_keeps_last_matching_confidence(self):
        p = rr.DiagFrameParser()
        evs = [e for line in self.L[:5] for e in p.feed(line)] + p.flush()
        self.assertEqual((evs[-1].confirmed, evs[-1].confirmed_conf, evs[-1].raw), ("palma_abierta", 1.0, None))

    def test_corrupt_lines_are_counted_and_produce_nothing(self):
        p = rr.DiagFrameParser()
        bad = [
            "2026-10-23 10:55:49,061 INFO __main__: [diag] Gesto candidato: palma_abierta (confianza=1.0.0, dedos_extendidos=5, confirmado=True)",
            "basura [diag] sin hora",
            "2026-10-23 10:55:49,192 INFO __main__: Gesto detectado: palma_abierta (confianza=)",
            "2026-10-23 10:55:49,192 INFO __main__: [diag] Gesto candidato: puño_cerrado (confian",
        ]
        self.assertEqual([e for line in bad for e in p.feed(line)] + p.flush(), [])
        self.assertEqual(p.illegible, 4)

    def test_connection_end_clears_confirmed_gesture(self):
        p = rr.DiagFrameParser()
        p.feed(self.L[1])
        p.feed(self.L[2])
        evs = p.feed("2026-10-23 11:01:27,617 INFO x: Conexión con ('127.0.0.1', 60756) finalizada. total_frames=1")
        self.assertIsNone(evs[-1].confirmed)
        self.assertIsNone(p.confirmed)


class RunnerTests(unittest.TestCase):
    def _log(self, lines):
        f = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False, encoding="utf-8")
        f.write("".join(l + "\n" for l in lines))
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    @staticmethod
    def frame_lines(gesture, seconds, period=0.143):
        out = []
        for i in range(int(seconds / period) + 1):
            ms = int(round(i * period * 1000))
            ts = f"2026-10-23 10:00:{ms // 1000:02d},{ms % 1000:03d}"
            out.append(f"{ts} INFO __main__: [diag] Gesto candidato: {gesture} (confianza=0.95, dedos_extendidos=0, confirmado=True)")
            if i == 4:
                out.append(f"{ts} INFO __main__: Gesto detectado: {gesture} (confianza=0.95)")
        return out

    def test_replay_dry_run_end_to_end(self):
        path = self._log(self.frame_lines("puño_cerrado", 4.0))
        with self.assertLogs(LOGGER, level="INFO") as logs:
            code = rr.main(["--config", CONF_FILE, "--reproducir", path, "--armar-al-inicio"])
        self.assertEqual(code, 0)
        sent = [m for m in logs.output if "ENSAYO: enviaría" in m]
        self.assertEqual(sum("ejecutar_ruta" in m for m in sent), 1)
        self.assertTrue(any("/detener_ejecucion" in m for m in sent))  # PARAR al salir

    def test_refuses_real_without_url_and_replay_in_real(self):
        path = self._log(["x"])
        with self.assertLogs("cva_gesture_bridge.actuators.robot_runner", level="ERROR"):
            self.assertEqual(rr.main(["--config", CONF_FILE, "--seguir", path, "--real"]), 2)
        with self.assertLogs("cva_gesture_bridge.actuators.robot_runner", level="ERROR"):
            self.assertEqual(rr.main(["--config", CONF_FILE, "--reproducir", path, "--real"]), 2)

    def test_real_mode_refuses_if_a_motor_is_not_connected(self):
        sim = RobotSim(connected_ports=("outC",))
        self.addCleanup(sim.close)
        conf = json.load(open(CONF_FILE, encoding="utf-8"))
        conf["robot_url"] = sim.url
        cpath = self._log([json.dumps(conf)])
        with self.assertLogs("cva_gesture_bridge.actuators.robot_runner", level="ERROR") as logs:
            self.assertEqual(rr.main(["--config", cpath, "--seguir", self._log(["x"]), "--real"]), 3)
        self.assertTrue(any("outD" in m for m in logs.output))
        self.assertEqual(sim.paths(), [("GET", rc.PATH_STATUS)])  # no se mandó nada más

    def _run_follow_and_signal(self, sig):
        path = self._log(["inicio"])
        idle = threading.Event()
        timer = threading.Timer(0.3, lambda: os.kill(os.getpid(), sig))
        with mock.patch.object(rr, "keyboard_loop", lambda c, s, stream=None: idle.wait(5)), \
                self.assertLogs(LOGGER, level="INFO") as logs:
            timer.start()
            code = rr.main(["--config", CONF_FILE, "--seguir", path])
        timer.cancel()
        idle.set()
        self.assertEqual(code, 0)
        self.assertTrue(any("PARAR (cierre del proceso)" in m for m in logs.output))
        self.assertTrue(any("enviaría POST /detener_ejecucion" in m for m in logs.output))

    def test_sigterm_runs_parar_before_exit(self):
        self._run_follow_and_signal(signal.SIGTERM)

    def test_sighup_runs_parar_before_exit(self):
        self._run_follow_and_signal(signal.SIGHUP)

    def test_ctrl_c_runs_parar_before_exit(self):
        self._run_follow_and_signal(signal.SIGINT)


class StatusLineAndDryRunLogTests(unittest.TestCase):
    """Tarea 10: línea de estado de --seguir (B) y registro honesto en ENSAYO (C)."""

    LINE = ("2026-10-27 10:00:00,000 INFO __main__: [diag] Gesto candidato: puño_cerrado "
            "(confianza=0.95, dedos_extendidos=0, confirmado=True)")

    def test_estado_cada_s_validation_and_repo_value(self):
        self.assertEqual(rc.load_robot_config(CONF_FILE).estado_cada_s, 30.0)
        for ok in (0, 0.0, 5, 30.0, 600):
            self.assertEqual(cfg(estado_cada_s=ok).estado_cada_s, float(ok))
        for bad in (4.9, 600.1, -1, True, "30", float("nan")):
            with self.assertRaises(rc.RobotConfigError, msg=repr(bad)):
                cfg(estado_cada_s=bad)

    def _parser_with_frame_at(self, line):
        p = rr.DiagFrameParser()
        p.feed(line)
        p.flush()
        return p

    def test_parser_counts_only_real_frames_with_log_time(self):
        p = rr.DiagFrameParser()
        p.feed(self.LINE)
        p.feed(self.LINE.replace("10:00:00,000", "10:00:00,143"))
        p.feed("2026-10-27 10:00:01,000 INFO x: Conexión con ('127.0.0.1', 1) finalizada. total_frames=2")
        p.feed("2026-10-27 10:00:01,100 INFO __main__: [diag] Gesto candidato: basura (confian")  # ilegible
        self.assertEqual(p.frames_read, 2)  # el corte de conexión no cuenta como cuadro
        self.assertEqual(p.last_frame_t, datetime.strptime("2026-10-27 10:00:00,143", "%Y-%m-%d %H:%M:%S,%f").timestamp())
        self.assertEqual(p.illegible, 1)

    def test_status_line_interval_fields_and_stale_warning(self):
        ctrl, _ = dry_controller()
        frames = self._parser_with_frame_at(self.LINE)
        t_frame = frames.last_frame_t
        rep = rr.StatusReporter(30.0, "ENSAYO", start=t_frame)
        self.assertFalse(rep.maybe_report(t_frame + 29.9, ctrl, frames))  # todavía no toca
        with self.assertLogs("cva_gesture_bridge.actuators.robot_runner", level="INFO") as logs:
            self.assertTrue(rep.maybe_report(t_frame + 30.0, ctrl, frames))
        info = [m for m in logs.output if m.startswith("INFO")]
        self.assertEqual(len(info), 1)
        for field in ("modo=ENSAYO", "estado=DESARMADO", "cuadros=1", "último=10:00:00,000",
                      "edad=30.0s", "ilegibles=0", "gestos_ilegibles=0", "pasos_encolados=0", "órdenes=0"):
            self.assertIn(field, info[0])
        self.assertTrue(any("WARNING" in m and "SIN CUADROS hace 30.0 s" in m for m in logs.output))
        self.assertFalse(rep.maybe_report(t_frame + 59.9, ctrl, frames))  # siguiente, 30 s después

    def test_no_warning_when_frames_are_fresh_and_warning_without_any_frame(self):
        ctrl, _ = dry_controller()
        frames = self._parser_with_frame_at(self.LINE)
        rep = rr.StatusReporter(5.0, "ENSAYO", start=frames.last_frame_t - 1.0)
        with self.assertLogs("cva_gesture_bridge.actuators.robot_runner", level="INFO") as logs:
            rep.maybe_report(frames.last_frame_t + 4.0, ctrl, frames)  # edad 4 s <= 5 s
        self.assertFalse(any("SIN CUADROS" in m for m in logs.output))
        empty = rr.DiagFrameParser()
        rep2 = rr.StatusReporter(5.0, "ENSAYO", start=0.0)
        with self.assertLogs("cva_gesture_bridge.actuators.robot_runner", level="INFO") as logs:
            rep2.maybe_report(10.0, ctrl, empty)
        self.assertTrue(any("cuadros=0" in m and "último=ninguno" in m for m in logs.output))
        self.assertTrue(any("SIN CUADROS desde el arranque" in m for m in logs.output))

    def test_status_line_disabled_with_zero(self):
        ctrl, _ = dry_controller()
        rep = rr.StatusReporter(0.0, "ENSAYO", start=0.0)
        self.assertFalse(rep.maybe_report(10_000.0, ctrl, rr.DiagFrameParser()))

    def test_status_line_does_not_alter_control(self):
        def run(with_status):
            ctrl, sent = dry_controller()
            frames = rr.DiagFrameParser()
            rep = rr.StatusReporter(5.0, "ENSAYO", start=0.0)
            ctrl.arm(0.0)
            for f in hold("puño_cerrado", 0.0, 6.5):
                ctrl.observe(f)
                if with_status:
                    rep.maybe_report(f.t + 6.0, ctrl, frames)  # fuerza líneas de estado a menudo
            return paths(sent), ctrl.state, ctrl.steps_enqueued
        with self.assertLogs("cva_gesture_bridge.actuators.robot_runner", level="INFO"):
            with_s = run(True)
        self.assertEqual(with_s, run(False))
        self.assertEqual(with_s[2], 2)  # pasos a los 3 y a los 6 s

    def test_dry_run_order_log_says_not_sent(self):
        ctrl, _ = dry_controller()
        ctrl.arm(0.0)
        with self.assertLogs(LOGGER, level="INFO") as logs:
            feed(ctrl, hold("puño_cerrado", 0.0, 3.2))
            ctrl.observe(fr("palma_abierta", 3.3))
            self.assertTrue(ctrl.stopper.wait(2.0))
        msgs = [m for m in logs.output if "Robot:" in m]
        self.assertTrue(any("Robot: ENSAYO (NO enviada): POST /ejecutar_ruta" in m for m in msgs))
        self.assertTrue(any("Robot: ENSAYO (NO enviada): POST /detener_ejecucion" in m for m in msgs))
        self.assertFalse(any("enviada" in m and "->" in m for m in msgs))  # nada de "enviada ... -> 200"

    def test_real_transport_still_logs_sent_with_status(self):
        sim = RobotSim()
        self.addCleanup(sim.close)
        t = rc.HttpRobotTransport(sim.url, 1.0)
        self.addCleanup(t.close)
        with self.assertLogs(LOGGER, level="INFO") as logs:
            rc.OrderLog().send(t, "GET", rc.PATH_STATUS)
        self.assertTrue(any("GET /status enviada" in m and "-> 200" in m for m in logs.output))
        self.assertFalse(any("ENSAYO" in m for m in logs.output))


class CodeVersionTests(unittest.TestCase):
    """El runner registra al arrancar qué código y qué configuración corren."""

    CLIENT = os.path.join(REPO, "cva_gesture_bridge", "actuators", "robot_client.py")

    def test_blob_id_matches_git_hash_object(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f")
            for data, expected in ((b"", "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"),
                                   (b"hello\n", "ce013625030ba8dba906f756967f9e9ca394464a")):
                with open(p, "wb") as f:
                    f.write(data)
                self.assertEqual(rr.git_blob_id(p), expected)

    def _line(self, head, status):
        def fake_git(args, cwd):
            return head if args[0] == "rev-parse" else status
        return rr.code_version_line(CONF_FILE, git=fake_git)

    def test_line_states_commit_and_whether_tree_differs(self):
        self.assertIn("commit=abc1234 sin cambios", self._line("abc1234", ""))
        self.assertIn("commit=abc1234 CON CAMBIOS SIN COMMIT", self._line("abc1234", " M robot_client.py"))
        self.assertIn("commit=abc1234 (estado del árbol desconocido)", self._line("abc1234", None))
        self.assertIn("commit=desconocido (sin repositorio git)", self._line(None, None))

    def test_line_has_blob_of_each_file_on_disk_even_without_git(self):
        line = self._line(None, None)
        self.assertIn(f"robot_client.py={rr.git_blob_id(self.CLIENT)[:7]}", line)
        self.assertIn(f"robot_runner.py={rr.git_blob_id(rr.__file__)[:7]}", line)
        self.assertIn(f"robot_actuador.json={rr.git_blob_id(CONF_FILE)[:7]}", line)

    def test_main_logs_version_at_startup(self):
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write("inicio\n")
        self.addCleanup(os.unlink, f.name)
        with self.assertLogs("cva_gesture_bridge.actuators.robot_runner", level="WARNING") as logs:
            self.assertEqual(rr.main(["--config", CONF_FILE, "--reproducir", f.name]), 0)
        lines = [m for m in logs.output if "Código: commit=" in m]
        self.assertEqual(len(lines), 1)
        self.assertIn(f"robot_client.py={rr.git_blob_id(self.CLIENT)[:7]}", lines[0])


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    unittest.main()
