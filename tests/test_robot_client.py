"""Pruebas del actuador del robot (Fase 11, Etapa 1). unittest, sin red real: la
lógica se prueba en línea con DryRunTransport y el HTTP contra `tests/robot_sim.py`
(127.0.0.1, puerto efímero). Corren igual con pytest y con `python -m unittest`."""

import copy
import json
import math
import os
import tempfile
import time
import unittest

from cva_gesture_bridge.actuators import robot_client as rc
from cva_gesture_bridge.actuators import robot_runner as rr
from tests.robot_sim import RobotSim

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = {
    "mapeo": {"palma_abierta": "PARAR", "puño_cerrado": "AVANZAR",
              "dedo_pulgar": "GIRAR_DERECHA", "dedo_menique": "GIRAR_IZQUIERDA"},
}
STOP_SEQ = [("POST", rc.PATH_DETENER), ("POST", rc.PATH_STOP)]


def cfg(**over):
    d = copy.deepcopy(BASE)
    d.update(over)
    return rc.config_from_dict(d)


def frames(gesture, t0, seconds, period=0.143, conf=0.95):
    """Cuadros a periodo fijo: [(gesto, confianza, t)] cubriendo `seconds`."""
    n = int(round(seconds / period)) + 1
    return [(gesture, conf, t0 + i * period) for i in range(n)]


def feed(ctrl, seq):
    for g, c, t in seq:
        ctrl.observe(g, c, t)


def dry_controller(config=None):
    transport = rc.DryRunTransport()
    ctrl = rc.RobotController(config or cfg(), lambda f: rc.OrderDispatcher(transport, f, threaded=False))
    return ctrl, transport


def paths(transport):
    return [(m, p) for m, p, _ in transport.sent]


