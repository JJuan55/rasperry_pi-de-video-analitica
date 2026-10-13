"""Tests de las herramientas de Fase D, sesión 2 (benchmarks/) -- no se importan
desde el paquete, pero cada una lleva su test (pedido explícito de JD). Importa
los módulos de benchmarks/ directamente (no son parte de cva_gesture_bridge).
"""

import os
import sys

BENCHMARKS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "benchmarks")
sys.path.insert(0, BENCHMARKS_DIR)

from fase_d_schedule import build_sequence  # noqa: E402
import analyze_fase_d_run as afr  # noqa: E402
import analyze_hard_cases as ahc  # noqa: E402


# --- C: build_sequence (repeticiones configurables) -------------------------


def test_build_sequence_default_repeats_is_10_per_gesture():
    gestures = ["a", "b", "c", "d"]
    seq = build_sequence(gestures, 10)
    assert len(seq) == 40
    assert seq[:10] == ["a"] * 10
    assert seq[10:20] == ["b"] * 10
    assert seq[20:30] == ["c"] * 10
    assert seq[30:40] == ["d"] * 10


def test_build_sequence_short_battery_5_per_gesture():
    gestures = ["puño_cerrado", "palma_abierta", "dedo_pulgar", "dedo_menique"]
    seq = build_sequence(gestures, 5)
    assert len(seq) == 20
    assert seq.count("puño_cerrado") == 5
    assert seq.count("palma_abierta") == 5
    assert seq.count("dedo_pulgar") == 5
    assert seq.count("dedo_menique") == 5
    # orden en bloques, no intercalado
    assert seq == (
        ["puño_cerrado"] * 5 + ["palma_abierta"] * 5
        + ["dedo_pulgar"] * 5 + ["dedo_menique"] * 5
    )


# --- A: descubrimiento de rotación + filtro por ventana ---------------------


def test_discover_rotated_logs_orders_by_real_content_not_filename(tmp_path):
    base = tmp_path / "cva_gesture_bridge.log"
    rotated_old = tmp_path / "cva_gesture_bridge.log.2026-10-08"
    rotated_newer_name_older_content = tmp_path / "cva_gesture_bridge.log.2026-10-09"

    # Contenido real: el rotado ".2026-10-09" en realidad tiene timestamps MÁS
    # VIEJOS que el ".2026-10-08" -- a propósito, para probar que el orden se
    # saca del contenido, no del nombre del archivo.
    rotated_newer_name_older_content.write_text(
        "2026-10-08 23:00:00,000 INFO x: linea vieja de verdad\n"
    )
    rotated_old.write_text(
        "2026-10-08 23:30:00,000 INFO x: linea mas nueva que la de arriba\n"
    )
    base.write_text(
        "2026-10-09 00:10:00,000 INFO x: linea del archivo activo\n"
    )

    files = afr.discover_rotated_logs(str(base))
    assert files == [
        str(rotated_newer_name_older_content),
        str(rotated_old),
        str(base),
    ]


def test_read_lines_with_rotation_merges_across_midnight_and_filters_window(tmp_path):
    base = tmp_path / "cva_gesture_bridge.log"
    rotated = tmp_path / "cva_gesture_bridge.log.2026-10-08"

    rotated.write_text(
        "2026-10-08 23:55:00,000 INFO x: antes de medianoche, fuera de ventana\n"
        "2026-10-08 23:59:00,000 INFO x: antes de medianoche, dentro de ventana\n"
    )
    base.write_text(
        "2026-10-09 00:01:00,000 INFO x: despues de medianoche, dentro de ventana\n"
        "2026-10-09 00:10:00,000 INFO x: despues de medianoche, fuera de ventana\n"
    )

    desde = afr.parse_window_ts("2026-10-08 23:58:00")
    hasta = afr.parse_window_ts("2026-10-09 00:05:00")
    lines = afr.read_lines_with_rotation(str(base), desde, hasta)

    assert len(lines) == 2
    assert "dentro de ventana" in lines[0]
    assert "dentro de ventana" in lines[1]
    assert "23:59:00" in lines[0]
    assert "00:01:00" in lines[1]


