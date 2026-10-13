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