class ConfigTests(unittest.TestCase):
    def test_repo_config_file_is_valid_and_has_no_robot_address(self):
        c = rc.load_robot_config(os.path.join(REPO, "config", "robot_actuador.json"))
        self.assertEqual(c.mapeo["palma_abierta"], rc.PARAR)
        self.assertEqual((c.grados_paso, c.potencia, c.motor_izq, c.motor_der), (90, 20, "outC", "outD"))
        self.assertIsNone(c.robot_url)

    def test_rejects_unknown_keys_gestures_actions_and_missing_parar(self):
        with self.assertRaises(rc.RobotConfigError):
            cfg(clave_rara=1)
        with self.assertRaises(rc.RobotConfigError):
            cfg(mapeo={"palma_abierta": "PARAR", "dedo_indice": "AVANZAR"})
        with self.assertRaises(rc.RobotConfigError):
            cfg(mapeo={"palma_abierta": "PARAR", "puño_cerrado": "SALTAR"})
        with self.assertRaises(rc.RobotConfigError):
            cfg(mapeo={"puño_cerrado": "AVANZAR"})

    def test_hard_limits_cannot_be_raised_from_config(self):
        for over in ({"grados_paso": 181}, {"potencia": 31}, {"potencia": 0},
                     {"min_entre_pasos_s": 0.5}, {"timeout_http_s": 30.0}):
            with self.assertRaises(rc.RobotConfigError, msg=str(over)):
                cfg(**over)
        cfg(grados_paso=180, potencia=30, min_entre_pasos_s=1.0)  # justo en el tope: válido

    def test_rejects_bad_types_and_urls(self):
        for over in ({"potencia": True}, {"potencia": "20"}, {"hueco_max_s": float("nan")},
                     {"robot_url": "https://x"}, {"robot_url": "http://h/ruta"}, {"motor_izq": "outZ"},
                     {"motor_izq": "outD"}):
            with self.assertRaises(rc.RobotConfigError, msg=str(over)):
                cfg(**over)

    def test_unreadable_file_fails_explicitly(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{no es json")
        try:
            with self.assertRaises(rc.RobotConfigError):
                rc.load_robot_config(f.name)
        finally:
            os.unlink(f.name)

    def test_real_mode_requires_robot_url_no_default(self):
        with self.assertRaises(rc.RobotConfigError):
            rc.make_transport(cfg(), real=True)
        self.assertIsInstance(rc.make_transport(cfg(), real=False), rc.DryRunTransport)
        self.assertIsInstance(rc.make_transport(cfg(robot_url="http://10.0.0.9:5000"), real=False),
                              rc.DryRunTransport)  # con URL pero sin --real: sigue en ENSAYO


class PayloadAndHardLimitTests(unittest.TestCase):
    def test_steps_per_action(self):
        c = cfg()
        exp = {rc.AVANZAR: (90, 90), rc.GIRAR_DERECHA: (90, -90), rc.GIRAR_IZQUIERDA: (-90, 90)}
        for action, (izq, der) in exp.items():
            p = rc.build_route_payload(action, c)
            self.assertEqual(p, {"motorIzq": "outC", "motorDer": "outD", "gradosIzq": izq,
                                 "gradosDer": der, "potencia": 20})
        with self.assertRaises(ValueError):
            rc.build_route_payload(rc.PARAR, c)

    def test_tampered_config_is_stopped_by_hard_limits_in_code(self):
        c = cfg()
        object.__setattr__(c, "potencia", 80)  # simula una config alterada tras validar
        with self.assertRaises(rc.HardLimitError):
            rc.build_route_payload(rc.AVANZAR, c)
        for bad in ({"gradosIzq": 181, "gradosDer": 90, "potencia": 20},
                    {"gradosIzq": 90, "gradosDer": 90, "potencia": 31},
                    {"gradosIzq": 90.0, "gradosDer": 90, "potencia": 20}):
            with self.assertRaises(rc.HardLimitError):
                rc.check_hard_limits(bad)

    def test_dispatcher_rechecks_limits_before_sending(self):
        t = rc.DryRunTransport()
        failures = []
        d = rc.OrderDispatcher(t, failures.append, threaded=False)
        d.submit_step({"motorIzq": "outC", "motorDer": "outD", "gradosIzq": 360, "gradosDer": 360, "potencia": 20})
        self.assertEqual(t.sent, [])
        self.assertTrue(failures and "tope duro" in failures[0])


class HoldTrackerTests(unittest.TestCase):
    def _steps(self, seq, max_gap=1.0):
        h = rc.HoldTracker(3.0, 3.0, max_gap)
        return [t for g, _, t in seq if h.observe(g, t)]

    def test_first_step_at_3s_then_every_3s_real_time(self):
        steps = self._steps(frames("puño_cerrado", 100.0, 9.5))
        self.assertEqual(len(steps), 3)
        self.assertTrue(103.0 <= steps[0] < 103.15)
        self.assertTrue(106.0 <= steps[1] < 106.15)

    def test_counts_real_time_not_frames(self):
        # Ráfaga de 40 cuadros a 30 ms = 1,2 s reales: no alcanza aunque sean muchos cuadros.
        self.assertEqual(self._steps(frames("puño_cerrado", 0.0, 1.2, period=0.03)), [])
        # A 4 fps tarda lo mismo en tiempo real.
        steps = self._steps(frames("puño_cerrado", 0.0, 3.5, period=0.25))
        self.assertEqual(len(steps), 1)
        self.assertAlmostEqual(steps[0], 3.0, places=6)

    def test_gap_of_1s_or_more_resets_and_shorter_does_not(self):
        a = frames("puño_cerrado", 0.0, 2.0)
        b = frames("puño_cerrado", a[-1][2] + 1.0, 2.0)   # hueco de 1,0 s exacto: reinicia
        self.assertEqual(self._steps(a + b), [])
        c = frames("puño_cerrado", a[-1][2] + 0.9, 2.0)   # 0,9 s: tolera y suma
        self.assertEqual(len(self._steps(a + c)), 1)

    def test_gap_tolerance_is_configurable(self):
        a = frames("puño_cerrado", 0.0, 2.0)
        b = frames("puño_cerrado", a[-1][2] + 1.2, 2.0)
        self.assertEqual(self._steps(a + b, max_gap=1.0), [])
        self.assertEqual(len(self._steps(a + b, max_gap=1.5)), 1)

    def test_change_none_and_clock_going_back_reset(self):
        for breaker in (("dedo_pulgar", 0.9, 2.1), (None, 0.0, 2.1)):
            seq = frames("puño_cerrado", 0.0, 2.0) + [breaker] + frames("puño_cerrado", 2.2, 2.0)
            self.assertEqual(self._steps(seq), [], msg=str(breaker))
        seq = frames("puño_cerrado", 10.0, 2.0) + frames("puño_cerrado", 9.0, 2.5)
        self.assertEqual(self._steps(seq), [])


class ControllerLogicTests(unittest.TestCase):
    def test_disarmed_never_moves(self):
        ctrl, t = dry_controller()
        feed(ctrl, frames("puño_cerrado", 0.0, 10.0))
        self.assertEqual(t.sent, [])

    def test_armed_hold_sends_one_step_per_3s(self):
        ctrl, t = dry_controller()
        ctrl.arm(0.0)
        feed(ctrl, frames("dedo_pulgar", 0.0, 6.5))
        self.assertEqual(paths(t), [("POST", rc.PATH_EJECUTAR)] * 2)
        self.assertEqual(t.sent[0][2]["gradosDer"], -90)

    def test_low_confidence_resets_hold(self):
        ctrl, t = dry_controller()
        ctrl.arm(0.0)
        seq = frames("puño_cerrado", 0.0, 2.0) + [("puño_cerrado", 0.5, 2.1)] + frames("puño_cerrado", 2.2, 2.5)
        feed(ctrl, seq)
        self.assertEqual(t.sent, [])

    def test_parar_is_immediate_without_hold_or_confidence_and_latches(self):
        ctrl, t = dry_controller()
        ctrl.arm(0.0)
        ctrl.observe("palma_abierta", 0.1, 0.0)  # una sola vez, confianza baja
        self.assertEqual(paths(t), STOP_SEQ)
        self.assertEqual(ctrl.state, rc.BLOQUEADO)

    def test_parar_takes_precedence_and_held_parar_does_not_spam(self):
        ctrl, t = dry_controller()
        ctrl.arm(0.0)
        feed(ctrl, frames("puño_cerrado", 0.0, 2.9))
        feed(ctrl, frames("palma_abierta", 3.0, 5.0))
        self.assertEqual(paths(t), STOP_SEQ)  # nada de AVANZAR y una sola parada

    def test_parar_works_even_disarmed(self):
        ctrl, t = dry_controller()
        ctrl.observe("palma_abierta", 0.99, 0.0)
        self.assertEqual(paths(t), STOP_SEQ)

    def test_latch_requires_explicit_rearm_and_fresh_hold(self):
        ctrl, t = dry_controller()
        ctrl.arm(0.0)
        ctrl.observe("palma_abierta", 0.9, 0.0)
        feed(ctrl, frames("puño_cerrado", 1.0, 5.0))
        self.assertEqual(paths(t), STOP_SEQ)  # bloqueado: no avanza
        ctrl.arm(6.2)
        feed(ctrl, frames("puño_cerrado", 6.2, 2.5))  # lo sostenido antes del re-armado no cuenta
        self.assertEqual(paths(t), STOP_SEQ)
        feed(ctrl, frames("puño_cerrado", 8.8, 1.0))
        self.assertEqual(paths(t)[-1], ("POST", rc.PATH_EJECUTAR))

    def test_auto_disarm_after_inactivity_sends_stop(self):
        ctrl, t = dry_controller(cfg(desarme_inactividad_s=10.0))
        ctrl.arm(0.0)
        ctrl.tick(9.9)
        self.assertEqual(ctrl.state, rc.ARMADO)
        ctrl.tick(10.0)
        self.assertEqual(ctrl.state, rc.DESARMADO)
        self.assertEqual(paths(t), STOP_SEQ)
        feed(ctrl, frames("puño_cerrado", 11.0, 4.0))
        self.assertEqual(paths(t), STOP_SEQ)

    def test_steps_count_as_activity(self):
        ctrl, t = dry_controller(cfg(desarme_inactividad_s=5.0))
        ctrl.arm(0.0)
        feed(ctrl, frames("puño_cerrado", 0.0, 12.0))  # pasos a los 3, 6, 9, 12 s
        self.assertEqual(ctrl.state, rc.ARMADO)
        self.assertNotIn(("POST", rc.PATH_STOP), paths(t))

    def test_min_interval_between_steps(self):
        ctrl, t = dry_controller(cfg(min_entre_pasos_s=4.0))
        ctrl.arm(0.0)
        feed(ctrl, frames("puño_cerrado", 0.0, 9.5))  # pasos debidos a 3, 6, 9 -> el de 6 se omite
        self.assertEqual(paths(t), [("POST", rc.PATH_EJECUTAR)] * 2)

    def test_corrupt_inputs_do_not_move(self):
        ctrl, t = dry_controller()
        ctrl.arm(0.0)
        bad = [("dedo_indice", 0.9), ("PUÑO", 0.9), (42, 0.9), ("puño_cerrado", float("nan")),
               ("puño_cerrado", "0.9"), ("puño_cerrado", 1.7), ("puño_cerrado", True), ("ninguno", 0.9)]
        tt = 0.0
        for _ in range(10):
            for g, c in bad:
                ctrl.observe(g, c, tt)
                tt += 0.143
        self.assertEqual(t.sent, [])
        self.assertGreater(ctrl.illegible, 0)

    def test_dry_run_sends_nothing_on_the_network(self):
        sim = RobotSim()
        self.addCleanup(sim.close)
        ctrl, t = dry_controller(cfg(robot_url=sim.url))
        ctrl.arm(0.0)
        feed(ctrl, frames("puño_cerrado", 0.0, 4.0))
        ctrl.observe("palma_abierta", 0.9, 4.2)
        self.assertEqual(len(t.sent), 3)  # lo que habría enviado queda registrado...
        self.assertEqual(sim.requests, [])  # ...pero el robot (simulado) no recibió nada


class HttpWithSimulatorTests(unittest.TestCase):
    def _controller(self, sim, timeout=1.0, **over):
        c = cfg(robot_url=sim.url, timeout_http_s=timeout, **over)
        transport = rc.HttpRobotTransport(c.robot_url, c.timeout_http_s)
        ctrl = rc.RobotController(c, lambda f: rc.OrderDispatcher(transport, f, threaded=True))
        self.addCleanup(ctrl.dispatcher.close)
        return ctrl

    def _sim(self, **kw):
        sim = RobotSim(**kw)
        self.addCleanup(sim.close)
        return sim

    def test_step_reaches_robot_with_send_time_and_response_time(self):
        sim = self._sim()
        ctrl = self._controller(sim)
        ctrl.arm(0.0)
        feed(ctrl, frames("puño_cerrado", 0.0, 3.2))
        self.assertTrue(ctrl.dispatcher.wait_idle(3.0))
        self.assertEqual(sim.paths(), [("POST", rc.PATH_EJECUTAR)])
        self.assertEqual(sim.requests[0][2]["gradosIzq"], 90)
        rec = ctrl.dispatcher.records[0]
        self.assertEqual(rec.status, 200)
        self.assertIsNotNone(rec.sent_wall)
        self.assertGreaterEqual(rec.elapsed_ms, 0.0)
        self.assertEqual(ctrl.state, rc.ARMADO)

    def test_409_is_not_retried(self):
        sim = self._sim(route_duration_s=30.0)
        ctrl = self._controller(sim)
        ctrl.arm(0.0)
        feed(ctrl, frames("puño_cerrado", 0.0, 3.2))
        self.assertTrue(ctrl.dispatcher.wait_idle(3.0))
        feed(ctrl, frames("puño_cerrado", 3.3, 3.0))  # segundo paso: el robot sigue ocupado
        self.assertTrue(ctrl.dispatcher.wait_idle(3.0))
        self.assertEqual(sim.paths(), [("POST", rc.PATH_EJECUTAR)] * 2)
        self.assertEqual([r.status for r in ctrl.dispatcher.records], [200, 409])
        self.assertEqual(ctrl.state, rc.ARMADO)

    def test_timeout_queries_state_then_stops_never_resends_and_latches(self):
        sim = self._sim()
        sim.delay_s = 0.5
        ctrl = self._controller(sim, timeout=0.2)
        ctrl.arm(0.0)
        feed(ctrl, frames("puño_cerrado", 0.0, 3.2))
        self.assertTrue(ctrl.dispatcher.wait_idle(5.0))
        time.sleep(0.6)  # deja que el simulador registre lo que le llegó tarde
        got = sim.paths()
        self.assertEqual(got.count(("POST", rc.PATH_EJECUTAR)), 1)  # nunca se reenvía
        self.assertEqual(got[1:], [("GET", rc.PATH_ESTADO)] + STOP_SEQ)
        self.assertEqual(ctrl.state, rc.BLOQUEADO)
        feed(ctrl, frames("puño_cerrado", 4.0, 4.0))
        self.assertEqual(sim.paths().count(("POST", rc.PATH_EJECUTAR)), 1)

    def test_lost_response_is_handled_like_timeout(self):
        sim = self._sim()
        sim.lose_next = 1
        ctrl = self._controller(sim)
        ctrl.arm(0.0)
        feed(ctrl, frames("puño_cerrado", 0.0, 3.2))
        self.assertTrue(ctrl.dispatcher.wait_idle(3.0))
        self.assertEqual(sim.paths(), [("POST", rc.PATH_EJECUTAR), ("GET", rc.PATH_ESTADO)] + STOP_SEQ)
        self.assertEqual(ctrl.state, rc.BLOQUEADO)

    def test_robot_down_latches_without_crashing(self):
        sim = self._sim()
        ctrl = self._controller(sim, timeout=0.3)
        sim.go_down()
        ctrl.arm(0.0)
        with self.assertLogs("cva_gesture_bridge.actuators.robot_client", level="ERROR") as logs:
            feed(ctrl, frames("puño_cerrado", 0.0, 3.2))
            self.assertTrue(ctrl.dispatcher.wait_idle(5.0))
        self.assertEqual(ctrl.state, rc.BLOQUEADO)
        self.assertTrue(any("PARADA NO CONFIRMADA" in m for m in logs.output))

    def test_vision_loop_never_blocks_and_one_order_in_flight(self):
        sim = self._sim()
        sim.delay_s = 0.5
        ctrl = self._controller(sim, timeout=1.0, min_entre_pasos_s=1.0, sostener_paso_s=0.2)
        ctrl.arm(0.0)
        worst = 0.0
        for g, c, t in frames("puño_cerrado", 0.0, 4.0):
            t0 = time.monotonic()
            ctrl.observe(g, c, t)
            worst = max(worst, time.monotonic() - t0)
        self.assertLess(worst, 0.05)  # observe() nunca espera al robot
        self.assertTrue(ctrl.dispatcher.wait_idle(3.0))
        # se pidieron pasos a 3,0 / 4,0 s (mín. 1 s); el segundo llegó con el primero en vuelo
        self.assertEqual(sim.paths(), [("POST", rc.PATH_EJECUTAR)])

    def test_stop_goes_out_after_in_flight_step_and_cancels_route(self):
        sim = self._sim(route_duration_s=30.0)
        sim.delay_s = 0.3
        ctrl = self._controller(sim)
        ctrl.arm(0.0)
        feed(ctrl, frames("puño_cerrado", 0.0, 3.2))
        time.sleep(0.05)  # el paso está en vuelo
        t0 = time.monotonic()
        ctrl.observe("palma_abierta", 0.9, 3.3)
        self.assertLess(time.monotonic() - t0, 0.05)
        self.assertEqual(ctrl.state, rc.BLOQUEADO)  # bloqueado al instante, sin esperar la red
        self.assertTrue(ctrl.dispatcher.wait_idle(3.0))
        self.assertEqual(sim.paths(), [("POST", rc.PATH_EJECUTAR)] + STOP_SEQ)
        self.assertFalse(sim.executing)  # /detener_ejecucion canceló la ruta en curso

    def test_stop_only_would_not_cancel_route_in_simulator(self):
        # Respaldo del orden detener -> stop: en el simulador /stop solo no cancela.
        sim = self._sim(route_duration_s=30.0)
        t = rc.HttpRobotTransport(sim.url, 1.0)
        self.addCleanup(t.close)
        t.request("POST", rc.PATH_EJECUTAR, rc.build_route_payload(rc.AVANZAR, cfg()))
        t.request("POST", rc.PATH_STOP)
        self.assertTrue(sim.executing)


class DiagParserTests(unittest.TestCase):
    L = [
        "2026-10-23 10:55:49,061 INFO __main__: [diag] Gesto candidato: palma_abierta (confianza=0.97, dedos_extendidos=5, confirmado=False)",
        "2026-10-23 10:55:49,192 INFO __main__: [diag] Gesto candidato: palma_abierta (confianza=1.00, dedos_extendidos=5, confirmado=True)",
        "2026-10-23 10:55:49,192 INFO __main__: Gesto detectado: palma_abierta (confianza=1.00)",
        "2026-10-23 10:55:49,300 INFO cva_gesture_bridge.transport.tcp_server: Frame #12 de ('127.0.0.1', 1): 1 bytes, fps_real=7.00, latencia_ms=1.0",
        "2026-10-23 10:55:49,330 INFO __main__: [diag] Sin gesto reconocido en este frame (dedos_extendidos=0)",
        "2026-10-23 10:55:53,946 INFO __main__: Gesto liberado (antes: palma_abierta)",
    ]

    def test_real_format_associates_confirmation_with_its_own_frame(self):
        p = rr.DiagFrameParser()
        evs = []
        for i, line in enumerate(self.L):
            evs += p.feed(line, float(i))
        # main.py escribe "Gesto detectado"/"Gesto liberado" DESPUÉS del [diag] del mismo
        # cuadro: la confirmación pertenece al cuadro 1 y la liberación al cuadro 4.
        self.assertEqual([(e.gesture, e.t) for e in evs],
                         [(None, 0.0), ("palma_abierta", 1.0), (None, 4.0)])
        self.assertEqual(p.illegible, 0)
        self.assertEqual(evs[1].confidence, 1.0)
        self.assertIsNone(p.confirmed)

    def test_frame_without_change_keeps_last_matching_confidence(self):
        p = rr.DiagFrameParser()
        evs = []
        for i, line in enumerate(self.L[:5]):
            evs += p.feed(line, float(i))
        evs += p.flush()
        self.assertEqual((evs[-1].gesture, evs[-1].confidence, evs[-1].t), ("palma_abierta", 1.0, 4.0))

    def test_corrupt_lines_are_counted_and_produce_nothing(self):
        p = rr.DiagFrameParser()
        bad = [
            "2026-10-23 10:55:49,061 INFO __main__: [diag] Gesto candidato: palma_abierta (confianza=1.0.0, dedos_extendidos=5, confirmado=True)",
            "basura [diag] sin hora",
            "2026-10-23 10:55:49,192 INFO __main__: Gesto detectado: palma_abierta (confianza=)",
            "2026-10-23 10:55:49,192 INFO __main__: [diag] Gesto candidato: puño_cerrado (confian",
        ]
        evs = [e for line in bad for e in p.feed(line, 0.0)] + p.flush()
        self.assertEqual(evs, [])
        self.assertEqual(p.illegible, 4)

    def test_connection_end_clears_confirmed_gesture(self):
        p = rr.DiagFrameParser()
        p.feed(self.L[1], 1.0)
        p.feed(self.L[2], 1.0)
        evs = p.feed("2026-10-23 11:01:27,617 INFO x: Conexión con ('127.0.0.1', 60756) finalizada. total_frames=1", 2.0)
        self.assertIsNone(evs[-1].gesture)
        self.assertIsNone(p.confirmed)


class RunnerReplayTests(unittest.TestCase):
    def _log(self, lines):
        f = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False, encoding="utf-8")
        f.write("\n".join(lines) + "\n")
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def _frames_lines(self, gesture, start_s, seconds, period=0.143):
        out = []
        for i in range(int(seconds / period) + 1):
            t = start_s + i * period
            ts = time.strftime("2026-10-23 10:%M:%S", time.gmtime(t)) + f",{int((t % 1) * 1000):03d}"
            out.append(f"{ts} INFO __main__: [diag] Gesto candidato: {gesture} (confianza=0.95, dedos_extendidos=0, confirmado=True)")
            if i == 0:
                out.append(f"{ts} INFO __main__: Gesto detectado: {gesture} (confianza=0.95)")
        return out

    def test_replay_dry_run_end_to_end(self):
        path = self._log(self._frames_lines("puño_cerrado", 0.0, 3.5))
        conf = os.path.join(REPO, "config", "robot_actuador.json")
        with self.assertLogs("cva_gesture_bridge.actuators.robot_client", level="INFO") as logs:
            rc_ = rr.main(["--config", conf, "--reproducir", path, "--armar-al-inicio"])
        self.assertEqual(rc_, 0)
        sent = [m for m in logs.output if "ENSAYO: enviaría" in m]
        self.assertEqual(sum("ejecutar_ruta" in m for m in sent), 1)
        self.assertTrue(sent[-1].endswith("/stop ") or "/stop" in sent[-1])  # al salir manda parada

    def test_runner_refuses_real_without_url_and_replay_in_real(self):
        conf = os.path.join(REPO, "config", "robot_actuador.json")
        path = self._log(["x"])
        with self.assertLogs("cva_gesture_bridge.actuators.robot_runner", level="ERROR"):
            self.assertEqual(rr.main(["--config", conf, "--seguir", path, "--real"]), 2)
        with self.assertLogs("cva_gesture_bridge.actuators.robot_runner", level="ERROR"):
            self.assertEqual(rr.main(["--config", conf, "--reproducir", path, "--real"]), 2)


if __name__ == "__main__":
    unittest.main()