def test_read_lines_with_rotation_no_window_reads_everything():
    # Sin --desde/--hasta, concatena todo lo relacionado sin filtrar.
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "x.log")
        rotated = os.path.join(d, "x.log.2026-01-01")
        with open(rotated, "w") as f:
            f.write("2026-01-01 00:00:00,000 INFO x: una\n")
        with open(base, "w") as f:
            f.write("2026-01-02 00:00:00,000 INFO x: dos\n")
        lines = afr.read_lines_with_rotation(base)
        assert len(lines) == 2


def test_parse_window_ts_accepts_with_and_without_milliseconds():
    a = afr.parse_window_ts("2026-10-08 13:43:30,500")
    b = afr.parse_window_ts("2026-10-08 13:43:30")
    assert a.hour == 13 and a.minute == 43 and a.microsecond == 500000
    assert b.microsecond == 0


# --- B: hand_present + tiempo de liberación del sistema ---------------------


def test_hand_present_false_only_when_gesture_none_and_zero_extended():
    assert afr.hand_present(None, 0) is False
    assert afr.hand_present(None, 2) is True  # mano presente, forma ambigua
    assert afr.hand_present("puño_cerrado", 0) is True  # mano real, 0 extendidos


def test_build_events_captures_extended_fingers_from_real_log_format():
    lines = [
        "2026-10-08 13:43:50,100 INFO __main__: [diag] Gesto candidato: puño_cerrado (confianza=0.99, dedos_extendidos=0, confirmado=False)\n",
        "2026-10-08 13:43:50,250 INFO __main__: [diag] Sin gesto reconocido en este frame (dedos_extendidos=0)\n",
        "2026-10-08 13:43:50,400 INFO __main__: Gesto detectado: puño_cerrado (confianza=0.99)\n",
        "2026-10-08 13:43:55,000 INFO __main__: Gesto liberado (antes: puño_cerrado)\n",
    ]
    events = afr.build_events(lines)
    kinds = [e[1] for e in events]
    assert kinds == ["diag_candidato", "diag_none", "detectado", "liberado"]
    assert events[0][2] == ("puño_cerrado", 0.99, 0)
    assert events[1][2] == 0


# --- E: evaluación de casos difíciles ---------------------------------------


def test_parse_hard_case_cue_log_extracts_windows_in_order():
    lines = (
        "2026-10-13 10:00:00,000 CUE: PREPARA -- instrucciones caso 1\n"
        "2026-10-13 10:00:10,000 CUE: ARRANCA caso 'movimiento_rapido' (objetivo=None) -- sostener 30s\n"
        "2026-10-13 10:00:40,000 CUE: TERMINA caso 'movimiento_rapido'\n"
        "2026-10-13 10:00:40,000 CUE: PREPARA -- instrucciones caso 2\n"
        "2026-10-13 10:00:50,000 CUE: ARRANCA caso 'mano_tapada_palma_abierta' (objetivo=palma_abierta) -- sostener 30s\n"
        "2026-10-13 10:01:20,000 CUE: TERMINA caso 'mano_tapada_palma_abierta'\n"
    )
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
        f.write(lines)
        path = f.name
    try:
        cases = ahc.parse_hard_case_cue_log(path)
    finally:
        os.unlink(path)

    assert len(cases) == 2
    assert cases[0][0] == "movimiento_rapido"
    assert cases[0][1] is None
    assert cases[1][0] == "mano_tapada_palma_abierta"
    assert cases[1][1] == "palma_abierta"


def test_evaluate_case_persona_detras_any_detection_is_not_acceptable():
    ok, detail = ahc.evaluate_case("persona_detras_sin_gesto", None, [])
    assert ok is True
    ok, detail = ahc.evaluate_case(
        "persona_detras_sin_gesto", None, [(None, "palma_abierta", 0.9)]
    )
    assert ok is False
    assert "NO ACEPTABLE" in detail


def test_evaluate_case_target_gesture_cases_accept_nothing_or_target():
    # aceptable: nada
    ok, _ = ahc.evaluate_case("mano_tapada_palma_abierta", "palma_abierta", [])
    assert ok is True
    # aceptable: el objetivo
    ok, _ = ahc.evaluate_case(
        "mano_tapada_palma_abierta", "palma_abierta", [(None, "palma_abierta", 0.9)]
    )
    assert ok is True
    # NO aceptable: un gesto distinto al objetivo
    ok, detail = ahc.evaluate_case(
        "mano_tapada_palma_abierta", "palma_abierta", [(None, "puño_cerrado", 0.9)]
    )
    assert ok is False
    assert "NO ACEPTABLE" in detail


def test_evaluate_case_movimiento_rapido_is_not_auto_verdicted():
    ok, detail = ahc.evaluate_case(
        "movimiento_rapido", None, [(None, "puño_cerrado", 0.9), (None, "palma_abierta", 0.9)]
    )
    assert ok is None  # no se fuerza un veredicto automático
    assert "no verificable automáticamente" in detail.lower() or "NO verificable automáticamente" in detail


def test_detections_in_window_filters_by_timestamp():
    from datetime import datetime
    events = [
        (datetime(2026, 1, 1, 10, 0, 0), "detectado", ("puño_cerrado", 0.9)),
        (datetime(2026, 1, 1, 10, 0, 5), "detectado", ("palma_abierta", 0.9)),
        (datetime(2026, 1, 1, 10, 1, 0), "detectado", ("dedo_pulgar", 0.9)),
    ]
    dets = ahc.detections_in_window(
        events, datetime(2026, 1, 1, 10, 0, 0), datetime(2026, 1, 1, 10, 0, 30)
    )
    assert len(dets) == 2
    assert dets[0][1] == "puño_cerrado"
    assert dets[1][1] == "palma_abierta"


# --- Correcciones de la revisión (rama fase-d-herramientas-fix) -------------
# a) liberación del sistema = (cuadros sin gesto necesarios) x (periodo)


def _synthetic_hold_then_no_hand(period_ms, hold_frames=7, absent_frames=25):
    """Secuencia cruda sintética a periodo fijo: `hold_frames` cuadros con
    palma_abierta (5 dedos), luego `absent_frames` cuadros sin mano
    (gesto=None, dedos=0 -> hand_present=False)."""
    from datetime import datetime, timedelta
    t0 = datetime(2026, 10, 13, 10, 0, 0)
    seq = []
    for i in range(hold_frames + absent_frames):
        ts = t0 + timedelta(milliseconds=i * period_ms)
        if i < hold_frames:
            seq.append((ts, "palma_abierta", 5))
        else:
            seq.append((ts, None, 0))
    return seq


def test_system_release_equals_required_misses_times_frame_period():
    from cva_gesture_bridge.vision.detector import GestureStabilizer
    misses_needed = GestureStabilizer()._release_after_misses_no_hand

    for period_ms in (143, 250):
        raw = _synthetic_hold_then_no_hand(period_ms)
        confirm_events, release_events = afr.replay_stabilizer(raw)
        assert len(confirm_events) == 1
        assert len(release_events) == 1
        times, skipped = afr.system_release_times_ms(release_events)
        assert skipped == 0
        assert times == [misses_needed * period_ms]


def test_system_release_changes_when_frame_period_changes():
    # Mismos cuadros, distinto fps -> distinta liberación: el número depende
    # del periodo real entre cuadros, no es una constante del sistema.
    at_7fps, _ = afr.system_release_times_ms(afr.replay_stabilizer(_synthetic_hold_then_no_hand(143))[1])
    at_4fps, _ = afr.system_release_times_ms(afr.replay_stabilizer(_synthetic_hold_then_no_hand(250))[1])
    assert at_7fps != at_4fps
    assert at_4fps[0] > at_7fps[0]


def test_raw_observations_from_events_keeps_order_and_none():
    from datetime import datetime
    events = [
        (datetime(2026, 1, 1, 10, 0, 0), "diag_candidato", ("puño_cerrado", 0.9, 0)),
        (datetime(2026, 1, 1, 10, 0, 1), "detectado", ("puño_cerrado", 0.9)),
        (datetime(2026, 1, 1, 10, 0, 2), "diag_none", 0),
    ]
    raw = afr.raw_observations_from_events(events)
    assert raw == [
        (datetime(2026, 1, 1, 10, 0, 0), "puño_cerrado", 0),
        (datetime(2026, 1, 1, 10, 0, 2), None, 0),
    ]


# b) emparejamiento QUITA <-> liberación por tiempo


def _cue(sec, rep, kind, gesture=None):
    from datetime import datetime, timedelta
    return (datetime(2026, 10, 13, 10, 0, 0) + timedelta(seconds=sec), rep, kind, gesture)


def _ts(sec):
    from datetime import datetime, timedelta
    return datetime(2026, 10, 13, 10, 0, 0) + timedelta(seconds=sec)


def test_match_quita_to_releases_by_time_with_one_rep_without_release():
    cues = [
        _cue(0, 1, "HAZ", "puño_cerrado"), _cue(3, 1, "QUITA"),
        _cue(8, 2, "HAZ", "puño_cerrado"), _cue(11, 2, "QUITA"),
        _cue(16, 3, "HAZ", "puño_cerrado"), _cue(19, 3, "QUITA"),
    ]
    releases = [
        _ts(1.5),   # liberación espuria DURANTE el sostenido de la rep 1 -- no cuenta
        _ts(5.5),   # rep 1: QUITA a los 3s -> 2500ms
        # rep 2: ninguna liberación entre la QUITA (11s) y el siguiente HAZ (16s)
        _ts(16.5),  # después del HAZ de la rep 3 -- NO debe asignarse a la rep 2
        _ts(21.4),  # rep 3: QUITA a los 19s -> 2400ms
    ]
    pairs = afr.match_quita_to_releases(cues, releases)
    assert [p[0] for p in pairs] == [1, 2, 3]
    assert pairs[0][2] == _ts(5.5) and round(pairs[0][3]) == 2500
    assert pairs[1][2] is None and pairs[1][3] is None
    assert pairs[2][2] == _ts(21.4) and round(pairs[2][3]) == 2400
    # el emparejamiento por ORDEN (versión anterior) habría asignado 1.5s a la
    # rep 1 (tiempo negativo) y descartado todo por conteos distintos.
    assert sum(1 for p in pairs if p[2] is None) == 1


def test_match_quita_last_rep_has_no_upper_bound():
    cues = [_cue(0, 1, "HAZ", "a"), _cue(3, 1, "QUITA")]
    pairs = afr.match_quita_to_releases(cues, [_ts(5.3)])
    assert pairs == [(1, _ts(3), _ts(5.3), 2300.0)]


def test_summarize_ms_nearest_rank_percentiles():
    vals = list(range(1, 21))  # 1..20
    s = afr.summarize_ms(vals)
    assert s["n"] == 20 and s["min"] == 1 and s["max"] == 20
    assert s["p50"] == 11 and s["p95"] == 19
    assert s["avg"] == 10.5
    assert afr.summarize_ms([]) is None


# c) casos difíciles: gesto activo al empezar + liberación dentro del bloque


def test_active_gesture_at_tracks_last_detected_or_released():
    events = [
        (_ts(0), "detectado", ("palma_abierta", 0.9)),
        (_ts(2), "detectado", ("puño_cerrado", 0.9)),  # cambio A->B sin liberar
        (_ts(5), "liberado", "puño_cerrado"),
        (_ts(8), "detectado", ("dedo_pulgar", 0.9)),
    ]
    assert ahc.active_gesture_at(events, _ts(1)) == "palma_abierta"
    assert ahc.active_gesture_at(events, _ts(3)) == "puño_cerrado"
    assert ahc.active_gesture_at(events, _ts(6)) is None
    assert ahc.active_gesture_at(events, _ts(8)) is None  # estrictamente anterior
    assert ahc.active_gesture_at(events, _ts(9)) == "dedo_pulgar"
    assert ahc.active_gesture_at([], _ts(9)) is None


def test_releases_in_window_filters_by_timestamp():
    events = [
        (_ts(1), "liberado", "palma_abierta"),
        (_ts(4), "detectado", ("puño_cerrado", 0.9)),
        (_ts(6), "liberado", "puño_cerrado"),
    ]
    assert ahc.releases_in_window(events, _ts(2), _ts(10)) == [(_ts(6), "puño_cerrado")]


def test_persona_detras_active_gesture_crossing_start_is_not_acceptable():
    # Sin ningún envío dentro del bloque, pero con un gesto vigente desde antes:
    ok, detail = ahc.evaluate_case(
        "persona_detras_sin_gesto", None, [], active_at_start="palma_abierta"
    )
    assert ok is False
    assert "NO ACEPTABLE" in detail and "ACTIVO al empezar" in detail
    # Nada activo y nada enviado: correcto.
    ok, _ = ahc.evaluate_case("persona_detras_sin_gesto", None, [], active_at_start=None)
    assert ok is True


def test_active_gesture_at_start_does_not_change_verdict_of_other_cases():
    ok, _ = ahc.evaluate_case(
        "mano_tapada_palma_abierta", "palma_abierta", [], active_at_start="puño_cerrado"
    )
    assert ok is True


# --- Repetición guiada de los casos 1, 4 y 5 --------------------------------

import fase_d_hard_cases as fhc  # noqa: E402


def test_select_cases_keeps_fixed_order_and_rejects_unknown():
    sel = fhc.select_cases("persona_detras_sin_gesto,movimiento_rapido,dos_manos_dedo_pulgar")
    assert [c[0] for c in sel] == ["movimiento_rapido", "dos_manos_dedo_pulgar", "persona_detras_sin_gesto"]
    assert len(fhc.select_cases(None)) == 5
    import pytest
    with pytest.raises(ValueError):
        fhc.select_cases("movimiento_rapido,no_existe")


def test_build_case_steps_movimiento_rapido_two_rounds_of_eight_in_order():
    steps, block = fhc.build_case_steps("movimiento_rapido")
    assert len(steps) == 16
    assert block == 16 * fhc.CHANGE_SECONDS
    assert [s[0] for s in steps] == [i * fhc.CHANGE_SECONDS for i in range(16)]
    gestures = [s[1].split("a: ")[1] for s in steps]
    assert gestures == fhc.CHANGE_ORDER * 4
    assert steps[0][1] == "CAMBIA [1/16] a: puño_cerrado"
    # dos gestos consecutivos nunca son iguales: cada señal es un cambio real
    assert all(a != b for a, b in zip(gestures, gestures[1:]))


def test_build_case_steps_dos_manos_and_persona_detras():
    steps, block = fhc.build_case_steps("dos_manos_dedo_pulgar")
    assert block == fhc.BLOCK_SECONDS
    assert len(steps) == 1 and steps[0][0] == fhc.SECOND_HAND_AT_SECONDS
    assert steps[0][1].startswith("METE")
    steps, _ = fhc.build_case_steps("persona_detras_sin_gesto")
    assert [s[0] for s in steps] == list(fhc.PERSON_PASSES_AT_SECONDS)
    assert all(s[1].startswith("PASA") for s in steps)
    assert fhc.build_case_steps("mano_tapada_palma_abierta") == ([], fhc.BLOCK_SECONDS)


def test_parse_inner_cues_and_arranca_still_parses(tmp_path):
    cue = tmp_path / "cue.log"
    cue.write_text(
        "2026-10-13 10:00:10,000 CUE: ARRANCA caso 'movimiento_rapido' (objetivo=None) -- bloque de 32s\n"
        "2026-10-13 10:00:10,001 CUE: CAMBIA [1/16] a: puño_cerrado\n"
        "2026-10-13 10:00:12,000 CUE: CAMBIA [2/16] a: palma_abierta\n"
        "2026-10-13 10:00:42,000 CUE: TERMINA caso 'movimiento_rapido'\n"
        "2026-10-13 10:00:58,000 CUE: METE la segunda mano ABIERTA (sin sacar la del pulgar)\n"
        "2026-10-13 10:01:40,000 CUE: PASA [1/2] -- la otra persona cruza detrás, brazos abajo\n",
        encoding="utf-8",
    )
    inner = ahc.parse_inner_cues(str(cue))
    assert [(k, g) for _, k, g in inner][:2] == [("CAMBIA", "puño_cerrado"), ("CAMBIA", "palma_abierta")]
    assert [k for _, k, _ in inner][2:] == ["METE", "PASA"]
    cases = ahc.parse_hard_case_cue_log(str(cue))
    assert cases[0][0] == "movimiento_rapido" and cases[0][1] is None


def _changes(*gestures, every=2.0):
    return [(_ts(i * every), g) for i, g in enumerate(gestures)]


def test_guided_changes_one_send_per_change_is_ok_and_missed_change_is_not_failure():
    changes = _changes("puño_cerrado", "palma_abierta", "dedo_pulgar", "dedo_menique")
    dets = [
        (_ts(0.9), "puño_cerrado", 1.0),
        (_ts(2.8), "palma_abierta", 1.0),
        # dedo_pulgar (cambio 3) nunca se confirmó
        (_ts(7.1), "dedo_menique", 1.0),
    ]
    ok, summary = ahc.evaluate_guided_changes(changes, dets)
    assert ok is True
    assert [i for i, _ in summary["asignados"]] == [1, 2, 4]
    assert summary["sin_envio"] == [3]
    assert round(summary["asignados"][0][1]) == 900


def test_guided_changes_wrong_gesture_and_duplicate_are_not_acceptable():
    changes = _changes("puño_cerrado", "palma_abierta")
    # gesto intermedio no pedido (dedo_pulgar) al pasar de puño a palma
    ok, summary = ahc.evaluate_guided_changes(
        changes, [(_ts(0.8), "puño_cerrado", 1.0), (_ts(2.4), "dedo_pulgar", 0.9), (_ts(2.9), "palma_abierta", 1.0)]
    )
    assert ok is False
    assert summary["sin_asignar"] == [(_ts(2.4), "dedo_pulgar")]
    # el mismo cambio enviado dos veces (ida y vuelta del mismo gesto)
    ok, summary = ahc.evaluate_guided_changes(
        changes, [(_ts(0.8), "puño_cerrado", 1.0), (_ts(1.5), "puño_cerrado", 1.0)]
    )
    assert ok is False
    assert summary["duplicados"] == [(1, _ts(1.5), "puño_cerrado")]


def test_guided_changes_send_beyond_tolerance_is_unassigned():
    changes = _changes("puño_cerrado", "palma_abierta")
    late = 2 * fhc.CHANGE_SECONDS + 0.5
    ok, summary = ahc.evaluate_guided_changes(changes, [(_ts(late), "puño_cerrado", 1.0)])
    assert ok is False
    assert summary["sin_asignar"] == [(_ts(late), "puño_cerrado")]
